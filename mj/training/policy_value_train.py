"""Policy/value distillation losses, metrics and optional Torch training."""

from __future__ import annotations

import math
from typing import Iterable, Mapping


def sample_weight(sample):
    return float(sample.sample_weight)


def value_target(sample, *, source=None):
    """Select a declared target while preserving teacher/terminal columns."""
    source = source or getattr(sample, "value_target_source", None)
    if source is None:
        source = getattr(sample, "target_source", "search-root-v1")
    if source in ("terminal-v1", "actual-terminal-v1"):
        return getattr(sample, "terminal_reward",
                       getattr(sample, "actual_round_score", None))
    if source in ("search-root-v1", "teacher-v1"):
        return getattr(sample, "root_value", None)
    if source == "terminal-preferred-v1":
        terminal = getattr(sample, "terminal_reward",
                           getattr(sample, "actual_round_score", None))
        return terminal if terminal is not None else getattr(sample, "root_value", None)
    raise ValueError(f"unknown value target source: {source}")


def regression_metrics(predictions: Iterable[float], targets: Iterable[float],
                       *, buckets=None):
    pairs = [(float(prediction), float(target))
             for prediction, target in zip(predictions, targets)]
    if not pairs:
        return {"count": 0, "mae": None, "rmse": None,
                "sign_accuracy": None, "ranking_accuracy": None,
                "bucket_calibration": {}}
    errors = [prediction - target for prediction, target in pairs]
    mae = sum(abs(value) for value in errors) / len(errors)
    rmse = math.sqrt(sum(value * value for value in errors) / len(errors))
    sign = sum((prediction >= 0) == (target >= 0)
               for prediction, target in pairs) / len(pairs)
    ranking = []
    for left in range(len(pairs)):
        for right in range(left + 1, len(pairs)):
            pred_delta = pairs[left][0] - pairs[right][0]
            target_delta = pairs[left][1] - pairs[right][1]
            if target_delta == 0:
                continue
            ranking.append((pred_delta >= 0) == (target_delta >= 0))
    edges = tuple(buckets or (-24, -8, 0, 8, 24))
    calibration = {}
    for low, high in zip(edges, edges[1:]):
        selected = [(prediction, target) for prediction, target in pairs
                    if low <= target < high]
        calibration[f"{low}:{high}"] = {
            "count": len(selected),
            "predicted_mean": (sum(x[0] for x in selected) / len(selected)
                                if selected else None),
            "actual_mean": (sum(x[1] for x in selected) / len(selected)
                            if selected else None),
        }
    return {
        "count": len(pairs), "mae": mae, "rmse": rmse,
        "sign_accuracy": sign,
        "ranking_accuracy": (sum(ranking) / len(ranking) if ranking else None),
        "bucket_calibration": calibration,
    }


def policy_kl(target: Mapping[int, float], predicted: Mapping[int, float]):
    """KL(target || predicted) over the legal soft-label support."""
    if not target:
        return None
    value = 0.0
    for action, target_probability in target.items():
        target_probability = float(target_probability)
        if target_probability <= 0:
            continue
        probability = max(1e-12, float(predicted.get(action, 0.0)))
        value += target_probability * math.log(target_probability / probability)
    return value


def policy_action_agreement(target: Mapping[int, float], predicted: Mapping[int, float]):
    if not target or not predicted:
        return None
    return max(target, key=target.get) == max(predicted, key=predicted.get)


def policy_metrics(targets: Iterable[Mapping[int, float]],
                   predictions: Iterable[Mapping[int, float]]):
    """Aggregate soft-label KL and top-action agreement for a split."""
    rows = [(policy_kl(target, prediction),
             policy_action_agreement(target, prediction))
            for target, prediction in zip(targets, predictions)]
    valid_kl = [value for value, _ in rows if value is not None]
    valid_agreement = [value for _, value in rows if value is not None]
    return {
        "count": len(rows),
        "kl": (sum(valid_kl) / len(valid_kl) if valid_kl else None),
        "action_agreement": (sum(valid_agreement) / len(valid_agreement)
                              if valid_agreement else None),
    }


def evaluate_value_predictions(samples, predictions, *, source=None):
    """Return declared value diagnostics for a dataset split."""
    targets = [value_target(sample, source=source) for sample in samples]
    pairs = [(prediction, target)
             for prediction, target in zip(predictions, targets)
             if target is not None]
    return regression_metrics((row[0] for row in pairs),
                              (row[1] for row in pairs))


