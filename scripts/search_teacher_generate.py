#!/usr/bin/env python3
"""Generate a search-teacher distillation dataset.

The teacher is offline only: trajectories come from a declared policy plus a
frozen opponent population, and every multi-action hero state is labelled by
the adaptive information-set search.  Hidden worlds never enter a sample.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from mj.training.distillation_profile import (
    FROZEN_SPLITS,
    OpponentPopulationProfile,
    SearchDistillationProfile,
    opponent_population_from_json,
    search_distillation_profile_from_json,
)
from mj.training.teacher_budget import (
    TeacherBudgetProfile,
    teacher_budget_profile_from_json,
)
from mj.training.teacher_generate import (
    GenerationConfig,
    generate_dataset,
    generation_manifest,
    generation_runtime_fingerprint,
    require_compatible_kernel,
    resume_dataset,
    scheduled_specs,
    write_reference_contexts,
)
from mj.training.search_data import write_search_dataset


def _population(value):
    if not value:
        return OpponentPopulationProfile()
    members = []
    for item in str(value).split(","):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            name, weight = item.split("=", 1)
            members.append((name.strip(), float(weight)))
        else:
            members.append((item, 1.0))
    return OpponentPopulationProfile(members=tuple(members))


def _profiles(args):
    if args.population_json:
        population = opponent_population_from_json(
            json.loads(Path(args.population_json).read_text(encoding="utf-8")))
    else:
        population = _population(args.population)
    if args.budget_json:
        budget = teacher_budget_profile_from_json(
            json.loads(Path(args.budget_json).read_text(encoding="utf-8")))
    else:
        budget = TeacherBudgetProfile(
            search_seed=args.teacher_seed,
            forced_sanity_ratio=args.forced_sanity_ratio)
    if args.search_json:
        from mj.search.profile import search_profile_from_json
        search = search_profile_from_json(
            json.loads(Path(args.search_json).read_text(encoding="utf-8")))
    else:
        from mj.search import SearchProfile
        search = SearchProfile(
            version=args.search_version,
            simulation_budget=args.search_simulations,
            seed=args.search_seed,
            wall_clock_ms=args.search_wall_clock_ms)
    if args.distillation_json:
        distillation = search_distillation_profile_from_json(
            json.loads(Path(args.distillation_json).read_text(encoding="utf-8")))
    else:
        distillation = SearchDistillationProfile(generation=args.generation)
    if args.belief_particles:
        from mj.belief import BeliefProfile
        belief = BeliefProfile(particle_count=args.belief_particles,
                               seed=args.belief_seed)
    else:
        from mj.belief import BeliefProfile
        belief = BeliefProfile(seed=args.belief_seed)
    return population, budget, search, belief, distillation


def _seed_range(args):
    if args.split:
        row = FROZEN_SPLITS[args.split]
        games = (int(args.games) if args.games is not None
                 else int(row["games"]))
        return int(row["seed_start"]), games
    games = (int(args.games) if args.games is not None
             else int(FROZEN_SPLITS["train"]["games"]))
    return int(args.seed_start), games


def _ycbk_variants(value):
    if value == "both":
        return (False, True)
    if value == "on":
        return (True,)
    return (False,)


def _label_pool(args, config):
    """Label an active-sampling selected candidate pool into a dataset."""
    from dataclasses import replace

    from mj.decision.profile import fingerprint
    from mj.training.active_sampling import read_candidate_pool
    from mj.training.search_data import SearchDataset, write_search_dataset
    from mj.training.teacher_generate import (
        label_snapshot,
        resume_dataset,
        snapshot_from_candidate,
        teacher_confidence_from_gap,
    )

    candidates = read_candidate_pool(args.pool_in)
    completed = frozenset()
    if args.out is not None and not args.no_resume and args.out.exists():
        _, completed = resume_dataset(args.out)
    samples = []
    for candidate in candidates:
        snapshot = snapshot_from_candidate(
            candidate, belief_profile=config.belief_profile)
        sample = label_snapshot(
            snapshot, source_group=candidate.source_group,
            generation=candidate.generation,
            policy_version_source=candidate.policy_version_source,
            budget_profile=config.budget_profile,
            search_profile=config.search_profile,
            belief_profile=config.belief_profile,
            completed_work_ids=completed)
        if sample is None:
            continue
        q_values = sample.q_by_action
        regret = None
        if (q_values and candidate.policy_action is not None
                and candidate.policy_action in q_values):
            regret = max(q_values.values()) - q_values[candidate.policy_action]
        samples.append(replace(
            sample, state_source=candidate.state_source,
            importance_factor=candidate.importance_factor,
            policy_action=candidate.policy_action,
            policy_prob_by_action=candidate.policy_prob_by_action,
            policy_entropy=candidate.policy_entropy,
            policy_regret=regret,
            teacher_confidence=teacher_confidence_from_gap(
                sample.teacher_q_gap)))
    existing = SearchDataset()
    if args.out is not None and not args.no_resume and args.out.exists():
        existing, _ = resume_dataset(args.out)
    merged = SearchDataset(sorted(
        existing.samples + samples,
        key=lambda item: (item.source_group, item.work_id)))
    if args.out is not None:
        write_search_dataset(args.out, merged,
                             include_features=not args.no_features)
    manifest = {
        "schema": "search-teacher-pool-labeling-v1",
        "pool_in": str(args.pool_in),
        "input_candidates": len(candidates),
        "labeled": len(samples),
        "total": len(merged.samples),
        "generation": int(args.generation),
        **generation_runtime_fingerprint(),
        "budget_profile": config.budget_profile.as_json(),
        "search_profile": config.search_profile.as_json(),
        "dataset_fingerprint": merged.fingerprint,
        "oracle": False,
    }
    manifest["fingerprint"] = fingerprint(manifest, 24)
    if args.manifest_out:
        args.manifest_out.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out), "labeled": len(samples),
                      "total": len(merged.samples),
                      "dataset_fingerprint": merged.fingerprint},
                     ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path,
                        help="dataset JSONL path (resume reads this file)")
    parser.add_argument("--generation", type=int, default=0)
    parser.add_argument("--policy-source", default="heuristic:shape-v2")
    parser.add_argument("--disagreement-source", default=None,
                        help="frozen baseline whose disagreement escalates")
    parser.add_argument("--population", default="legacy=1,shape-v1=1,shape-v2=1")
    parser.add_argument("--population-json", type=Path)
    parser.add_argument("--budget-json", type=Path)
    parser.add_argument("--search-json", type=Path)
    parser.add_argument("--distillation-json", type=Path)
    parser.add_argument("--split", choices=sorted(FROZEN_SPLITS))
    parser.add_argument("--seed-start", type=int,
                        default=FROZEN_SPLITS["train"]["seed_start"])
    parser.add_argument("--games", type=int, default=None,
                        help="override the split's game count")
    parser.add_argument("--limit-specs", type=int, default=0)
    parser.add_argument("--ycbk", choices=("off", "on", "both"), default="off")
    parser.add_argument("--no-rotate-seats", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--reference-out", type=Path,
                        help="write a frozen reference context set instead")
    parser.add_argument("--reference-simulations", type=int, default=16000,
                        choices=(8000, 16000))
    parser.add_argument("--pool-in", type=Path, default=None,
                        help="label an active-sampling selected candidate pool")
    parser.add_argument("--teacher-cache-dir", type=Path, default=None,
                        help="sharded teacher result cache directory")
    parser.add_argument("--reference-shard-dir", type=Path, default=None,
                        help="per-game reference shards (crash-safe resume)")
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--no-features", action="store_true",
                        help="write provenance-only rows (no planes/scalars)")
    parser.add_argument("--teacher-seed", type=int, default=0)
    parser.add_argument("--search-seed", type=int, default=0)
    parser.add_argument("--belief-seed", type=int, default=0)
    parser.add_argument("--belief-particles", type=int, default=0)
    parser.add_argument("--search-simulations", type=int, default=2048)
    parser.add_argument("--search-version", default="search-v1")
    parser.add_argument("--search-wall-clock-ms", type=float, default=None)
    parser.add_argument("--forced-sanity-ratio", type=float, default=0.03)
    parser.add_argument("--allow-degraded-kernel", action="store_true",
                        help="explicit opt-out of the kernel-compatibility "
                             "gate (parity/smoke only, never production data)")
    args = parser.parse_args(argv)
    if args.out is None and args.reference_out is None and args.pool_in is None:
        parser.error("either --out, --reference-out or --pool-in is required")

    require_compatible_kernel(allow_degraded=args.allow_degraded_kernel)

    population, budget, search, belief, distillation = _profiles(args)
    seed_start, games = _seed_range(args)
    specs = scheduled_specs(
        seed_start=seed_start, games=games,
        ycbk_variants=_ycbk_variants(args.ycbk),
        rotate_seats=not args.no_rotate_seats)
    if args.limit_specs:
        specs = specs[:int(args.limit_specs)]

    reference_mode = args.reference_out is not None
    reference_simulations = int(args.reference_simulations)
    shard_dir = None
    if reference_mode:
        shard_dir = (args.reference_shard_dir or
                     args.reference_out.with_suffix(
                         args.reference_out.suffix + ".shards"))
    config = GenerationConfig(
        generation=args.generation, policy_source=args.policy_source,
        population=population, budget_profile=budget, search_profile=search,
        belief_profile=belief, disagreement_source=args.disagreement_source,
        reference_mode=reference_mode,
        reference_simulations=reference_simulations,
        teacher_cache_dir=(str(args.teacher_cache_dir)
                           if args.teacher_cache_dir else None),
        reference_shard_dir=(str(shard_dir) if shard_dir else None))

    if args.pool_in is not None:
        return _label_pool(args, config)

    completed_groups = set()
    if reference_mode and not args.no_resume and shard_dir is not None:
        from mj.training.teacher_generate import completed_reference_groups
        completed_groups = completed_reference_groups(shard_dir)
        if completed_groups:
            specs = [spec for spec in specs
                     if spec.source_group not in completed_groups]
            print(json.dumps({"resumed_reference_groups":
                              len(completed_groups),
                              "remaining_specs": len(specs)}))

    completed = frozenset()
    if (not args.no_resume and not reference_mode and args.out is not None
            and args.out.exists()):
        _, completed = resume_dataset(args.out)
    if reference_mode and args.reference_out.exists() and not args.no_resume:
        from mj.training.search_data import work_identity
        from mj.training.teacher_generate import read_reference_contexts
        completed = frozenset(
            work_identity(
                source_group=row["source_group"],
                context_hash=row["sample"]["context_hash"],
                history_hash=row["sample"]["history_hash"],
                teacher_seed=row["sample"].get("teacher_seed", 0),
                search_fingerprint=row["sample"]["search_fingerprint"])
            for row in read_reference_contexts(args.reference_out))

    result = generate_dataset(
        specs, config, workers=args.workers,
        completed_work_ids=completed)
    if result.errors:
        print(json.dumps({"errors": result.errors[:5],
                          "error_count": len(result.errors)},
                         ensure_ascii=False, indent=2), file=sys.stderr)

    if reference_mode:
        from mj.training.teacher_generate import (
            read_reference_shards, sort_reference_rows)
        rows = result.reference_rows
        if shard_dir is not None:
            rows = read_reference_shards(shard_dir) + rows
        deduped = {}
        for row in rows:
            key = (row["source_group"],
                   row["sample"].get("fingerprint"))
            deduped[key] = row
        rows = sort_reference_rows(deduped.values())
        write_reference_contexts(args.reference_out, rows)
        summary = {"reference_out": str(args.reference_out),
                   "rows": len(rows),
                   "shards": str(shard_dir) if shard_dir else None,
                   "errors": len(result.errors)}
    else:
        if not args.no_resume and args.out.exists():
            existing, _ = resume_dataset(args.out)
            from mj.training.search_data import SearchDataset
            merged = SearchDataset(sorted(
                existing.samples + result.dataset.samples,
                key=lambda sample: (sample.source_group, sample.work_id)))
        else:
            merged = result.dataset
        write_search_dataset(args.out, merged,
                             include_features=not args.no_features)
        from mj.training.teacher_generate import GenerationResult
        manifest = generation_manifest(
            GenerationResult(merged, result.reference_rows, result.errors,
                             result.specs), config)
        manifest["resumed_work_ids"] = len(completed)
        manifest["new_samples"] = len(result.dataset.samples)
        if args.manifest_out:
            args.manifest_out.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2,
                           sort_keys=True) + "\n", encoding="utf-8")
        summary = {"out": str(args.out), "samples": len(merged.samples),
                   "new_samples": len(result.dataset.samples),
                   "reference_rows": len(result.reference_rows),
                   "errors": len(result.errors),
                   "coverage_passed": manifest["coverage"]["passed"],
                   "dataset_fingerprint": manifest["dataset_fingerprint"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not result.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
