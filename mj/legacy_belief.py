"""Public feature projection and calibrated opponent response estimates.

Training labels can be obtained offline; inference accepts only immutable
PublicDecisionContext values. The current rules allow self draw HU only.
"""
from dataclasses import dataclass
import math

from .decision.context import PublicDecisionContext, ContextError, _visible
from .decision.profile import fingerprint

MODEL_VERSION = "public-bucket-belief-v2"


@dataclass(frozen=True)
class LegacyDecisionFeatures:
    context: PublicDecisionContext
    model_version: str = MODEL_VERSION

    def __post_init__(self):
        if not isinstance(self.context, PublicDecisionContext):
            raise TypeError("opponent inference requires PublicDecisionContext")
        self.context.validate_for("fast")
        if self.context.visible != _visible(self.context.hand, self.context.discards, self.context.melds):
            raise ContextError("inconsistent public visible material")
        if self.context.live_wall is None or self.context.dealer is None or self.context.base is None:
            raise ContextError("missing public wall/scoring fields")
        if not self.context.legal_actions:
            raise ContextError("missing legal actions")

    @classmethod
    def from_game(cls, game, seat):
        return cls(cls.normalize(PublicDecisionContext.from_game(game, seat,
            gid=getattr(game, "gid", None), round_no=getattr(game, "round_no", None))))

    @classmethod
    def from_mirror(cls, mirror, **kwargs):
        return cls(cls.normalize(PublicDecisionContext.from_mirror(mirror, **kwargs)))

    @staticmethod
    def normalize(context):
        if context.phase in ("draw", "discard"):
            return context.replace(phase="discard", react_seq=(), react_index=None, react_claim_count=None)
        return context.replace(phase="react") if context.phase in ("response_peng", "response_chi") else context

    def cache_key(self, calibration_id=""):
        return fingerprint((self.context.input_hash, self.model_version, calibration_id))

    def bucket(self, seat, tile=None):
        c = self.context
        phase = "early" if c.live_wall > 48 else "middle" if c.live_wall > 24 else "late"
        # Complete public rivers remain in identity; model features use length
        # and tile/suit recency to retain adequate calibration sample density.
        category = "none"
        if tile is not None:
            river = c.discards[seat]
            category = ("recent" if tile in river[-4:] else
                        "honor" if tile >= 27 else
                        "suit_recent" if any(t < 27 and t // 9 == tile // 9 for t in river[-4:]) else
                        "suit_other")
        return f"{phase}:{len(c.melds[seat])}:{category}"


@dataclass(frozen=True)
class Estimate:
    probability: float
    uncertainty: float
    samples: int
    calibrated: bool
    conditional_multiplier: float = 1.0
    multiplier_uncertainty: float = 0.0


class OpponentBelief:
    def __init__(self, calibration=None, min_samples=128, z=1.96):
        self.calibration = calibration
        self.min_samples = min_samples
        self.z = z
        self._estimates = {}

    def estimate(self, features, seat, target, tile=None):
        if not isinstance(features, LegacyDecisionFeatures):
            raise TypeError("public feature projection required")
        key = (features.context.input_hash, features.model_version, seat, target, tile)
        if key not in self._estimates:
            if len(self._estimates) >= 256:
                self._estimates.clear()
            self._estimates[key] = self._estimate(features, seat, target, tile)
        return self._estimates[key]

    def _estimate(self, features, seat, target, tile=None):
        c = features.context
        if target in ("chi", "pong", "kong") and (tile == 33 or
                (c.freeze > 0 and seat != c.freezer) or (target == "chi" and c.chows[seat] >= 2)
                or (target == "kong" and c.live_wall <= 0)):
            return Estimate(0.0, 0.0, 0, True)
        bucket = features.bucket(seat, tile)
        row = (self.calibration or {}).get("buckets", {}).get(f"{target}|{bucket}")
        if row and int(row["samples"]) >= self.min_samples:
            n = int(row["samples"])
            p = float(row["probability"])
            if not math.isfinite(p) or not 0 <= p <= 1:
                raise ValueError("invalid calibrated probability")
            # Wilson radius is positive even for zero-event calibration cells.
            z2 = self.z ** 2
            radius = self.z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
            calibrated = target != "next_win" or int(row.get("event_samples", 0)) >= 8
            return Estimate(p, radius, n, calibrated, float(row.get("conditional_multiplier", 1)),
                            float(row.get("multiplier_uncertainty", 1)))
        melds = len(c.melds[seat])
        rounds = len(c.discards[seat])
        tenpai = min(.90, .03 + .09 * melds + .013 * rounds)
        unseen = max(1, c.unknown_pool)
        probability = {"tenpai": tenpai, "next_win": tenpai * 5 / unseen,
                       "pong": min(.22, 3 / unseen), "chi": min(.35, 8 / unseen),
                       "kong": min(.05, 1 / unseen)}.get(target)
        if probability is None:
            raise ValueError(f"unknown belief target {target}")
        if target in ("pong", "chi") and (c.freeze > 0 and seat != c.freezer):
            probability = 0.0
        return Estimate(probability, 1.0, 0, False)

    def evaluate(self, features, tile=None):
        c = features.context
        return {seat: {target: self.estimate(features, seat, target, tile if target in ("chi", "pong", "kong") else None)
                       for target in ("tenpai", "next_win", "pong", "chi", "kong")}
                for seat in range(4) if seat != c.hero_seat}
