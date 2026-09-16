"""Public-information belief modelling for information-set decisions.

The package deliberately keeps hidden material behind :class:`BeliefState`.
Callers that need to explain a belief receive only aggregate marginals and
version fingerprints; sampled worlds are available to the offline search
adapter, not to online log serialization.
"""

from .events import (
    EVENT_TYPES,
    InformationHistory,
    PublicReplayState,
    PublicEvent,
    history_from_game,
    history_from_records,
    public_state_hash,
    replay_against_context,
    replay_public_history,
)
from .calibration import (
    CalibrationReport,
    brier_score,
    calibration_gate,
    coverage,
    evaluate_belief_rows,
    log_loss,
)
from .profile import BeliefProfile, belief_profile_from_json
from .resample import effective_sample_size, entropy, systematic_resample
from .state import BeliefError, BeliefState, Particle

__all__ = [
    "BeliefError", "BeliefProfile", "BeliefState", "EVENT_TYPES",
    "CalibrationReport", "brier_score", "calibration_gate", "coverage",
    "InformationHistory", "Particle", "PublicEvent", "PublicReplayState",
    "belief_profile_from_json", "effective_sample_size", "entropy",
    "history_from_game", "history_from_records", "public_state_hash",
    "evaluate_belief_rows", "log_loss", "replay_against_context",
    "replay_public_history", "systematic_resample",
]
