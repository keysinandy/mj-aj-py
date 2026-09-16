#!/usr/bin/env python3
"""Train a search-distillation BC policy (Phase 1 policy-only by default)."""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

from mj.training.distillation_profile import SearchDistillationProfile
from mj.training.search_bc_train import (
    SearchBCTrainProfile,
    aggregate_version,
    build_training_rows,
    train_search_bc,
    write_training_manifest,
)
from mj.training.search_data import SearchDataset, read_search_dataset


def _load_dataset(patterns):
    samples = []
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            loaded = read_search_dataset(path)
            samples.extend(loaded.samples)
    if not samples:
        raise ValueError("no dataset rows matched the declared inputs")
    return SearchDataset(samples)


def _unique(values, name):
    values = sorted({str(value) for value in values if str(value)})
    if len(values) > 1:
        raise ValueError(f"dataset mixes {name}: {values}")
    return values[0] if values else ""


def _build_model(args, dataset):
    from mj.models.policy_value import PolicyValueNet

    width = args.width or 64
    blocks = args.blocks or 2
    model = PolicyValueNet(blocks=blocks, width=width)
    if args.init:
        import torch

        checkpoint = torch.load(args.init, map_location="cpu",
                                weights_only=True)
        if checkpoint.get("manifest"):
            from mj.decision.policy_v3 import load_policy_value_model
            model = load_policy_value_model(args.init, device="cpu")
        else:
            from mj.bc_train import load_init_weights
            load_init_weights(model, checkpoint)
    return model


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", nargs="+", required=True,
                        help="JSONL dataset paths or globs")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--generation", type=int, default=0)
    parser.add_argument("--model-version", default=None)
    parser.add_argument("--blocks", type=int, default=2)
    parser.add_argument("--width", type=int, default=64)
    parser.add_argument("--init", type=Path, default=None)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--bs", type=int, default=64)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--target-mode", choices=("visit", "q-soft", "hybrid"),
                        default="visit")
    parser.add_argument("--tau-q", type=float, default=4.0)
    parser.add_argument("--lambda-visit", type=float, default=1.0)
    parser.add_argument("--lambda-q", type=float, default=0.0)
    parser.add_argument("--value-weight", type=float, default=0.0)
    parser.add_argument("--value-contract", default="")
    parser.add_argument("--variance-scale", type=float, default=None)
    parser.add_argument("--ambiguous-weight", type=float, default=0.5)
    parser.add_argument("--reset-weight", type=float, default=0.5)
    parser.add_argument("--tau-gap", type=float, default=2.0)
    parser.add_argument("--min-importance-weight", type=float, default=0.25)
    parser.add_argument("--full-evidence-simulations", type=int, default=2048)
    parser.add_argument("--augment", choices=("none", "suit"), default="none")
    parser.add_argument("--assignments", type=Path, default=None,
                        help="JSON source_group -> split mapping")
    parser.add_argument("--split", default="train")
    parser.add_argument("--provenance", type=Path, default=None,
                        help="generation manifest for belief/search provenance")
    args = parser.parse_args(argv)

    dataset = _load_dataset(args.data)
    assignments = None
    if args.assignments:
        assignments = json.loads(args.assignments.read_text(encoding="utf-8"))
        from mj.training.dataset_report import validate_split_integrity
        integrity = validate_split_integrity(dataset, assignments)
        print(json.dumps({"split_integrity": integrity["counts"]},
                         ensure_ascii=False))
        train_dataset = SearchDataset(
            sample for sample in dataset.samples
            if assignments.get(sample.source_group) == args.split)
        if not train_dataset.samples:
            raise ValueError(f"no samples in split {args.split!r}")
    else:
        train_dataset = dataset

    provenance = (json.loads(args.provenance.read_text(encoding="utf-8"))
                  if args.provenance else {})
    belief = provenance.get("belief_profile")
    bff = belief.get("fingerprint") if isinstance(belief, dict) else ""
    teacher_budget = provenance.get("teacher_budget")
    tbf = (teacher_budget.get("fingerprint")
           if isinstance(teacher_budget, dict) else "")
    population = provenance.get("population")
    population_fingerprint = (population.get("fingerprint")
                              if isinstance(population, dict) else "")
    spf = aggregate_version((sample.search_fingerprint
                     for sample in train_dataset.samples), "search fingerprints")
    opponent_versions = aggregate_version(
        (sample.opponent_policy_version
         for sample in train_dataset.samples), "opponent versions")
    profile = SearchDistillationProfile(
        generation=args.generation,
        policy_version_source=aggregate_version(
            (sample.policy_version_source
             for sample in train_dataset.samples), "policy source versions"),
        teacher_budget_fingerprint=str(tbf or ""),
        feature_contract_fingerprint=str(
            provenance.get("feature_contract_fingerprint", "")),
        belief_profile_fingerprint=str(bff or ""),
        search_profile_fingerprint=spf,
        opponent_population_fingerprint=str(
            population_fingerprint or opponent_versions),
        leaf_version=_unique((sample.leaf_version
                              for sample in train_dataset.samples),
                             "leaf versions"),
        target_mode=args.target_mode,
        lambda_visit=args.lambda_visit, lambda_q=args.lambda_q,
        tau_q=args.tau_q, full_evidence_simulations=args.full_evidence_simulations,
        ambiguity_weight=args.ambiguous_weight,
        reset_weight=args.reset_weight, tau_gap=args.tau_gap,
        min_importance_weight=args.min_importance_weight,
        variance_scale=args.variance_scale,
        value_weight=args.value_weight,
        value_contract_fingerprint=args.value_contract)
    train = SearchBCTrainProfile(
        epochs=args.epochs, batch_size=args.bs, lr=args.lr, seed=args.seed,
        device=args.device, augmentation=args.augment)
    model = _build_model(args, train_dataset)
    model_version = args.model_version or f"search-bc-gen{args.generation}"
    history, rows = train_search_bc(
        model, train_dataset, profile=profile, train=train,
        value_contract_fingerprint=args.value_contract,
        output_dir=args.out, generation=args.generation,
        model_version=model_version, blocks=args.blocks, width=args.width)
    manifest = write_training_manifest(
        args.out / "training_manifest.json", model=model, profile=profile,
        train=train, dataset=train_dataset, history=history, rows=rows,
        generation=args.generation, model_version=model_version,
        blocks=args.blocks, width=args.width,
        extra={"trained_on_split": args.split if assignments else "all",
               "dataset_fingerprint": train_dataset.fingerprint,
               "data_paths": list(args.data)})
    summary = {
        "out": str(args.out), "model_version": model_version,
        "epochs": history,
        "usable_rows": len(rows.rows), "skipped_rows": dict(rows.skipped),
        "profile_fingerprint": profile.fingerprint,
        "train_profile_fingerprint": train.fingerprint,
        "manifest_fingerprint": manifest["fingerprint"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
