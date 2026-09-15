"""Deterministic offline seek benchmark for the replay debugger."""

from __future__ import annotations

import random
import resource
import statistics
import time
import tracemalloc
from typing import Any

from .model import Boundary, Cursor, LocalStep, LocalStepType, ReplaySession, SeqFrame, stable_id
from .timeline import TimelineIndex, verify_checkpoints


def benchmark_seek(session: ReplaySession, *, iterations: int = 2_000,
                   round_no: int | None = None, seed: int = 17) -> dict[str, Any]:
    """Measure indexed direct seeks and JSON export for one compiled session.

    The benchmark does not rebuild or mutate the session.  It uses a seeded
    sequence of valid and invalid targets so the result is reproducible while
    also exercising the cursor-retention path for unavailable seq values.
    """
    index = TimelineIndex(session.frames, session.local_steps, session.game_id)
    if round_no is None:
        available = [r for r in index.rounds if r is not None]
        round_no = available[0] if available else (index.rounds[0] if index.rounds else None)
    seqs = index.seqs(round_no)
    if not seqs:
        return {
            "iterations": 0,
            "round": round_no,
            "seqCount": 0,
            "localStepCount": len(session.local_steps),
            "seekP95Ms": None,
            "seekMeanMs": None,
            "exportBytes": len(session.to_json()),
            "peakMemoryBytes": 0,
            "processMaxRssBytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "checkpointCheck": verify_checkpoints(session),
            "passed": False,
            "reason": "no indexed server seqs",
        }

    rng = random.Random(seed)
    cursor = index.first(round_no) or Cursor(session.game_id, round_no, seqs[0], Boundary.BEFORE)
    samples: list[float] = []
    invalid = 0
    tracemalloc.start()
    try:
        for _ in range(max(0, int(iterations))):
            target = seqs[rng.randrange(len(seqs))]
            if rng.randrange(20) == 0:
                target += 1_000_000
                invalid += 1
            started = time.perf_counter_ns()
            candidate, message = index.jump_seq(cursor, target)
            elapsed = (time.perf_counter_ns() - started) / 1_000_000
            samples.append(elapsed)
            if message is None:
                cursor = candidate
    finally:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    export_bytes = len(session.to_json())
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))] if ordered else None
    mean = statistics.fmean(samples) if samples else None
    return {
        "iterations": len(samples),
        "invalidTargets": invalid,
        "round": round_no,
        "seqCount": len(seqs),
        "localStepCount": len(session.local_steps),
        "seekP95Ms": round(p95, 4) if p95 is not None else None,
        "seekMeanMs": round(mean, 4) if mean is not None else None,
        "exportBytes": export_bytes,
        "peakMemoryBytes": peak,
        "processMaxRssBytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "checkpointCheck": verify_checkpoints(session),
        "passed": bool(p95 is not None and p95 <= 200),
    }


def make_benchmark_session(*, seq_count: int = 2_000,
                           local_step_count: int = 20_000) -> ReplaySession:
    """Build the fixed-size synthetic session used by the acceptance command."""
    gid = "replay-debugger-benchmark"
    frames = [SeqFrame(seq_no=seq, round_no=1,
                       server_event={"eventId": stable_id("benchmark-event", seq),
                                     "type": "DISCARD", "seqNo": seq},
                       local_steps=[])
              for seq in range(1, max(0, int(seq_count)) + 1)]
    steps = []
    for index in range(max(0, int(local_step_count))):
        seq = frames[index % len(frames)].seq_no if frames else None
        step = LocalStep(step_id=stable_id("benchmark-step", index), index=index,
                         local_ordinal=index, type=LocalStepType.INPUT,
                         related_seq_no=seq, payload={"benchmark": True})
        steps.append(step)
        if frames:
            frames[index % len(frames)].local_steps.append(step.step_id)
    return ReplaySession(session_id=stable_id("benchmark-session", seq_count, local_step_count),
                         game_id=gid, frames=frames, local_steps=steps,
                         metadata={"benchmark": {"seqCount": seq_count,
                                                  "localStepCount": local_step_count}})


def main(argv=None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="run the offline replay seek benchmark")
    parser.add_argument("--seqs", type=int, default=2_000)
    parser.add_argument("--local-steps", type=int, default=20_000)
    parser.add_argument("--iterations", type=int, default=2_000)
    args = parser.parse_args(argv)
    session = make_benchmark_session(seq_count=args.seqs,
                                     local_step_count=args.local_steps)
    print(json.dumps(benchmark_seek(session, iterations=args.iterations),
                     ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
