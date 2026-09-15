"""CLI for deterministic local Mahjong replay export."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .adapters import find_local_logs
from .compiler import compile_replay
from .export import write_export


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile local Mahjong evidence into replay.json and offline index.html")
    parser.add_argument("target", help="local JSONL path or gid searched below --root")
    parser.add_argument("--root", default="local/games",
                        help="local game log root used when target is a gid")
    parser.add_argument("--server", help="optional independent server timeline JSON")
    parser.add_argument("--trace", help="optional versioned replay trace JSONL")
    parser.add_argument("--http-dump", help="optional DumpingApi JSON file or directory")
    parser.add_argument("--round", dest="round_no", type=int,
                        help="only export this round")
    parser.add_argument("--out", help="output directory (default: replay-output/<gid>)")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace existing replay outputs explicitly")
    parser.add_argument("--print-json", action="store_true",
                        help="also print the machine-readable result to stdout")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    paths = find_local_logs(args.target, args.root)
    if not paths:
        print(f"no local JSONL log found for {args.target!r}", file=sys.stderr)
        return 2
    if len(paths) > 1:
        print("ambiguous local game target; choose one path:", file=sys.stderr)
        for path in paths:
            print(f"  {path}", file=sys.stderr)
        return 2
    local_path = paths[0]
    session = compile_replay(local_path, server_path=args.server,
                             trace_path=args.trace,
                             http_dump_path=args.http_dump,
                             round_no=args.round_no)
    gid = session.game_id or os.path.splitext(os.path.basename(local_path))[0]
    out = args.out or os.path.join("replay-output", gid)
    inputs = [local_path, args.server, args.trace, args.http_dump]
    try:
        json_path, html_path = write_export(session, out,
                                            overwrite=args.overwrite,
                                            input_paths=inputs)
    except (OSError, ValueError) as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 2
    report = {
        "gameId": session.game_id,
        "round": session.metadata.get("selectedRound"),
        "rawRecords": len(session.raw_records),
        "normalizedEvents": len(session.normalized_events),
        "localSteps": len(session.local_steps),
        "requests": len(session.requests),
        "diagnostics": len(session.diagnostics),
        "json": str(json_path),
        "html": str(html_path),
        "coverage": session.source_coverage,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.print_json:
        print(session.to_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

