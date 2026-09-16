#!/usr/bin/env bash
# Staged promotion pipeline for one candidate checkpoint:
#   offline gate -> hard-set gate -> fast paired -> full paired
# Usage:
#   scripts/search_distill_pipeline.sh <candidate.pt> <reference.jsonl> [run_dir]
# Optional environment:
#   BASELINE=heuristic:shape-v2  MATRICES=legacy_shape_v1,self_play
#   SMALL_PAIRS=256  FULL_PAIRS=4096  HARD_REGISTRY=path/to/hard.json
#   LOSS_CATASTROPHIC_LIMIT=0.02  LATENCY_P95_MS=36
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.
PY=.venv/bin/python

CANDIDATE="${1:?candidate checkpoint required}"
REFERENCE="${2:?reference context set required}"
RUN_DIR="${3:-runs/search_bc/gates}"
BASELINE="${BASELINE:-heuristic:shape-v2}"
MATRICES="${MATRICES:-legacy_shape_v1}"
SMALL_PAIRS="${SMALL_PAIRS:-256}"
FULL_PAIRS="${FULL_PAIRS:-4096}"
HARD_REGISTRY="${HARD_REGISTRY:-}"
CATASTROPHIC_LIMIT="${LOSS_CATASTROPHIC_LIMIT:-0.02}"
LATENCY_P95_MS="${LATENCY_P95_MS:-36}"

mkdir -p "$RUN_DIR"
GIT_COMMIT=$($PY -c "import subprocess;print(subprocess.run(['git','rev-parse','HEAD'],capture_output=True,text=True).stdout.strip())")
echo "[gates] candidate=$CANDIDATE commit=$GIT_COMMIT"

$PY scripts/search_bc_eval.py \
  --reference "$REFERENCE" --checkpoint "$CANDIDATE" \
  --out "$RUN_DIR/offline_report.json" \
  --benchmark-samples 32 --benchmark-repeats 2 \
  --catastrophic-limit "$CATASTROPHIC_LIMIT" --latency-p95-ms "$LATENCY_P95_MS" \
  || { echo "[gates] offline evaluation failed mechanically"; exit 2; }
OFFLINE_OK=$($PY -c "import json;print(int(bool(json.load(open('$RUN_DIR/offline_report.json'))['selection']['selected'])))")
[ "$OFFLINE_OK" = "1" ] || { echo "[gates] Gate A failed (offline)"; exit 1; }

if [ -n "$HARD_REGISTRY" ]; then
  if ! $PY - "$CANDIDATE" "$HARD_REGISTRY" "$RUN_DIR/hard_report.json" <<'PYEOF'
import json, sys
from mj.decision.policy_v3 import load_policy_value_model
from mj.training.distillation_profile import SearchDistillationProfile
from mj.training.hard_states import HardStateRegistry, hard_set_evaluation
from pathlib import Path
candidate, registry_path, out = sys.argv[1:4]
registry = HardStateRegistry.load(registry_path)
model = load_policy_value_model(candidate)
report = hard_set_evaluation(model, registry, profile=SearchDistillationProfile())
Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"hard_mean": report.get("mean"), "hard_p95": report.get("p95")}))
PYEOF
  then
    echo "[gates] Gate B failed (hard set)"; exit 1
  fi
fi

if ! $PY - "$CANDIDATE" "$BASELINE" "$MATRICES" "$SMALL_PAIRS" "$RUN_DIR" <<'PYEOF'
import json, sys
from pathlib import Path
from mj.training.distillation_profile import OpponentPopulationProfile
from mj.training.paired_eval import (PairedSchedule, opponent_factory,
                                     paired_score_report, play_pair,
                                     policy_callable)
candidate_path, baseline, matrices, small_pairs, run_dir = sys.argv[1:6]
candidate = policy_callable("checkpoint:" + candidate_path)
base = policy_callable(baseline)
population = OpponentPopulationProfile()
schedule = PairedSchedule(seed_start=240000, games=int(small_pairs),
                          ycbk_variants=(False,))
out = {}
for matrix in matrices.split(","):
    factory = opponent_factory(matrix, candidate=candidate,
                               population=population)
    rows = [play_pair(row, candidate=candidate, baseline=base,
                      opponents=factory(row["cluster"]))
            for row in schedule.rows()]
    report = paired_score_report(rows, required_pairs=int(small_pairs),
                                 rounds=500, seed=7, matrix=matrix)
    out[matrix] = report
Path(run_dir, "fast_paired.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
print(json.dumps({name: rep["verdict"] for name, rep in out.items()}))
PYEOF
then
  echo "[gates] Gate B failed (fast paired)"; exit 1
fi

echo "[gates] candidate passed offline + fast paired; run the full paired gate"
echo "        (scripts/search_bc_paired.py --games \$((FULL_PAIRS/2)) --ycbk both ...)"
exit 0
