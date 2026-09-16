"""Regret-aware training: ranking loss, catastrophic margin, sample weights.

All constants live in a fingerprinted profile so a checkpoint always states
exactly which objective produced it.  Phase-one defaults keep
``catastrophic_weight=0`` and ``ranking_weight=0.25``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping

from ..decision.profile import fingerprint

LOSS_SCHEMA = "regret-aware-loss-profile-v1"
RANKING_MODES = ("best_vs_others", "top2_vs_bad")


def _finite(value, name):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


@dataclass(frozen=True)
class RegretAwareLossProfile:
    schema: str = LOSS_SCHEMA
    version: str = "regret-aware-loss-v1"
    policy_weight: float = 1.0
    ranking_weight: float = 0.25
    catastrophic_weight: float = 0.0
    ranking_mode: str = "best_vs_others"
    q_scale: float = 4.0
    ranking_max_weight: float = 1.0
    catastrophic_threshold: float = 24.0
    catastrophic_margin: float = 2.0
    regret_scale: float = 24.0
    regret_cap: float = 2.0
    error_alpha: float = 1.0
    confidence_min: float = 0.5
    confidence_max: float = 1.5
    confidence_tau: float = 8.0
    importance_min: float = 0.5
    importance_max: float = 2.0
    weight_min: float = 0.25
    weight_max: float = 4.0

    def __post_init__(self):
        if self.schema != LOSS_SCHEMA:
            raise ValueError(f"unsupported loss schema: {self.schema}")
        if self.ranking_mode not in RANKING_MODES:
            raise ValueError(f"unsupported ranking mode: {self.ranking_mode}")
        for name in ("policy_weight", "ranking_weight", "catastrophic_weight"):
            value = _finite(getattr(self, name), name)
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)
        if self.policy_weight + self.ranking_weight + self.catastrophic_weight <= 0:
            raise ValueError("loss profile must carry positive weight mass")
        for name in ("q_scale", "ranking_max_weight", "catastrophic_threshold",
                     "catastrophic_margin", "regret_scale", "confidence_tau"):
            value = _finite(getattr(self, name), name)
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, value)
        if self.regret_cap < 0 or self.error_alpha < 0:
            raise ValueError("regret cap/alpha must be non-negative")
        for low_name, high_name in (
                ("confidence_min", "confidence_max"),
                ("importance_min", "importance_max"),
                ("weight_min", "weight_max")):
            low = _finite(getattr(self, low_name), low_name)
            high = _finite(getattr(self, high_name), high_name)
            if low <= 0 or high < low:
                raise ValueError(f"{low_name}/{high_name} bounds are invalid")
            object.__setattr__(self, low_name, low)
            object.__setattr__(self, high_name, high)
        object.__setattr__(self, "regret_scale", float(self.regret_scale))
        object.__setattr__(self, "regret_cap", float(self.regret_cap))
        object.__setattr__(self, "error_alpha", float(self.error_alpha))

    def payload(self):
        return asdict(self)

    @property
    def fingerprint(self):
        return fingerprint(self.payload(), 24)

    def as_json(self):
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value


def regret_aware_weight(sample, profile: RegretAwareLossProfile):
    """teacher_confidence x policy_error x importance, clipped."""
    confidence = getattr(sample, "teacher_confidence", None)
    if confidence is None:
        gap = getattr(sample, "teacher_q_gap", None)
        confidence = 1.0 if gap is None else (
            0.5 + min(1.0, float(gap) / float(profile.confidence_tau)))
    confidence = min(profile.confidence_max,
                     max(profile.confidence_min, float(confidence)))
    regret = getattr(sample, "policy_regret", None)
    normalized = (min(float(regret) / profile.regret_scale, profile.regret_cap)
                  if regret is not None else 0.0)
    policy_error = 1.0 + profile.error_alpha * normalized
    importance = float(getattr(sample, "importance_factor", 1.0))
    importance = min(profile.importance_max,
                     max(profile.importance_min, importance))
    combined = confidence * policy_error * importance
    return min(profile.weight_max, max(profile.weight_min, combined))


def _ranking_loss_torch(logits, legal_mask, q_values, q_valid, *,
                        q_scale, max_weight, mode):
    import torch

    negative_inf = torch.finfo(logits.dtype).min
    masked = logits.masked_fill(~legal_mask, negative_inf)
    valid_q = q_values.masked_fill(~q_valid, float("-inf"))
    best_q, best_index = valid_q.max(dim=-1)
    has_pair = q_valid.sum(dim=-1) >= 2
    logit_best = masked.gather(1, best_index.unsqueeze(1)).squeeze(1)
    delta = logit_best.unsqueeze(1) - masked
    pair_weight = ((best_q.unsqueeze(1) - q_values).clamp(min=0)
                   / float(q_scale)).clamp(max=float(max_weight))
    if mode == "top2_vs_bad":
        second_q = valid_q.masked_fill(
            q_valid & (q_values >= best_q.unsqueeze(1)), float("-inf")
        ).max(dim=-1).values
        bad = q_valid & (q_values < (second_q.unsqueeze(1) - 0.0))
    else:
        bad = q_valid & (q_values < best_q.unsqueeze(1))
    pair_weight = pair_weight * bad.float()
    pair_loss = torch.nn.functional.softplus(-delta) * pair_weight
    total_weight = pair_weight.sum(dim=-1)
    row_loss = torch.where(total_weight > 0, pair_loss.sum(dim=-1) /
                           total_weight.clamp_min(1e-12),
                           torch.zeros_like(total_weight))
    return torch.where(has_pair, row_loss, torch.zeros_like(row_loss))


def _catastrophic_loss_torch(logits, legal_mask, q_values, q_valid, *,
                             threshold, margin):
    import torch

    negative_inf = torch.finfo(logits.dtype).min
    masked = logits.masked_fill(~legal_mask, negative_inf)
    best_q, best_index = q_values.masked_fill(~q_valid, float("-inf")).max(
        dim=-1)
    best_logit = masked.gather(1, best_index.unsqueeze(1)).squeeze(1)
    catastrophic = q_valid & (best_q.unsqueeze(1) - q_values >
                              float(threshold))
    gap = best_logit.unsqueeze(1) - masked
    loss = (float(margin) - gap).clamp(min=0) * catastrophic.float()
    count = catastrophic.sum(dim=-1)
    return torch.where(count > 0, loss.sum(dim=-1) / count.clamp_min(1),
                       torch.zeros_like(best_logit))


def regret_aware_policy_value_loss(
        logits, values, target_policy, target_value, legal_mask, *,
        q_values=None, q_valid=None, weights=None,
        profile: RegretAwareLossProfile,
        value_weight=0.0):
    """Policy KL + optional ranking + optional catastrophic + value term."""
    import torch

    from .policy_value_train import masked_soft_targets

    target, log_probs = masked_soft_targets(logits, target_policy, legal_mask)
    log_probs = torch.where(target > 0, log_probs,
                            torch.zeros_like(log_probs))
    policy_row = -(target * log_probs).sum(dim=-1)
    row_loss = profile.policy_weight * policy_row
    if profile.ranking_weight > 0:
        if q_values is None or q_valid is None:
            raise ValueError("ranking loss requires q_values and q_valid")
        row_loss = row_loss + profile.ranking_weight * _ranking_loss_torch(
            logits, legal_mask, q_values, q_valid,
            q_scale=profile.q_scale,
            max_weight=profile.ranking_max_weight,
            mode=profile.ranking_mode)
    if profile.catastrophic_weight > 0:
        if q_values is None or q_valid is None:
            raise ValueError("catastrophic loss requires q_values and q_valid")
        row_loss = row_loss + profile.catastrophic_weight * \
            _catastrophic_loss_torch(
                logits, legal_mask, q_values, q_valid,
                threshold=profile.catastrophic_threshold,
                margin=profile.catastrophic_margin)
    value_row = torch.zeros_like(policy_row)
    if value_weight > 0:
        value_row = torch.nn.functional.huber_loss(
            values, torch.as_tensor(target_value, dtype=values.dtype,
                                    device=values.device),
            reduction="none")
    if weights is None:
        weights = torch.ones_like(policy_row)
    else:
        weights = torch.as_tensor(weights, dtype=policy_row.dtype,
                                  device=policy_row.device)
    denom = weights.sum().clamp_min(1e-12)
    total = ((row_loss * weights).sum() +
             float(value_weight) * (value_row * weights).sum()) / denom
    return total, {
        "policy_loss": float((policy_row * weights).sum().item() /
                             denom.item()),
        "loss_weighted": float(total.item()),
    }
