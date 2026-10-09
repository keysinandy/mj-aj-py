"""Draw opportunity distribution conditional on public seat order and survival."""
from dataclasses import asdict, dataclass

from .scoring import settle


@dataclass(frozen=True)
class CompletionEstimate:
    next_draw_probability: float
    horizon: tuple
    completion_probability: float
    loss_ev: float
    uncertainty: float
    calibrated: bool
    coverage: float
    model_version: str = "public-survival-v2"
    next_draw_uncertainty: float = 0.0

    def as_json(self):
        return asdict(self)


class CompletionEstimator:
    def __init__(self, budget=None):
        self.budget = budget
        self._cache = {}

    def evaluate(self, features, belief, *, first_draw_delay=4, conditional_win_probability=0.0,
                 next_draw_only=False):
        c = features.context
        if first_draw_delay < 1 or not 0 <= conditional_win_probability <= 1:
            raise ValueError("invalid horizon")
        if self.budget:
            self.budget.check()
        key = (c.input_hash, id(belief), first_draw_delay, conditional_win_probability, next_draw_only)
        cached = self._cache.get(key)
        if cached is not None and cached[0] is belief:
            return cached[1]
        survival = 1.0
        survival_low = survival_high = 1.0
        loss, lower_loss, upper_loss = 0.0, 0.0, 0.0
        reach_low = reach_high = 0.0
        checks = []
        horizon = []
        estimates = {}
        limit = min(c.live_wall, first_draw_delay) if next_draw_only else c.live_wall
        for index in range(1, limit + 1):
            # Replacement draws (delay=1) are immediate. Reaction PASS can
            # have a shorter first hero draw than a claim + forced discard.
            if index >= first_draw_delay and (index - first_draw_delay) % 4 == 0:
                if not horizon:
                    reach_low, reach_high = survival_low, survival_high
                horizon.append((index, survival))
            else:
                seat = (c.hero_seat + index - first_draw_delay) % 4
                if seat == c.hero_seat:
                    continue
                if seat not in estimates:
                    estimates[seat] = belief.estimate(features, seat, "next_win")
                e = estimates[seat]
                p_low, p_high = max(0.0, e.probability-e.uncertainty), min(1.0, e.probability+e.uncertainty)
                payment = -float(settle(seat, c.dealer, e.conditional_multiplier, c.base)[c.hero_seat])
                if not horizon:
                    loss -= survival * e.probability * payment
                    magnitude_error = -float(settle(seat, c.dealer, e.multiplier_uncertainty, c.base)[c.hero_seat])
                    # Multiplier error matters only when that opponent wins.
                    lower_loss += survival_low*p_low*max(0.0, payment-magnitude_error)
                    upper_loss += survival_high*p_high*(payment+magnitude_error)
                survival *= 1 - e.probability
                survival_low *= 1-p_high
                survival_high *= 1-p_low
                checks.append(e.calibrated)
        reach = horizon[0][1] if horizon else 0.0
        # Distribution includes attrition after nonwinning hero draws.
        alive, completion = 1.0, 0.0
        for index, probability in horizon:
            completion += alive * probability * conditional_win_probability
            alive *= 1 - conditional_win_probability
        result = CompletionEstimate(reach, tuple(horizon), min(1.0, completion), loss,
            max(-loss-lower_loss, upper_loss+loss, 0.0), all(checks),
            sum(checks)/len(checks) if checks else 1.0,
            next_draw_uncertainty=max(reach-reach_low, reach_high-reach, 0.0))
        if len(self._cache) >= 256:
            self._cache.clear()
        self._cache[key] = (belief, result)
        return result

    def after_discard(self, features, belief, tile, *, conditional_win_probability=0.0,
                      next_draw_only=False):
        """Mix public response branches before the next hero draw.

        A claim skips the claimant's draw and all earlier seats; subsequent
        draws start at claimant+1. PONG priority is clockwise, then next-seat
        CHI. White cannot be claimed under this project's rules.
        """
        from .tiles import W
        c = features.context
        base = self.evaluate(features, belief, conditional_win_probability=conditional_win_probability,
                             next_draw_only=next_draw_only)
        if tile == W:
            return base
        branches = []
        remaining = 1.0
        errors, calibrated = [], []
        for step in (1, 2, 3):
            seat = (c.hero_seat + step) % 4
            if c.freeze > 0 and seat != c.freezer:
                continue
            pong = belief.estimate(features, seat, "pong", tile)
            kong = belief.estimate(features, seat, "kong", tile)
            if pong.probability + kong.probability > 1:
                raise ValueError("exclusive claim probabilities exceed one")
            branches.append((remaining*pong.probability, step))
            # A kong gives the claimant a replacement draw before the usual
            # subsequent seats. Encode that additional draw as step-1.
            branches.append((remaining*kong.probability, step-1))
            remaining *= 1-pong.probability-kong.probability
            errors.append(pong.uncertainty + kong.uncertainty)
            calibrated.extend((pong.calibrated, kong.calibrated))
        next_seat = (c.hero_seat+1) % 4
        if c.chows[next_seat] < 2 and not (c.freeze > 0 and next_seat != c.freezer):
            chi = belief.estimate(features, next_seat, "chi", tile)
            branches.append((remaining*chi.probability, 1))
            remaining *= 1-chi.probability
            errors.append(chi.uncertainty)
            calibrated.append(chi.calibrated)
        branches.append((remaining, 0))
        reach, loss, error, probability, reach_error = 0.0, 0.0, 0.0, 0.0, 0.0
        branch_losses, branch_reaches = [], []
        horizons = {}
        for weight, step in branches:
            estimate = self.evaluate(features, belief, first_draw_delay=4-step,
                                     conditional_win_probability=conditional_win_probability,
                                     next_draw_only=next_draw_only)
            reach += weight*estimate.next_draw_probability
            loss += weight*estimate.loss_ev
            error += weight*estimate.uncertainty
            reach_error += weight*estimate.next_draw_uncertainty
            branch_losses.append(estimate.loss_ev)
            branch_reaches.append(estimate.next_draw_probability)
            probability += weight*estimate.completion_probability
            calibrated.append(estimate.calibrated)
            for index, mass in estimate.horizon:
                horizons[index] = horizons.get(index, 0)+weight*mass
        # Response probability uncertainty contributes at most the spread of
        # the branch values; shared zero-loss branches need no false penalty.
        error += sum(errors)*(max(branch_losses)-min(branch_losses))
        reach_error += sum(errors)*(max(branch_reaches)-min(branch_reaches))
        return CompletionEstimate(reach, tuple(sorted(horizons.items())), probability, loss,
                                  error, all(calibrated), sum(calibrated)/len(calibrated) if calibrated else 1,
                                  next_draw_uncertainty=min(1.0, reach_error))
