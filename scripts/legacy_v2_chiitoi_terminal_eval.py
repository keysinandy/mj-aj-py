"""Fair score/performance comparison against a preserved pre-fix native DLL.

Both kernels are loaded in one interpreter. Only the candidate hero uses the
corrected kernel; opponents always use the preserved production binary.
Kernel-dependent Python caches are isolated. This harness is local/offline.
"""
from __future__ import annotations

import argparse
from collections import Counter, OrderedDict
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import time

import mj.bot as bot
import mj.hand_eval as hand_eval
import mj.legacy_react as react
import mj.shanten as sh
import mj_kernels.mj_kernels as corrected
from mj.game import Game, HU
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.legacy_kong import kong_actions
from mj.tiles import counts
from scripts.legacy_v2_big_hand_grid_scan import _decide, bootstrap, percentile


def legacy_components(natural_pairs, natural_singles, wilds, locked=0):
    if locked:
        return 9
    paired = min(natural_singles, wilds)
    rest = wilds-paired
    return 7-natural_pairs-paired-rest//2-int(natural_singles+rest%2 > 0)


class KernelPair:
    def __init__(self, frozen_path):
        spec = importlib.util.spec_from_file_location("frozen.mj_kernels", frozen_path)
        frozen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(frozen)
        if corrected.shanten_semantics_version() != "hangzhou-chiitoi-terminal-v2":
            raise ValueError("corrected native semantics required")
        terminal = counts("1122m3344p556677s")
        if frozen.shanten(terminal, 0) != 0 or corrected.shanten(terminal, 0) != -1:
            raise ValueError("frozen/corrected kernels do not distinguish the audited bug")
        self.modules = {"baseline": frozen, "candidate": corrected}
        self.functions = [name for name in vars(sh) if name.startswith("_rust_")]
        self.caches = {key: {"piao": {}, "shanten": {}, "decomp": OrderedDict(),
                            "progress": lru_cache(maxsize=512)(react._cached_legacy_shape_progress.__wrapped__)}
                       for key in self.modules}
        self.fixed_components = sh.chiitoi_shanten_components

    @contextmanager
    def use(self, arm):
        saved = {name: getattr(sh, name) for name in self.functions}
        state = (sh._piao_draw_mask_cache, sh._shanten_cache, hand_eval._DECOMP_CACHE,
                 react._cached_legacy_shape_progress, bot._cached_legacy_shape_progress,
                 sh.chiitoi_shanten_components)
        module, caches = self.modules[arm], self.caches[arm]
        try:
            for name in self.functions:
                setattr(sh, name, getattr(module, name[len("_rust_"):], None))
            sh._piao_draw_mask_cache, sh._shanten_cache = caches["piao"], caches["shanten"]
            hand_eval._DECOMP_CACHE = caches["decomp"]
            react._cached_legacy_shape_progress = bot._cached_legacy_shape_progress = caches["progress"]
            sh.chiitoi_shanten_components = (legacy_components if arm == "baseline" else self.fixed_components)
            yield
        finally:
            for name, value in saved.items():
                setattr(sh, name, value)
            (sh._piao_draw_mask_cache, sh._shanten_cache, hand_eval._DECOMP_CACHE,
             react._cached_legacy_shape_progress, bot._cached_legacy_shape_progress,
             sh.chiitoi_shanten_components) = state


_PAIR = None


