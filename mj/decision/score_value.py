"""Rule-backed score and root-transition adapters for Fast EV."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping, Optional

from ..game import HU
from ..scoring import hand_multiplier, settle
from ..win import is_baotou, is_win
from ..tiles import W
from .profile import fingerprint


# A conservative rule-derived ceiling for a single game.  The live wall starts
# with 63 drawable tiles; a hero action chain cannot contain more state-changing
# kong/piao events than that.  Add the maximum two independent doubling flags
# and the largest seven-pairs multiplier.  This is intentionally loose (and
# therefore rarely prunes) but is never based on observed maxima.
MAX_RULE_CHAIN_EVENTS = 63
MAX_RULE_EXTRA_DOUBLINGS = 9

# The envelope is deliberately versioned independently from the legacy scalar
# bound.  A profile/teacher fingerprint therefore changes when either the
# proof formula or its supported reward model changes.
REWARD_ENVELOPE_VERSION = "reward-envelope-v1"
FAST_REWARD_MODEL = "uniform_unseen_no_opponent_actions_score_v1"
ROLLOUT_REWARD_MODEL = "four_player_terminal_hero_settlement_v1"
BOUND_MODES = frozenset((
    "derived", "override", "legacy_conservative_fallback", "unknown",
))


def theoretical_reward_bound(base=1):
    """Return a conservative absolute hero settlement bound in score units."""
    return float(24 * max(1, int(base)) *
                 (2 ** (MAX_RULE_CHAIN_EVENTS + MAX_RULE_EXTRA_DOUBLINGS)))


@dataclass(frozen=True)
class RewardCertificate:
    """Auditable proof metadata attached to a :class:`RewardEnvelope`.

    ``mode`` is intentionally not inferred from whether a number happens to
    be present.  In particular, a historical scalar bound is not allowed to
    look like a newly derived candidate bound in an explanation.
    """

    mode: str = "unknown"
    proof: str = ""
    missing: tuple = ()
    details: Mapping[str, Any] = field(default_factory=dict)
    fingerprint: str = ""

    def __post_init__(self):
        if self.mode not in BOUND_MODES:
            raise ValueError(f"unknown reward-bound mode: {self.mode}")
        object.__setattr__(self, "missing", tuple(sorted(set(
            str(value) for value in (self.missing or ())))))
        object.__setattr__(self, "details", dict(self.details or {}))
        expected = fingerprint({
            "mode": self.mode, "proof": self.proof,
            "missing": self.missing, "details": self.details,
        }, 24)
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("reward certificate fingerprint mismatch")
        object.__setattr__(self, "fingerprint", expected)

    def as_json(self):
        return {
            "mode": self.mode, "proof": self.proof,
            "missing": list(self.missing), "details": dict(self.details),
            "fingerprint": self.fingerprint,
        }

    # Small mapping conveniences keep the value pleasant to use from older
    # report code which treated certificate payloads as dictionaries.
    def __getitem__(self, key):
        return self.as_json()[key]

    def get(self, key, default=None):
        return self.as_json().get(key, default)


@dataclass(frozen=True)
class RewardEnvelope:
    """Versioned rule envelope for Fast EV and terminal rollout rewards.

    ``fast_upper`` proves ``0 <= R_fast <= fast_upper`` for the supported
    hero-only future-draw model.  ``rollout_lower`` and ``rollout_upper`` are
    the signed hero settlement range for a complete four-player terminal
    rollout; ``rollout_abs`` is the symmetric bound used by paired intervals.
    All values are in the declared base-score settlement units.
    """

    version: str = REWARD_ENVELOPE_VERSION
    candidate: Optional[int] = None
    fast_upper: Optional[float] = None
    rollout_lower: Optional[float] = None
    rollout_upper: Optional[float] = None
    rollout_abs: Optional[float] = None
    components: Mapping[str, Any] = field(default_factory=dict)
    certificate: RewardCertificate = field(default_factory=RewardCertificate)
    fast_model: str = FAST_REWARD_MODEL
    rollout_model: str = ROLLOUT_REWARD_MODEL
    reward_units: str = "base-score points"

    def __post_init__(self):
        version = str(self.version)
        if not version:
            raise ValueError("reward envelope version must not be empty")
        object.__setattr__(self, "version", version)
        if self.candidate is not None:
            try:
                object.__setattr__(self, "candidate", int(self.candidate))
            except (TypeError, ValueError) as exc:
                raise ValueError("reward envelope candidate must be integer") from exc
        for name in ("fast_upper", "rollout_lower", "rollout_upper",
                     "rollout_abs"):
            value = getattr(self, name)
            if value is not None:
                value = float(value)
                if (not math.isfinite(value) or
                        (name in ("fast_upper", "rollout_abs") and
                         value < 0)):
                    raise ValueError(f"invalid {name}={value!r}")
                object.__setattr__(self, name, value)
        if (self.rollout_lower is not None and
                self.rollout_upper is not None and
                self.rollout_lower > self.rollout_upper):
            raise ValueError("rollout_lower must not exceed rollout_upper")
        if self.rollout_abs is not None and (
                self.rollout_lower is not None and
                self.rollout_upper is not None and self.rollout_abs < max(
                    abs(self.rollout_lower), abs(self.rollout_upper))):
            raise ValueError("rollout_abs does not cover rollout range")
        object.__setattr__(self, "components", dict(self.components or {}))
        if isinstance(self.certificate, Mapping):
            object.__setattr__(
                self, "certificate", RewardCertificate(
                    mode=str(self.certificate.get("mode", "unknown")),
                    proof=str(self.certificate.get("proof", "")),
                    missing=tuple(self.certificate.get("missing", ())),
                    details=dict(self.certificate.get("details", {})),
                    fingerprint=str(self.certificate.get("fingerprint", "")),
                ))
        elif not isinstance(self.certificate, RewardCertificate):
            raise ValueError("reward envelope certificate must be a mapping")

    @property
    def mode(self):
        return self.certificate.mode

    @property
    def known(self):
        return self.certificate.mode != "unknown"

    @property
    def safe_for_fast_pruning(self):
        return (self.fast_upper is not None and
                self.mode in ("derived", "override"))

    def as_json(self):
        certificate = self.certificate.as_json()
        rollout_missing = tuple(sorted(set(
            str(value) for value in self.components.get(
                "rollout_missing", self.certificate.missing))))
        missing = tuple(sorted(set(
            str(value) for value in self.certificate.missing)))
        value = {
            "version": self.version, "candidate": self.candidate,
            "fast_model": self.fast_model, "fast_upper": self.fast_upper,
            "rollout_model": self.rollout_model,
            "rollout_lower": self.rollout_lower,
            "rollout_upper": self.rollout_upper,
            "rollout_abs": self.rollout_abs,
            "components": dict(self.components),
            "certificate": certificate,
            "certificate_fingerprint": certificate["fingerprint"],
            "mode": certificate["mode"],
            "fast_status": ("known" if self.fast_upper is not None
                             else "unknown"),
            "rollout_status": ("known" if self.rollout_abs is not None
                                else "unknown"),
            "fast_missing": list(self.components.get(
                "fast_missing", missing if self.fast_upper is None else ())),
            "rollout_missing": list(rollout_missing
                                     if self.rollout_abs is None else ()),
            "missing": list(missing),
            "reward_units": self.reward_units,
        }
        return value

    to_json = as_json

    def __getitem__(self, key):
        return self.as_json()[key]

    def get(self, key, default=None):
        return self.as_json().get(key, default)


def _unknown_envelope(candidate, missing, *, reason=""):
    missing = tuple(sorted(set(str(value) for value in (missing or ()))))
    certificate = RewardCertificate(
        mode="unknown", proof=reason or "insufficient_public_information",
        missing=missing, details={"reason": reason} if reason else {})
    return RewardEnvelope(candidate=candidate, components={},
                          certificate=certificate)


def _legacy_envelope(base, candidate, missing=()):
    bound = theoretical_reward_bound(base or 1)
    certificate = RewardCertificate(
        mode="legacy_conservative_fallback",
        proof="theoretical_reward_bound",
        missing=missing,
        details={"legacy_bound": bound},
    )
    return RewardEnvelope(
        candidate=candidate, fast_upper=bound,
        rollout_lower=-bound, rollout_upper=bound, rollout_abs=bound,
        components={"legacy_bound": bound}, certificate=certificate)


def _finite_positive(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _candidate_tiles(context, candidate):
    """Return a normalized ordinary candidate or a reason for rejection."""
    try:
        candidate = int(candidate)
    except (TypeError, ValueError):
        return None, "candidate_not_integer"
    if context.legal_actions:
        legal = tuple(int(action) for action in context.legal_actions
                      if 0 <= int(action) < 34)
        if not legal:
            return None, "context_has_no_ordinary_discard"
    else:
        legal = tuple(int(action) for action in context.legal_discards)
    if not 0 <= candidate < 34:
        return None, "candidate_not_discard"
    if context.hand[candidate] <= 0:
        return None, "candidate_not_in_hand"
    if legal and candidate not in legal:
        return None, "candidate_not_in_legal_set"
    return candidate, None


def _meld_resource(context, seat, *, hero_hand=None):
    melds = context.melds[seat]
    locked = len(melds)
    pong_count = sum(kind == "pong" for kind, _tile in melds)
    # A pong can be upgraded once.  It does not consume an additional meld
    # slot, hence the explicit +pong_count term from the rule contract.
    kong_slots = max(0, 4 - locked + pong_count)
    if hero_hand is not None:
        hand = tuple(hero_hand)
        white_material = hand[W]
    else:
        hand = None
        white_material = None
    return {
        "locked": locked, "existing_pong_count": pong_count,
        "kong_slots_max": kong_slots, "hand": hand,
        "white_material": white_material,
    }


def _seven_pair_groups(context, hand, locked):
    if locked != 0:
        return 0
    # This is a capacity upper bound, not a claim that every quad can occur
    # in one seven-pairs hand.  It is safe because it only enlarges the set of
    # potentially realizable high-fan paths.
    possible = 0
    for tile in range(33):
        held = int(hand[tile])
        remaining = max(0, int(context.remaining[tile]))
        if held + remaining >= 4:
            possible += 1
    return min(7, possible)


def _seat_resource(context, seat, *, hero_hand=None, hero_chain=None,
                   hero_piao=None, future_draws=None):
    resource = _meld_resource(context, seat, hero_hand=hero_hand)
    locked = resource["locked"]
    if hero_hand is not None:
        hand = tuple(hero_hand)
        hand_white = int(hand[W])
        white_supply = hand_white + max(0, int(context.remaining[W]))
    else:
        hand = None
        # All material not visible may be held by this potential winner.  It
        # is intentionally a count-only worst case; no hidden identity is
        # inspected or exported.
        hand_white = max(0, int(context.remaining[W]))
        # For an unknown opponent hand, ``remaining`` already partitions all
        # possible concealed whiteboards and wall cards.  Do not count that
        # public pool twice.
        white_supply = hand_white
    if future_draws is None:
        future_draws = max(0, int(context.live_wall or 0))
    future_draws = max(0, int(future_draws))
    piao_events = min(white_supply, future_draws)
    chain_value = hero_chain
    piao_value = hero_piao
    if chain_value is None:
        chain_value = context.chain_counts[seat]
    if piao_value is None:
        piao_value = context.chain_piao_counts[seat]
    if chain_value is None or piao_value is None:
        return None
    chain_value = max(0, int(chain_value))
    piao_value = max(0, int(piao_value))
    kong_events = min(resource["kong_slots_max"], future_draws)
    chain_max = chain_value + min(future_draws,
                                  kong_events + piao_events)
    seven_groups = (_seven_pair_groups(context, hand, locked)
                    if hand is not None else (0 if locked else 7))
    # For a known hero hand, future draws are limited by the Fast horizon.
    # For a hidden opponent, this is a deliberately broad public-resource
    # capacity bound.
    future_white = min(max(0, int(context.remaining[W])), future_draws)
    four_white = (hand_white + future_white + piao_value + piao_events >= 4)
    baotou = False
    if hand is not None and is_baotou(hand, locked):
        baotou = True
    elif future_draws > 0 and sum(context.remaining) > 0:
        # Unless every future card/path has been ruled out, the envelope must
        # retain the extra doubling.  A sampled/observed absence is not proof
        # that this rare path is impossible.
        baotou = True
    return {
        "locked": locked,
        "existing_pong_count": resource["existing_pong_count"],
        "kong_slots_max": resource["kong_slots_max"],
        "kong_max": kong_events,
        "piao_max": piao_value + piao_events,
        "chain_max": chain_max,
        "seven_pairs_groups_max": seven_groups,
        "four_white_possible": bool(four_white),
        "baotou_possible": bool(baotou),
        "white_material": hand_white,
        "future_draws": future_draws,
    }


def _multiplier_from_resource(resource):
    if resource is None:
        return None
    standard = 1
    seven = (2 << int(resource["seven_pairs_groups_max"])
             if int(resource["locked"]) == 0 else 0)
    branch = max(standard, seven)
    multiplier = branch * (2 ** int(resource["chain_max"]))
    if resource["four_white_possible"]:
        multiplier *= 2
    if resource["baotou_possible"]:
        multiplier *= 2
    return int(multiplier)


def reward_envelope(context, candidate, *, scalar_override=None,
                    allow_legacy_fallback=False, bound_mode="derived",
                    fast_horizon=2):
    """Build a public, candidate-specific reward envelope.

    The function never reads a sampled world.  It accepts only a
    ``PublicDecisionContext`` and an ordinary root discard.  Invalid or
    incomplete inputs return an ``unknown`` envelope, allowing callers to
    disable pruning without manufacturing a zero bound.  ``scalar_override``
    is an explicit compatibility/diagnostic escape hatch and is recorded as
    such in the certificate.
    """
    try:
        candidate, candidate_error = _candidate_tiles(context, candidate)
    except (AttributeError, TypeError, ValueError) as exc:
        return _unknown_envelope(None, ("context_or_candidate",),
                                 reason=f"invalid_context:{exc}")
    if candidate_error:
        return _unknown_envelope(candidate, (candidate_error,),
                                 reason=candidate_error)

    if str(getattr(context, "phase", "")) not in ("discard", "draw"):
        return _unknown_envelope(candidate, ("root_phase",),
                                 reason="ordinary_discard_required")

    context_missing = tuple(sorted(set(
        str(value) for value in
        (getattr(context, "missing_fields", ()) or ()) +
        (getattr(context, "unsupported", ()) or ()))))
    if not getattr(context, "fast_valid", True):
        if allow_legacy_fallback and getattr(context, "base", None) is not None:
            return _legacy_envelope(context.base, candidate,
                                    context_missing or ("fast_context",))
        return _unknown_envelope(
            candidate, context_missing or ("fast_context",),
            reason="fast_context_unsupported")

    if bound_mode not in BOUND_MODES:
        return _unknown_envelope(candidate, ("bound_mode",),
                                 reason="unknown_bound_mode")
    if bound_mode == "unknown":
        return _unknown_envelope(candidate, ("bound_mode",),
                                 reason="bound_mode_unknown")

    override = _finite_positive(scalar_override)
    if scalar_override is not None and override is None:
        return _unknown_envelope(candidate, ("invalid_scalar_override",),
                                 reason="invalid_scalar_override")
    if override is not None:
        certificate = RewardCertificate(
            mode="override", proof="explicit_scalar_bound_override",
            details={"scalar_bound": override})
        return RewardEnvelope(
            candidate=candidate, fast_upper=override,
            rollout_lower=-override, rollout_upper=override,
            rollout_abs=override, components={"scalar_bound": override},
            certificate=certificate)
    if bound_mode == "override":
        return _unknown_envelope(candidate, ("scalar_override",),
                                 reason="override_bound_missing")
    if bound_mode == "legacy_conservative_fallback":
        if allow_legacy_fallback and context.base is not None:
            return _legacy_envelope(context.base, candidate,
                                    ("explicit_legacy_mode",))
        return _unknown_envelope(candidate, ("legacy_fallback_disabled",),
                                 reason="legacy_fallback_disabled")

    fast_missing = []
    for name, value in (("base", context.base),
                        ("dealer", context.dealer),
                        ("chain_count", context.chain_count),
                        ("chain_piao", context.chain_piao),
                        ("live_wall", context.live_wall)):
        if value is None:
            fast_missing.append(name)
    if context.material_errors():
        fast_missing.extend(
            f"material:{value}" for value in context.material_errors())
    if fast_missing:
        if allow_legacy_fallback and context.base is not None:
            return _legacy_envelope(context.base, candidate, fast_missing)
        return _unknown_envelope(candidate, fast_missing,
                                 reason="missing_public_bound_fields")

    try:
        scorer = ScoreValue(context.dealer, context.base,
                            bool(context.you_cai_bi_kao), context.hero_seat)
        root_hand, post_chain, post_piao, is_piao = scorer.discard(
            context.hand, candidate, context.locked,
            context.chain_count, context.chain_piao)
    except (TypeError, ValueError, IndexError) as exc:
        if allow_legacy_fallback:
            return _legacy_envelope(context.base, candidate, (str(exc),))
        return _unknown_envelope(candidate, ("root_transition",),
                                 reason=f"root_transition:{exc}")

    horizon = max(0, int(fast_horizon))
    # A future own draw requires the other three seats' turns to consume at
    # least four live-wall cards.  This matches future_values' explicit wall
    # boundary while remaining safe if a caller asks for a shorter horizon.
    fast_draws = min(horizon, max(0, int(context.live_wall) // 4))
    hero = _seat_resource(
        context, context.hero_seat, hero_hand=root_hand,
        hero_chain=post_chain, hero_piao=post_piao,
        future_draws=fast_draws)
    if hero is None:
        if allow_legacy_fallback:
            return _legacy_envelope(context.base, candidate,
                                    ("hero_chain_state",))
        return _unknown_envelope(candidate, ("hero_chain_state",),
                                 reason="hero_chain_state_unknown")

    hero_multiplier = _multiplier_from_resource(hero)
    positive_factor = 24 if context.hero_seat == context.dealer else 10
    fast_upper = (0.0 if fast_draws <= 0 else
                  float(context.base * positive_factor * hero_multiplier))

    # Rollout can end with any seat winning.  We use only public meld counts,
    # public remaining-card capacity and the complete chain counters; each
    # seat receives the same worst-case live-wall budget.  The hero resource
    # includes the root transition, while the other seats remain public
    # count-only worst cases.
    rollout_missing = [
        f"chain_counts[{seat}]" for seat, value
        in enumerate(context.chain_counts) if value is None
    ]
    rollout_missing.extend(
        f"chain_piao_counts[{seat}]" for seat, value
        in enumerate(context.chain_piao_counts) if value is None
    )
    if rollout_missing:
        components = {
            "candidate": candidate,
            "root": {"post_chain": int(post_chain),
                     "post_chain_piao": int(post_piao),
                     "is_piao": bool(is_piao), "locked": context.locked},
            "chain_max": hero["chain_max"],
            "fast_draws": fast_draws,
            "kong_max": hero["kong_max"],
            "kong_slots_max": hero["kong_slots_max"],
            "existing_pong_count": hero["existing_pong_count"],
            "piao_max": hero["piao_max"],
            "seven_pairs_groups_max": hero["seven_pairs_groups_max"],
            "four_white_possible": hero["four_white_possible"],
            "baotou_possible": hero["baotou_possible"],
            "multiplier_max": hero_multiplier,
            "fast_multiplier_max": hero_multiplier,
            "fast_upper": fast_upper,
            "settlement_factor": positive_factor,
            "rollout_missing": rollout_missing,
        }
        certificate = RewardCertificate(
            mode="derived", proof="public_root_resource_envelope",
            missing=rollout_missing,
            details={
                "fast_model": FAST_REWARD_MODEL,
                "rollout_model": ROLLOUT_REWARD_MODEL,
                "rollout_status": "unknown",
            })
        return RewardEnvelope(
            candidate=candidate, fast_upper=fast_upper,
            components=components, certificate=certificate)

    rollout_resources = []
    for seat in range(4):
        resource = _seat_resource(
            context, seat,
            hero_hand=(root_hand if seat == context.hero_seat else None),
            hero_chain=(post_chain if seat == context.hero_seat else None),
            hero_piao=(post_piao if seat == context.hero_seat else None),
            future_draws=max(0, int(context.live_wall)))
        if resource is None:
            if allow_legacy_fallback:
                return _legacy_envelope(context.base, candidate,
                                        (f"chain_counts[{seat}]",))
            return _unknown_envelope(
                candidate, (f"chain_counts[{seat}]",),
                reason="rollout_chain_state_unknown")
        rollout_resources.append(resource)

    winner_bounds = []
    lower = 0.0
    upper = 0.0
    for winner, resource in enumerate(rollout_resources):
        multiplier = _multiplier_from_resource(resource)
        positive = float(context.base *
                         (24 if winner == context.dealer else 10) *
                         multiplier)
        negative_factor = (8 if winner == context.dealer or
                           context.hero_seat == context.dealer else 1)
        negative = (0.0 if winner == context.hero_seat else
                    -float(context.base * negative_factor * multiplier))
        lower = min(lower, negative)
        upper = max(upper, positive)
        winner_bounds.append({
            "winner": winner, "multiplier_max": multiplier,
            "positive_upper": positive, "negative_lower": negative,
            "settlement_positive_factor": (24 if winner == context.dealer
                                             else 10),
            "settlement_negative_factor": negative_factor,
        })

    components = {
        "candidate": candidate,
        "root": {"post_chain": int(post_chain),
                 "post_chain_piao": int(post_piao),
                 "is_piao": bool(is_piao), "locked": context.locked},
        "chain_max": hero["chain_max"],
        "fast_draws": fast_draws,
        "kong_max": hero["kong_max"],
        "kong_slots_max": hero["kong_slots_max"],
        "existing_pong_count": hero["existing_pong_count"],
        "piao_max": hero["piao_max"],
        "seven_pairs_groups_max": hero["seven_pairs_groups_max"],
        "four_white_possible": hero["four_white_possible"],
        "baotou_possible": hero["baotou_possible"],
        "multiplier_max": hero_multiplier,
        "fast_multiplier_max": hero_multiplier,
        "fast_upper": fast_upper,
        "settlement_factor": positive_factor,
        "settlement_positive_factor": positive_factor,
        "rollout_winner_bounds": winner_bounds,
        "rollout_lower": lower,
        "rollout_upper": upper,
        "rollout_abs": max(abs(lower), abs(upper)),
        "rollout_resources": [
            {key: value for key, value in resource.items()
             if key != "hand"}
            for resource in rollout_resources
        ],
    }
    certificate = RewardCertificate(
        mode=("legacy_conservative_fallback" if
              bound_mode == "legacy_conservative_fallback" else "derived"),
        proof="public_root_resource_envelope",
        details={
            "fast_model": FAST_REWARD_MODEL,
            "rollout_model": ROLLOUT_REWARD_MODEL,
            "formula": "settlement_factor*multiplier_max",
        })
    return RewardEnvelope(
        candidate=candidate, fast_upper=fast_upper,
        rollout_lower=lower, rollout_upper=upper,
        rollout_abs=max(abs(lower), abs(upper)),
        components=components, certificate=certificate)


# Names used by offline callers and older experimental branches.  Keeping one
# implementation avoids subtly divergent proof formulas.
compute_reward_envelope = reward_envelope
build_reward_envelope = reward_envelope
reward_bound = reward_envelope


@dataclass(frozen=True)
class ScoreBreakdown:
    legal: bool
    reward: Optional[float]
    multiplier: Optional[int]
    parts: tuple = ()
    reason: str = ""
    settlement: Optional[tuple] = None

    def as_json(self):
        return {
            "legal": self.legal, "reward": self.reward,
            "multiplier": self.multiplier, "parts": list(self.parts),
            "reason": self.reason,
            "settlement": list(self.settlement) if self.settlement is not None else None,
        }


class ScoreValue:
    """Single source of truth for HU legality and hero round reward.

    This adapter deliberately calls the existing win/multiplier/settlement
    functions.  It never adds to an imported room score: ``settle`` returns a
    fresh per-round delta vector and the hero component is the reward.
    """

    def __init__(self, dealer=0, base=1, you_cai_bi_kao=False, hero=0):
        self.dealer = int(dealer)
        self.base = int(base)
        self.you_cai_bi_kao = bool(you_cai_bi_kao)
        self.hero = int(hero)

    @staticmethod
    def standing_before_draw(hand, drawn):
        if drawn is None or not 0 <= int(drawn) < 34:
            return None
        hand = list(int(x) for x in hand)
        if len(hand) != 34 or hand[int(drawn)] <= 0:
            return None
        hand[int(drawn)] -= 1
        return tuple(hand)

    def can_hu(self, hand, standing13=None, locked=0, drawn=None,
               kong_draw=False, chain_count=0, chain_piao=0):
        hand = tuple(int(x) for x in hand)
        locked = int(locked)
        if len(hand) != 34 or not 0 <= locked <= 4:
            return False
        if drawn is None:
            return False
        drawn = int(drawn)
        if not 0 <= drawn < 34 or not is_win(hand, locked):
            return False
        standing = (self.standing_before_draw(hand, drawn)
                    if standing13 is None else tuple(int(x) for x in standing13))
        if (standing is None or len(standing) != 34 or
                sum(standing) != 13 - 3 * locked or
                any(standing[t] < 0 for t in range(34)) or
                any(standing[t] + (1 if t == drawn else 0) != hand[t]
                    for t in range(34))):
            return False
        if self.you_cai_bi_kao and hand[W] > 0:
            if not kong_draw and not is_baotou(standing, int(locked)):
                return False
        return True

    def hu(self, hand, standing13=None, locked=0, drawn=None,
           kong_draw=False, chain_count=0, chain_piao=0) -> ScoreBreakdown:
        standing = (self.standing_before_draw(hand, drawn)
                    if standing13 is None else tuple(int(x) for x in standing13))
        if not self.can_hu(hand, standing, locked, drawn, kong_draw,
                           chain_count, chain_piao):
            reason = "not_drawn_or_not_win"
            if (self.you_cai_bi_kao and drawn is not None and
                    len(tuple(hand)) == 34 and tuple(hand)[W] > 0 and
                    not kong_draw):
                reason = "you_cai_bi_kao_gate"
            return ScoreBreakdown(False, None, None, reason=reason)
        mult, parts = hand_multiplier(
            tuple(hand), standing, int(locked), int(chain_count),
            int(chain_piao))
        payment = tuple(settle(self.hero, self.dealer, mult, self.base))
        if sum(payment) != 0:
            raise ValueError("settlement vector is not zero-sum")
        return ScoreBreakdown(
            True, float(payment[self.hero]), int(mult), tuple(parts),
            reason="legal_hu", settlement=payment)

    def hu_from_game(self, game, seat=None):
        seat = self.hero if seat is None else int(seat)
        adapter = self if seat == self.hero else ScoreValue(
            dealer=game.dealer, base=game.base,
            you_cai_bi_kao=game.you_cai_bi_kao, hero=seat)
        drawn = game.drawn[seat]
        standing = adapter.standing_before_draw(game.hands[seat], drawn)
        return adapter.hu(game.hands[seat], standing,
                          len(game.melds[seat]), drawn,
                          bool(getattr(game, "_kong_draw", False)),
                          int(game.chain[seat]), int(game.chain_piao[seat]))

    def discard(self, hand, tile, locked=0, chain_count=0, chain_piao=0):
        """Apply a root discard and return value-only transition fields."""
        hand = list(int(x) for x in hand)
        tile = int(tile)
        if len(hand) != 34 or not 0 <= tile < 34 or hand[tile] <= 0:
            raise ValueError("illegal discard")
        piao = False
        if tile == W:
            after = list(hand)
            after[W] -= 1
            piao = is_baotou(after, int(locked))
        hand[tile] -= 1
        if piao:
            chain_count += 1
            chain_piao += 1
        else:
            chain_count = 0
            chain_piao = 0
        return tuple(hand), int(chain_count), int(chain_piao), bool(piao)


def score_value_for_game(game, seat):
    """Convenience function returning a serialisable HU score breakdown."""
    return ScoreValue(
        dealer=game.dealer, base=game.base,
        you_cai_bi_kao=game.you_cai_bi_kao, hero=seat).hu_from_game(game, seat)


# Explicit aliases keep calibration and fixture code on this adapter rather
# than introducing a second scoring path.
evaluate_hu = ScoreValue.hu
settle_hu = ScoreValue.hu