def inference_benchmark(infer, inputs, *, repeats=1):
    """Measure a complete callable path, not only a model forward.

    The caller supplies the same feature/legal-mask wrapper used at runtime;
    this keeps the benchmark honest about preprocessing and mask overhead.
    """
    import time

    inputs = list(inputs)
    if not inputs:
        return {"count": 0, "repeats": 0, "p50_ms": None,
                "p95_ms": None, "p99_ms": None, "max_ms": None}
    durations = []
    for _ in range(max(1, int(repeats))):
        for item in inputs:
            started = time.perf_counter()
            infer(item)
            durations.append((time.perf_counter() - started) * 1000.0)
    durations.sort()

    def percentile(p):
        index = min(len(durations) - 1,
                    max(0, int(math.ceil(float(p) * len(durations)) - 1)))
        return durations[index]

    return {"count": len(durations), "repeats": max(1, int(repeats)),
            "p50_ms": percentile(.50), "p95_ms": percentile(.95),
            "p99_ms": percentile(.99), "max_ms": durations[-1]}


def masked_soft_targets(logits, target, legal_mask):
    """Torch helper kept import-light until a training caller invokes it."""
    import torch

    mask = torch.as_tensor(legal_mask, dtype=torch.bool, device=logits.device)
    if mask.ndim == 1:
        mask = mask.unsqueeze(0)
    if mask.ndim != 2 or mask.shape[-1] != logits.shape[-1]:
        raise ValueError("legal mask shape does not match policy logits")
    if not bool(mask.any(dim=-1).all().item()):
        raise ValueError("legal mask contains no action")
    target = torch.as_tensor(target, dtype=torch.float32, device=logits.device)
    if target.shape != mask.shape:
        raise ValueError("policy target shape does not match legal mask")
    if not bool(torch.isfinite(target).all().item()) or bool((target < 0).any().item()):
        raise ValueError("policy target must be finite and non-negative")
    target = target.masked_fill(~mask, 0.0)
    total = target.sum(dim=-1, keepdim=True)
    target = torch.where(total > 0, target / total, mask.float() / mask.sum(dim=-1, keepdim=True))
    log_probs = torch.log_softmax(logits.masked_fill(~mask, float("-inf")), dim=-1)
    return target, log_probs


def policy_value_loss(logits, values, target_policy, target_value, legal_mask,
                      *, weights=None, policy_weight=1.0, value_weight=1.0):
    import torch
    import torch.nn.functional as F

    target, log_probs = masked_soft_targets(logits, target_policy, legal_mask)
    policy_loss = -(target * log_probs).sum(dim=-1)
    value_target = torch.as_tensor(target_value, dtype=torch.float32,
                                  device=values.device)
    value_loss = F.huber_loss(values, value_target, reduction="none")
    if weights is None:
        weights = torch.ones_like(value_loss)
    else:
        weights = torch.as_tensor(weights, dtype=value_loss.dtype,
                                   device=value_loss.device)
    denom = weights.sum().clamp_min(1e-12)
    return ((policy_weight * (policy_loss * weights).sum() / denom) +
            (value_weight * (value_loss * weights).sum() / denom),
            {"policy_loss": float((policy_loss * weights).sum().item() / denom.item()),
             "value_loss": float((value_loss * weights).sum().item() / denom.item())})


def train_policy_value(model, dataset, *, epochs=1, lr=3e-4, device="cpu",
                       batch_size=64, value_scale=24.0,
                       target_source="search-root-v1"):
    """Small deterministic training loop for an already featureized dataset."""
    import torch

    if not math.isfinite(float(value_scale)) or float(value_scale) <= 0:
        raise ValueError("value_scale must be finite and positive")
    samples = [sample for sample in dataset.samples
               if sample.planes is not None and sample.scalars is not None
               and value_target(sample, source=target_source) is not None]
    if not samples:
        raise ValueError("training dataset has no featureized samples")
    model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    history = []
    for _epoch in range(int(epochs)):
        model.train()
        total = 0.0
        count = 0
        # Do not shuffle source order implicitly: reproducibility belongs to
        # the frozen split/seed contract.  Callers can pre-shuffle groups.
        for start in range(0, len(samples), int(batch_size)):
            batch = samples[start:start + int(batch_size)]
            planes = torch.as_tensor([sample.planes for sample in batch],
                                     dtype=torch.float32, device=device)
            scalars = torch.as_tensor([sample.scalars for sample in batch],
                                      dtype=torch.float32, device=device)
            target_policy = torch.as_tensor(
                [[sample.policy_target.get(action, 0.0) for action in range(109)]
                 for sample in batch], dtype=torch.float32, device=device)
            target_value = torch.as_tensor(
                [value_target(sample, source=target_source) /
                 float(value_scale) for sample in batch],
                dtype=torch.float32, device=device)
            mask = torch.as_tensor([sample.legal_mask for sample in batch],
                                   dtype=torch.bool, device=device)
            weights = torch.as_tensor([sample.sample_weight for sample in batch],
                                      dtype=torch.float32, device=device)
            logits, values = model(planes, scalars)
            loss, metrics = policy_value_loss(
                logits, values, target_policy, target_value, mask,
                weights=weights)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total += float(loss.item()) * len(batch)
            count += len(batch)
        history.append({"epoch": _epoch + 1,
                        "loss": total / max(1, count), "samples": count})
    return history
