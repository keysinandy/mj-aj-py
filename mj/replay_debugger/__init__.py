"""Offline, read-only Mahjong evidence replay and diagnosis."""

from .adapters import (
    EvidenceBundle,
    canonical_tile,
    find_local_logs,
    import_http_dump,
    import_local_jsonl,
    import_server_timeline,
    import_sources,
    import_trace,
    normalize_event,
    read_jsonl,
)
from .compiler import compile_bundle, compile_replay
from .diagnostics import (
    classify_request,
    compare_states,
    detect_missing_transitions,
    effective_merge_diff,
    expected_observed_diff,
    request_to_response_diff,
)
from .export import export_replay, render_html, safe_json, write_export
from .model import *
from .state import (
    ReferenceReducer,
    business_projection,
    project_visibility,
    replay_events,
    state_from_dict,
    validate_invariants,
    validate_state,
)
from .timeline import (ReplayNavigator, TimelineIndex, deduplicate_events,
                       establish_causality, state_at, verify_checkpoints)
from .trace import ReplayTraceWriter, TraceRecorder, scrub_credentials


def benchmark_seek(*args, **kwargs):
    from .benchmark import benchmark_seek as _benchmark_seek
    return _benchmark_seek(*args, **kwargs)


def make_benchmark_session(*args, **kwargs):
    from .benchmark import make_benchmark_session as _make_benchmark_session
    return _make_benchmark_session(*args, **kwargs)

__all__ = [
    "EvidenceBundle", "canonical_tile", "find_local_logs", "import_http_dump",
    "import_local_jsonl", "import_server_timeline", "import_sources", "import_trace",
    "normalize_event", "read_jsonl", "compile_bundle", "compile_replay",
    "compare_states", "request_to_response_diff", "effective_merge_diff",
    "expected_observed_diff", "classify_request", "detect_missing_transitions",
    "export_replay", "render_html", "safe_json", "write_export",
    "ReferenceReducer", "business_projection", "project_visibility", "replay_events",
    "state_from_dict", "validate_state", "validate_invariants", "TimelineIndex", "ReplayNavigator", "deduplicate_events", "establish_causality", "state_at", "verify_checkpoints",
    "ReplayTraceWriter", "TraceRecorder", "scrub_credentials",
    "benchmark_seek", "make_benchmark_session",
]
