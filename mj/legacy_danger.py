"""Settlement risk with explicit mutually exclusive self-draw event semantics."""
from dataclasses import asdict, dataclass
from .scoring import settle


def aggregate_loss(events, multi_winner=False):
    """Events are (probability, positive conditional loss), in priority order.

    Multiple winners can coexist only when a rules adapter explicitly permits
    it. Sequential self-draw events consume surviving mass once each.
    """
    survival = 1.0
    total = 0.0
    for probability, loss in events:
        if not 0 <= probability <= 1 or loss < 0:
            raise ValueError("invalid loss event")
        total += probability * loss * (1.0 if multi_winner else survival)
        if not multi_winner:
            survival *= 1 - probability
    return total


@dataclass(frozen=True)
class TileDanger:
    tile: int
    loss_ev: float
    tempo_ev: float
    uncertainty: float
    coverage: float
    calibrated: bool
    opponents: tuple
    immediate_ron_probability: float = 0.0
    rules: str = "self_draw_only_single_winner"

    def as_json(self):
        return asdict(self)


class DangerEstimator:
    def evaluate(self, features, legal_tiles, belief, *, completion_estimator=None):
        c = features.context
        from .legacy_completion import CompletionEstimator
        estimator = completion_estimator or CompletionEstimator()
        next_only = completion_estimator is not None
        base = estimator.evaluate(features, belief, next_draw_only=next_only)
        result = {}
        for tile in legal_tiles:
            rows = []
            for step in (1, 2, 3):
                seat = (c.hero_seat + step) % 4
                win = belief.estimate(features, seat, "next_win")
                pong = belief.estimate(features, seat, "pong", tile)
                kong = belief.estimate(features, seat, "kong", tile)
                chi = belief.estimate(features, seat, "chi", tile) if step == 1 else None
                payment = -float(settle(seat, c.dealer, win.conditional_multiplier, c.base)[c.hero_seat])
                rows.append({"seat": seat, "next_self_draw_win": win.probability,
                             "pong": pong.probability, "chi": chi.probability if chi else 0,
                             "kong": kong.probability, "conditional_loss": payment,
                             "calibrated": win.calibrated and pong.calibrated and kong.calibrated and (chi is None or chi.calibrated)})
            interrupted = estimator.after_discard(features, belief, tile, next_draw_only=next_only)
            tempo = interrupted.loss_ev - base.loss_ev
            result[tile] = TileDanger(tile, base.loss_ev, tempo, interrupted.uncertainty,
                                      interrupted.coverage, interrupted.calibrated, tuple(rows))
        return result
