"""Generation replay: recent/historical/hard/special buckets with quotas.

Replay keeps old capabilities from being forgotten when DAgger generations
arrive.  Selection is deterministic for a given (profile, buffer, seed), and
historical storage is bounded by a reservoir so aggregates do not grow
without limit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import random
from typing import Iterable, Mapping

from ..decision.profile import fingerprint
from .search_data import SearchDataset, SearchSample

REPLAY_SCHEMA = "generation-replay-profile-v1"
BUCKETS = ("recent", "historical", "hard", "special")
DEFAULT_RATIOS = (("recent", 0.50), ("historical", 0.25), ("hard", 0.15),
                  ("special", 0.10))


@dataclass(frozen=True)
class ReplayProfile:
    schema: str = REPLAY_SCHEMA
    version: str = "generation-replay-v1"
    ratios: tuple[tuple[str, float], ...] = DEFAULT_RATIOS
    hard_regret_threshold: float = 8.0
    historical_cap: int = 200000
    seed: int = 0

    def __post_init__(self):
        if self.schema != REPLAY_SCHEMA:
            raise ValueError(f"unsupported replay schema: {self.schema}")
        ratios = tuple((str(name), float(weight))
                       for name, weight in (self.ratios or ()))
        if tuple(name for name, _ in ratios) != BUCKETS:
            raise ValueError("replay ratios must declare every bucket once")
        if any(not math.isfinite(weight) or weight < 0
               for _, weight in ratios):
            raise ValueError("replay ratios must be finite and non-negative")
        if abs(sum(weight for _, weight in ratios) - 1.0) > 1e-6:
            raise ValueError("replay ratios must sum to 1")
        if int(self.historical_cap) <= 0:
            raise ValueError("historical_cap must be positive")
        object.__setattr__(self, "ratios", ratios)
        object.__setattr__(self, "historical_cap", int(self.historical_cap))
        object.__setattr__(self, "seed", int(self.seed))

    @property
    def ratio_map(self):
        return dict(self.ratios)

    def payload(self):
        value = asdict(self)
        value["ratios"] = [list(row) for row in self.ratios]
        return value

    @property
    def fingerprint(self):
        return fingerprint(self.payload(), 24)

    def as_json(self):
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value


def replay_profile_from_json(data: Mapping):
    value = dict(data)
    supplied = value.pop("fingerprint", None)
    allowed = set(ReplayProfile.__dataclass_fields__)
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("unknown replay profile fields: " +
                         ", ".join(sorted(unknown)))
    profile = ReplayProfile(**value)
    if supplied is not None and supplied != profile.fingerprint:
        raise ValueError("replay profile fingerprint mismatch")
    return profile


def _is_hard(sample: SearchSample, threshold):
    if sample.state_source == "hard":
        return True
    return (sample.policy_regret is not None and
            float(sample.policy_regret) >= float(threshold))


def _is_special(sample: SearchSample):
    return bool(sample.special_state_tags)


class ReplayBuffer:
    """In-memory replay buckets rebuilt from dataset versions."""

    def __init__(self, profile: ReplayProfile | None = None):
        self.profile = profile or ReplayProfile()
        self.buckets: dict[str, list[SearchSample]] = {name: []
                                                       for name in BUCKETS}
        self._reservoir_counter = 0

    def __len__(self):
        return sum(len(rows) for rows in self.buckets.values())

    def add_generation(self, dataset: SearchDataset, *, generation,
                       assignments: Mapping[str, str] | None = None,
                       split="train"):
        """Add one generation; split-filtered when assignments are given."""
        added = 0
        for sample in dataset.samples:
            if assignments is not None:
                if assignments.get(sample.source_group) != split:
                    continue
            if _is_hard(sample, self.profile.hard_regret_threshold):
                self.buckets["hard"].append(sample)
            elif _is_special(sample):
                self.buckets["special"].append(sample)
            if int(sample.generation) >= int(generation):
                self.buckets["recent"].append(sample)
            else:
                self._reservoir_add(sample, int(generation))
            added += 1
        return added

    def _reservoir_add(self, sample, generation):
        rows = self.buckets["historical"]
        cap = self.profile.historical_cap
        self._reservoir_counter += 1
        if len(rows) < cap:
            rows.append(sample)
            return
        rng = random.Random(int(fingerprint(
            {"seed": self.profile.seed, "counter": self._reservoir_counter,
             "generation": generation}, 16), 16) % (2 ** 63))
        index = rng.randrange(self._reservoir_counter)
        if index < cap:
            rows[index] = sample

    def sample_batch(self, *, size, seed=None):
        """Deterministic quota sample across replay buckets."""
        if int(size) <= 0:
            raise ValueError("size must be positive")
        seed = int(self.profile.seed if seed is None else seed)
        available = {name: list(rows) for name, rows in self.buckets.items()}
        for rows in available.values():
            rows.sort(key=lambda sample: sample.work_id)
        target = min(int(size), sum(len(rows) for rows in available.values()))
        quotas: dict[str, int] = {}
        remainders = []
        for name, ratio in self.profile.ratios:
            exact = target * float(ratio)
            quotas[name] = int(math.floor(exact))
            remainders.append((exact - quotas[name], name))
        remaining = target - sum(quotas.values())
        for _, name in sorted(remainders, key=lambda item: (-item[0], item[1])):
            if remaining <= 0:
                break
            quotas[name] += 1
            remaining -= 1
        selected, used = [], set()
        for name in BUCKETS:
            take = quotas[name]
            rows = available[name]
            start = int(fingerprint({"seed": seed, "bucket": name}, 16), 16) % max(1, len(rows))
            for offset in range(len(rows)):
                if take <= 0:
                    break
                sample = rows[(start + offset) % len(rows)]
                if sample.work_id in used:
                    continue
                selected.append((name, sample))
                used.add(sample.work_id)
                take -= 1
        shortfall = target - len(selected)
        if shortfall > 0:
            for name in BUCKETS:
                if shortfall <= 0:
                    break
                for sample in available[name]:
                    if shortfall <= 0:
                        break
                    if sample.work_id in used:
                        continue
                    selected.append((name, sample))
                    used.add(sample.work_id)
                    shortfall -= 1
        return selected

    def sample_dataset(self, *, size, seed=None):
        return SearchDataset(sample for _, sample in
                             self.sample_batch(size=size, seed=seed))

    def manifest(self):
        value = {
            "schema": "generation-replay-manifest-v1",
            "profile": self.profile.as_json(),
            "counts": {name: len(rows)
                       for name, rows in sorted(self.buckets.items())},
            "total": len(self),
            "historical_cap": self.profile.historical_cap,
            "oracle": False,
        }
        value["fingerprint"] = fingerprint(value, 24)
        return value


def replay_manifest(buffer: ReplayBuffer, selected):
    counts: dict[str, int] = {}
    for name, _ in selected:
        counts[name] = counts.get(name, 0) + 1
    value = {
        "schema": "generation-replay-batch-v1",
        "buffer": buffer.manifest(),
        "selected": len(selected),
        "selected_by_bucket": dict(sorted(counts.items())),
        "oracle": False,
    }
    value["fingerprint"] = fingerprint(value, 24)
    return value
