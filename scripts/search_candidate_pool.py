#!/usr/bin/env python3
"""Collect on-policy candidate states and active-sample them for teacher search.

Only selected candidates are handed to the expensive teacher; the full pool
stays on disk for later generations.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from mj.belief import BeliefProfile
from mj.decision.profile import fingerprint
from mj.features import action_to_flat
from mj.game import Game
from mj.training.active_sampling import (
    ActiveSamplingProfile,
    CandidateState,
    active_sampling_profile_from_json,
    classify_candidate,
    pool_manifest,
    sample_pool,
    write_candidate_pool,
)
from mj.training.distillation_profile import (
    FROZEN_SPLITS,
    OpponentPopulationProfile,
)
from mj.training.teacher_budget import TeacherBudgetProfile
from mj.training.search_data import state_identity
from mj.training.teacher_generate import (
    PopulationChooser,
    SourceGameSpec,
    decision_snapshot,
    policy_from_source,
    scheduled_specs,
)


def _flat_mask(legal_actions):
    mask = [False] * 109
    for action in legal_actions:
        mask[action_to_flat(int(action))] = True
    return tuple(mask)


def _entropy(probabilities):
    values = [max(0.0, float(value)) for value in probabilities.values()]
    total = sum(values)
    if total <= 0:
        return None
    return -sum((value / total) * math.log(value / total)
                for value in values if value > 0) / math.log(max(2, len(values)))


def _model_for(source):
    if source.startswith(("checkpoint:", "policy_value:")):
        from mj.decision.policy_v3 import load_policy_value_model

        return load_policy_value_model(source.split(":", 1)[1])
    return None


def _student_action_and_probs(model, policy, game, seat, legal):
    if model is None:
        action = int(policy.choose(game, seat))
        probabilities = {int(action): 1.0}
        return action, probabilities
    distribution = model.predict_game(game, seat, legal_actions=tuple(legal))
    probabilities = {int(action): float(probability)
                     for action, probability in zip(distribution.actions,
                                                    distribution.probabilities)}
    action = max(probabilities, key=lambda item: probabilities[item])
    return int(action), probabilities


def collect_spec(spec, *, policy_source, population, belief_profile,
                 generation, critical_tags, cheap_source=None):
    model = _model_for(policy_source)
    hero_policy = policy_from_source(policy_source)
    cheap = policy_from_source(cheap_source) if cheap_source else None
    chooser = PopulationChooser(population)
    game = Game(seed=spec.seed, dealer=spec.dealer,
                you_cai_bi_kao=spec.you_cai_bi_kao)
    candidates = []
    while not game.done:
        actor = int(game.current_seat())
        legal = tuple(int(action) for action in game.legal_actions())
        if not legal:
            raise RuntimeError("non-terminal state has no legal actions")
        if actor == spec.hero_seat:
            action, probabilities = _student_action_and_probs(
                model, hero_policy, game, actor, legal)
            if action not in legal:
                raise RuntimeError("student policy selected an illegal action")
            snapshot = decision_snapshot(
                game, actor, belief_profile=belief_profile, hero_action=action)
            cheap_action = None
            if cheap is not None:
                cheap_action = action_to_flat(
                    int(cheap.choose(game, actor)))
            flat_probabilities = {action_to_flat(key): value
                                  for key, value in probabilities.items()}
            source, importance, regret = classify_candidate(
                legal_mask=_flat_mask(snapshot.legal_actions),
                policy_action=action_to_flat(action),
                policy_prob_by_action=flat_probabilities,
                special_tags=snapshot.tags,
                critical_tags=critical_tags or (),
                cheap_teacher_action=cheap_action)
            candidates.append(CandidateState(
                state_id=state_identity(snapshot.context.context_hash,
                                        snapshot.history.history_hash),
                source_group=spec.source_group,
                generation=int(generation),
                policy_version_source=getattr(hero_policy, "version", ""),
                context=snapshot.context.as_json(),
                history=snapshot.history.as_json(),
                legal_mask=_flat_mask(snapshot.legal_actions),
                planes=snapshot.planes, scalars=snapshot.scalars,
                policy_action=action_to_flat(action),
                policy_prob_by_action=flat_probabilities,
                policy_entropy=_entropy(flat_probabilities),
                special_state_tags=snapshot.tags, phase=snapshot.phase,
                dealer=snapshot.dealer, hero_seat=snapshot.hero_seat,
                you_cai_bi_kao=snapshot.you_cai_bi_kao,
                shanten=snapshot.shanten,
                wall_remaining=snapshot.wall_remaining,
                state_source=(source if source != "forced" else "forced"),
                importance_factor=importance, policy_regret=regret))
        if actor == spec.hero_seat:
            game.step(action)
        else:
            _, policy, _ = chooser.resolve(
                generation=generation, source_group=spec.source_group,
                seat=actor)
            game.step(int(policy.choose(game, actor)))
    return candidates


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True,
                        help="full candidate pool JSONL")
    parser.add_argument("--selected-out", type=Path, default=None,
                        help="active-sampled selection for teacher labeling")
    parser.add_argument("--manifest-out", type=Path, default=None)
    parser.add_argument("--policy-source", default="heuristic:shape-v2")
    parser.add_argument("--cheap-teacher", default=None,
                        help="optional cheap teacher action source for disagreement")
    parser.add_argument("--population", default="legacy=1,shape-v1=1,shape-v2=1")
    parser.add_argument("--generation", type=int, default=0)
    parser.add_argument("--split", choices=sorted(FROZEN_SPLITS), default="train")
    parser.add_argument("--games", type=int, default=None)
    parser.add_argument("--limit-specs", type=int, default=0)
    parser.add_argument("--ycbk", choices=("off", "on", "both"), default="off")
    parser.add_argument("--belief-particles", type=int, default=0)
    parser.add_argument("--belief-seed", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=0,
                        help="active-sampled selection size; 0 selects everything")
    parser.add_argument("--sampling-json", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    members = tuple((item.split("=", 1)[0], float(item.split("=", 1)[1]))
                    if "=" in item else (item, 1.0)
                    for item in args.population.split(",") if item)
    population = OpponentPopulationProfile(members=members)
    if args.sampling_json:
        sampling = active_sampling_profile_from_json(
            json.loads(args.sampling_json.read_text(encoding="utf-8")))
    else:
        sampling = ActiveSamplingProfile(seed=args.seed)
    belief = BeliefProfile(particle_count=args.belief_particles or 512,
                           seed=args.belief_seed)
    row = FROZEN_SPLITS[args.split]
    games = args.games if args.games is not None else int(row["games"])
    variants = {"off": (False,), "on": (True,), "both": (False, True)}[args.ycbk]
    specs = scheduled_specs(seed_start=row["seed_start"], games=games,
                            ycbk_variants=variants)
    if args.limit_specs:
        specs = specs[:int(args.limit_specs)]

    pool = []
    for spec in specs:
        candidates = collect_spec(
            spec, policy_source=args.policy_source, population=population,
            belief_profile=belief, generation=args.generation,
            critical_tags=TeacherBudgetProfile().critical_tags,
            cheap_source=args.cheap_teacher)
        pool.extend(candidates)
    write_candidate_pool(args.out, pool)
    print(json.dumps({"pool_out": str(args.out), "candidates": len(pool)}))

    selected = (sample_pool(pool, batch_size=args.batch_size,
                            profile=sampling, seed=args.seed)
                if args.batch_size > 0 else pool)
    if args.selected_out:
        write_candidate_pool(args.selected_out, selected)
    manifest = pool_manifest(pool, profile=sampling, selected=selected)
    manifest.update({"policy_source": args.policy_source,
                     "generation": args.generation, "split": args.split,
                     "specs": len(specs)})
    if args.selected_out:
        manifest["selected_out"] = str(args.selected_out)
    manifest["fingerprint"] = fingerprint(manifest, 24)
    if args.manifest_out:
        args.manifest_out.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"selected": len(selected),
                      "selected_out": (str(args.selected_out)
                                       if args.selected_out else None),
                      "manifest_fingerprint": manifest["fingerprint"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
