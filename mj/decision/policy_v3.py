"""Low-latency policy-v3 runtime with auditable safety fallbacks.

The runtime is intentionally independent from the offline POMCP module.  A
model may suggest an action, but the current Game/Mirror legal set remains
the only authorization boundary.  NumPy/Torch are imported only when a model
checkpoint is explicitly loaded or a supplied model is invoked.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import time
from typing import Any, Mapping

from ..belief import BeliefProfile, BeliefState, InformationHistory
from ..belief.events import history_from_game
from .context import PublicDecisionContext
from .profile import fingerprint


RUNTIME_SCHEMA = "policy-runtime-v3"


@dataclass(frozen=True)
class PolicyV3Profile:
    schema: str = RUNTIME_SCHEMA
    version: str = "policy-v3"
    model_version: str = ""
    feature_contract_fingerprint: str = ""
    belief_profile_fingerprint: str = ""
    search_profile_fingerprint: str = ""
    fallback_chain: tuple[str, ...] = ("shape-v2", "legacy")
    confidence_threshold: float = 0.0
    confidence_definition: str = "probability-margin-v1"
    calibration_policy: str = "value-contract-v1"
    shadow_belief: bool = True
    max_shadow_particles: int = 32
    search_level: str = "policy-only-v1"
    shallow_search: bool = False
    rules_version: str = "hangzhou-platform-guide-v34"

    def __post_init__(self):
        if self.schema != RUNTIME_SCHEMA:
            raise ValueError(f"unsupported policy runtime schema: {self.schema}")
        if not str(self.version):
            raise ValueError("policy runtime version must not be empty")
        if self.confidence_definition != "probability-margin-v1":
            raise ValueError("unsupported confidence definition")
        if self.calibration_policy not in ("value-contract-v1",
                                           "policy-only-v1"):
            raise ValueError(f"unsupported calibration policy: "
                             f"{self.calibration_policy}")
        threshold = float(self.confidence_threshold)
        if not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("confidence_threshold must be in [0, 1]")
        chain = tuple(str(value) for value in (self.fallback_chain or ()))
        if chain != ("shape-v2", "legacy"):
            raise ValueError("fallback_chain must be shape-v2 then legacy")
        if int(self.max_shadow_particles) <= 0:
            raise ValueError("max_shadow_particles must be positive")
        if self.search_level != "policy-only-v1" and not self.shallow_search:
            raise ValueError("non-policy search level requires shallow_search")
        object.__setattr__(self, "confidence_threshold", threshold)
        object.__setattr__(self, "fallback_chain", chain)
        object.__setattr__(self, "max_shadow_particles",
                           int(self.max_shadow_particles))

    def payload(self):
        return asdict(self)

    @property
    def fingerprint(self):
        return fingerprint(self.payload(), 24)

    def as_json(self):
        value = self.payload()
        value["fallback_chain"] = list(self.fallback_chain)
        value["fingerprint"] = self.fingerprint
        return value


def policy_v3_profile_from_json(data: Mapping[str, Any]):
    value = dict(data)
    supplied = value.pop("fingerprint", None)
    allowed = set(PolicyV3Profile.__dataclass_fields__)
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("unknown policy-v3 profile fields: " +
                         ", ".join(sorted(unknown)))
    profile = PolicyV3Profile(**value)
    if supplied is not None and supplied != profile.fingerprint:
        raise ValueError("policy-v3 profile fingerprint mismatch")
    return profile


def _finite(value, name):
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


class ModelOutputError(ValueError):
    """A model output failed a declared safety check (auditable kind)."""

    def __init__(self, message, *, kind):
        super().__init__(message)
        self.kind = str(kind)


def _empty_runtime_stats():
    return {
        "decisions": 0, "network": 0, "only_legal_action": 0,
        "fallbacks": 0, "fallback_by_reason": {}, "emergency": 0,
        "illegal_selected": 0, "model_output_errors": 0,
        "model_output_error_kinds": {},
        "no_search": 0,
    }


class PolicyV3Runtime:
    """Run a policy/value model and fall back without changing legality."""

    def __init__(self, model=None, *, profile=None, belief_profile=None,
                 shadow_belief=None, device="cpu"):
        if profile is None:
            profile = PolicyV3Profile()
        elif isinstance(profile, Mapping):
            profile = policy_v3_profile_from_json(profile)
        if not isinstance(profile, PolicyV3Profile):
            raise TypeError("profile must be a PolicyV3Profile or mapping")
        self.profile = profile
        if belief_profile is None:
            belief_profile = BeliefProfile(
                particle_count=profile.max_shadow_particles, seed=0)
        elif isinstance(belief_profile, Mapping):
            from ..belief.profile import belief_profile_from_json
            belief_profile = belief_profile_from_json(belief_profile)
        if not isinstance(belief_profile, BeliefProfile):
            raise TypeError("belief_profile must be a BeliefProfile or mapping")
        self.belief_profile = belief_profile
        self.shadow_belief = (profile.shadow_belief if shadow_belief is None
                              else bool(shadow_belief))
        self.device = device
        self.model = model
        self.model_error = None
        self.stats = _empty_runtime_stats()
        self._belief = None
        self._history_hash = None
        self._context_hash = None
        self._last_gid = None
        self._observed_history = None
        self._validate_model_contract()

    @property
    def model_fingerprint(self):
        manifest = getattr(self.model, "manifest", None)
        return getattr(manifest, "fingerprint", None)

    def _validate_model_contract(self):
        if self.model is None:
            self.model_error = "model_missing"
            return
        manifest = getattr(self.model, "manifest", None)
        if manifest is None:
            self.model_error = "model_manifest_missing"
            return
        if getattr(manifest, "oracle", True):
            self.model_error = "model_oracle_forbidden"
        elif (not getattr(manifest, "calibrated", False) and
              self.profile.calibration_policy == "value-contract-v1"):
            self.model_error = "model_not_calibrated"
        elif (self.profile.model_version and
              getattr(manifest, "model_version", None) != self.profile.model_version):
            self.model_error = "model_version_mismatch"
        elif (self.profile.feature_contract_fingerprint and
              getattr(manifest, "feature_contract_fingerprint", None) !=
              self.profile.feature_contract_fingerprint):
            self.model_error = "feature_contract_mismatch"
        elif (self.profile.belief_profile_fingerprint and
              getattr(manifest, "belief_profile_fingerprint", None) !=
              self.profile.belief_profile_fingerprint):
            self.model_error = "belief_profile_mismatch"
        elif (self.profile.search_profile_fingerprint and
              getattr(manifest, "search_profile_fingerprint", None) !=
              self.profile.search_profile_fingerprint):
            self.model_error = "search_profile_mismatch"

    def reset(self, gid=None):
        """Forget shadow state at reconnect/round boundaries."""
        self._belief = None
        self._history_hash = None
        self._context_hash = None
        self._last_gid = gid
        self._observed_history = None

    def observe_history(self, history, *, gid=None):
        """Install an externally reconstructed public history for shadow use."""
        if not isinstance(history, InformationHistory):
            history = InformationHistory.from_json(history)
        if gid is not None and self._last_gid is not None and gid != self._last_gid:
            self.reset(gid)
        self._last_gid = gid if gid is not None else self._last_gid
        self._observed_history = history
        self._belief = None
        self._history_hash = None
        self._context_hash = None
        return history

    def observe_event(self, event, *, context=None, gid=None):
        """Append one public event; gaps/snapshots remain explicitly degraded."""
        if gid is not None and self._last_gid is not None and gid != self._last_gid:
            self.reset(gid)
        if gid is not None:
            self._last_gid = gid
        history = self._observed_history or InformationHistory()
        self._observed_history = history.append(event)
        self._belief = None
        self._history_hash = None
        self._context_hash = None
        return self._observed_history

    def observe_snapshot(self, *, reason="full_snapshot_without_history", gid=None):
        """Reset shadow posterior at a snapshot while retaining degradation."""
        self.reset(gid)
        self._observed_history = InformationHistory().mark_incomplete(reason)
        return self._observed_history

    def _history(self, game):
        if self._observed_history is not None:
            return self._observed_history
        history = getattr(game, "_public_history", None)
        if isinstance(history, InformationHistory):
            return history
        return history_from_game(game)

    def _context(self, game, seat):
        if isinstance(game, PublicDecisionContext):
            return game
        # A local instrumented Game has public chain counters and complete
        # action history.  Mirror-built Game.__new__ projections do not carry
        # that marker and stay on the conservative online projection.
        from ..game import Game
        if isinstance(game, Game) and isinstance(
                getattr(game, "_public_history", None), InformationHistory):
            return PublicDecisionContext.from_game_complete(game, int(seat))
        return PublicDecisionContext.from_game(game, int(seat))

    def _update_shadow(self, context, history, gid=None):
        if not self.shadow_belief:
            return None
        if gid is not None and self._last_gid is not None and gid != self._last_gid:
            self.reset(gid)
        self._last_gid = gid
        if (self._belief is not None and self._history_hash == history.history_hash
                and self._context_hash == context.context_hash):
            return self._belief.summary()
        try:
            profile = self.belief_profile
            if profile.particle_count > self.profile.max_shadow_particles:
                profile = BeliefProfile(
                    particle_count=self.profile.max_shadow_particles,
                    ess_ratio=profile.ess_ratio,
                    likelihood_policy=profile.likelihood_policy,
                    temperature=profile.temperature,
                    epsilon=profile.epsilon,
                    resampler=profile.resampler,
                    rejuvenation=profile.rejuvenation,
                    seed=profile.seed,
                    min_effective_particles=1,
                    history_schema=profile.history_schema,
                    compatibility=profile.compatibility)
            self._belief = BeliefState(
                context, history=history, profile=profile, strict=False)
            self._history_hash = history.history_hash
            self._context_hash = context.context_hash
            return self._belief.summary()
        except Exception as exc:
            self._belief = None
            self._history_hash = history.history_hash
            self._context_hash = context.context_hash
            return {
                "schema": "belief-state-v2",
                "history_hash": history.history_hash,
                "profile_fingerprint": self.belief_profile.fingerprint,
                "particle_count": 0, "ess": 0.0, "entropy": 0.0,
                "history_incomplete": True, "belief_degraded": True,
                "last_reset_reason": f"shadow_error:{type(exc).__name__}",
                "marginals": {},
            }

    @staticmethod
    def _distribution(model, game, seat, legal, *, history=None, belief=None):
        if hasattr(model, "predict_game"):
            try:
                return model.predict_game(
                    game, seat, legal_actions=legal, history=history,
                    belief=belief)
            except TypeError as exc:
                # Keep compatibility with lightweight test/dummy models;
                # only retry for an unsupported optional keyword, not for a
                # model's actual inference failure.
                if "unexpected keyword" not in str(exc):
                    raise
                return model.predict_game(game, seat, legal_actions=legal)
        if hasattr(model, "distribution"):
            return model.distribution(game, legal)
        if callable(model):
            return model(game, seat, legal)
        raise TypeError("model has no policy distribution interface")

    @staticmethod
    def _pick_distribution(distribution, legal):
        actions = tuple(int(action) for action in getattr(distribution, "actions", ()))
        if set(actions) != set(legal) or len(actions) != len(legal):
            raise ModelOutputError(
                "model distribution does not match legal action set",
                kind="legal_set_mismatch")
        probabilities = tuple(float(value) for value in
                             getattr(distribution, "probabilities", ()))
        if len(probabilities) != len(actions) or not probabilities:
            raise ModelOutputError("model distribution is empty",
                                   kind="empty_distribution")
        if any(not math.isfinite(value) or value < 0 for value in probabilities):
            raise ModelOutputError(
                "model distribution contains non-finite probability",
                kind="nonfinite_probability")
        ranked = sorted(zip(actions, probabilities), key=lambda item: (
            -item[1], legal.index(item[0])))
        best = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        total = sum(probabilities)
        if total <= 0 or not math.isfinite(total):
            raise ModelOutputError("model distribution has no finite mass",
                                   kind="no_finite_mass")
        return best[0], max(0.0, best[1] / total - second / total)

    def _record_fallback(self, level, reason):
        """Count every fallback so release evidence can prove None occurred."""
        self.stats["fallbacks"] += 1
        key = str(reason or level)
        counts = self.stats["fallback_by_reason"]
        counts[key] = counts.get(key, 0) + 1
        if str(level) == "legal-order-emergency":
            self.stats["emergency"] += 1

    def _fallback(self, game, seat, legal, reason):
        from ..bot import choose_action

        errors = []
        for evaluator in self.profile.fallback_chain:
            try:
                result = choose_action(
                    game, seat, evaluator=evaluator, return_evaluation=True)
                action, evaluation = (result if isinstance(result, tuple)
                                      and len(result) == 2 else (result, None))
                if action not in legal:
                    raise ValueError("fallback selected an illegal action")
                return action, evaluator, evaluation, \
                    f"{reason};fallback={evaluator}", errors
            except Exception as exc:
                errors.append(f"{evaluator}:{type(exc).__name__}")
        # This is deterministic and explicitly marked emergency.  It is only
        # reachable when both declared policy fallbacks fail; it never repairs
        # a network suggestion silently or samples a random legal action.
        if not legal:
            raise ValueError("cannot fallback with an empty legal set")
        return legal[0], "legal-order-emergency", None, \
            f"{reason};fallback_exhausted", errors

    def choose(self, game, seat, *, history=None, gid=None,
               return_evaluation=False):
        started = time.perf_counter()
        seat = int(seat)
        legal = tuple(int(action) for action in game.legal_actions())
        if not legal:
            raise ValueError("policy-v3 received an empty legal set")
        context = self._context(game, seat)
        history = history or self._history(game)
        belief_summary = self._update_shadow(context, history, gid=gid)
        suggested = None
        confidence = None
        fallback_reason = None
        fallback_errors = []
        evaluation = None
        level = "network"
        self.stats["decisions"] += 1
        self.stats["no_search"] += 1
        model_version = getattr(getattr(self.model, "manifest", None),
                                "model_version", None)
        if len(legal) == 1:
            action = legal[0]
            suggested = action
            confidence = 1.0
            level = "only-legal-action"
            self.stats["only_legal_action"] += 1
        elif self.model_error is not None:
            action, level, evaluation, fallback_reason, fallback_errors = \
                self._fallback(game, seat, legal, self.model_error)
            self._record_fallback(level, fallback_reason)
        else:
            try:
                distribution = self._distribution(
                    self.model, game, seat, legal, history=history,
                    belief=self._belief)
                suggested, confidence = self._pick_distribution(distribution, legal)
                if confidence < self.profile.confidence_threshold:
                    action, level, evaluation, fallback_reason, fallback_errors = \
                        self._fallback(game, seat, legal, "low_network_confidence")
                    self._record_fallback(level, fallback_reason)
                else:
                    action = suggested
                    self.stats["network"] += 1
                    model_version = (getattr(distribution, "version", None)
                                     or model_version)
            except Exception as exc:
                if isinstance(exc, ModelOutputError):
                    self.stats["model_output_errors"] += 1
                    kinds = self.stats["model_output_error_kinds"]
                    kinds[exc.kind] = kinds.get(exc.kind, 0) + 1
                fallback_reason = f"model_error:{type(exc).__name__}"
                action, level, evaluation, fallback_reason, fallback_errors = \
                    self._fallback(game, seat, legal, fallback_reason)
                self._record_fallback(level, fallback_reason)
        if action not in legal:
            self.stats["illegal_selected"] += 1
            raise ValueError("policy-v3 produced an illegal selected action")
        belief = belief_summary or {}
        result = {
            "schema": "policy-v3-explanation-v1",
            "version": "policy-v3", "profile": "policy-v3",
            "profile_fingerprint": self.profile.fingerprint,
            "selected": action, "actual_action": action,
            "suggested_action": suggested,
            "counterfactual_suggestion": (
                {"action": suggested, "accepted": suggested == action}
                if suggested is not None else None),
            "level": level, "search_level": self.profile.search_level,
            "search_enabled": False,
            "model_version": model_version,
            "model_fingerprint": self.model_fingerprint,
            "belief_profile_fingerprint": self.belief_profile.fingerprint,
            "belief_fingerprint": getattr(self._belief, "fingerprint", None),
            "history_hash": history.history_hash,
            "history_incomplete": bool(history.history_incomplete),
            "belief_degraded": bool(belief.get("belief_degraded", True)),
            "belief_ess": belief.get("ess", 0.0),
            "belief_entropy": belief.get("entropy", 0.0),
            "belief_marginals": belief.get("marginals", {}),
            "network_confidence": confidence,
            "fallback_reason": fallback_reason,
            "fallback_errors": fallback_errors,
            "legal_actions": list(legal),
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "oracle": False,
        }
        if evaluation is not None:
            result["fallback_evaluation"] = evaluation
        return (action, result) if return_evaluation else action

    def as_json(self):
        return {
            "schema": "policy-v3-runtime-v1",
            "profile": self.profile.as_json(),
            "belief_profile": self.belief_profile.as_json(),
            "model_fingerprint": self.model_fingerprint,
            "model_error": self.model_error,
            "shadow_belief": self.shadow_belief,
            "stats": dict(self.stats),
            "oracle": False,
        }


def load_policy_value_model(path, *, device="cpu"):
    """Load only manifest-bearing policy-v3 checkpoints.

    Legacy BC/PPO checkpoints intentionally are not auto-adapted: their
    feature contract may contain oracle planes or a different head layout.
    """
    import torch

    from ..models.policy_value import (PolicyValueNet,
                                       policy_value_manifest_from_json)

    checkpoint = torch.load(path, map_location=device, weights_only=True)
    manifest_data = checkpoint.get("manifest")
    if manifest_data is None:
        raise ValueError("policy-v3 checkpoint is missing manifest")
    manifest = (manifest_data if hasattr(manifest_data, "fingerprint")
                else policy_value_manifest_from_json(manifest_data))
    if manifest.oracle:
        raise ValueError("policy-v3 checkpoint cannot be oracle")
    model = PolicyValueNet(
        blocks=manifest.blocks, width=manifest.width, manifest=manifest)
    state = (checkpoint.get("state_dict") or
             checkpoint.get("model_state_dict") or
             checkpoint.get("model"))
    if state is None:
        raise ValueError("policy-v3 checkpoint is missing state_dict")
    model.load_state_dict(state)
    model.to(device)
    model.eval()
    return model
