"""Public, calibrated terminal value after a nonwinning next hero draw.

Labels are produced offline with the frozen legacyV2 continuation policy.
They exclude wins/losses before that draw, which are valued separately.
"""
from dataclasses import dataclass
from functools import lru_cache
import math

from .legacy_belief import LegacyDecisionFeatures
from .shanten import ukeire
from .tiles import W
from .decision.profile import fingerprint

TAIL_MODEL_VERSION = "public-conditional-tail-v1"
TAIL_HORIZON = "next_hero_draw_then_calibrated_terminal_tail"
TAIL_SCORE_VERSION = "hero-net-score-tail-v1"


@lru_cache(maxsize=1)
def frozen_tail_policy():
    from .legacy_eval import LegacyTwoPlyProfile
    from .legacy_react import LegacyReactionProfile
    return fingerprint((LegacyTwoPlyProfile.weighted_online().fingerprint,
                        LegacyReactionProfile.v2_online().fingerprint))


def continuation_bucket(features, standing, locked, *, first_draw_delay=4,
                        chain=0, chain_piao=0, kong_draw=False):
    if not isinstance(features, LegacyDecisionFeatures):
        raise TypeError("public feature projection required")
    c = features.context
    s, _, live = ukeire(standing, locked, c.visible)
    draws = max(0, (c.live_wall - first_draw_delay) // 4)
    opportunity = 0 if draws == 0 else 1 if draws <= 3 else 2 if draws <= 7 else 3
    speed = 0 if live < 8 else 1 if live < 16 else 2 if live < 32 else 3
    # Scoring differences and multiplier chains must never share a cell.
    return ":".join(map(str, (s, locked, opportunity, speed, min(2, standing[W]),
        int(c.hero_seat == c.dealer), int(bool(c.you_cai_bi_kao)),
        chain, chain_piao, int(kong_draw))))


@dataclass(frozen=True)
class TailEstimate:
    mean: float = 0.0
    uncertainty: float = 0.0
    calibrated: bool = False
    samples: int = 0
    bucket: str = ""


class ContinuationValue:
    def __init__(self, calibration, min_samples=32, z=1.96):
        self.calibration = calibration
        self.min_samples = min_samples
        self.z = z
        self.policy = frozen_tail_policy()

    def estimate(self, features, standing, locked, **kwargs):
        bucket = continuation_bucket(features, standing, locked, **kwargs)
        artifact = self.calibration or {}
        if (artifact.get("tail_model_version") != TAIL_MODEL_VERSION
                or artifact.get("tail_policy_fingerprint") != self.policy):
            return TailEstimate(bucket=bucket)
        row = artifact.get("tail_buckets", {}).get(bucket)
        if not row or row["seed_samples"] < self.min_samples or row["validation_seed_samples"] < 8:
            return TailEstimate(bucket=bucket)
        scale = features.context.base
        # Include held-out bias rather than claiming the training mean is exact.
        error = self.z * row["standard_error"] + abs(row["validation_bias"])
        if not math.isfinite(error):
            raise ValueError("nonfinite continuation uncertainty")
        return TailEstimate(row["mean"] * scale, error * scale, True,
                            row["seed_samples"], bucket)
