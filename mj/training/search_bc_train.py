"""Search-distillation BC training: soft targets, unit-safe weights, provenance.

Phase 1 is policy-only: ``SearchDistillationProfile.value_weight`` defaults to
zero and the trainer refuses a non-zero value weight without a declared Value
v2 contract fingerprint.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
import random
from typing import Any, Iterable, Mapping

import numpy as np

from ..decision.profile import fingerprint
from .distillation_profile import SearchDistillationProfile
from .policy_value_train import (
    is_usable_sample,
    policy_value_loss,
    search_policy_target,
    unit_safe_weight,
    value_target,
)
from .search_data import SearchDataset

TRAIN_SCHEMA = "search-bc-train-profile-v1"


@dataclass(frozen=True)
class SearchBCTrainProfile:
    schema: str = TRAIN_SCHEMA
    version: str = "search-bc-train-v1"
    epochs: int = 1
    batch_size: int = 64
    lr: float = 3e-4
    seed: int = 0
    device: str = "cpu"
    value_target_source: str = "search-root-v1"
    augmentation: str = "none"
    shuffle: bool = True

    def __post_init__(self):
        if self.schema != TRAIN_SCHEMA:
            raise ValueError(f"unsupported train profile schema: {self.schema}")
        for name in ("epochs", "batch_size"):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        lr = float(self.lr)
        if not math.isfinite(lr) or lr <= 0:
            raise ValueError("lr must be finite and positive")
        if self.augmentation not in ("none", "suit"):
            raise ValueError(f"unsupported augmentation: {self.augmentation}")
        if self.value_target_source not in (
                "search-root-v1", "terminal-v1", "terminal-preferred-v1",
                "actual-terminal-v1", "teacher-v1"):
            raise ValueError("unsupported value target source")
        object.__setattr__(self, "epochs", int(self.epochs))
        object.__setattr__(self, "batch_size", int(self.batch_size))
        object.__setattr__(self, "lr", lr)
        object.__setattr__(self, "seed", int(self.seed))

    def payload(self):
        return asdict(self)

    @property
    def fingerprint(self):
        return fingerprint(self.payload(), 24)

    def as_json(self):
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value


@dataclass
class TrainingRow:
    work_id: str
    source_group: str
    planes: Any
    scalars: Any
    mask: Any
    target: Any
    q_values: Any
    value: float
    weight: float


@dataclass
class TrainingRows:
    rows: list
    skipped: dict = field(default_factory=dict)

    def __len__(self):
        return len(self.rows)


def build_training_rows(dataset: SearchDataset, *,
                        profile: SearchDistillationProfile,
                        train: SearchBCTrainProfile | None = None,
                        value_contract_fingerprint: str = ""):
    """Featureize usable samples; audit every skipped row by reason."""
    train = train or SearchBCTrainProfile()
    rows = []
    skipped = {"status": 0, "features": 0, "zero_weight": 0,
               "missing_q": 0, "missing_value": 0}
    for sample in sorted(dataset.samples,
                         key=lambda item: (item.source_group, item.work_id)):
        if not is_usable_sample(sample):
            skipped["status"] += 1
            continue
        if sample.planes is None or sample.scalars is None:
            skipped["features"] += 1
            continue
        weight = unit_safe_weight(sample, profile)
        if weight <= 0:
            skipped["zero_weight"] += 1
            continue
        try:
            target = search_policy_target(sample, profile)
        except ValueError:
            skipped["missing_q"] += 1
            continue
        value = value_target(sample, source=train.value_target_source)
        if profile.value_weight > 0:
            if value is None:
                skipped["missing_value"] += 1
                continue
            if not value_contract_fingerprint:
                raise ValueError("value training requires a Value v2 contract")
        target_vector = np.zeros(109, dtype=np.float32)
        q_vector = np.zeros(109, dtype=np.float32)
        for action, probability in target.items():
            target_vector[action] = float(probability)
        for action, q_value in sample.q_by_action.items():
            q_vector[int(action)] = float(q_value)
        rows.append(TrainingRow(
            work_id=sample.work_id, source_group=sample.source_group,
            planes=np.asarray(sample.planes, dtype=np.float32),
            scalars=np.asarray(sample.scalars, dtype=np.float32),
            mask=np.asarray(sample.legal_mask, dtype=bool),
            target=target_vector, q_values=q_vector,
            value=float(value) if value is not None else 0.0,
            weight=float(weight)))
    return TrainingRows(rows, skipped)


def _permuted(row: TrainingRow, permutation: int):
    """Suit-relabel one row; only tile-indexed planes/actions are touched."""
    from ..features import SUIT_PERMS

    if permutation == 0:
        return row
    q_index, action_map = SUIT_PERMS[permutation]
    planes = np.ascontiguousarray(row.planes[..., q_index])
    mask = np.empty_like(row.mask)
    mask[action_map] = row.mask
    target = np.zeros_like(row.target)
    target[action_map] = row.target
    q_values = np.zeros_like(row.q_values)
    q_values[action_map] = row.q_values
    return TrainingRow(
        work_id=row.work_id, source_group=row.source_group,
        planes=planes, scalars=row.scalars, mask=mask, target=target,
        q_values=q_values, value=row.value, weight=row.weight)


def _permutation_for(row, *, seed, epoch):
    index = int(fingerprint({"seed": int(seed), "epoch": int(epoch),
                             "work_id": row.work_id}, 16), 16)
    return index % 6


def augment_parity_row(row):
    """Return every suit-permuted variant of one row for parity checks."""
    return [_permuted(row, index) for index in range(6)]


def collate_rows(rows, *, augmentation="none", seed=0, epoch=0):
    """Turn training rows into torch tensors (optional suit augmentation)."""
    import torch

    if augmentation == "suit":
        rows = [_permuted(row, _permutation_for(row, seed=seed, epoch=epoch))
                for row in rows]
    planes = torch.as_tensor(np.stack([row.planes for row in rows]),
                             dtype=torch.float32)
    scalars = torch.as_tensor(np.stack([row.scalars for row in rows]),
                              dtype=torch.float32)
    mask = torch.as_tensor(np.stack([row.mask for row in rows]),
                           dtype=torch.bool)
    target = torch.as_tensor(np.stack([row.target for row in rows]),
                             dtype=torch.float32)
    value = torch.as_tensor([row.value for row in rows], dtype=torch.float32)
    weight = torch.as_tensor([row.weight for row in rows], dtype=torch.float32)
    return planes, scalars, mask, target, value, weight


def epoch_order(count, *, seed, epoch, shuffle=True):
    order = list(range(int(count)))
    if shuffle:
        random.Random(int(seed) + int(epoch)).shuffle(order)
    return order


def _checkpoint_manifest(model, *, profile, train, dataset, epoch,
                         generation, model_version, blocks=None, width=None):
    from ..models.policy_value import (PolicyValueModelManifest,
                                       ValueFeatureContract)

    contract = ValueFeatureContract()
    model_manifest = getattr(model, "manifest", None)
    blocks = (int(model_manifest.blocks) if model_manifest is not None
              else int(blocks or 0))
    width = (int(model_manifest.width) if model_manifest is not None
             else int(width or 0))
    if width <= 0:
        raise ValueError("checkpoint manifest requires a positive width")
    return PolicyValueModelManifest(
        model_version=model_version,
        feature_contract_fingerprint=contract.fingerprint,
        belief_profile_fingerprint=profile.belief_profile_fingerprint,
        search_profile_fingerprint=profile.search_profile_fingerprint,
        opponent_policy_version=profile.opponent_population_fingerprint,
        leaf_version=profile.leaf_version,
        rules_version=profile.rules_version,
        calibrated=False,
        training_source="search-distillation",
        policy_iteration=int(generation),
        blocks=blocks, width=width)


def _checkpoint_provenance(*, profile, train, dataset, epoch, generation,
                           blocks=None, width=None, extra=None):
    value = {
        "schema": "search-bc-checkpoint-provenance-v1",
        "epoch": int(epoch),
        "generation": int(generation),
        "distillation_profile": profile.as_json(),
        "train_profile": train.as_json(),
        "dataset_fingerprint": dataset.fingerprint,
        "source_groups": sorted({sample.source_group
                                 for sample in dataset.samples}),
        "generations": sorted({int(sample.generation)
                               for sample in dataset.samples}),
        "value_weight": float(profile.value_weight),
        "value_contract_fingerprint": profile.value_contract_fingerprint,
        "architecture": {"blocks": blocks, "width": width},
        "oracle": False,
    }
    if extra:
        value.update(dict(extra))
    return value


def save_checkpoint(path, model, *, profile, train, dataset, epoch, generation,
                    model_version, history, blocks=None, width=None, extra=None):
    import torch

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = _checkpoint_manifest(
        model, profile=profile, train=train, dataset=dataset, epoch=epoch,
        generation=generation, model_version=model_version, blocks=blocks,
        width=width)
    checkpoint = {
        "model": model.state_dict(),
        "manifest": manifest.as_json(),
        "provenance": _checkpoint_provenance(
            profile=profile, train=train, dataset=dataset, epoch=epoch,
            generation=generation, blocks=blocks, width=width, extra=extra),
        "history": list(history),
        "oracle": False,
    }
    torch.save(checkpoint, path)
    return path


def train_search_bc(model, dataset: SearchDataset, *,
                    profile: SearchDistillationProfile,
                    train: SearchBCTrainProfile | None = None,
                    value_contract_fingerprint: str = "",
                    rows: TrainingRows | None = None,
                    output_dir=None, generation=0, model_version="",
                    blocks=None, width=None, extra_manifest=None):
    """Train one policy-value model; returns (history, rows)."""
    import torch

    train = train or SearchBCTrainProfile()
    if profile.value_weight > 0 and not (
            value_contract_fingerprint or profile.value_contract_fingerprint):
        raise ValueError("value training requires a Value v2 contract")
    rows = rows or build_training_rows(
        dataset, profile=profile, train=train,
        value_contract_fingerprint=(value_contract_fingerprint or
                                    profile.value_contract_fingerprint))
    if not rows.rows:
        raise ValueError("no usable training rows")
    device = train.device
    model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=train.lr)
    history = []
    for epoch in range(train.epochs):
        order = epoch_order(len(rows.rows), seed=train.seed, epoch=epoch,
                            shuffle=train.shuffle)
        model.train()
        total, count = 0.0, 0
        for start in range(0, len(order), train.batch_size):
            batch = [rows.rows[index]
                     for index in order[start:start + train.batch_size]]
            planes, scalars, mask, target, value, weight = collate_rows(
                batch, augmentation=train.augmentation, seed=train.seed,
                epoch=epoch)
            planes = planes.to(device)
            scalars = scalars.to(device)
            mask = mask.to(device)
            target = target.to(device)
            value = value.to(device)
            weight = weight.to(device)
            logits, values = model(planes, scalars)
            loss, metrics = policy_value_loss(
                logits, values, target, value, mask, weights=weight,
                policy_weight=1.0, value_weight=float(profile.value_weight))
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total += float(loss.item()) * len(batch)
            count += len(batch)
        record = {"epoch": epoch + 1, "loss": total / max(1, count),
                  "samples": count, "target_mode": profile.target_mode,
                  "augmentation": train.augmentation}
        history.append(record)
        if output_dir is not None:
            save_checkpoint(
                Path(output_dir) / f"epoch_{epoch + 1:03d}.pt", model,
                profile=profile, train=train, dataset=dataset, epoch=epoch + 1,
                generation=generation, model_version=model_version,
                history=history, blocks=blocks, width=width,
                extra=extra_manifest)
    return history, rows


def write_training_manifest(path, *, model, profile, train, dataset, history,
                            rows, generation, model_version, blocks=None,
                            width=None, extra=None):
    value = _checkpoint_provenance(
        profile=profile, train=train, dataset=dataset, epoch=len(history),
        generation=generation, blocks=blocks, width=width, extra=extra)
    value["model_manifest"] = _checkpoint_manifest(
        model, profile=profile, train=train, dataset=dataset,
        epoch=len(history), generation=generation, model_version=model_version,
        blocks=blocks, width=width).as_json()
    value["schema"] = "search-bc-training-manifest-v1"
    value["history"] = list(history)
    value["skipped_rows"] = dict(rows.skipped)
    value["usable_rows"] = len(rows.rows)
    value["fingerprint"] = fingerprint(value, 24)
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return value
