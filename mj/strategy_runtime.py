"""Shared, public strategy configuration and execution diagnostics.

These helpers only describe the selected strategy and the result already
returned by one invocation. They never call an evaluator or inspect hidden
game state.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping


SNAPSHOT_VERSION = 1
AUDIT_VERSION = 1
DECISION_SCOPES = {
    "baotou_scope", "weighted_two_ply", "legacy", "reaction_v2",
    "hu_window_arbitration", "kong_continuation", "fallback", "policy",
    "policy-v3", "random", "unknown",
}


def _json_value(value):
    if hasattr(value, "as_json"):
        value = value.as_json()
    elif hasattr(value, "payload") and callable(value.payload):
        value = value.payload()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _feature(status, **values):
    result = {"status": status}
    result.update(values)
    return result


def _public_model_name(value):
    if value is None:
        return None
    name = os.path.basename(os.path.normpath(str(value)))
    return name or None


@dataclass(frozen=True)
class StrategySnapshot:
    strategy: str
    evaluator: str | None
    profile: str | None
    profile_fingerprint: str | None
    features: Mapping[str, Mapping[str, Any]]
    profile_config: Mapping[str, Any]
    config_hash: str
    runtime: Mapping[str, Any]
    model_name: str | None = None
    commit_sha: str | None = None
    version: int = SNAPSHOT_VERSION

    def as_json(self):
        return {
            "version": self.version,
            "strategy": self.strategy,
            "evaluator": self.evaluator,
            "profile": self.profile,
            "profile_fingerprint": self.profile_fingerprint,
            "features": _json_value(self.features),
            "profile_config": _json_value(self.profile_config),
            "config_hash": self.config_hash,
            "runtime": _json_value(self.runtime),
            "model_name": self.model_name,
            "commit_sha": self.commit_sha,
        }


def _profile_from_config(strategy, evaluator, config):
    """Resolve known built-in profiles without loading a model or game state."""
    if strategy == "policy-v3":
        from .decision.policy_v3 import PolicyV3Profile
        profile_config = config.get("policy_profile")
        if isinstance(profile_config, Mapping):
            allowed = set(PolicyV3Profile.__dataclass_fields__)
            profile_config = {key: value for key, value in
                              profile_config.items() if key in allowed}
            return PolicyV3Profile(**profile_config), None
        try:
            threshold = float(config.get("confidence_threshold", 0.0))
        except (TypeError, ValueError):
            threshold = 0.0
        return PolicyV3Profile(confidence_threshold=threshold), None
    if strategy != "bot":
        return None, None
    from .legacy_eval import (
        DEFAULT_BOT_EVALUATOR,
        LEGACY_V2_BASELINE_EVALUATORS,
        LEGACY_V2_EVALUATORS,
        LEGACY_V2_OFFLINE_EVALUATORS,
        LEGACY_V2_PHASE_A_EVALUATORS,
        LEGACY_V2_PHASE_B_EVALUATORS,
        LEGACY_V2_SHAPE_PHASE_A_EVALUATORS,
        LEGACY_V2_SHAPE_PHASE_B_EVALUATORS,
        LegacyTwoPlyProfile,
        canonical_evaluator,
    )
    from .legacy_react import LegacyReactionProfile

    evaluator = canonical_evaluator(evaluator or DEFAULT_BOT_EVALUATOR)
    if evaluator in LEGACY_V2_EVALUATORS:
        def _config_bool(name, default):
            value = config.get(name, default)
            if isinstance(value, str):
                return value.strip().lower() in {
                    "1", "true", "yes", "on", "enabled",
                }
            return bool(value)

        marginal_enabled = _config_bool(
            "marginal_structure_guard_enabled", True)
        speed_enabled = _config_bool("speed_band_enabled", False)
        pareto_enabled = _config_bool(
            "pareto_frontier_enabled", speed_enabled)
        profile_kwargs = {
            "marginal_structure_guard_enabled": bool(marginal_enabled),
            "speed_band_enabled": speed_enabled,
            "pareto_frontier_enabled": pareto_enabled,
        }
        if "marginal_structure_role_version" in config:
            profile_kwargs["marginal_structure_role_version"] = str(
                config["marginal_structure_role_version"])
        if "marginal_structure_slack_by_shanten" in config:
            profile_kwargs["marginal_structure_slack_by_shanten"] = config[
                "marginal_structure_slack_by_shanten"]
        if "speed_band_version" in config:
            profile_kwargs["speed_band_version"] = str(
                config["speed_band_version"])
        if "speed_band_min_ratio_by_shanten" in config:
            profile_kwargs["speed_band_min_ratio_by_shanten"] = config[
                "speed_band_min_ratio_by_shanten"]
        return (LegacyTwoPlyProfile.weighted_online(**profile_kwargs),
                LegacyReactionProfile.v2_online())
    if evaluator in LEGACY_V2_BASELINE_EVALUATORS:
        return (LegacyTwoPlyProfile.weighted_online(
            big_hand_enabled=False, big_hand_same_shanten_enabled=False,
            big_hand_plus_one_enabled=False, shape_quality_enabled=False,
            shape_quality_guard_enabled=False,
            marginal_structure_guard_enabled=False),
                LegacyReactionProfile.v2_online())
    if evaluator in LEGACY_V2_PHASE_A_EVALUATORS:
        return (LegacyTwoPlyProfile.weighted_online(
            big_hand_enabled=True, big_hand_same_shanten_enabled=True,
            big_hand_plus_one_enabled=False, shape_quality_enabled=False,
            shape_quality_guard_enabled=False,
            marginal_structure_guard_enabled=False),
                LegacyReactionProfile.v2_online())
    if evaluator in LEGACY_V2_PHASE_B_EVALUATORS:
        return (LegacyTwoPlyProfile.weighted_online(
            big_hand_enabled=True, big_hand_same_shanten_enabled=True,
            big_hand_plus_one_enabled=True, shape_quality_enabled=False,
            shape_quality_guard_enabled=False,
            marginal_structure_guard_enabled=False),
                LegacyReactionProfile.v2_online())
    if evaluator in LEGACY_V2_SHAPE_PHASE_A_EVALUATORS:
        return (LegacyTwoPlyProfile.weighted_online(
            big_hand_enabled=False, big_hand_same_shanten_enabled=False,
            big_hand_plus_one_enabled=False, shape_quality_enabled=True,
            shape_quality_stage="root",
            shape_quality_guard_enabled=True,
            marginal_structure_guard_enabled=False),
                LegacyReactionProfile.v2_online())
    if evaluator in LEGACY_V2_SHAPE_PHASE_B_EVALUATORS:
        return (LegacyTwoPlyProfile.weighted_online(
            big_hand_enabled=False, big_hand_same_shanten_enabled=False,
            big_hand_plus_one_enabled=False, shape_quality_enabled=True,
            shape_quality_stage="full",
            shape_quality_guard_enabled=True,
            marginal_structure_guard_enabled=False),
                LegacyReactionProfile.v2_online())
    if evaluator in LEGACY_V2_OFFLINE_EVALUATORS:
        return (LegacyTwoPlyProfile.weighted_offline(),
                LegacyReactionProfile.v2_offline())
    if evaluator in ("legacy-two-ply-v1", "legacy_v1", "legacy-v1"):
        return LegacyTwoPlyProfile.default(), LegacyReactionProfile.v1()
    if evaluator in ("shape-v2", "shape_v2", "ev2"):
        from .decision.profile import ProfileSpec
        try:
            fallback_ms = float(config.get("fallback_ms", 36.0))
        except (TypeError, ValueError):
            fallback_ms = 36.0
        return ProfileSpec.shape_v2_discard(time_budget_ms=fallback_ms), None
    if evaluator in ("policy-v3", "policy_v3"):
        from .decision.policy_v3 import PolicyV3Profile
        profile_config = config.get("policy_profile")
        if isinstance(profile_config, Mapping):
            allowed = set(PolicyV3Profile.__dataclass_fields__)
            profile_config = {key: value for key, value in
                              profile_config.items() if key in allowed}
            return PolicyV3Profile(**profile_config), None
        return PolicyV3Profile(), None
    return None, None


def strategy_snapshot(strategy, evaluator=None, *, profile=None,
                      reaction_profile=None, model_name=None,
                      config=None) -> StrategySnapshot:
    """Build a deterministic snapshot from a resolved strategy config."""
    from .legacy_eval import canonical_evaluator

    config = config if isinstance(config, Mapping) else {}
    strategy = str(strategy or "unknown")
    evaluator = (canonical_evaluator(evaluator)
                 if strategy == "bot" else
                 str(evaluator or strategy) if evaluator or strategy else None)
    if profile is None:
        profile, inferred_reaction = _profile_from_config(
            strategy, evaluator, config)
        if reaction_profile is None:
            reaction_profile = inferred_reaction

    profile_data = _json_value(profile) if profile is not None else {}
    if not isinstance(profile_data, dict):
        profile_data = {"value": profile_data}
    profile_name = (profile_data.get("name") or profile_data.get("version")
                    or evaluator)
    profile_fingerprint = (profile_data.get("fingerprint") or
                            getattr(profile, "fingerprint", None))

    runtime = {}
    legacy_profile = None
    if profile is not None:
        try:
            from .legacy_eval import LegacyTwoPlyProfile
            if isinstance(profile, LegacyTwoPlyProfile):
                legacy_profile = profile
        except ImportError:
            pass
    if legacy_profile is not None and legacy_profile.mode == "weighted":
        from .bot import (
            PIAO_WALL_GUARD,
            PIAO_SEARCH_MAX_PASSES,
            PIAO_SEARCH_MIN_RATIO,
            PIAO_SEARCH_MIN_SELF_DRAWS,
        )
        from .shanten import kernel_runtime_diagnostic
        runtime = kernel_runtime_diagnostic()
        enabled = bool(legacy_profile.enabled)
        weighted = enabled
        features = {
            "weighted_two_ply": _feature(
                "enabled" if weighted else "disabled"),
            "stage_b": _feature("enabled" if weighted else "disabled"),
            "shape_guard": _feature(
                "enabled" if legacy_profile.shape_guard_enabled else "disabled"),
            "marginal_structure_guard": _feature(
                "enabled" if legacy_profile.marginal_structure_guard_enabled
                else "disabled",
                role_version=legacy_profile.marginal_structure_role_version,
                slack_by_shanten=list(
                    legacy_profile.marginal_structure_slack_by_shanten)),
            "speed_band": _feature(
                "enabled" if legacy_profile.speed_band_enabled
                else "disabled",
                version=legacy_profile.speed_band_version,
                min_ratio_by_shanten=list(
                    legacy_profile.speed_band_min_ratio_by_shanten)),
            "pareto_frontier": _feature(
                "enabled" if legacy_profile.pareto_frontier_enabled
                else "disabled"),
            "shape_quality": _feature(
                "enabled" if legacy_profile.shape_quality_enabled else "disabled",
                stage=legacy_profile.shape_quality_stage),
            "big_hand_intent": _feature(
                "enabled" if legacy_profile.big_hand_enabled else "disabled"),
            "plus_one": _feature(
                "enabled" if (legacy_profile.big_hand_enabled and
                              legacy_profile.big_hand_plus_one_enabled)
                else "disabled"),
            "baotou_scope": _feature("enabled"),
            "piao_wall_guard": _feature(
                "enabled", min_live=PIAO_WALL_GUARD),
            "piao_search": _feature(
                "enabled",
                min_ratio=PIAO_SEARCH_MIN_RATIO,
                min_self_draws=PIAO_SEARCH_MIN_SELF_DRAWS,
                max_search_passes=PIAO_SEARCH_MAX_PASSES,
                pass_cap_persisted=False,
                kernel=runtime.get("piao_draw_mask_kernel", "unknown")),
            "guaranteed_next_draw_hu": _feature(
                "enabled", hard_wall=PIAO_WALL_GUARD),
            "reaction_v2": _feature(
                "enabled" if reaction_profile is not None else "unknown"),
            "kong_continuation": _feature(
                "enabled" if reaction_profile is not None else "unknown"),
        }
    elif profile is not None and evaluator in ("policy-v3", "policy_v3"):
        features = {
            "weighted_two_ply": _feature("not_applicable"),
            "stage_b": _feature("not_applicable"),
            "shape_guard": _feature("not_applicable"),
            "marginal_structure_guard": _feature("not_applicable"),
            "speed_band": _feature("not_applicable"),
            "pareto_frontier": _feature("not_applicable"),
            "big_hand_intent": _feature("not_applicable"),
            "reaction_v2": _feature("not_applicable"),
            "kong_continuation": _feature("not_applicable"),
            "piao_search": _feature("not_applicable"),
            "guaranteed_next_draw_hu": _feature("not_applicable"),
        }
    else:
        features = {
            "weighted_two_ply": _feature("not_applicable"),
            "stage_b": _feature("not_applicable"),
            "shape_guard": _feature("not_applicable"),
            "marginal_structure_guard": _feature("not_applicable"),
            "speed_band": _feature("not_applicable"),
            "pareto_frontier": _feature("not_applicable"),
            "big_hand_intent": _feature("not_applicable"),
            "reaction_v2": _feature("unknown"),
            "kong_continuation": _feature("unknown"),
        }

    if reaction_profile is not None:
        reaction_data = _json_value(reaction_profile)
        if isinstance(reaction_data, dict):
            features["reaction_v2"] = _feature(
                "enabled" if reaction_data.get("enabled", True) and
                reaction_data.get("version", "").endswith("v2") else
                "disabled")
            features["kong_continuation"] = _feature(
                "enabled" if reaction_data.get("continuation_node_budget", 0)
                else "disabled")
            profile_data["reaction"] = reaction_data

    if strategy != "bot":
        features.update({
            "weighted_two_ply": _feature("not_applicable"),
            "stage_b": _feature("not_applicable"),
            "shape_guard": _feature("not_applicable"),
            "marginal_structure_guard": _feature("not_applicable"),
            "speed_band": _feature("not_applicable"),
            "pareto_frontier": _feature("not_applicable"),
            "big_hand_intent": _feature("not_applicable"),
            "plus_one": _feature("not_applicable"),
            "baotou_scope": _feature("not_applicable"),
            "piao_wall_guard": _feature("not_applicable"),
            "piao_search": _feature("not_applicable"),
            "guaranteed_next_draw_hu": _feature("not_applicable"),
        })

    public_model_name = _public_model_name(model_name)
    public_config = {
        "strategy": strategy,
        "evaluator": evaluator,
        "profile": profile_data,
        "model_name": public_model_name,
    }
    config_hash = hashlib.sha256(_canonical(public_config).encode("utf-8"))\
        .hexdigest()[:16]
    commit_sha = (os.environ.get("MJ_BUILD_SHA") or
                  os.environ.get("GIT_COMMIT_SHA"))
    if commit_sha:
        commit_sha = str(commit_sha)[:40]
    return StrategySnapshot(
        strategy=strategy,
        evaluator=evaluator,
        profile=str(profile_name) if profile_name else None,
        profile_fingerprint=str(profile_fingerprint)
        if profile_fingerprint else None,
        features=features,
        profile_config=profile_data,
        config_hash=config_hash,
        runtime=runtime,
        model_name=public_model_name,
        commit_sha=commit_sha,
    )


def snapshot_with_model_name(snapshot, model_name):
    """Add the selected public model label without exposing its file path."""
    if not isinstance(snapshot, StrategySnapshot) or not model_name:
        return snapshot
    public_config = {
        "strategy": snapshot.strategy,
        "evaluator": snapshot.evaluator,
        "profile": _json_value(snapshot.profile_config),
        "model_name": _public_model_name(model_name),
    }
    config_hash = hashlib.sha256(_canonical(public_config).encode("utf-8"))\
        .hexdigest()[:16]
    return StrategySnapshot(
        strategy=snapshot.strategy,
        evaluator=snapshot.evaluator,
        profile=snapshot.profile,
        profile_fingerprint=snapshot.profile_fingerprint,
        features=snapshot.features,
        profile_config=snapshot.profile_config,
        config_hash=config_hash,
        runtime=snapshot.runtime,
        model_name=_public_model_name(model_name),
        commit_sha=snapshot.commit_sha,
        version=snapshot.version,
    )


def snapshot_for_config(config, *, default_strategy=None):
    from .legacy_eval import DEFAULT_BOT_EVALUATOR

    config = config if isinstance(config, Mapping) else {}
    strategy = config.get("strategy") or default_strategy or "unknown"
    evaluator = config.get("evaluator")
    profile = None
    reaction_profile = None
    if strategy == "bot":
        profile, reaction_profile = _profile_from_config(
            strategy, evaluator, config)
        if profile is None and (evaluator is None or evaluator == "legacy"):
            profile, reaction_profile = _profile_from_config(
                strategy, DEFAULT_BOT_EVALUATOR, config)
    return strategy_snapshot(
        strategy, evaluator, profile=profile,
        reaction_profile=reaction_profile,
        model_name=config.get("model_name"), config=config)


def _as_evaluation(value):
    value = _json_value(value)
    return value if isinstance(value, dict) else {}


def decision_audit(evaluation, action, elapsed_ms, *, phase=None,
                   snapshot=None, decision_id=None):
    """Normalize one already-computed evaluator result into a compact audit."""
    data = _as_evaluation(evaluation)
    snapshot_data = (snapshot.as_json() if hasattr(snapshot, "as_json") else
                     _json_value(snapshot) if snapshot is not None else {})
    features_config = (snapshot_data.get("features", {})
                       if isinstance(snapshot_data, dict) else {})
    profile_config = (snapshot_data.get("profile_config", {})
                      if isinstance(snapshot_data, dict) else {})

    def configured_state(name):
        feature = features_config.get(name)
        if not isinstance(feature, dict):
            return None
        status = feature.get("status")
        if status == "enabled":
            return True
        if status == "disabled":
            return False
        return None

    def config_unknown_reason(name):
        status = (features_config.get(name, {}).get("status")
                  if isinstance(features_config.get(name), dict) else None)
        return ("NOT_APPLICABLE" if status == "not_applicable" else
                "CONFIG_UNKNOWN")

    weighted_configured = configured_state("weighted_two_ply")
    is_weighted = weighted_configured is True
    scope = data.get("decision_scope")
    if scope not in DECISION_SCOPES:
        slow_path = data.get("pong_kong_slow_path")
        if isinstance(slow_path, dict) and slow_path.get("entered"):
            scope = "kong_continuation"
        elif str(phase or "").startswith("response_") and data.get(
                "reaction_profile"):
            scope = "reaction_v2"
        else:
            strategy_name = snapshot_data.get("strategy")
            if strategy_name in ("policy", "policy-v3", "random"):
                scope = strategy_name
            elif (strategy_name == "bot" and
                  snapshot_data.get("evaluator") in {
                      "legacy", "legacyV2", "legacy-v2",
                      "legacy-two-ply-v1", "legacy-v1",
                      "weighted-two-ply-frontier-v1",
                  }):
                scope = "legacy"
            else:
                scope = "unknown"
    fallback_reason = (data.get("fallback_reason") or
                       data.get("future_fallback_reason") or
                       data.get("u2_fallback_reason"))
    missing = data.get("missing")
    if not isinstance(missing, (list, tuple)):
        missing = ()
    hu_kong_scope = "hu_kong_scope" in missing
    hu_window_scope = scope == "hu_window_arbitration"
    search_used = data.get("search_used")
    metrics = data.get("search_metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    reaction_profile_data = data.get("reaction_profile")
    if not isinstance(reaction_profile_data, dict):
        reaction_profile_data = {}
    reaction_entered = bool(
        str(phase or "").startswith("response_") and reaction_profile_data)
    reaction_fallback = data.get("u2_fallback_reason")
    slow_path = data.get("pong_kong_slow_path")
    if not isinstance(slow_path, dict):
        slow_path = {}
    stage_b_entered = bool(data.get("stage_b_entered") or
                           metrics.get("stage_b_entered"))
    weighted_entered = data.get("weighted_two_ply_entered")
    if weighted_entered is None:
        weighted_entered = bool(search_used or stage_b_entered or
                                metrics.get("draw_nodes", 0))
    selected_scope = scope
    weighted_eligible = bool(
        is_weighted and phase in (None, "discard", "draw") and
        selected_scope != "baotou_scope" and not hu_kong_scope)
    benign_reasons = {"only_legal_action", "profile_disabled"}
    partial = bool(data.get("partial_accepted") or
                   data.get("u2_partial_accepted"))
    generic_fallback = bool(data.get("fallback") or
                            (fallback_reason and
                             fallback_reason not in benign_reasons and
                             not partial))
    fallback_flag = generic_fallback

    singleton = data.get("short_circuit_reason") == "frontier_singleton" or \
        metrics.get("short_circuit") == "frontier_singleton"
    if singleton and selected_scope == "weighted_two_ply":
        scope = "legacy"
        selected_scope = "legacy"
    if fallback_flag:
        scope = "fallback"
    if selected_scope == "baotou_scope":
        weighted_eligible = False
        weighted_entered = False
        weighted_skip = "BAOTOU_SCOPE_EARLY_RETURN"
    elif hu_kong_scope:
        weighted_eligible = False
        weighted_entered = False
        weighted_skip = "HU_KONG_SCOPE_EARLY_RETURN"
    elif singleton:
        weighted_eligible = False
        weighted_skip = "FRONTIER_SINGLETON"
        weighted_entered = False
    elif not weighted_eligible:
        weighted_skip = (
            config_unknown_reason("weighted_two_ply")
            if weighted_configured is None else
            "STRATEGY_NOT_APPLICABLE" if not is_weighted else
            "SCOPE_NOT_ELIGIBLE")
    elif not weighted_entered:
        weighted_skip = str(fallback_reason or "SEARCH_NOT_ENTERED")
    else:
        weighted_skip = None

    stage_a_entered = bool(metrics.get("draw_nodes", 0) or
                           data.get("search_attempt_phase") or
                           data.get("search_phase"))
    stage_b_configured = configured_state("stage_b")
    stage_b_eligible = bool(
        stage_b_configured is True and stage_a_entered and weighted_entered and
        data.get("search_phase") != "future_shanten")
    stage_b_skip = None
    if stage_b_configured is not True:
        stage_b_skip = (config_unknown_reason("stage_b")
                        if stage_b_configured is None else "NOT_CONFIGURED")
    elif not stage_a_entered:
        stage_b_skip = weighted_skip or "STAGE_A_NOT_ENTERED"
    elif not stage_b_entered:
        stage_b_skip = ("STAGE_A_PROVED_WINNER" if
                        data.get("search_phase") == "future_shanten" else
                        "NO_STAGE_B_RESULT")

    shape_guard_data = data.get("frontier_guard")
    if not isinstance(shape_guard_data, dict):
        shape_guard_data = {}
    shape_guard_configured = configured_state("shape_guard")
    shape_guard_reason = shape_guard_data.get("skipped_reason")
    if shape_guard_configured is None:
        shape_guard_reason = config_unknown_reason("shape_guard")
    elif selected_scope == "baotou_scope":
        shape_guard_reason = "BAOTOU_SCOPE_EARLY_RETURN"
    elif hu_kong_scope:
        shape_guard_reason = "HU_KONG_SCOPE_EARLY_RETURN"
    elif hu_window_scope:
        shape_guard_reason = "HU_WINDOW_ARBITRATION"
    pre_entry_guard_skips = {
        "shape_quality_guard_disabled", "kernel_unavailable",
        "primary_not_singleton", "slack_zero",
    }
    shape_guard_eligible = bool(
        shape_guard_configured is True and
        phase in (None, "draw", "discard") and
        selected_scope != "baotou_scope" and not hu_kong_scope and
        shape_guard_reason not in pre_entry_guard_skips)
    shape_guard_entered = bool(
        shape_guard_configured is True and shape_guard_data and
        shape_guard_reason not in pre_entry_guard_skips)

    marginal_guard_data = data.get("marginal_structure_guard")
    if not isinstance(marginal_guard_data, dict):
        marginal_guard_data = {}
    marginal_configured = configured_state("marginal_structure_guard")
    marginal_reason = marginal_guard_data.get("skipped_reason")
    marginal_blocked = bool(data.get("frontier_singleton_blocked") or
                            marginal_guard_data.get(
                                "frontier_singleton_blocked"))
    marginal_proven = bool(data.get("frontier_singleton_proven") or
                           marginal_guard_data.get(
                               "frontier_singleton_proven"))
    marginal_eligible = bool(
        marginal_configured is True and
        phase in (None, "draw", "discard") and
        selected_scope != "baotou_scope" and not hu_kong_scope)
    if marginal_configured is False:
        marginal_reason = "FEATURE_DISABLED"
    elif selected_scope == "baotou_scope":
        marginal_reason = "BAOTOU_SCOPE_EARLY_RETURN"
    elif hu_kong_scope:
        marginal_reason = "HU_KONG_SCOPE_EARLY_RETURN"
    elif hu_window_scope:
        marginal_reason = "HU_WINDOW_ARBITRATION"
    elif marginal_configured is None:
        marginal_reason = config_unknown_reason("marginal_structure_guard")
    elif marginal_reason is None and not marginal_blocked and not marginal_proven:
        marginal_reason = "NOT_ENTERED"
    marginal_entered = bool(
        marginal_configured is True and marginal_guard_data and
        marginal_eligible)

    intents = []
    candidates = data.get("candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if isinstance(candidate, dict):
                intents.extend(candidate.get("intent_kinds") or [])
    intent = data.get("intent")
    if isinstance(intent, str) and intent:
        intents.append(intent)
    intent = sorted(set(str(value) for value in intents))
    big_hand_configured = configured_state("big_hand_intent")
    big_hand_phase = data.get("big_hand_phase")
    big_hand_entered = big_hand_phase not in (None, "disabled")
    big_hand_eligible = (
        None if big_hand_configured is None else
        bool(big_hand_configured and phase in (None, "draw", "discard") and
             selected_scope != "baotou_scope" and not hu_kong_scope))
    if big_hand_configured is False:
        big_hand_skip = "FEATURE_DISABLED"
    elif selected_scope == "baotou_scope":
        big_hand_skip = "BAOTOU_SCOPE_EARLY_RETURN"
    elif hu_kong_scope:
        big_hand_skip = "HU_KONG_SCOPE_EARLY_RETURN"
    elif hu_window_scope:
        big_hand_skip = "HU_WINDOW_ARBITRATION"
    elif big_hand_configured is None:
        big_hand_skip = config_unknown_reason("big_hand_intent")
    elif big_hand_entered:
        big_hand_skip = None
    elif big_hand_eligible:
        big_hand_skip = "BIG_HAND_INTENT_NOT_ENTERED"
    else:
        big_hand_skip = "NOT_ELIGIBLE"
    reaction_configured = configured_state("reaction_v2")
    reaction_enabled = reaction_configured is True
    reaction_completed = bool(data.get("u2_complete_or_safe_partial"))
    kong_configured = configured_state("kong_continuation")
    kong_enabled = kong_configured is True
    kong_entered = bool(slow_path.get("entered"))
    if reaction_fallback and reaction_entered:
        reaction_skip = reaction_fallback
    elif reaction_configured is None:
        reaction_skip = config_unknown_reason("reaction_v2")
    elif not reaction_enabled:
        reaction_skip = "FEATURE_DISABLED"
    elif not str(phase or "").startswith("response_"):
        reaction_skip = "NOT_REACTION_PHASE"
    elif not reaction_entered:
        reaction_skip = "REACTION_RESULT_NOT_RECORDED"
    else:
        reaction_skip = None
    if kong_configured is None:
        kong_skip = config_unknown_reason("kong_continuation")
    elif not kong_enabled:
        kong_skip = "FEATURE_DISABLED"
    elif not reaction_entered:
        kong_skip = "REACTION_NOT_ENTERED"
    elif not data.get("kong"):
        kong_skip = "KONG_ACTION_NOT_ELIGIBLE"
    elif slow_path.get("fallback_reason"):
        kong_skip = slow_path["fallback_reason"]
    elif not kong_entered:
        kong_skip = "KONG_PATH_NOT_ENTERED"
    else:
        kong_skip = None

    budget = data.get("budget")
    timeout = bool(data.get("timeout") or
                   any(marker in str(fallback_reason or "").lower()
                       for marker in ("deadline", "timeout", "budget_exceeded")))
    piao_search_data = data.get("piao_search")
    if not isinstance(piao_search_data, dict):
        piao_search_data = {}
    piao_configured = configured_state("piao_search")
    guaranteed_configured = configured_state("guaranteed_next_draw_hu")
    hu_candidates = [candidate for candidate in
                     (data.get("hu_window_candidates") or
                      data.get("candidates") or [])
                     if isinstance(candidate, dict)]
    guaranteed_candidates = [candidate for candidate in hu_candidates
                             if candidate.get("guaranteed_next_draw_hu")]
    audit_features = {
        "shape_guard": {
            "configured": shape_guard_configured,
            "eligible": shape_guard_eligible if shape_guard_configured is not None
            else None,
            "entered": shape_guard_entered,
            "skip_reason": shape_guard_reason,
        },
        "marginal_structure_guard": {
            "configured": marginal_configured,
            "eligible": marginal_eligible if marginal_configured is not None
            else None,
            "entered": marginal_entered,
            "blocked": marginal_blocked,
            "proven": marginal_proven,
            "slack": data.get("role_guard_slack"),
            "challengers": data.get("role_guard_challengers") or
            marginal_guard_data.get("role_guard_challengers") or
            marginal_guard_data.get("challengers") or [],
            "skip_reason": marginal_reason,
        },
        "weighted_two_ply": {
            "configured": weighted_configured,
            "eligible": weighted_eligible if weighted_configured is not None
            else None,
            "entered": bool(weighted_entered),
            "completed": bool(data.get("complete")) if weighted_entered
            else False,
            "fallback": bool(weighted_entered and fallback_flag),
            "skip_reason": weighted_skip,
        },
        "stage_a": {
            "configured": weighted_configured,
            "eligible": weighted_eligible if weighted_configured is not None
            else None,
            "entered": stage_a_entered,
            "completed": bool(data.get("complete") or
                               data.get("partial_accepted"))
            if stage_a_entered else False,
            "candidate_count": metrics.get("root_candidates"),
            "draw_nodes": metrics.get("draw_nodes"),
            "winner": data.get("speed_winner"),
            "skip_reason": (None if stage_a_entered else weighted_skip),
        },
        "stage_b": {
            "configured": stage_b_configured,
            "eligible": stage_b_eligible if stage_b_configured is not None
            else None,
            "entered": stage_b_entered,
            "completed": bool(data.get("complete")) if stage_b_entered
            else False,
            "winner": data.get("selected") if stage_b_entered else None,
            "skip_reason": stage_b_skip,
        },
        "big_hand_intent": {
            "configured": big_hand_configured,
            "eligible": big_hand_eligible,
            "intent": intent[0] if len(intent) == 1 else intent or None,
            "entered": big_hand_entered,
            "skip_reason": big_hand_skip,
        },
        "baotou_scope": {
            "configured": configured_state("baotou_scope"),
            "eligible": (True if selected_scope in {
                             "baotou_scope", "hu_window_arbitration"} else
                         False if configured_state("baotou_scope") is False
                         else None),
            "entered": (selected_scope == "baotou_scope" or
                        bool(data.get("baotou_scope_entered")) or
                        bool((data.get("baotou_scope") or {}).get("entered"))),
        },
        "hu_window_arbitration": {
            "configured": True,
            "eligible": hu_window_scope,
            "entered": hu_window_scope,
            "candidate_types": [
                candidate.get("type") for candidate in
                (data.get("hu_window_candidates") or
                 data.get("candidates") or [])
                if isinstance(candidate, dict) and candidate.get("type")
            ],
            "selected_type": data.get("selected_type"),
            "selected": data.get("selected", action),
            "reason": data.get("reason"),
            "observed_delay_reasons": data.get(
                "observed_delay_reasons") or [],
            "ignored_delay_reasons": data.get(
                "ignored_delay_reasons") or [],
            "applied_delay_reasons": data.get(
                "applied_delay_reasons") or [],
            "guaranteed_candidate_count": len(guaranteed_candidates),
            "selected_guaranteed_next_draw_hu": bool(
                next((candidate for candidate in guaranteed_candidates
                      if candidate.get("action") == data.get(
                          "selected", action)), None)),
        },
        "piao_search": {
            "configured": piao_configured,
            "eligible": (piao_search_data.get("piao_search_eligible")
                         if piao_search_data else False),
            "entered": bool(piao_search_data),
            "piao_ready_now": piao_search_data.get("piao_ready_now"),
            "piao_live": piao_search_data.get("piao_live"),
            "draw_live": piao_search_data.get("draw_live"),
            "piao_types": piao_search_data.get("piao_types"),
            "piao_ratio": piao_search_data.get("piao_ratio"),
            "full_piao_search": piao_search_data.get("full_piao_search"),
            "self_draw_horizon": piao_search_data.get(
                "self_draw_horizon"),
            "search_allowed": piao_search_data.get("search_allowed"),
            "search_skip_reason": piao_search_data.get(
                "search_skip_reason"),
            "nodes": piao_search_data.get("nodes"),
            "max_search_passes": piao_search_data.get(
                "max_search_passes"),
            "search_passes_cap_enabled": piao_search_data.get(
                "search_passes_cap_enabled", False),
        },
        "guaranteed_next_draw_hu": {
            "configured": guaranteed_configured,
            "eligible": bool(guaranteed_candidates),
            "entered": bool(guaranteed_candidates),
            "candidate_count": len(guaranteed_candidates),
            "selected": next((candidate.get("action")
                               for candidate in guaranteed_candidates
                               if candidate.get("action") == data.get(
                                   "selected", action)), None),
            "ignored_delay_reasons": sorted({
                reason for candidate in guaranteed_candidates
                for reason in candidate.get("ignored_delay_reasons", ())}),
        },
        "reaction_v2": {
            "configured": reaction_configured,
            "eligible": (bool(reaction_enabled and
                               str(phase or "").startswith("response_"))
                         if reaction_configured is not None else None),
            "entered": reaction_entered,
            "completed": reaction_completed if reaction_entered else False,
            "fallback": bool(reaction_fallback and reaction_entered),
            "skip_reason": reaction_skip,
        },
        "kong_continuation": {
            "configured": kong_configured,
            "eligible": (bool(kong_enabled and reaction_entered and
                               data.get("kong"))
                         if kong_configured is not None else None),
            "entered": kong_entered,
            "completed": bool(kong_entered and
                              slow_path.get("q_pong") is not None and
                              slow_path.get("q_kong") is not None),
            "fallback": bool(slow_path.get("fallback_reason") and
                              kong_entered),
            "skip_reason": kong_skip,
        },
    }
    if isinstance(budget, dict):
        budget_summary = {
            key: budget.get(key) for key in
            ("soft_budget_ms", "hard_budget_ms", "node_budget",
             "work_budget") if budget.get(key) is not None
        }
    else:
        budget_summary = {}
    config_hash = (snapshot_data.get("config_hash")
                   if isinstance(snapshot_data, dict) else None)
    result = {
        "version": AUDIT_VERSION,
        "decision_id": decision_id,
        "strategy_config_hash": config_hash,
        "decision_scope": scope,
        "phase": phase,
        "features": audit_features,
        "decision_candidates": data.get("hu_window_candidates") or
        data.get("candidates") or [],
        "result": {"action": action, "selected": data.get("selected", action)},
        "runtime": {
            "elapsed_ms": round(float(elapsed_ms), 3)
            if elapsed_ms is not None else None,
            "timeout": timeout,
            "partial": partial,
            "fallback": fallback_flag,
            "fallback_reason": fallback_reason,
            "budget": budget_summary,
        },
    }
    return _json_value(result)


def format_snapshot(snapshot):
    data = snapshot.as_json() if hasattr(snapshot, "as_json") else snapshot
    if not isinstance(data, dict):
        return "Strategy Snapshot unavailable"
    lines = ["========== Strategy Configuration =========="]
    lines.extend((f"strategy             {data.get('strategy') or 'unknown'}",
                  f"evaluator            {data.get('evaluator') or 'unknown'}",
                  f"profile              {data.get('profile') or 'unknown'}",
                  f"config_hash          {data.get('config_hash') or 'unknown'}"))
    if data.get("model_name"):
        lines.append(f"model                {data['model_name']}")
    if data.get("commit_sha"):
        lines.append(f"build                {data['commit_sha']}")
    features = data.get("features") or {}
    for key in sorted(features):
        feature = features[key]
        if isinstance(feature, dict):
            state = feature.get("status", "unknown").upper()
            extra = ", ".join(
                f"{name}={value}" for name, value in feature.items()
                if name != "status")
            lines.append(f"{key:20} {state}" + (f" ({extra})" if extra else ""))
    profile = data.get("profile_config") or {}
    if isinstance(profile, dict):
        for key in ("max_frontier_candidates", "soft_budget_ms",
                    "hard_budget_ms"):
            if profile.get(key) is not None:
                lines.append(f"{key:20} {profile[key]}")
    runtime = data.get("runtime") or {}
    if isinstance(runtime, dict) and runtime:
        state = ("COMPATIBLE" if runtime.get("weighted_kernel_compatible")
                 else "DEGRADED")
        lines.append(f"weighted_kernel      {state} "
                     f"{runtime.get('weighted_kernel') or 'unknown'}"
                     f"({runtime.get('weighted_kernel_version') or 'n/a'})")
        if runtime.get("piao_draw_mask_kernel"):
            lines.append(f"piao_mask_kernel     "
                         f"{runtime['piao_draw_mask_kernel']}")
        if runtime.get("baotou_wait_kernel"):
            lines.append(f"baotou_wait_kernel   "
                         f"{runtime['baotou_wait_kernel']}")
        if runtime.get("reason"):
            lines.append(f"kernel_reason        {runtime['reason']}")
    lines.append("============================================")
    return "\n".join(lines)


def format_decision_audit(audit, *, evaluation=None, verbose=False):
    if not isinstance(audit, dict):
        return "[decision] audit unavailable"
    result = audit.get("result") or {}
    runtime = audit.get("runtime") or {}

    def action_label(action):
        if isinstance(action, int):
            from .logview import action_name
            return action_name(action)
        return action

    parts = [
        f"scope={audit.get('decision_scope', 'unknown')}",
        f"action={action_label(result.get('action'))}",
    ]
    weighted = (audit.get("features") or {}).get("weighted_two_ply") or {}
    stage_a = (audit.get("features") or {}).get("stage_a") or {}
    runtime = audit.get("runtime") or {}
    if weighted.get("entered"):
        state = ("DONE" if weighted.get("completed") else
                 "PARTIAL" if runtime.get("partial") else
                 "FALLBACK" if weighted.get("fallback") else "INCOMPLETE")
    else:
        state = f"SKIP({weighted.get('skip_reason') or 'UNKNOWN'})"
    parts.append(f"two_ply={state}")
    parts.append("stage_a=" + ("ENTERED" if stage_a.get("entered") else
                               f"SKIP({stage_a.get('skip_reason') or 'UNKNOWN'})"))
    stage_b = (audit.get("features") or {}).get("stage_b") or {}
    parts.append("stage_b=" + ("ENTERED" if stage_b.get("entered") else
                               f"SKIP({stage_b.get('skip_reason') or 'UNKNOWN'})"))
    hu_window = (audit.get("features") or {}).get(
        "hu_window_arbitration") or {}
    if hu_window.get("entered"):
        types = ",".join(hu_window.get("candidate_types") or []) or "none"
        parts.append(f"hu_roots={types}")
        parts.append(f"hu_selected={hu_window.get('selected_type') or 'unknown'}")
        if hu_window.get("ignored_delay_reasons"):
            parts.append("ignored=" + ",".join(
                hu_window["ignored_delay_reasons"]))
    piao_search = (audit.get("features") or {}).get("piao_search") or {}
    if piao_search.get("entered"):
        parts.append("piao_search=" + (
            "OPEN" if piao_search.get("search_allowed") else
            f"SKIP({piao_search.get('search_skip_reason') or 'UNKNOWN'})"))
        if piao_search.get("piao_ratio") is not None:
            parts.append(f"piao_ratio={piao_search['piao_ratio']}")
    guaranteed = (audit.get("features") or {}).get(
        "guaranteed_next_draw_hu") or {}
    if guaranteed.get("entered"):
        parts.append(f"guaranteed={guaranteed.get('candidate_count', 0)}")
    intent = (audit.get("features") or {}).get("big_hand_intent") or {}
    parts.append(f"intent={intent.get('intent') or 'NONE'}")
    parts.append(f"fallback={str(bool(runtime.get('fallback'))).lower()}")
    parts.append(f"timeout={str(bool(runtime.get('timeout'))).lower()}")
    parts.append(f"partial={str(bool(runtime.get('partial'))).lower()}")
    parts.append(f"partial={str(bool(runtime.get('partial'))).lower()}")
    parts.append(f"{runtime.get('elapsed_ms')}ms")
    lines = ["[strategy] " + " ".join(parts)]
    if verbose and evaluation is not None:
        data = _as_evaluation(evaluation)
        candidates = data.get("candidates")
        if isinstance(candidates, list):
            lines.append("  roots (current invocation):")
            selected = data.get("selected")
            for candidate in candidates[:5]:
                if not isinstance(candidate, dict):
                    continue
                fields = ("tile", "shanten", "ukeire", "future_improve_weight",
                          "future_ukeire", "shape_loss", "feed_risk",
                          "intent_strength")
                values = []
                for name in fields:
                    value = candidate.get(name)
                    if value is None:
                        continue
                    if name == "tile":
                        value = action_label(value)
                    values.append(f"{name}={value}")
                summary = " ".join(values)
                if candidate.get("tile") == selected:
                    summary += " selected=true"
                lines.append(f"    {summary}")
    return "\n".join(lines)
