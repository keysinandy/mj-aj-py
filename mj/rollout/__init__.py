"""Offline-only hidden-world sampling and paired rollout teacher."""

from .belief import BeliefSampler, SampledWorld, BeliefError
from .simulator import (
    RolloutFailure, RolloutOutcome, build_world_game, run_rollout,
    FixedContinuation,
)
from .evaluator import PAIRWISE_RACING_VERSION, PairedTeacher, TeacherResult
from .teacher_data import teacher_artifact, write_teacher_artifact

__all__ = [
    "BeliefSampler", "SampledWorld", "BeliefError", "RolloutFailure",
    "RolloutOutcome", "build_world_game", "run_rollout",
    "FixedContinuation", "PairedTeacher", "TeacherResult",
    "PAIRWISE_RACING_VERSION",
    "teacher_artifact", "write_teacher_artifact",
]
