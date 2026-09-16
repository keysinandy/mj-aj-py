"""Versioned leaf evaluators for search-v1."""

from __future__ import annotations

from dataclasses import dataclass
import math

from ..decision.context import PublicDecisionContext
from ..rollout.simulator import FixedContinuation, RolloutFailure, actor_view


@dataclass(frozen=True)
class LeafEvaluation:
    status: str
    reward: float | None
    source: str
    steps: int = 0
    error: str | None = None
    fallback_reason: str | None = None

    @property
    def valid(self):
        return self.status == "ok" and self.reward is not None

    def as_json(self):
        return {"status": self.status, "reward": self.reward,
                "source": self.source, "steps": self.steps,
                "error": self.error, "fallback_reason": self.fallback_reason}


class LeafModelMismatch(RuntimeError):
    """A value artifact cannot be used for the requested search contract."""


class SafeLeafFallback:
    """Keep a requested leaf contract while evaluating with terminal rollout."""

    def __init__(self, terminal_evaluator, *, requested_version,
                 reason="leaf_model_mismatch"):
        self.terminal_evaluator = terminal_evaluator
        self.version = str(requested_version)
        self.fallback_reason = str(reason)

    def evaluate(self, game, hero: int, *, depth=0):
        result = self.terminal_evaluator.evaluate(game, hero, depth=depth)
        return LeafEvaluation(
            result.status, result.reward, self.version, result.steps,
            result.error, self.fallback_reason)


class TerminalRolloutEvaluator:
    version = "terminal-rollout-v1"

    def __init__(self, continuation=None, *, max_steps=4096):
        self.continuation = continuation or FixedContinuation("shape-v1")
        self.max_steps = max(1, int(max_steps))

    def evaluate(self, game, hero: int, *, depth=0):
        steps = 0
        try:
            while not game.done:
                if steps >= self.max_steps:
                    raise RolloutFailure("leaf rollout exceeded max_steps")
                actor = game.current_seat()
                legal = tuple(game.legal_actions())
                if not legal:
                    raise RolloutFailure("leaf non-terminal state has no legal actions")
                action = self.continuation.action(game, actor)
                if action not in legal:
                    raise RolloutFailure("leaf continuation selected an illegal action")
                game.step(action)
                steps += 1
            reward = float(game.scores[hero])
            if not math.isfinite(reward):
                raise RolloutFailure("terminal reward is non-finite")
            return LeafEvaluation("ok", reward, self.version, steps)
        except Exception as exc:
            return LeafEvaluation("failed", None, self.version, steps,
                                  f"{type(exc).__name__}:{exc}")


class _ActorPolicyContinuation:
    def __init__(self, policy):
        self.policy = policy
        self.version = getattr(policy, "version", "actor-policy-continuation-v1")

    def action(self, game, actor):
        view = actor_view(game, actor)
        legal = tuple(game.legal_actions())
        return self.policy.choose(view, legal)


