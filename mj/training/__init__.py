"""Search distillation and policy-iteration training utilities."""

from .search_data import (
    SearchDataset,
    SearchSample,
    feature_fingerprint,
    freeze_source_splits,
    read_search_dataset,
    write_search_dataset,
)
from .value_data import (
    ValueDataset,
    ValueSample,
    read_value_dataset,
    write_value_dataset,
)
from .policy_iteration import (
    IterationRecord,
    PolicyIterationRunner,
    PolicyIterationManifest,
    iteration_versions,
    promotion_gate,
    should_stop_iteration,
    validate_teacher_provenance,
)
from .teacher_budget import (
    TeacherBudgetProfile,
    TeacherEvidence,
    TeacherTierDecision,
    escalation_reasons,
    forced_decision,
    forced_sanity_selected,
    next_decision,
    run_decision,
    start_decision,
    teacher_budget_profile_from_json,
)
from .distillation_profile import (
    BASELINE_GIT,
    FROZEN_SPLITS,
    RULES_VERSION,
    SCORE_UNITS,
    OpponentPopulationProfile,
    PolicyCandidate,
    SearchDistillationProfile,
    baseline_freeze,
    opponent_population_from_json,
    search_distillation_profile_from_json,
    select_strongest_fast_policy,
    special_state_tags,
    write_baseline_freeze,
)
from .dataset_report import (
    coverage_gate,
    dataset_report,
    duplicate_report,
    merge_split_assignments,
    validate_split_integrity,
    verify_feature_fingerprints,
)

__all__ = [
    "BASELINE_GIT", "FROZEN_SPLITS", "RULES_VERSION", "SCORE_UNITS",
    "IterationRecord", "PolicyIterationManifest", "PolicyIterationRunner",
    "OpponentPopulationProfile", "PolicyCandidate",
    "SearchDataset", "SearchDistillationProfile", "SearchSample",
    "TeacherBudgetProfile", "TeacherEvidence", "TeacherTierDecision",
    "baseline_freeze", "coverage_gate", "dataset_report",
    "duplicate_report", "escalation_reasons", "feature_fingerprint",
    "forced_decision", "forced_sanity_selected", "freeze_source_splits",
    "merge_split_assignments", "next_decision",
    "opponent_population_from_json", "promotion_gate",
    "read_search_dataset", "run_decision", "search_distillation_profile_from_json",
    "select_strongest_fast_policy", "should_stop_iteration",
    "special_state_tags", "start_decision", "teacher_budget_profile_from_json",
    "validate_split_integrity", "validate_teacher_provenance",
    "verify_feature_fingerprints", "ValueDataset", "ValueSample",
    "write_baseline_freeze", "write_search_dataset", "iteration_versions",
    "read_value_dataset", "write_value_dataset",
]
