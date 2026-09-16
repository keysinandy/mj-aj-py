"""Regret-first checkpoint selection tests (P4)."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from mj.models.opponent_policy import ActionDistribution
from mj.training.distillation_profile import SearchDistillationProfile
from mj.training.regret_selection import (
    CheckpointEvaluation,
    evaluate_checkpoint,
    freeze_reference_contexts,
    load_reference_rows,
    reference_split,
    select_best_checkpoint,
    write_selection_report,
)
from mj.training.search_data import SearchSample


def _sample(*, seed=0, q=(1.0, 0.0), tags=("hu",), phase="discard"):
    mask = [True, True] + [False] * 107
    visit = {0: 3, 1: 1}
    return SearchSample(
        context_hash=f"c{seed}", history_hash=f"h{seed}",
        legal_mask=tuple(mask), visit_counts=visit,
        q_by_action={0: q[0], 1: q[1]}, root_value=1.0,
        simulations=4096, ambiguous=False, confidence=1.0,
        source_group=f"game:{240000 + seed}", belief_fingerprint="b",
        search_fingerprint="s", opponent_policy_version="o",
        leaf_version="terminal-rollout-v1", teacher_status="ok",
        teacher_q_gap=abs(q[0] - q[1]), special_state_tags=tags,
        phase=phase,
        planes=np.random.RandomState(seed).rand(79, 34).astype(np.float32),
        scalars=np.random.RandomState(1000 + seed).rand(12).astype(np.float32))


def _row(sample, *, context=None, history=None):
    return {"schema": "search-reference-context-v1",
            "source_group": sample.source_group,
            "generation": 0, "policy_version_source": "pi0",
            "context": context, "history": history,
            "sample": sample.as_json()}


class _Model:
    """Always picks flat action 1 with a soft distribution."""

    def to(self, device):
        return self

    def eval(self):
        return self

    def select_action(self, planes, scalars, mask):
        return 1

    def policy_distribution(self, planes, scalars, mask):
        return ActionDistribution((0, 1), (0.25, 0.75),
                                  version="ref-test")

    def extract_features(self, context, history=None, belief=None):
        return np.zeros((79, 34), dtype=np.float32), np.zeros(12,
                                                              dtype=np.float32)


class TestReferenceSet(unittest.TestCase):
    def test_reference_rows_must_be_outside_training_groups(self):
        rows = [_row(_sample(seed=0)), _row(_sample(seed=1))]
        assignments = {"game:240000": "train", "game:240001": "validation"}
        selected, rejected = reference_split(rows, assignments)
        self.assertEqual([row["source_group"] for row in selected],
                         ["game:240001"])
        self.assertEqual(rejected[0]["split"], "train")
        with self.assertRaises(ValueError):
            freeze_reference_contexts(rows, assignments)
        frozen = freeze_reference_contexts(
            [rows[1]], {"game:240001": "final-test"})
        self.assertEqual(frozen["count"], 1)
        self.assertTrue(frozen["fingerprint"])


class TestEvaluateCheckpoint(unittest.TestCase):
    def test_regret_kl_and_agreement_are_reported(self):
        rows = [_row(_sample(seed=index)) for index in range(4)]
        evaluation = evaluate_checkpoint(
            _Model(), rows, profile=SearchDistillationProfile())
        self.assertEqual(evaluation.count, 4)
        self.assertAlmostEqual(evaluation.mean_reference_regret, 1.0)
        self.assertEqual(evaluation.top1_action_agreement, 0.0)
        self.assertEqual(evaluation.catastrophic_regret_rate, 0.0)
        self.assertIsNotNone(evaluation.policy_kl)
        self.assertIn("tag:hu", evaluation.bucket_regret)
        self.assertIn("phase:discard", evaluation.bucket_regret)
        self.assertIn("forced", evaluation.skipped)

    def test_catastrophic_threshold_counts_high_regret(self):
        rows = [_row(_sample(seed=index)) for index in range(4)]
        evaluation = evaluate_checkpoint(
            _Model(), rows, profile=SearchDistillationProfile(),
            catastrophe_threshold=0.5)
        self.assertAlmostEqual(evaluation.catastrophic_regret_rate, 1.0)

    def test_batch1_benchmark_uses_stored_features(self):
        rows = [_row(_sample(seed=index)) for index in range(4)]
        evaluation = evaluate_checkpoint(
            _Model(), rows, profile=SearchDistillationProfile(),
            benchmark_samples=2, benchmark_repeats=2)
        self.assertIsNotNone(evaluation.latency["p95_ms"])
        self.assertGreater(evaluation.latency["count"], 0)

    def test_forced_and_missing_q_rows_are_skipped(self):
        forced = SearchSample(
            context_hash="cf", history_hash="hf",
            legal_mask=tuple([True] + [False] * 108), visit_counts={0: 1},
            q_by_action={}, root_value=None, simulations=0, ambiguous=False,
            confidence=None, source_group="g", belief_fingerprint="b",
            search_fingerprint="s", opponent_policy_version="o",
            leaf_version="terminal-rollout-v1", teacher_status="forced-sanity",
            forced=True, planes=np.zeros((79, 34), dtype=np.float32),
            scalars=np.zeros(12, dtype=np.float32))
        rows = [_row(forced), {"source_group": "g", "context": None,
                               "history": None,
                               "sample": _sample().as_json()}]
        evaluation = evaluate_checkpoint(
            _Model(), rows, profile=SearchDistillationProfile())
        self.assertEqual(evaluation.count, 1)
        self.assertEqual(evaluation.skipped["forced"], 1)


class TestSelection(unittest.TestCase):
    def _evaluation(self, path, mean, p95, catastrophic=0.0,
                    latency=10.0):
        return CheckpointEvaluation(
            path=path, mean_reference_regret=mean,
            p50_reference_regret=mean, p95_reference_regret=p95,
            catastrophic_regret_rate=catastrophic, policy_kl=0.1,
            top1_action_agreement=0.5, count=100, skipped={},
            bucket_regret={}, latency={"p95_ms": latency})

    def test_lowest_mean_wins_and_accuracy_is_diagnostic(self):
        selection = select_best_checkpoint([
            self._evaluation("accurate.pt", 2.0, 4.0),
            self._evaluation("regret.pt", 1.0, 5.0),
        ])
        self.assertEqual(selection["selected"], "regret.pt")
        self.assertTrue(selection["promoted"])

    def test_p95_and_catastrophic_gates_reject(self):
        selection = select_best_checkpoint([
            self._evaluation("spiky.pt", 0.5, 200.0),
            self._evaluation("stable.pt", 2.0, 4.0),
        ], p95_limit=24.0, catastrophic_limit=0.02)
        self.assertEqual(selection["selected"], "stable.pt")
        selection = select_best_checkpoint([
            self._evaluation("cat.pt", 0.1, 1.0, catastrophic=0.5),
            self._evaluation("stable.pt", 2.0, 4.0),
        ], catastrophic_limit=0.02)
        self.assertEqual(selection["selected"], "stable.pt")

    def test_regression_against_previous_blocks_promotion(self):
        selection = select_best_checkpoint([
            self._evaluation("candidate.pt", 1.5, 3.0),
        ], previous_mean=1.0, previous_p95=3.0)
        self.assertEqual(selection["selected"], "candidate.pt")
        self.assertFalse(selection["promoted"])
        self.assertEqual(selection["reason"], "reference_regret_not_improved")

    def test_latency_gate_and_report_round_trip(self):
        selection = select_best_checkpoint([
            self._evaluation("slow.pt", 0.1, 1.0, latency=500.0),
        ], latency_p95_ms=36.0)
        self.assertIsNone(selection["selected"])
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "report.json"
            report = write_selection_report(
                path, evaluations=[self._evaluation("a.pt", 1.0, 2.0)],
                selection=selection)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["fingerprint"], report["fingerprint"])

    def test_load_reference_rows_round_trip(self):
        rows = [_row(_sample(seed=0))]
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "reference.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows),
                            encoding="utf-8")
            loaded = load_reference_rows(path)
        self.assertEqual(loaded[0]["source_group"], "game:240000")
        self.assertEqual(loaded[0]["sample"]["context_hash"], "c0")


if __name__ == "__main__":
    unittest.main()
