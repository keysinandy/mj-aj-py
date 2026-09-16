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
        return int(row["seed_start"]), int(row["games"])
    return int(args.seed_start), int(args.games)


def _ycbk_variants(value):
    if value == "both":
        return (False, True)
    if value == "on":
        return (True,)
    return (False,)


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
    parser.add_argument("--games", type=int,
                        default=FROZEN_SPLITS["train"]["games"])
    parser.add_argument("--limit-specs", type=int, default=0)
    parser.add_argument("--ycbk", choices=("off", "on", "both"), default="off")
    parser.add_argument("--no-rotate-seats", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--reference-out", type=Path,
                        help="write a frozen reference context set instead")
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
    args = parser.parse_args(argv)
    if args.out is None and args.reference_out is None:
        parser.error("either --out or --reference-out is required")

    population, budget, search, belief, distillation = _profiles(args)
    seed_start, games = _seed_range(args)
    specs = scheduled_specs(
        seed_start=seed_start, games=games,
        ycbk_variants=_ycbk_variants(args.ycbk),
        rotate_seats=not args.no_rotate_seats)
    if args.limit_specs:
        specs = specs[:int(args.limit_specs)]

    reference_mode = args.reference_out is not None
    reference_simulations = 16000
    if reference_mode:
        # The reference set is derived from the validation split by default.
        reference_simulations = 16000
    config = GenerationConfig(
        generation=args.generation, policy_source=args.policy_source,
        population=population, budget_profile=budget, search_profile=search,
        belief_profile=belief, disagreement_source=args.disagreement_source,
        reference_mode=reference_mode,
        reference_simulations=reference_simulations)

    completed = frozenset()
    if not args.no_resume and args.out.exists() and not reference_mode:
        _, completed = resume_dataset(args.out)
    if reference_mode and args.reference_out.exists() and not args.no_resume:
        from mj.training.teacher_generate import read_reference_contexts
        completed = frozenset(
            row["sample"]["work_id"]
            for row in read_reference_contexts(args.reference_out))

    result = generate_dataset(
        specs, config, workers=args.workers,
        completed_work_ids=completed)
    if result.errors:
        print(json.dumps({"errors": result.errors[:5],
                          "error_count": len(result.errors)},
                         ensure_ascii=False, indent=2), file=sys.stderr)

    if reference_mode:
        write_reference_contexts(args.reference_out, result.reference_rows)
        summary = {"reference_out": str(args.reference_out),
                   "rows": len(result.reference_rows),
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
