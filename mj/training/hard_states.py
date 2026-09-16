"""Permanent hard-state regression set with failure-mode deduplication.

Entries are evaluation data: they are never used for gradient updates unless
their source group belongs to the training split.  Each checkpoint reports
per-entry regret/catastrophic evidence plus fixed/regressed counts against
the previous promoted policy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..decision.profile import fingerprint

REGISTRY_SCHEMA = "hard-state-registry-v1"
TRIGGERS = (
    "extreme_regret", "catastrophic_action", "special_failure",
    "paired_game_mistake", "online_failure", "regression_recurrence",
)
REFERENCE_ROW_SCHEMA = "search-reference-context-v1"


@dataclass(frozen=True)
class HardStateEntry:
    state_id: str
    source_group: str
    trigger: str
    failure_tag: str
    context: Mapping[str, Any] | None = None
    history: Mapping[str, Any] | None = None
    sample: Mapping[str, Any] | None = None
    reference_regret: float | None = None
    generation: int = 0
    notes: str = ""

    def __post_init__(self):
        if not str(self.state_id) or not str(self.source_group):
            raise ValueError("hard state requires state_id/source_group")
        if self.trigger not in TRIGGERS:
            raise ValueError(f"unknown hard-state trigger: {self.trigger}")
        if not str(self.failure_tag):
            raise ValueError("hard state requires a failure tag")

    def as_json(self):
        value = asdict(self)
        value["schema"] = REGISTRY_SCHEMA
        value["oracle"] = False
        value["fingerprint"] = fingerprint(value, 24)
        return value

    @classmethod
    def from_json(cls, data: Mapping[str, Any]):
        value = dict(data)
        supplied = value.pop("fingerprint", None)
        value.pop("schema", None)
        value.pop("oracle", None)
        allowed = set(cls.__dataclass_fields__)
        unknown = set(value) - allowed
        if unknown:
            raise ValueError("unknown hard-state fields: " +
                             ", ".join(sorted(unknown)))
        entry = cls(**value)
        if supplied is not None and supplied != entry.as_json()["fingerprint"]:
            raise ValueError("hard-state fingerprint mismatch")
        return entry

    def as_reference_row(self):
        """A reference-context row consumable by the regret evaluator."""
        return {
            "schema": REFERENCE_ROW_SCHEMA,
            "source_group": self.source_group,
            "generation": int(self.generation),
            "policy_version_source": "hard-set",
            "context": self.context,
            "history": self.history,
            "sample": self.sample,
        }


class HardStateRegistry:
    """Append-only, state-id deduplicated hard-state set."""

    def __init__(self, entries: Iterable[HardStateEntry] = ()):
        self.entries: dict[str, HardStateEntry] = {}
        for entry in entries:
            self.add(entry)

    def __len__(self):
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries.values())

    def add(self, entry: HardStateEntry):
        existing = self.entries.get(entry.state_id)
        if existing is None:
            self.entries[entry.state_id] = entry
            return entry
        # Same information state: keep the strongest documented failure and
        # merge the tag so failure-mode coverage is preserved.
        tags = sorted({existing.failure_tag, entry.failure_tag})
        trigger = (entry.trigger
                   if entry.reference_regret is not None and
                   (existing.reference_regret is None or
                    entry.reference_regret > existing.reference_regret)
                   else existing.trigger)
        merged = HardStateEntry(
            state_id=existing.state_id, source_group=existing.source_group,
            trigger=trigger, failure_tag="|".join(tags),
            context=existing.context or entry.context,
            history=existing.history or entry.history,
            sample=existing.sample or entry.sample,
            reference_regret=max(
                [value for value in (existing.reference_regret,
                                     entry.reference_regret)
                 if value is not None], default=None),
            generation=existing.generation, notes=existing.notes or entry.notes)
        self.entries[entry.state_id] = merged
        return merged

    def add_sample(self, sample, *, trigger, failure_tag=None, generation=0,
                   context=None, history=None, notes=""):
        """Register one teacher sample with its regret evidence."""
        tag = failure_tag or (
            ",".join(sample.special_state_tags) if sample.special_state_tags
            else (sample.phase or "unknown"))
        entry = HardStateEntry(
            state_id=sample.state_id, source_group=sample.source_group,
            trigger=trigger, failure_tag=str(tag),
            context=(context if context is not None
                     else getattr(sample, "_hard_context", None)),
            history=(history if history is not None
                     else getattr(sample, "_hard_history", None)),
            sample=sample.as_json(), reference_regret=sample.policy_regret,
            generation=int(generation), notes=str(notes))
        return self.add(entry)

    def add_from_dataset(self, dataset, *, regret_threshold=24.0,
                         generation=0):
        """Auto-register extreme-regret and catastrophic rows."""
        added = []
        for sample in dataset.samples:
            catastrophic = (sample.policy_regret is not None and
                            sample.policy_regret >= float(regret_threshold))
            tag = sample.special_state_tags or ()
            if catastrophic:
                added.append(self.add_sample(
                    sample, trigger="catastrophic_action",
                    failure_tag="|".join(tag) or sample.phase or "unknown",
                    generation=generation))
            elif sample.teacher_status in ("ambiguous",) and tag:
                added.append(self.add_sample(
                    sample, trigger="special_failure",
                    failure_tag="|".join(tag), generation=generation))
        return added

    def failure_modes(self):
        modes: dict[str, int] = {}
        for entry in self.entries.values():
            for tag in str(entry.failure_tag).split("|"):
                modes[tag] = modes.get(tag, 0) + 1
        return dict(sorted(modes.items()))

    def rows(self):
        return [entry.as_reference_row() for entry in self.entries.values()
                if entry.sample is not None and entry.context is not None]

    def as_json(self):
        value = {
            "schema": REGISTRY_SCHEMA,
            "count": len(self.entries),
            "failure_modes": self.failure_modes(),
            "entries": [entry.as_json()
                        for entry in sorted(self.entries.values(),
                                            key=lambda item: item.state_id)],
            "oracle": False,
        }
        value["fingerprint"] = fingerprint(value, 24)
        return value

    def save(self, path):
        Path(path).write_text(
            json.dumps(self.as_json(), ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("schema") != REGISTRY_SCHEMA:
            raise ValueError("unsupported hard-state registry schema")
        supplied = data.pop("fingerprint", None)
        entries = [HardStateEntry.from_json(item)
                   for item in data.get("entries", ())]
        registry = cls(entries)
        if supplied is not None and supplied != registry.as_json()["fingerprint"]:
            raise ValueError("hard-state registry fingerprint mismatch")
        return registry


def hard_set_evaluation(model, registry: HardStateRegistry, *,
                        profile, device="cpu",
                        catastrophe_threshold=None):
    """Evaluate every checkpoint on the same hard states."""
    from .regret_selection import evaluate_checkpoint

    rows = registry.rows()
    if not rows:
        raise ValueError("hard-state registry has no evaluable rows")
    evaluation = evaluate_checkpoint(
        model, rows, profile=profile, device=device,
        catastrophe_threshold=catastrophe_threshold)
    value = evaluation.as_json()
    value["schema"] = "hard-state-evaluation-v1"
    value["failure_modes"] = registry.failure_modes()
    value["oracle"] = False
    return value


def hard_set_regression(previous: Mapping[str, Any],
                        current: Mapping[str, Any], *,
                        mean_tolerance=0.0, p95_tolerance=0.0,
                        catastrophic_delta=0.0):
    """Compare two hard-set evaluations and list fixed/regressed states."""
    current_rows = {row.get("context_hash"): row
                    for row in current.get("rows", ())}
    previous_rows = {row.get("context_hash"): row
                     for row in previous.get("rows", ())}
    fixed, regressed = [], []
    for context_hash, row in current_rows.items():
        old = previous_rows.get(context_hash)
        if old is None:
            continue
        old_regret = old.get("regret")
        new_regret = row.get("regret")
        if old_regret is None or new_regret is None:
            continue
        if new_regret < old_regret:
            fixed.append({"context_hash": context_hash,
                          "from": old_regret, "to": new_regret})
        elif new_regret > old_regret:
            regressed.append({"context_hash": context_hash,
                              "from": old_regret, "to": new_regret})
    previous_mean = previous.get("mean")
    current_mean = current.get("mean")
    previous_p95 = previous.get("p95")
    current_p95 = current.get("p95")
    previous_cat = previous.get("catastrophic_regret_rate") or 0.0
    current_cat = current.get("catastrophic_regret_rate") or 0.0
    mean_ok = (previous_mean is None or current_mean is None or
               current_mean <= previous_mean + float(mean_tolerance))
    p95_ok = (previous_p95 is None or current_p95 is None or
              current_p95 <= previous_p95 + float(p95_tolerance))
    catastrophic_ok = current_cat <= previous_cat + float(catastrophic_delta)
    value = {
        "schema": "hard-state-regression-report-v1",
        "mean_delta": (None if previous_mean is None or current_mean is None
                       else current_mean - previous_mean),
        "p95_delta": (None if previous_p95 is None or current_p95 is None
                      else current_p95 - previous_p95),
        "catastrophic_delta": current_cat - previous_cat,
        "fixed": fixed, "regressed": regressed,
        "passed": bool(mean_ok and p95_ok and catastrophic_ok),
        "checks": {"mean": mean_ok, "p95": p95_ok,
                   "catastrophic": catastrophic_ok},
        "oracle": False,
    }
    value["fingerprint"] = fingerprint(value, 24)
    return value
