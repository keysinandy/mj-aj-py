"""Numerically stable, deterministic particle utilities."""

from __future__ import annotations

import math
import random
from typing import Iterable, Sequence


def normalize_log_weights(log_weights: Iterable[float]) -> tuple[list[float], float] | None:
    """Return normalized weights and log normalizer, or ``None`` for collapse."""
    values = [float(value) for value in log_weights]
    if not values:
        return None
    finite = [value for value in values if math.isfinite(value)]
    if not finite:
        return None
    peak = max(finite)
    scaled = [math.exp(value - peak) if math.isfinite(value) else 0.0
              for value in values]
    total = math.fsum(scaled)
    if not math.isfinite(total) or total <= 0:
        return None
    return [value / total for value in scaled], peak + math.log(total)


def effective_sample_size(weights: Sequence[float]) -> float:
    """Compute ``1 / sum(w**2)`` after accepting only finite non-negative w."""
    values = [float(value) for value in weights]
    if not values:
        return 0.0
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("weights must be finite and non-negative")
    total = math.fsum(values)
    if total <= 0:
        return 0.0
    normalized = [value / total for value in values]
    denominator = math.fsum(value * value for value in normalized)
    return 0.0 if denominator <= 0 else 1.0 / denominator


def entropy(weights: Sequence[float]) -> float:
    """Shannon entropy of a normalized-or-unnormalized weight vector."""
    values = [float(value) for value in weights]
    total = math.fsum(value for value in values if value >= 0 and math.isfinite(value))
    if total <= 0:
        return 0.0
    return -math.fsum((value / total) * math.log(value / total)
                      for value in values
                      if value > 0 and math.isfinite(value))


def systematic_resample(weights: Sequence[float], count: int | None = None,
                        seed: int = 0) -> tuple[int, ...]:
    """Deterministic systematic resampling.

    The random offset is derived only from ``seed`` and the normalized input;
    no process-global RNG or worker scheduling is consulted.
    """
    values = [float(value) for value in weights]
    n = len(values)
    count = n if count is None else int(count)
    if n == 0 or count <= 0:
        return ()
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("weights must be finite and non-negative")
    total = math.fsum(values)
    if total <= 0:
        raise ValueError("cannot resample zero total weight")
    values = [value / total for value in values]
    cumulative = []
    running = 0.0
    for value in values:
        running += value
        cumulative.append(running)
    cumulative[-1] = 1.0
    rng = random.Random(int(seed))
    offset = rng.random() / count
    indexes = []
    cursor = 0
    for i in range(count):
        target = offset + i / count
        while cursor < n - 1 and target > cumulative[cursor]:
            cursor += 1
        indexes.append(cursor)
    return tuple(indexes)


def weighted_index(weights: Sequence[float], seed: int = 0) -> int:
    """Choose one weighted index using a worker-independent RNG stream."""
    indexes = systematic_resample(weights, 1, seed=seed)
    if not indexes:
        raise ValueError("cannot sample from an empty/zero belief")
    return indexes[0]
