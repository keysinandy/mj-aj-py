"""Independent, default-off rollout contract for legacyV2 decision quality."""
from dataclasses import asdict, dataclass
from functools import cached_property
import math

from .decision.profile import fingerprint


FLAGS = (
    "belief_shadow_enabled", "danger_rerank_enabled", "score_ev_rerank_enabled",
    "completion_enabled", "joint_reaction_enabled", "survival_hu_enabled",
    "adaptive_budget_enabled", "hand_plan_enabled", "continuation_ev_enabled",
)


@dataclass(frozen=True)
class LegacyQualityProfile:
    version: str = "legacy-quality-v2"
    budget_version: str = "shared-deadline-perf-counter-v2"
    belief_shadow_enabled: bool = False
    danger_rerank_enabled: bool = False
    score_ev_rerank_enabled: bool = False
    completion_enabled: bool = False
    joint_reaction_enabled: bool = False
    survival_hu_enabled: bool = False
    adaptive_budget_enabled: bool = False
    hand_plan_enabled: bool = False
    continuation_ev_enabled: bool = False
    calibration_id: str = ""
    root_cap: int = 3
    min_samples: int = 128
    min_tail_samples: int = 32
    min_margin: float = 0.10
    confidence_z: float = 1.96
    discard_budget_ms: float = 50.0
    reaction_budget_ms: float = 10.0
    hu_kong_budget_ms: float = 15.0
    route_hysteresis: float = 0.25

    def __post_init__(self):
        if not 1 <= self.root_cap <= 3 or self.min_samples < 2 or self.min_tail_samples < 2:
            raise ValueError("root cap must be 1..3 and sample minimum >= 2")
        for name in ("min_margin", "confidence_z", "discard_budget_ms",
                     "reaction_budget_ms", "hu_kong_budget_ms", "route_hysteresis"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid {name}")
        if self.discard_budget_ms > 50 or self.reaction_budget_ms > 10 or self.hu_kong_budget_ms > 15:
            raise ValueError("quality budgets may not expand the frozen window budgets")

    @property
    def active(self):
        return any(getattr(self, name) for name in FLAGS)

    @cached_property
    def fingerprint(self):
        return fingerprint(self._payload)

    @cached_property
    def _payload(self):
        return asdict(self)

    def as_json(self):
        # Frozen scalar fields can be cached; each audit still owns its dict.
        return dict(self._payload, fingerprint=self.fingerprint)

    @classmethod
    def phase(cls, phase, **overrides):
        values = {
            "shadow": {"belief_shadow_enabled": True},
            "A": {"danger_rerank_enabled": True},
            "B": {"score_ev_rerank_enabled": True, "completion_enabled": True,
                  "continuation_ev_enabled": True},
            "C": {"joint_reaction_enabled": True, "survival_hu_enabled": True,
                  "completion_enabled": True, "continuation_ev_enabled": True},
            "D": {"adaptive_budget_enabled": True},
            "route": {"hand_plan_enabled": True},
            "combined": {"danger_rerank_enabled": True, "score_ev_rerank_enabled": True,
                         "completion_enabled": True, "joint_reaction_enabled": True,
                         "survival_hu_enabled": True, "adaptive_budget_enabled": True,
                         "hand_plan_enabled": True, "continuation_ev_enabled": True},
            "off": {},
        }
        if phase not in values:
            raise ValueError(f"unknown quality phase {phase}")
        return cls(**(values[phase] | overrides))
