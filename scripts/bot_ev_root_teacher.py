#!/usr/bin/env python3
"""Generate teacher artifacts for the HU/财飘 and all-root scopes.

The fixture is deliberately public: it contains a hero hand, public rivers and
melds, count-only hidden material, and an explicit response cursor, but never a
source game's opponent hands or wall order.  This is an offline harness and
does not change the online ``choose_action`` default.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mj.decision.context import PublicDecisionContext, _visible
from mj.decision.profile import ProfileSpec
from mj.rollout.evaluator import PairedTeacher
from mj.rollout.teacher_data import teacher_artifact
from mj.tiles import counts


DEFAULT_FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "bot_ev_root_cases.json"


def _context_from_case(case):
    """Convert the compact fixture notation into a public context."""
    data = dict(case["context"])
    hand = data.get("hand")
    if isinstance(hand, str):
        data["hand"] = counts(hand)
    melds = data.get("melds", ((), (), (), ()))
    melds = tuple(tuple((str(kind), int(tile)) for kind, tile in row)
                  for row in melds)
    data["melds"] = melds
    data["discards"] = tuple(tuple(int(tile) for tile in row)
                             for row in data.get("discards", ((),) * 4))
    if "visible" not in data:
        data["visible"] = _visible(tuple(data["hand"]), data["discards"],
                                    melds)
    return PublicDecisionContext.from_public(data)


def load_fixture(path=DEFAULT_FIXTURE):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_case(case, *, seed=0, n0=32, batch=32, nmax=512, fixture_path=None):
    scope = str(case["scope"])
    if scope == "hu-piao":
        profile = ProfileSpec.shape_v2_hu_piao(calibrated=True)
    elif scope == "all-root":
        profile = ProfileSpec.shape_v2_all_root(calibrated=True)
    else:
        raise ValueError(f"root teacher does not support scope {scope!r}")
    context = _context_from_case(case)
    actions = tuple(int(action) for action in case.get(
        "teacher_actions", context.legal_actions))
    teacher = PairedTeacher(
        context, profile_fingerprint=profile.fingerprint, seed=seed,
        belief_version=profile.belief_version, n0=n0, batch=batch,
        nmax=nmax)
    result = teacher.evaluate(actions)
    return teacher_artifact(
        result, context=context, profile=profile,
        metadata={
            "fixture": str(fixture_path or DEFAULT_FIXTURE),
            "fixture_id": case["id"], "scope": scope, "oracle": False,
            "source_group": f"fixture:{case['id']}",
        })


def run(*, fixture=DEFAULT_FIXTURE, case_id=None, seed=0, n0=32,
        batch=32, nmax=512, output=None):
    document = load_fixture(fixture)
    cases = document.get("cases", ())
    selected = [case for case in cases
                if case_id is None or case.get("id") == case_id]
    if not selected:
        raise ValueError(f"fixture case not found: {case_id!r}")
    artifacts = [run_case(case, seed=seed + index, n0=n0, batch=batch,
                          nmax=nmax, fixture_path=fixture)
                 for index, case in enumerate(selected)]
    value = artifacts[0] if case_id is not None else {"artifacts": artifacts}
    if output:
        Path(output).write_text(json.dumps(value, ensure_ascii=False, indent=2)
                                + "\n", encoding="utf-8")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--case", dest="case_id")
    parser.add_argument("--seed", type=int, default=20260916)
    parser.add_argument("--n0", type=int, default=32)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--nmax", type=int, default=512)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    value = run(fixture=args.fixture, case_id=args.case_id, seed=args.seed,
                n0=args.n0, batch=args.batch, nmax=args.nmax,
                output=args.output)
    if args.case_id is not None:
        summary = {key: value.get(key) for key in (
            "status", "scope", "context_hash", "profile_fingerprint",
            "best_action", "runner_up", "sample_count", "attempted_samples",
            "failed_samples", "ambiguous", "stop_reason",
            "artifact_fingerprint") if key in value}
    else:
        summary = {"cases": len(value["artifacts"]),
                   "statuses": [item.get("status")
                                for item in value["artifacts"]]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