def play_pair(task):
    global _PAIR
    index, seed, frozen_path, null_control, timings = task
    if _PAIR is None:
        _PAIR = KernelPair(frozen_path)
    kernels = _PAIR
    profile = LegacyTwoPlyProfile.weighted_online()
    hero, dealer = index % 4, (index//4) % 4
    results, traces, latency = {}, {}, {arm: {} for arm in kernels.modules}
    diagnostics = {arm: {} for arm in kernels.modules}
    for arm in (("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")):
        game, trace = Game(seed=seed, dealer=dealer), []
        started = time.perf_counter()
        while not game.done:
            seat, phase = game.current_seat(), game.phase
            legal = tuple(game.legal_actions())
            kernel = "candidate" if arm == "candidate" and seat == hero and not null_control else "baseline"
            scope = ("hu" if HU in legal else "kong" if phase == "discard" and kong_actions(legal)
                     else "discard" if phase == "discard" else "reaction")
            tick = time.perf_counter()
            with kernels.use(kernel):
                action, info = _decide(game, seat, profile, reaction_diagnostics=True)
            elapsed = (time.perf_counter()-tick)*1000
            if timings and seat == hero and len(legal) > 1:
                latency[arm].setdefault(scope, []).append(elapsed)
                counter = diagnostics[arm].setdefault(scope, Counter())
                counter["decisions"] += 1
                counter["fallbacks"] += int(isinstance(info, dict) and bool(
                    info.get("fallback_reason") or info.get("u2_fallback_reason")
                    or info.get("continuation_fallback_reason")))
            if action not in legal:
                raise AssertionError(f"illegal decision {seed}, {arm}, {seat}, {action}")
            trace.append((seat, phase, action))
            game.step(action)
        if sum(game.scores) != 0:
            raise AssertionError("score conservation")
        results[arm] = {"score": game.scores[hero], "win": bool(game.result and game.result[0] == hero),
                        "elapsed_ms": (time.perf_counter()-started)*1000}
        traces[arm] = trace
    return {"seed": seed, "hero": hero, "dealer": dealer, **results,
            "delta": results["candidate"]["score"]-results["baseline"]["score"],
            "trajectory_changed": traces["candidate"] != traces["baseline"], "latency_ms": latency,
            "diagnostics": diagnostics}


def distribution(values):
    return {"n": len(values), "p50": percentile(values, .5), "p95": percentile(values, .95),
            "p99": percentile(values, .99), "max": max(values) if values else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=int, default=1024)
    parser.add_argument("--seed-start", type=int, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--frozen-kernel", required=True)
    parser.add_argument("--null-control", action="store_true")
    parser.add_argument("--performance", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.pairs <= 0 or args.pairs % 16 or args.jobs <= 0:
        parser.error("positive multiple of 16 pairs and positive jobs required")
    if args.performance and args.jobs != 1:
        parser.error("performance runs must use one process")
    frozen_path = str(Path(args.frozen_kernel).resolve())
    root = Path(__file__).resolve().parents[1]
    sources = ("mj/shanten.py", "mj/hand_eval.py", "mj/bot.py", "mj/legacy_eval.py",
               "mj/legacy_react.py", "mj/legacy_kong.py", "rust/src/lib.rs",
               "scripts/legacy_v2_chiitoi_terminal_eval.py")
    manifest = {path: hashlib.sha256((root/path).read_bytes()).hexdigest() for path in sources}
    started = time.perf_counter()
    tasks = [(i, args.seed_start+i, frozen_path, args.null_control, args.performance) for i in range(args.pairs)]
    rows = []
    def collect(iterator):
        for row in iterator:
            rows.append(row)
            if len(rows) % 64 == 0:
                print(f"pairs {len(rows)}/{args.pairs}", flush=True)
    if args.jobs == 1:
        collect(map(play_pair, tasks))
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            collect(pool.map(play_pair, tasks, chunksize=4))
    payload = {"schema": "chiitoi-terminal-native-paired-v1", "pairs": args.pairs,
        "seed_start": args.seed_start, "null_control": args.null_control,
        "mode": "performance" if args.performance else "score",
        "pairing": "single corrected hero vs 3 frozen kernel opponents; balanced 16-case hero/dealer; alternating order",
        "paired_score": bootstrap([row["delta"] for row in rows]),
        "nonzero_pairs": sum(row["delta"] != 0 for row in rows),
        "changed_trajectories": sum(row["trajectory_changed"] for row in rows),
        "source_sha256": manifest, "frozen_kernel_sha256": hashlib.sha256(Path(frozen_path).read_bytes()).hexdigest(),
        "corrected_kernel_sha256": hashlib.sha256(Path(corrected.__file__).read_bytes()).hexdigest(),
        "kernel": sh.kernel_runtime_diagnostic(), "runtime_s": time.perf_counter()-started,
        "hero_score_mean": {arm: statistics.fmean(row[arm]["score"] for row in rows) for arm in ("baseline", "candidate")},
        "rows": rows}
    if args.performance:
        report = {}
        for arm in ("baseline", "candidate"):
            scopes = {scope: [value for row in rows for value in row["latency_ms"][arm].get(scope, ())]
                      for scope in ("discard", "reaction", "hu", "kong")}
            report[arm] = {scope: distribution(values) for scope, values in scopes.items()}
            for scope in scopes:
                counts = Counter()
                for row in rows:
                    counts.update(row["diagnostics"][arm].get(scope, {}))
                report[arm][scope]["fallback_rate"] = (counts["fallbacks"]/counts["decisions"]
                                                        if counts["decisions"] else None)
            report[arm]["game"] = distribution([row[arm]["elapsed_ms"] for row in rows])
        payload["latency"] = report
        gates, regressions = {}, {}
        for scope in ("discard", "reaction", "hu", "kong"):
            control, candidate = report["baseline"][scope], report["candidate"][scope]
            covered = control["n"] >= 64 and candidate["n"] >= 64
            ratios = {key: candidate[key]/control[key] if control[key] and candidate[key] else None
                      for key in ("p95", "p99")}
            fallback_delta = (candidate["fallback_rate"]-control["fallback_rate"]
                              if candidate["fallback_rate"] is not None and control["fallback_rate"] is not None else None)
            gates[scope] = bool(covered and ratios["p95"] <= 1.10 and ratios["p99"] <= 1.15
                                and fallback_delta <= .01)
            regressions[scope] = {key: (ratio-1)*100 if ratio is not None else None for key, ratio in ratios.items()}
            regressions[scope]["fallback_delta_pp"] = fallback_delta*100 if fallback_delta is not None else None
        batches = [{arm: statistics.fmean(row[arm]["elapsed_ms"] for row in rows[start:start+400])
                    for arm in ("baseline", "candidate")} for start in range(0, len(rows), 400)]
        elapsed_ratio = statistics.median(batch["candidate"] for batch in batches)/statistics.median(batch["baseline"] for batch in batches)
        gates.update(sample_size=len(rows) >= 1200, elapsed=elapsed_ratio <= 1.10)
        payload.update(performance_gates=gates, latency_regression_pct=regressions,
                       batches=batches, elapsed_regression_pct=(elapsed_ratio-1)*100)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key != "rows"}), flush=True)


if __name__ == "__main__":
    main()
