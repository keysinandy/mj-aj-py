"""Additive values in hero net points on one common next-hero-draw horizon."""
from dataclasses import asdict, dataclass
import math

from .decision.score_value import ScoreValue
from .legacy_completion import CompletionEstimator
from .legacy_tail import TAIL_HORIZON, TAIL_SCORE_VERSION
from .shanten import shanten, ukeire


@dataclass(frozen=True)
class ScoreEV:
    win_ev: float = 0.0
    loss_ev: float = 0.0
    continuation_ev: float = 0.0
    tempo_ev: float = 0.0
    uncertainty: float = 0.0
    coverage: float = 0.0
    complete: bool = False
    calibrated: bool = False
    horizon: str = "next_hero_draw_terminal_or_zero_tail"
    model_version: str = "hero-net-score-v1"
    continuation_bucket: str = ""
    continuation_samples: int = 0

    def __post_init__(self):
        if any(not math.isfinite(getattr(self, key)) for key in
               ("win_ev", "loss_ev", "continuation_ev", "tempo_ev", "uncertainty", "coverage")):
            raise ValueError("nonfinite score EV")
        if self.win_ev < 0 or self.loss_ev > 0 or self.uncertainty < 0 or not 0 <= self.coverage <= 1:
            raise ValueError("invalid score sign/coverage")

    @property
    def total_ev(self):
        return self.win_ev + self.loss_ev + self.continuation_ev + self.tempo_ev

    def as_json(self):
        return dict(asdict(self), total_ev=self.total_ev)


def standing_value(features, standing, locked, belief, *, first_draw_delay=4,
                   chain=0, chain_piao=0, kong_draw=False, budget=None, discard_tile=None,
                   continuation=None, completion_estimator=None):
    c = features.context
    remaining = c.remaining
    total = sum(remaining)
    reward, winning = 0.0, 0
    scorer = ScoreValue(c.dealer, c.base, c.you_cai_bi_kao, c.hero_seat)
    if shanten(standing, locked) == 0 and total:
        for tile in ukeire(standing, locked, c.visible)[1]:
            if budget:
                budget.check()
            mass = remaining[tile]
            final = list(standing)
            final[tile] += 1
            score = scorer.hu(final, standing, locked, tile, kong_draw, chain, chain_piao)
            if score.legal:
                reward += mass * score.reward / total
                winning += mass
    conditional = winning / total if total else 0
    estimator = completion_estimator or CompletionEstimator(budget)
    completion = (estimator.after_discard(features, belief, discard_tile,
                  conditional_win_probability=conditional, next_draw_only=True) if discard_tile is not None and first_draw_delay == 4 else
                  estimator.evaluate(features, belief, first_draw_delay=first_draw_delay,
                  conditional_win_probability=conditional, next_draw_only=True))
    # Opponent loss events end before hero's opportunity; hero wins happen
    # only on the surviving mass. Tail labels condition on reaching that draw
    # without a legal HU, so they cannot duplicate either terminal component.
    tail_ev, tail_error, tail_calibrated = 0.0, 0.0, True
    tail = None
    residual = completion.next_draw_probability * (1-conditional)
    if continuation is not None and residual:
        tail = continuation.estimate(features, standing, locked, first_draw_delay=first_draw_delay,
                                     chain=chain, chain_piao=chain_piao, kong_draw=kong_draw)
        tail_ev = residual*tail.mean
        tail_error = residual*tail.uncertainty + (1-conditional)*completion.next_draw_uncertainty*(abs(tail.mean)+tail.uncertainty)
        tail_calibrated = tail.calibrated
    value = ScoreEV(reward * completion.next_draw_probability, completion.loss_ev,
                    continuation_ev=tail_ev,
                    uncertainty=completion.uncertainty + reward*completion.next_draw_uncertainty + tail_error,
                    coverage=min(completion.coverage, float(tail_calibrated)), complete=True,
                    calibrated=completion.calibrated and tail_calibrated,
                    horizon=TAIL_HORIZON if continuation is not None else ScoreEV.horizon,
                    model_version=TAIL_SCORE_VERSION if continuation is not None else ScoreEV.model_version,
                    continuation_bucket=tail.bucket if tail else "",
                    continuation_samples=tail.samples if tail else 0)
    return value, completion


def confident_winner(values, baseline, profile):
    if baseline not in values:
        return baseline, "baseline_not_admitted"
    if len(values) > profile.root_cap:
        return baseline, "root_cap_exceeded"
    rows = list(values.values())
    if not all(v.complete and v.calibrated for v in rows):
        return baseline, "incomplete_or_uncalibrated"
    if len({(v.horizon, v.model_version) for v in rows}) != 1:
        return baseline, "incompatible_horizon"
    best = max(values, key=lambda a: (values[a].total_ev, a == baseline, -a))
    if best == baseline:
        return baseline, "baseline_best"
    lower = values[best].total_ev - values[best].uncertainty
    other_upper = max(v.total_ev + v.uncertainty for a, v in values.items() if a != best)
    if lower <= other_upper + profile.min_margin:
        return baseline, "overlapping_confidence_or_margin"
    return best, "confidence_bounded_score_gain"
