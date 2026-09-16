"""Common-random-number paired rollout evaluation and bounded racing.

The teacher is deliberately offline-only.  It receives public context and
candidate actions, samples one shared world per ``sample_id`` and exposes
only world fingerprints and terminal outcomes.  Candidate-specific reward
envelopes are used for both marginal diagnostics and paired-delta proof
intervals; an unknown envelope disables racing rather than manufacturing a
wide or zero value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import math
import statistics

from ..decision.context import ContextError
from ..decision.profile import canonical_json, fingerprint
from ..decision.score_value import (
    REWARD_ENVELOPE_VERSION, RewardCertificate, RewardEnvelope, reward_envelope,
)
from .belief import BeliefSampler
from .simulator import FixedContinuation, RolloutOutcome, run_rollout


PAIRWISE_RACING_VERSION = "paired-racing-v3"
TEACHER_RESUME_SCHEMA = "rollout-teacher-resume-v2"


def _mean(values):
    return statistics.fmean(values) if values else None


def _bounded_interval(values, bound, alpha, comparisons=1):
    """Return a Hoeffding interval without clipping observed rewards.

    ``bound=None`` is a first-class unknown state.  It retains the empirical
    mean for diagnostics but deliberately has no interval endpoints, so it
    can never authorize pruning or racing.
    """
    n = len(values)
    result = {"n": n, "mean": None, "low": None, "high": None,
              "alpha": float(alpha), "comparisons": int(comparisons),
              "bound": bound,
              "method": ("unknown-bound" if bound is None
                          else "hoeffding-bounded-simultaneous")}
    if not values:
        return result
    mean = statistics.fmean(values)
    result["mean"] = mean
    try:
        bound = float(bound)
    except (TypeError, ValueError):
        result["bound"] = None
        return result
    if not math.isfinite(bound) or bound < 0:
        result["method"] = "unknown-bound"
        result["bound"] = None
        return result
    alpha = float(alpha)
    comparisons = max(1, int(comparisons))
    log_term = math.log(max(2.0, 2.0 * comparisons / alpha))
    half = 2.0 * bound * math.sqrt(log_term / (2.0 * n))
    result.update({"low": mean - half, "high": mean + half,
                   "half_width": half, "bound": bound,
                   "comparisons": comparisons,
                   "method": "hoeffding-bounded-simultaneous"})
    return result


def _paired_interval(values, reward_bound=None, alpha=0.05, comparisons=1,
                     *, left_bound=None, right_bound=None,
                     bound_version=REWARD_ENVELOPE_VERSION,
                     certificate=None):
    """Build a shared-world delta interval using ``B_left + B_right``.

    The positional ``reward_bound`` form remains compatible with the first
    teacher implementation and means a symmetric bound for both sides.  New
    callers should provide the two candidate bounds explicitly.
    """
    if left_bound is None and right_bound is None:
        left_bound = right_bound = reward_bound
    pair_bound = None
    if left_bound is not None and right_bound is not None:
        try:
            left_bound = float(left_bound)
            right_bound = float(right_bound)
            if (math.isfinite(left_bound) and left_bound >= 0 and
                    math.isfinite(right_bound) and right_bound >= 0):
                pair_bound = left_bound + right_bound
        except (TypeError, ValueError):
            pair_bound = None
    interval = _bounded_interval(values, pair_bound, alpha, comparisons)
    interval["method"] = "hoeffding-paired-bounded-simultaneous"
    interval["left_bound"] = left_bound
    interval["right_bound"] = right_bound
    interval["pair_bound"] = pair_bound
    # ``difference_bound`` is the historical spelling retained in artifacts.
    interval["difference_bound"] = pair_bound
    interval["bound_version"] = bound_version
    if certificate is not None:
        interval["certificate"] = certificate
    return interval


def pair_interval(values, left_bound, right_bound, alpha=0.05, *,
                  comparisons=1, bound_version=REWARD_ENVELOPE_VERSION,
                  certificate=None):
    """Public helper for rechecking a candidate pair certificate."""
    return _paired_interval(
        values, alpha=alpha, comparisons=comparisons,
        left_bound=left_bound, right_bound=right_bound,
        bound_version=bound_version, certificate=certificate)


def _paired_values(rows, left, right):
    """Return valid CRN deltas and sample ids for two actions."""
    values = []
    sample_ids = []
    for row in rows:
        if row.get("group_failed"):
            continue
        outcomes = row.get("outcomes") or {}
        a = outcomes.get(str(left), outcomes.get(left))
        b = outcomes.get(str(right), outcomes.get(right))
        if (not a or not b or a.get("status") != "ok" or
                b.get("status") != "ok" or a.get("reward") is None or
                b.get("reward") is None):
            continue
        try:
            left_reward = float(a["reward"])
            right_reward = float(b["reward"])
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(left_reward) and math.isfinite(right_reward)):
            continue
        values.append(left_reward - right_reward)
        sample_ids.append(int(row["sample_id"]))
    return values, sample_ids


@dataclass(frozen=True)
class TeacherResult:
    status: str
    context_hash: str
    profile_fingerprint: str
    belief_version: str
    continuation_version: str
    actions: tuple
    candidates: tuple
    best_action: int | None
    runner_up: int | None
    paired_deltas: tuple
    sample_count: int
    attempted_samples: int
    failed_samples: int
    ambiguous: bool
    unsupported: bool
    stop_reason: str
    failures: tuple = ()
    rows: tuple = ()
    paired_rows: tuple = ()
    resume_state: dict | None = None
    fingerprint: str = ""
    alpha: float = 0.05
    max_looks: int = 1
    reward_bound: float | None = None
    pairwise_deltas: tuple = ()
    elimination_history: tuple = ()
    pairwise_racing_version: str = PAIRWISE_RACING_VERSION
    reward_bounds: Mapping | dict = field(default_factory=dict)
    bound_version: str = REWARD_ENVELOPE_VERSION
    bound_mode: str = "unknown"
    candidate_envelopes: tuple = ()
    resume_schema: str = TEACHER_RESUME_SCHEMA

    def as_json(self):
        return {
            "status": self.status, "context_hash": self.context_hash,
            "profile_fingerprint": self.profile_fingerprint,
            "belief_version": self.belief_version,
            "continuation_version": self.continuation_version,
            "actions": list(self.actions), "candidates": list(self.candidates),
            "best_action": self.best_action, "runner_up": self.runner_up,
            "paired_deltas": list(self.paired_deltas),
            "sample_count": self.sample_count,
            "attempted_samples": self.attempted_samples,
            "failed_samples": self.failed_samples,
            "ambiguous": self.ambiguous, "unsupported": self.unsupported,
            "stop_reason": self.stop_reason, "failures": list(self.failures),
            "rows": list(self.rows), "resume_state": self.resume_state,
            "paired_rows": list(self.paired_rows),
            "fingerprint": self.fingerprint, "alpha": self.alpha,
            "max_looks": self.max_looks, "reward_bound": self.reward_bound,
            "reward_bounds": {str(k): v for k, v in self.reward_bounds.items()},
            "bound_certificates": {
                str(item.get("action")): (
                    ((item.get("envelope") or {}).get("certificate") or {})
                    .get("fingerprint")
                )
                for item in self.candidate_envelopes
                if isinstance(item, Mapping) and item.get("action") is not None
            },
            "bound_version": self.bound_version,
            "bound_mode": self.bound_mode,
            "candidate_envelopes": list(self.candidate_envelopes),
            "pairwise_deltas": list(self.pairwise_deltas),
            "elimination_history": list(self.elimination_history),
            "pairwise_racing_version": self.pairwise_racing_version,
            "resume_schema": self.resume_schema,
        }


class PairedTeacher:
    """Evaluate root actions on shared sampled worlds with paired racing."""

    def __init__(self, context, *, profile_fingerprint="", seed=0,
                 belief_version="uniform_unseen-v1", continuation=None,
                 n0=32, batch=32, nmax=512, alpha=0.05,
                 reward_bound=None, reward_bounds=None,
                 candidate_bounds=None, envelopes=None,
                 bound_version=REWARD_ENVELOPE_VERSION,
                 bound_mode="derived", allow_legacy_bound_fallback=False,
                 profile=None, max_steps=4096):
        if profile is not None:
            if not profile_fingerprint:
                profile_fingerprint = profile.fingerprint
            bound_version = getattr(profile, "bound_version", bound_version)
            bound_mode = getattr(profile, "bound_mode", bound_mode)
            allow_legacy_bound_fallback = bool(getattr(
                profile, "allow_legacy_bound_fallback",
                allow_legacy_bound_fallback))
            if reward_bound is None:
                reward_bound = getattr(profile, "effective_bound_override", None)
        self.context = context
        self.profile_fingerprint = profile_fingerprint
        self.seed = int(seed)
        self.belief_version = str(belief_version)
        self.continuation = continuation or FixedContinuation("shape-v1")
        self.n0 = max(1, int(n0))
        self.batch = max(1, int(batch))
        self.nmax = max(self.n0, int(nmax))
        self.alpha = float(alpha)
        if not 0 < self.alpha < 1:
            raise ValueError("alpha must be between zero and one")
        self.max_steps = max(1, int(max_steps))
        self.bound_version = str(bound_version)
        self.bound_mode = str(bound_mode)
        self.allow_legacy_bound_fallback = bool(allow_legacy_bound_fallback)
        provided = reward_bounds if reward_bounds is not None else candidate_bounds
        if provided is None and envelopes is not None:
            provided = envelopes
        if provided is None and isinstance(reward_bound, Mapping):
            provided = reward_bound
            reward_bound = None
        if provided is None:
            self._provided_bounds = {}
        elif isinstance(provided, Mapping):
            self._provided_bounds = dict(provided)
        else:
            normalized = {}
            try:
                for item in provided:
                    if isinstance(item, RewardEnvelope):
                        action = item.candidate
                        value = item
                    elif isinstance(item, Mapping) and item.get("action") is not None:
                        action = item.get("action")
                        value = item.get("envelope", item)
                    else:
                        raise ValueError("invalid candidate envelope sequence")
                    if action is None:
                        raise ValueError("candidate envelope sequence lacks action")
                    normalized[int(action)] = value
            except TypeError as exc:
                raise ValueError("candidate bounds must be a mapping or sequence") from exc
            self._provided_bounds = normalized
        self._explicit_reward_bound = reward_bound
        self._envelopes = {}
        self.reward_bounds = {}
        self.reward_bound = None
        self.total_pairs = 0
        self.max_looks = max(
            1, 1 + max(0, self.nmax - self.n0) // self.batch)
        self.look_alpha = self.alpha / self.max_looks
        self.pair_alpha = self.alpha

    @staticmethod
    def _lookup(mapping, action):
        if action in mapping:
            return mapping[action]
        return mapping.get(str(action))

    def _unknown_envelope(self, action, missing, reason):
        return RewardEnvelope(
            version=self.bound_version, candidate=action,
            certificate=RewardCertificate(
                mode="unknown", proof=reason,
                missing=tuple(missing), details={"reason": reason}))

    def _override_envelope(self, action, value, *, proof="explicit_scalar_bound_override"):
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("scalar reward bound must be numeric") from exc
        if not math.isfinite(value) or value <= 0:
            raise ValueError("scalar reward bound must be finite and positive")
        return RewardEnvelope(
            version=self.bound_version, candidate=action,
            fast_upper=value, rollout_lower=-value, rollout_upper=value,
            rollout_abs=value, components={"scalar_bound": value},
            certificate=RewardCertificate(
                mode="override", proof=proof,
                details={"scalar_bound": value}))

    def _check_supplied_envelope(self, action, envelope):
        if envelope.candidate is not None and int(envelope.candidate) != int(action):
            raise ValueError(
                f"candidate envelope action mismatch: {envelope.candidate} != {action}")
        if envelope.version != self.bound_version:
            raise ValueError(
                "candidate envelope version does not match teacher bound version")
        if envelope.candidate is None:
            envelope = replace(envelope, candidate=int(action))
        return envelope

    def _prepare_bounds(self, actions):
        self.total_pairs = len(actions) * (len(actions) - 1) // 2
        self.pair_alpha = self.alpha / (
            self.max_looks * max(1, self.total_pairs))
        envelopes = {}
        for action in actions:
            supplied = self._lookup(self._provided_bounds, action)
            supplied_present = (action in self._provided_bounds or
                                str(action) in self._provided_bounds)
            if supplied_present:
                if supplied is None:
                    envelope = self._unknown_envelope(
                        action, ("rollout_abs",),
                        "supplied_reward_bound_unknown")
                    envelopes[int(action)] = envelope
                    continue
                if isinstance(supplied, RewardEnvelope):
                    envelope = self._check_supplied_envelope(action, supplied)
                elif isinstance(supplied, Mapping):
                    try:
                        supplied_version = supplied.get("version")
                        if supplied_version is None:
                            envelope = self._unknown_envelope(
                                action, ("bound_version",),
                                "supplied_envelope_version_missing")
                            envelopes[int(action)] = envelope
                            continue
                        certificate = supplied.get("certificate", {
                            "mode": supplied.get("mode", "unknown"),
                            "proof": "supplied_envelope",
                            "fingerprint": supplied.get(
                                "certificate_fingerprint", ""),
                        })
                        if isinstance(certificate, str):
                            certificate = {
                                "mode": supplied.get("mode", "unknown"),
                                "proof": "supplied_envelope",
                                "fingerprint": certificate,
                            }
                        envelope = RewardEnvelope(
                            version=str(supplied_version),
                            candidate=int(supplied.get("candidate", action)),
                            fast_upper=supplied.get("fast_upper"),
                            rollout_lower=supplied.get("rollout_lower"),
                            rollout_upper=supplied.get("rollout_upper"),
                            rollout_abs=supplied.get("rollout_abs"),
                            components=dict(supplied.get("components", {})),
                            certificate=certificate,
                        )
                    except (TypeError, ValueError):
                        envelope = self._unknown_envelope(
                            action, ("supplied_envelope",),
                            "invalid_supplied_envelope")
                    else:
                        envelope = self._check_supplied_envelope(action, envelope)
                else:
                    try:
                        envelope = self._override_envelope(action, supplied)
                    except ValueError:
                        envelope = self._unknown_envelope(
                            action, ("scalar_override",),
                            "invalid_scalar_override")
            elif self._explicit_reward_bound is not None:
                try:
                    envelope = self._override_envelope(action,
                                                       self._explicit_reward_bound)
                except ValueError:
                    envelope = self._unknown_envelope(
                        action, ("scalar_override",),
                        "invalid_scalar_override")
            elif self.bound_version != REWARD_ENVELOPE_VERSION:
                envelope = reward_envelope(
                    self.context, action,
                    allow_legacy_fallback=self.allow_legacy_bound_fallback,
                    bound_mode=("legacy_conservative_fallback"
                                if self.allow_legacy_bound_fallback
                                else "unknown"))
                envelope = replace(envelope, version=self.bound_version,
                                   candidate=action)
            elif self.bound_mode == "unknown":
                envelope = self._unknown_envelope(
                    action, ("bound_mode",), "bound_mode_unknown")
            else:
                envelope = reward_envelope(
                    self.context, action,
                    allow_legacy_fallback=(
                        self.allow_legacy_bound_fallback or
                        self.bound_mode == "legacy_conservative_fallback"),
                    bound_mode=self.bound_mode)
                if envelope.version != self.bound_version:
                    envelope = replace(envelope, version=self.bound_version,
                                       candidate=action)
            envelopes[int(action)] = envelope
        self._envelopes = envelopes
        self.reward_bounds = {
            action: (envelope.rollout_abs
                     if envelope.mode != "unknown" else None)
            for action, envelope in envelopes.items()
        }
        known = [value for value in self.reward_bounds.values()
                 if value is not None]
        self.reward_bound = max(known) if known else None

    def _aggregate_bound_mode(self):
        modes = {envelope.mode for envelope in self._envelopes.values()}
        if len(modes) == 1:
            return next(iter(modes))
        if not modes:
            return "unknown"
        return "mixed"

    def _config_fingerprint(self, actions):
        return fingerprint({
            "schema": TEACHER_RESUME_SCHEMA,
            "context_hash": self.context.context_hash,
            "actions": list(actions),
            "profile_fingerprint": self.profile_fingerprint,
            "belief_version": self.belief_version,
            "continuation": self.continuation.as_json(),
            "seed": self.seed, "n0": self.n0, "batch": self.batch,
            "nmax": self.nmax, "alpha": self.alpha,
            "max_looks": self.max_looks,
            "reward_bounds": {str(action): self.reward_bounds.get(action)
                               for action in actions},
            "envelope_certificates": {
                str(action): self._envelopes[action].certificate.fingerprint
                for action in actions
            },
            "bound_version": self.bound_version,
            "bound_mode": self._aggregate_bound_mode(),
            "pairwise_racing_version": PAIRWISE_RACING_VERSION,
        })

    def _unsupported_result(self, config_fp, actions, error):
        message = f"{type(error).__name__}:{error}"
        state = {
            "schema": TEACHER_RESUME_SCHEMA, "rows": [],
            "next_sample_id": 0, "config_fingerprint": config_fp,
            "actions": list(actions), "terminal": True,
            "stop_reason": "unsupported_context", "ambiguous": False,
            "active_actions": list(actions), "elimination_history": [],
            "pairwise_racing_version": PAIRWISE_RACING_VERSION,
            "bound_version": self.bound_version,
            "bound_mode": self._aggregate_bound_mode(),
            "reward_bounds": {str(k): v for k, v in self.reward_bounds.items()},
            "bound_certificates": {
                str(k): self._envelopes[k].certificate.fingerprint
                for k in actions
            },
            "candidate_envelopes": {
                str(k): self._envelopes[k].as_json() for k in actions
            },
            "pairwise_certificates": [], "max_looks": self.max_looks,
            "alpha": self.alpha,
        }
        return TeacherResult(
            status="unsupported", context_hash=self.context.context_hash,
            profile_fingerprint=self.profile_fingerprint,
            belief_version=self.belief_version,
            continuation_version=self.continuation.version,
            actions=tuple(actions), candidates=(), best_action=None,
            runner_up=None, paired_deltas=(), sample_count=0,
            attempted_samples=0, failed_samples=0, ambiguous=False,
            unsupported=True, stop_reason="unsupported_context",
            failures=({"error": message},), rows=(), paired_rows=(),
            resume_state=state, fingerprint=config_fp, alpha=self.alpha,
            max_looks=self.max_looks, reward_bound=self.reward_bound,
            reward_bounds=self.reward_bounds,
            bound_version=self.bound_version,
            bound_mode=self._aggregate_bound_mode(),
            candidate_envelopes=tuple({
                "action": action, "envelope": self._envelopes[action].as_json()
            } for action in actions))

    def _candidate_summary(self, action, rewards, outcomes, comparisons):
        bound = self.reward_bounds.get(action)
        interval = _bounded_interval(
            rewards, bound, self.pair_alpha, comparisons=1)
        valid_outcomes = [o for o in outcomes
                          if o.get("status") == "ok" and
                          not o.get("group_failed", False)]
        wins = [o for o in valid_outcomes
                if o.get("winner") == self.context.hero_seat]
        mults = [o.get("multiplier") for o in valid_outcomes
                 if o.get("winner") == self.context.hero_seat and
                 o.get("multiplier") is not None]
        draws = sum(bool(o.get("draw")) for o in valid_outcomes)
        return {
            "action": action, "EV": interval["mean"], "CI": interval,
            "samples": len(rewards), "attempted_samples": len(outcomes),
            "valid_samples": len(rewards),
            "bound": bound,
            "reward_envelope": (self._envelopes[action].as_json()
                                 if action in self._envelopes else None),
            "active": None,
            "statistical_role": "marginal_diagnostic_only",
            "racing_stop_authority": False,
            "win_rate": len(wins) / len(valid_outcomes)
            if valid_outcomes else None,
            "avg_win_multiplier": _mean(mults) if mults else None,
            "draw_rate": draws / len(valid_outcomes) if valid_outcomes else None,
            "failed": sum(o.get("status") != "ok" or
                           o.get("group_failed", False) for o in outcomes),
        }

    def _pairwise_summary(self, rows, left, right, *, look=None):
        values, sample_ids = _paired_values(rows, left, right)
        left_bound = self.reward_bounds.get(left)
        right_bound = self.reward_bounds.get(right)
        envelope_certificate = {
            "left": (self._envelopes[left].certificate.fingerprint
                     if left in self._envelopes else None),
            "right": (self._envelopes[right].certificate.fingerprint
                      if right in self._envelopes else None),
        }
        interval = _paired_interval(
            values, alpha=self.pair_alpha, comparisons=1,
            left_bound=left_bound, right_bound=right_bound,
            bound_version=self.bound_version,
            certificate=envelope_certificate)
        if len(values) > 1:
            interval["variance"] = statistics.pvariance(values)
            interval["std_error"] = math.sqrt(
                interval["variance"] / len(values))
        elif values:
            interval["variance"] = 0.0
            interval["std_error"] = None
        interval["paired_sample_ids"] = sample_ids
        interval["alpha_total"] = self.alpha
        interval["pair_count"] = self.total_pairs
        interval["max_looks"] = self.max_looks
        interval["look"] = look
        interval["effective_n"] = len(values)
        interval["statistical_role"] = "paired_racing"
        interval["racing_stop_authority"] = True
        return {"left": left, "right": right, "delta": interval}

    def _active_pairwise(self, rows, actions, *, look=None):
        actions = tuple(sorted(int(action) for action in actions))
        return {
            (left, right): self._pairwise_summary(
                rows, left, right, look=look)
            for left in actions for right in actions if left != right
        }

    @staticmethod
    def _leader(actions, rewards):
        return max(tuple(actions), key=lambda action: (
            statistics.fmean(rewards[action]) if rewards.get(action) else
            -math.inf,
            -int(action)))

    def _look_for_count(self, count):
        if count < self.n0:
            return 0
        return 1 + max(0, (int(count) - self.n0) // self.batch)

    def _should_race(self, count):
        return (count >= self.n0 and
                (count == self.n0 or (count - self.n0) % self.batch == 0))

    def _validate_resume(self, state, actions, config_fp):
        if not isinstance(state, dict):
            raise ValueError("resume state must be an object")
        if state.get("schema") != TEACHER_RESUME_SCHEMA:
            raise ValueError("resume schema is not supported by paired racing")
        if state.get("pairwise_racing_version") != PAIRWISE_RACING_VERSION:
            raise ValueError("resume pairwise racing version does not match")
        if state.get("bound_version") != self.bound_version:
            raise ValueError("resume bound version does not match")
        if state.get("bound_mode") != self._aggregate_bound_mode():
            raise ValueError("resume bound mode does not match")
        if int(state.get("max_looks", -1)) != self.max_looks:
            raise ValueError("resume max_looks does not match")
        try:
            same_alpha = float(state.get("alpha")) == self.alpha
        except (TypeError, ValueError):
            same_alpha = False
        if not same_alpha:
            raise ValueError("resume alpha does not match")
        if state.get("config_fingerprint") != config_fp:
            raise ValueError("resume config_fingerprint does not match teacher plan")
        saved_actions = tuple(sorted(int(x) for x in state.get("actions", ())))
        if saved_actions != tuple(actions):
            raise ValueError("resume action set does not match teacher plan")
        expected_bounds = {
            str(action): self.reward_bounds.get(action) for action in actions}
        saved_bounds = {
            str(key): value for key, value in
            (state.get("reward_bounds") or {}).items()
        }
        if saved_bounds != expected_bounds:
            raise ValueError("resume candidate reward bounds do not match")
        expected_certificates = {
            str(action): self._envelopes[action].certificate.fingerprint
            for action in actions
        }
        if dict(state.get("bound_certificates") or {}) != expected_certificates:
            raise ValueError("resume bound certificates do not match")
        saved_envelopes = state.get("candidate_envelopes")
        if not isinstance(saved_envelopes, Mapping):
            raise ValueError("resume is missing candidate envelopes")
        for action in actions:
            saved = saved_envelopes.get(str(action), saved_envelopes.get(action))
            if not isinstance(saved, Mapping):
                raise ValueError("resume is missing a candidate envelope")
            if (saved.get("version") != self._envelopes[action].version or
                    saved.get("certificate_fingerprint") !=
                    self._envelopes[action].certificate.fingerprint):
                raise ValueError("resume candidate envelope does not match")
        required = ("rows", "next_sample_id", "active_actions",
                    "elimination_history",
                    "pairwise_certificates", "candidate_envelopes",
                    "terminal", "stop_reason", "ambiguous")
        missing = [name for name in required if name not in state]
        if missing:
            raise ValueError("resume is missing fields: " + ", ".join(missing))

    def _restore_rows(self, state, actions, rewards, outcomes, failures):
        """Restore sparse complete rows once and return ``(rows, ids)``."""
        rows = []
        by_id = {}
        action_set = set(actions)

        def normalize_actions(value, name):
            try:
                result = tuple(sorted(set(int(action) for action in (value or ()))))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"resume row has invalid {name}") from exc
            if not set(result).issubset(action_set):
                raise ValueError("resume row contains an unknown action")
            return result

        def valid_output(value):
            if not isinstance(value, Mapping) or value.get("status") != "ok":
                return False
            try:
                return (value.get("reward") is not None and
                        math.isfinite(float(value.get("reward"))))
            except (TypeError, ValueError):
                return False

        for raw in state.get("rows", ()):
            if not isinstance(raw, dict) or raw.get("sample_id") is None:
                continue
            sid = int(raw["sample_id"])
            row = dict(raw)
            active_raw = row.get("active_actions")
            sampled_raw = row.get("sampled_actions")
            evaluated_raw = row.get("evaluated_actions")
            if active_raw is None and sampled_raw is None and evaluated_raw is None:
                raise ValueError("dense/legacy resume row lacks sampled_actions")
            if active_raw is None:
                active_raw = sampled_raw if sampled_raw is not None else evaluated_raw
            if sampled_raw is None:
                sampled_raw = evaluated_raw
            if evaluated_raw is None:
                evaluated_raw = sampled_raw
            active = normalize_actions(active_raw, "active_actions")
            sampled = normalize_actions(sampled_raw, "sampled_actions")
            evaluated = normalize_actions(evaluated_raw, "evaluated_actions")
            if not set(sampled).issubset(set(active)):
                raise ValueError("resume sampled actions are not active")
            if not set(evaluated).issubset(set(sampled)):
                raise ValueError("resume evaluated actions are not sampled")
            row_outcomes = row.get("outcomes") or {}
            if not isinstance(row_outcomes, Mapping):
                raise ValueError("resume row outcomes must be an object")
            normalized = {}
            for action in evaluated:
                raw_out = row_outcomes.get(str(action), row_outcomes.get(action))
                if raw_out is not None:
                    if not isinstance(raw_out, Mapping):
                        raise ValueError("resume row outcome must be an object")
                    normalized[str(action)] = dict(raw_out)
            row["sampled_actions"] = list(sampled)
            row["active_actions"] = list(active)
            row["evaluated_actions"] = list(evaluated)
            row["outcomes"] = normalized
            row_failed = bool(row.get("group_failed"))
            if set(sampled) != set(active) or set(evaluated) != set(sampled):
                row_failed = True
            if len(normalized) < len(evaluated):
                row_failed = True
            if any(not valid_output(out) for out in normalized.values()):
                row_failed = True
            row["group_failed"] = row_failed
            if sid in by_id:
                if canonical_json(by_id[sid]) != canonical_json(row):
                    raise ValueError(
                        f"conflicting duplicate resume row sample_id={sid}")
                continue
            by_id[sid] = row
        for sid in sorted(by_id):
            row = by_id[sid]
            rows.append(row)
            for action in row["active_actions"]:
                if action not in row["sampled_actions"]:
                    failures.append({"sample_id": sid, "action": action,
                                     "error": "missing_active_outcome"})
            for action in row["sampled_actions"]:
                out = row["outcomes"].get(str(action))
                if out is None:
                    failures.append({"sample_id": sid, "action": action,
                                     "error": "missing_outcome"})
                    continue
                out = dict(out)
                out["group_failed"] = bool(row["group_failed"])
                outcomes[action].append(out)
                if (not row["group_failed"] and out.get("status") == "ok" and
                        out.get("reward") is not None):
                    rewards[action].append(float(out["reward"]))
                else:
                    failures.append({"sample_id": sid, "action": action,
                                     "error": out.get("error") or
                                     "shared_group_failed"})
        return rows, set(by_id)

    def evaluate(self, actions=None, *, resume=None):
        """Run deterministic batches and return a replayable teacher artifact."""
        actions = tuple(sorted(set(
            int(action) for action in
            (self.context.legal_actions if actions is None else actions))))
        if not actions:
            raise ValueError("teacher requires at least one root action")
        if self.context.legal_actions:
            legal = set(int(action) for action in self.context.legal_actions)
            illegal = [action for action in actions if action not in legal]
            if illegal:
                raise ValueError(
                    "teacher action is not legal in context: " +
                    ", ".join(str(action) for action in illegal))
        self._prepare_bounds(actions)
        config_fp = self._config_fingerprint(actions)
        try:
            self.context.validate_for("rollout")
        except ContextError as exc:
            return self._unsupported_result(config_fp, actions, exc)
        for action in actions:
            if self.context.legal_actions and action not in self.context.legal_actions:
                raise ValueError(f"root action {action} is not legal in context")

        rewards = {action: [] for action in actions}
        outcomes = {action: [] for action in actions}
        rows = []
        failures = []
        active_actions = list(actions)
        elimination_history = []
        resume_data = resume
        if isinstance(resume, dict) and isinstance(
                resume.get("resume_state"), dict):
            resume_data = resume["resume_state"]
        if resume_data is not None:
            self._validate_resume(resume_data, actions, config_fp)
            rows, completed_ids = self._restore_rows(
                resume_data, actions, rewards, outcomes, failures)
            restored_active = resume_data.get("active_actions")
            if restored_active is not None:
                try:
                    restored_set = set(int(action) for action in restored_active)
                except (TypeError, ValueError) as exc:
                    raise ValueError("resume active action set is invalid") from exc
                if not restored_set.issubset(set(actions)):
                    raise ValueError("resume active action set contains unknown action")
                active_actions = [action for action in actions
                                  if action in restored_set]
                if not active_actions:
                    raise ValueError("resume active action set is empty")
            elimination_history = [dict(item) for item in
                                   resume_data.get("elimination_history", ())]
        else:
            completed_ids = set()

        sampler = BeliefSampler(self.context, seed=self.seed,
                                belief_version=self.belief_version)
        terminal = bool(resume_data and resume_data.get("terminal"))
        stop_reason = (str(resume_data.get("stop_reason", "nmax"))
                       if terminal else "nmax")
        ambiguous = (bool(resume_data.get("ambiguous", True))
                     if terminal else True)

        def race_at_boundary():
            nonlocal active_actions, stop_reason, ambiguous
            if len(active_actions) <= 1:
                ambiguous = False
                stop_reason = ("only_legal_action" if len(actions) == 1
                               else "paired_elimination")
                return
            look = self._look_for_count(len(rows))
            pairwise = self._active_pairwise(
                rows, active_actions, look=look)
            leader = self._leader(active_actions, rewards)
            eliminated = []
            certificates = []
            # Every decision in this pass compares against the same leader;
            # chained within-batch eliminations could otherwise make the
            # leader depend on iteration order.
            for candidate in active_actions:
                if candidate == leader:
                    continue
                entry = pairwise[(candidate, leader)]
                interval = entry["delta"]
                if interval.get("high") is not None and interval["high"] < 0:
                    eliminated.append(candidate)
                    certificates.append({
                        "candidate": candidate, "leader": leader,
                        "pair": {"left": candidate, "right": leader},
                        "look": look, "sample_count": len(rows),
                        "alpha": interval.get("alpha"),
                        "alpha_total": self.alpha,
                        "left_bound": interval.get("left_bound"),
                        "right_bound": interval.get("right_bound"),
                        "pair_bound": interval.get("pair_bound"),
                        "delta_interval": dict(interval),
                        "certificate": interval.get("certificate"),
                    })
            if not eliminated:
                return
            before = list(active_actions)
            eliminated_set = set(eliminated)
            active_actions = [action for action in active_actions
                              if action not in eliminated_set]
            elimination_history.append({
                "sample_count": len(rows), "look": look,
                "leader": leader, "before": before,
                "eliminated": sorted(eliminated),
                "after": list(active_actions),
                "certificates": certificates,
                # Keep the original aggregate spelling for existing readers.
                "pairwise": [certificate["delta_interval"]
                             for certificate in certificates],
            })
            if len(active_actions) == 1:
                ambiguous = False
                stop_reason = "paired_elimination"

        # A saved non-terminal row may have been interrupted exactly at a
        # batch barrier.  Apply the same barrier decision before drawing a new
        # world, which is what a one-shot run would have done.
        if not terminal and self._should_race(len(rows)):
            race_at_boundary()
            if len(active_actions) == 1:
                terminal = True

        while not terminal and len(completed_ids) < self.nmax:
            sid = 0
            while sid in completed_ids:
                sid += 1
            world = sampler.sample(sid)
            sampled_actions = tuple(active_actions)
            row_outcomes = {}
            row_failed = False
            for action in sampled_actions:
                try:
                    outcome = run_rollout(
                        self.context, world, action, self.continuation,
                        max_steps=self.max_steps)
                    data = outcome.as_json()
                    reward = data.get("reward")
                    valid_reward = (reward is not None and
                                    math.isfinite(float(reward)))
                    if not getattr(outcome, "valid", False) or not valid_reward:
                        if getattr(outcome, "valid", False) and not valid_reward:
                            outcome = RolloutOutcome(
                                "failed", None, world.sample_id,
                                world.fingerprint,
                                error="non_finite_reward")
                            data = outcome.as_json()
                except Exception as exc:
                    # A worker/continuation exception is a failed outcome for
                    # this shared group, never a reason to synthesize zero.
                    outcome = RolloutOutcome(
                        "failed", None, world.sample_id, world.fingerprint,
                        error=f"{type(exc).__name__}:{exc}")
                    data = outcome.as_json()
                data["group_failed"] = False
                row_outcomes[str(action)] = data
                if not getattr(outcome, "valid", False):
                    row_failed = True
            if row_failed:
                for action in sampled_actions:
                    out = row_outcomes[str(action)]
                    out["group_failed"] = True
                    outcomes[action].append(out)
                    failures.append({"sample_id": sid, "action": action,
                                     "error": out.get("error") or
                                     "shared_group_failed"})
            else:
                for action in sampled_actions:
                    out = row_outcomes[str(action)]
                    outcomes[action].append(out)
                    rewards[action].append(float(out["reward"]))
            rows.append({
                "sample_id": sid, "world_fingerprint": world.fingerprint,
                "outcomes": row_outcomes, "group_failed": row_failed,
                "sampled_actions": list(sampled_actions),
                "active_actions": list(sampled_actions),
                "evaluated_actions": list(sampled_actions),
            })
            completed_ids.add(sid)
            if self._should_race(len(rows)):
                race_at_boundary()
                if len(active_actions) == 1:
                    terminal = True
        if not terminal:
            terminal = True
            if len(active_actions) == 1:
                ambiguous = False
                stop_reason = ("only_legal_action" if len(actions) == 1
                               else "paired_elimination")
            else:
                ambiguous = True
                stop_reason = "nmax_ambiguous"

        if not rows:
            # A terminal unsupported result returned above; a valid teacher
            # must have at least one shared row before it can classify a run.
            raise ValueError("teacher did not execute a sample")

        summaries = [self._candidate_summary(
            action, rewards[action], outcomes[action], 1)
                     for action in actions]
        summary_by_action = {item["action"]: item for item in summaries}
        for item in summaries:
            item["active"] = item["action"] in active_actions
        summaries.sort(key=lambda item: (
            item["EV"] if item["EV"] is not None else -math.inf,
            -int(item["action"])), reverse=True)
        active_for_choice = [action for action in active_actions
                             if action in summary_by_action]
        best_action = (self._leader(active_for_choice, rewards)
                       if active_for_choice else summaries[0]["action"])
        runner_candidates = [action for action in active_for_choice
                             if action != best_action]
        if runner_candidates:
            runner_action = self._leader(runner_candidates, rewards)
        else:
            runner_action = next((item["action"] for item in summaries
                                  if item["action"] != best_action), None)

        deltas = []
        paired_rows = []
        if runner_action is not None:
            by_id = {int(row["sample_id"]): row for row in rows}
            for sid in sorted(by_id):
                row = by_id[sid]
                a = row["outcomes"].get(str(best_action))
                b = row["outcomes"].get(str(runner_action))
                if (a and b and not row.get("group_failed") and
                        a.get("status") == b.get("status") == "ok" and
                        a.get("reward") is not None and
                        b.get("reward") is not None):
                    delta = float(a["reward"]) - float(b["reward"])
                    deltas.append(delta)
                    paired_rows.append({
                        "sample_id": sid,
                        "world_fingerprint": row.get("world_fingerprint"),
                        "delta": delta,
                    })
        delta_summary = _paired_interval(
            deltas, alpha=self.pair_alpha, comparisons=1,
            left_bound=self.reward_bounds.get(best_action),
            right_bound=self.reward_bounds.get(runner_action)
            if runner_action is not None else None,
            bound_version=self.bound_version,
            certificate={
                "left": self._envelopes[best_action].certificate.fingerprint,
                "right": (self._envelopes[runner_action].certificate.fingerprint
                          if runner_action is not None else None),
            })
        if deltas:
            delta_summary["variance"] = (statistics.pvariance(deltas)
                                          if len(deltas) > 1 else 0.0)
            delta_summary["std_error"] = (
                math.sqrt(statistics.pvariance(deltas) / len(deltas))
                if len(deltas) > 1 else None)
            delta_summary["paired_sample_ids"] = [
                row["sample_id"] for row in paired_rows]
        paired = [{"best": best_action, "runner_up": runner_action,
                   "delta": delta_summary}]
        final_look = self._look_for_count(len(rows))
        pairwise = self._active_pairwise(rows, actions, look=final_look)
        pairwise_deltas = tuple(pairwise.values())
        status = "ok" if not ambiguous and not failures else (
            "ambiguous" if ambiguous else "ok_with_failures")
        next_sample_id = 0
        while next_sample_id in completed_ids:
            next_sample_id += 1
        state = {
            "schema": TEACHER_RESUME_SCHEMA, "rows": rows,
            "next_sample_id": next_sample_id,
            "config_fingerprint": config_fp, "actions": list(actions),
            "terminal": terminal, "stop_reason": stop_reason,
            "ambiguous": ambiguous, "active_actions": list(active_actions),
            "elimination_history": elimination_history,
            "pairwise_certificates": list(pairwise_deltas),
            "reward_bounds": {str(k): v for k, v in self.reward_bounds.items()},
            "candidate_envelopes": {
                str(action): self._envelopes[action].as_json()
                for action in actions
            },
            "bound_version": self.bound_version,
            "bound_mode": self._aggregate_bound_mode(),
            "max_looks": self.max_looks, "alpha": self.alpha,
            "pairwise_racing_version": PAIRWISE_RACING_VERSION,
            "bound_certificates": {
                str(action): self._envelopes[action].certificate.fingerprint
                for action in actions
            },
        }
        return TeacherResult(
            status=status, context_hash=self.context.context_hash,
            profile_fingerprint=self.profile_fingerprint,
            belief_version=self.belief_version,
            continuation_version=self.continuation.version,
            actions=actions, candidates=tuple(summaries),
            best_action=best_action, runner_up=runner_action,
            paired_deltas=tuple(paired),
            sample_count=sum(not bool(row.get("group_failed")) for row in rows),
            attempted_samples=len(rows),
            failed_samples=sum(bool(row.get("group_failed")) for row in rows),
            ambiguous=ambiguous, unsupported=False, stop_reason=stop_reason,
            failures=tuple(failures), rows=tuple(rows),
            paired_rows=tuple(paired_rows), resume_state=state,
            fingerprint=config_fp, alpha=self.alpha,
            max_looks=self.max_looks, reward_bound=self.reward_bound,
            pairwise_deltas=pairwise_deltas,
            elimination_history=tuple(elimination_history),
            reward_bounds=self.reward_bounds,
            bound_version=self.bound_version,
            bound_mode=self._aggregate_bound_mode(),
            candidate_envelopes=tuple({
                "action": action, "envelope": self._envelopes[action].as_json()
            } for action in actions))


def evaluate_paired(context, actions=None, **kwargs):
    return PairedTeacher(context, **kwargs).evaluate(actions)