class ValueNetLeafEvaluator:
    version = "value-net-v1"

    def __init__(self, model, *, expected_belief_fingerprint="",
                 expected_search_fingerprint="",
                 expected_feature_contract_fingerprint="", value_scale=None,
                 contract=None, terminal_evaluator=None):
        from ..models.policy_value import ValueTransformContract

        self.model = model
        self.expected_belief_fingerprint = expected_belief_fingerprint
        self.expected_search_fingerprint = expected_search_fingerprint
        self.contract = contract or ValueTransformContract()
        if value_scale is not None and abs(
                float(value_scale) - float(self.contract.scale)) > 1e-9:
            raise ValueError(
                "value_scale contradicts the declared value transform contract")
        self.contract_fingerprint = self.contract.fingerprint
        self.terminal_evaluator = terminal_evaluator or TerminalRolloutEvaluator()
        manifest = getattr(model, "manifest", None)
        if manifest is None or not getattr(manifest, "calibrated", False):
            raise LeafModelMismatch("value model is not calibrated")
        if expected_belief_fingerprint and (
                manifest.belief_profile_fingerprint != expected_belief_fingerprint):
            raise LeafModelMismatch("value model belief fingerprint mismatch")
        if expected_search_fingerprint and (
                manifest.search_profile_fingerprint != expected_search_fingerprint):
            raise LeafModelMismatch("value model search fingerprint mismatch")
        if expected_feature_contract_fingerprint and (
                manifest.feature_contract_fingerprint !=
                expected_feature_contract_fingerprint):
            raise LeafModelMismatch("value model feature contract mismatch")
        feature_contract = getattr(model, "feature_contract", None)
        actual_feature_fingerprint = getattr(feature_contract, "fingerprint", None)
        if (actual_feature_fingerprint and
                manifest.feature_contract_fingerprint and
                actual_feature_fingerprint != manifest.feature_contract_fingerprint):
            raise LeafModelMismatch("model feature contract is inconsistent")
        if getattr(manifest, "oracle", True):
            raise LeafModelMismatch("value model is marked oracle")

    def evaluate(self, game, hero: int, *, depth=0):
        try:
            context = PublicDecisionContext.from_game(game, hero)
            planes, scalars = self.model.extract_features(context)
            import torch
            with torch.no_grad():
                _, value = self.model(
                    torch.as_tensor(planes[None], dtype=torch.float32),
                    torch.as_tensor(scalars[None], dtype=torch.float32))
            reward = self.contract.inverse_transform_value(
                float(value[0].item()))
            if not math.isfinite(reward):
                raise LeafModelMismatch("value model returned non-finite value")
            return LeafEvaluation("ok", reward, self.version)
        except LeafModelMismatch:
            raise
        except Exception as exc:
            # A runtime model error is not evidence of a zero-valued leaf.
            # Finish the same state with the declared terminal continuation;
            # the fallback reason remains visible to offline reports.
            fallback = self.terminal_evaluator.evaluate(
                game, hero, depth=depth)
            return LeafEvaluation(
                fallback.status, fallback.reward, self.version,
                fallback.steps, fallback.error,
                f"value_model_inference_failed:{type(exc).__name__}")


class HybridLeafEvaluator:
    version = "hybrid-v1"

    def __init__(self, value_evaluator, terminal_evaluator=None, *,
                 rollout_on_special=True):
        self.value_evaluator = value_evaluator
        self.terminal_evaluator = terminal_evaluator or TerminalRolloutEvaluator()
        self.rollout_on_special = bool(rollout_on_special)
        self.fallback_reason = getattr(value_evaluator, "fallback_reason", None)

    def evaluate(self, game, hero: int, *, depth=0):
        # Special/high-risk states stay on the fully observable terminal
        # evaluator.  This is a conservative fallback, not a second rule set.
        legal = tuple(game.legal_actions())
        if self.rollout_on_special and (
                game.phase != "discard" or any(action < 0 for action in legal)):
            return self.terminal_evaluator.evaluate(game, hero, depth=depth)
        try:
            result = self.value_evaluator.evaluate(game, hero, depth=depth)
        except LeafModelMismatch:
            return self.terminal_evaluator.evaluate(game, hero, depth=depth)
        return (result if result.valid else
                self.terminal_evaluator.evaluate(game, hero, depth=depth))


def make_leaf_evaluator(leaf_version, *, model=None, continuation=None,
                        terminal_evaluator=None,
                        expected_belief_fingerprint="",
                        expected_search_fingerprint="",
                        expected_feature_contract_fingerprint=""):
    """Build a leaf evaluator with an explicit, safe downgrade path.

    The returned object's ``version`` remains the requested contract, while
    a downgrade exposes ``fallback_reason`` and every result carries the
    same reason.  Search artifacts therefore cannot silently look like a
    calibrated ValueNet run.
    """
    terminal = terminal_evaluator or TerminalRolloutEvaluator(continuation)
    requested = str(leaf_version)
    if requested == TerminalRolloutEvaluator.version:
        return terminal
    if requested not in (ValueNetLeafEvaluator.version,
                         HybridLeafEvaluator.version):
        raise LeafModelMismatch(f"unsupported leaf version: {requested}")
    value = None
    reason = None
    if model is None:
        reason = "leaf_model_missing"
    else:
        try:
            value = ValueNetLeafEvaluator(
                model,
                expected_belief_fingerprint=expected_belief_fingerprint,
                expected_search_fingerprint=expected_search_fingerprint,
                expected_feature_contract_fingerprint=(
                    expected_feature_contract_fingerprint),
                terminal_evaluator=terminal)
        except Exception as exc:
            reason = f"leaf_model_mismatch:{type(exc).__name__}"
    if value is None:
        value = SafeLeafFallback(
            terminal, requested_version=ValueNetLeafEvaluator.version,
            reason=reason or "leaf_model_mismatch")
    if requested == ValueNetLeafEvaluator.version:
        return value
    return HybridLeafEvaluator(value, terminal)
