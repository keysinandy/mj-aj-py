"""Run summary for one reduced generation (pi0/dataset/training/gates).

Reads whatever artifacts already exist and marks the rest as pending, so
it can run while the pipeline is still progressing.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path


def _load(path):
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(value, digits=4):
    if value is None:
        return "pending"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def build_summary(*, run_dir="runs/search_bc/gen0",
                  dataset_manifest="data/distill/dataset0.manifest.json",
                  pi0_selection="runs/search_bc/pi0_selection.json",
                  pipeline_log="data/distill/pipeline_gen0.log"):
    run_dir = Path(run_dir)
    pi0 = _load(pi0_selection) or {}
    dataset = _load(dataset_manifest) or {}
    training = _load(run_dir / "training_manifest.json") or {}
    offline = _load(run_dir / "selection_report.json") or {}
    paired = {}
    for path in sorted(glob.glob(str(run_dir / "paired_*.json"))):
        name = Path(path).stem.replace("paired_", "")
        paired[name] = _load(path) or {}
    log_lines = (Path(pipeline_log).read_text(encoding="utf-8").splitlines()
                 if Path(pipeline_log).exists() else [])

    summary = {
        "schema": "search-distill-gen0-summary-v1",
        "pi0": {
            "selected": (pi0.get("selection") or {}).get("selected"),
            "reason": (pi0.get("selection") or {}).get("reason"),
            "candidates": [
                {"path": row.get("path"),
                 "mean_regret": row.get("mean_reference_regret"),
                 "p95_regret": row.get("p95_reference_regret"),
                 "top1_agreement": row.get("top1_action_agreement"),
                 "latency_p95_ms": (row.get("latency") or {}).get("p95_ms")}
                for row in pi0.get("evaluations", ())],
        },
        "dataset": {
            "rows": dataset.get("count"),
            "dataset_fingerprint": dataset.get("dataset_fingerprint"),
            "coverage_passed": (dataset.get("coverage") or {}).get("passed"),
            "coverage_missing": (dataset.get("coverage") or {}).get("missing"),
            "teacher_status": (dataset.get("report") or {}).get(
                "teacher_status"),
            "special_state_tags": (dataset.get("report") or {}).get(
                "special_state_tags"),
            "generation": (dataset.get("report") or {}).get("generation"),
        },
        "training": {
            "epochs": training.get("history"),
            "usable_rows": training.get("usable_rows"),
            "skipped_rows": training.get("skipped_rows"),
            "model_manifest": training.get("model_manifest"),
            "loss_profile": (training.get("loss_profile")
                             or (training.get("extra") or {}).get("loss_profile")),
            "replay": (training.get("replay")
                       or (training.get("extra") or {}).get("replay")),
            "dataset_fingerprint": training.get("dataset_fingerprint"),
        },
        "offline": {
            "selected": (offline.get("selection") or {}).get("selected"),
            "promoted": (offline.get("selection") or {}).get("promoted"),
            "reason": (offline.get("selection") or {}).get("reason"),
            "checkpoints": [
                {"path": row.get("path"),
                 "mean_regret": row.get("mean_reference_regret"),
                 "p95_regret": row.get("p95_reference_regret"),
                 "catastrophic_rate": row.get("catastrophic_regret_rate"),
                 "top1_agreement": row.get("top1_action_agreement"),
                 "kl": row.get("policy_kl"),
                 "latency_p95_ms": (row.get("latency") or {}).get("p95_ms")}
                for row in offline.get("evaluations", ())],
        },
        "paired": {
            name: {
                "pairs": report.get("pairs"),
                "mean_delta": report.get("mean_delta"),
                "ci95": report.get("ci95"),
                "verdict": report.get("verdict"),
                "secondary": report.get("secondary_diagnostics"),
            }
            for name, report in paired.items()},
        "pipeline_tail": log_lines[-8:],
        "oracle": False,
    }
    return summary


def render_markdown(summary):
    lines = ["# Reduced Gen0 summary", ""]
    pi0 = summary["pi0"]
    lines += ["## pi0", "",
              f"- selected: `{pi0.get('selected')}` ({pi0.get('reason')})",
              "", "| candidate | mean regret | p95 | top1 | p95 ms |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for row in pi0["candidates"]:
        lines.append("| {path} | {m} | {p} | {t} | {l} |".format(
            path=row["path"], m=_fmt(row["mean_regret"]),
            p=_fmt(row["p95_regret"]), t=_fmt(row["top1_agreement"]),
            l=_fmt(row["latency_p95_ms"])))
    dataset = summary["dataset"]
    lines += ["", "## dataset0", "",
              f"- rows: {_fmt(dataset.get('rows'))}",
              f"- fingerprint: `{dataset.get('dataset_fingerprint')}`",
              f"- coverage passed: {dataset.get('coverage_passed')}",
              f"- coverage missing: {dataset.get('coverage_missing')}"]
    training = summary["training"]
    lines += ["", "## training (pi1)", "",
              f"- usable rows: {_fmt(training.get('usable_rows'))}",
              f"- epochs: `{json.dumps(training.get('epochs'), ensure_ascii=False)}`",
              f"- skipped: `{json.dumps(training.get('skipped_rows'), ensure_ascii=False)}`",
              f"- dataset fingerprint: `{training.get('dataset_fingerprint')}`"]
    offline = summary["offline"]
    lines += ["", "## offline selection", "",
              f"- selected: `{offline.get('selected')}` "
              f"(promoted={offline.get('promoted')}, {offline.get('reason')})",
              "", "| checkpoint | mean regret | p95 | catastrophic | top1 | KL | p95 ms |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in offline["checkpoints"]:
        lines.append("| {path} | {m} | {p} | {c} | {t} | {k} | {l} |".format(
            path=row["path"], m=_fmt(row["mean_regret"]),
            p=_fmt(row["p95_regret"]), c=_fmt(row["catastrophic_rate"]),
            t=_fmt(row["top1_agreement"]), k=_fmt(row["kl"]),
            l=_fmt(row["latency_p95_ms"])))
    lines += ["", "## paired games", "",
              "| matrix | pairs | mean Δ | CI95 | verdict |",
              "| --- | ---: | ---: | --- | --- |"]
    for name, row in summary["paired"].items():
        lines.append("| {name} | {pairs} | {mean} | {ci} | {verdict} |".format(
            name=name, pairs=_fmt(row.get("pairs")),
            mean=_fmt(row.get("mean_delta")),
            ci=_fmt(row.get("ci95")), verdict=row.get("verdict") or "pending"))
    if not summary["paired"]:
        lines.append("| (none yet) | | | | pending |")
    lines += ["", "## pipeline tail", "", "```"] + summary["pipeline_tail"] + \
             ["```", ""]
    return "\n".join(lines)


__all__ = ["build_summary", "render_markdown"]
