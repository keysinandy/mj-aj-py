"""Public-information decision primitives for versioned Mahjong evaluators.

The package is intentionally kept below :mod:`mj.rollout`: rules and public
decision code never imports the offline teacher.
"""

from .context import PublicDecisionContext, ContextError
from .frontier import DiscardFrontierItem, discard_frontier
from .profile import (
    ProfileSpec, ProfileFingerprintError, canonical_json, fingerprint,
    validate_profile_fingerprint, profile_from_json,
)
from .score_value import (
    BOUND_MODES, FAST_REWARD_MODEL, REWARD_ENVELOPE_VERSION,
    ROLLOUT_REWARD_MODEL, RewardCertificate, RewardEnvelope, ScoreValue,
    ScoreBreakdown, build_reward_envelope, compute_reward_envelope,
    reward_bound, reward_envelope, theoretical_reward_bound,
)
from .report import (
    sanitize_public, compact_evaluation, decision_key,
    associate_counterfactual, build_offline_report,
)
from .root import RootEvaluation, evaluate_root_context, choose_root_game_action

_CALIBRATION_EXPORTS = frozenset({
    "CalibrationError", "CalibrationRow", "LinearModel", "SparseLUT",
    "candidate_features", "fit_global_linear", "fit_sparse_lut",
    "calibrated_profile", "paired_q_regret", "paired_q_regret_report",
    "cluster_bootstrap", "cluster_bootstrap_simultaneous",
    "release_gate", "freeze_split_manifest", "split_for_seed", "split_rows",
    "split_calibration_artifact", "evidence_contract",
    "calibration_evidence_manifest", "scheduled_game",
})


def __getattr__(name):
    """Load NumPy-backed calibration only when an offline caller asks for it."""
    if name in _CALIBRATION_EXPORTS:
        from . import calibration
        value = getattr(calibration, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "PublicDecisionContext", "ContextError", "DiscardFrontierItem",
    "discard_frontier", "ProfileSpec", "ProfileFingerprintError",
    "canonical_json", "fingerprint", "validate_profile_fingerprint",
    "profile_from_json",
    "ScoreValue", "ScoreBreakdown", "theoretical_reward_bound",
    "REWARD_ENVELOPE_VERSION", "BOUND_MODES", "FAST_REWARD_MODEL",
    "ROLLOUT_REWARD_MODEL", "RewardCertificate", "RewardEnvelope",
    "reward_envelope", "compute_reward_envelope", "build_reward_envelope",
    "reward_bound",
    "CalibrationError", "CalibrationRow", "LinearModel", "SparseLUT",
    "candidate_features", "fit_global_linear", "fit_sparse_lut",
    "calibrated_profile",
    "paired_q_regret", "paired_q_regret_report", "cluster_bootstrap",
    "cluster_bootstrap_simultaneous",
    "release_gate", "freeze_split_manifest", "split_for_seed", "split_rows",
    "split_calibration_artifact", "evidence_contract",
    "calibration_evidence_manifest", "scheduled_game",
    "sanitize_public", "compact_evaluation", "decision_key",
    "associate_counterfactual", "build_offline_report",
    "RootEvaluation", "evaluate_root_context", "choose_root_game_action",
]
