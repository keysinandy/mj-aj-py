"""Search distillation and policy-iteration training utilities."""

from .search_data import (
    SearchDataset,
    SearchSample,
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

__all__ = [
    "IterationRecord", "PolicyIterationManifest", "PolicyIterationRunner",
    "SearchDataset",
    "SearchSample", "freeze_source_splits", "read_search_dataset",
    "should_stop_iteration", "write_search_dataset", "iteration_versions",
    "validate_teacher_provenance", "promotion_gate", "ValueDataset", "ValueSample",
    "read_value_dataset", "write_value_dataset",
]
