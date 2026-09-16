"""Value v2 transform and calibration contract tests (P5)."""

from types import SimpleNamespace
import unittest

import numpy as np

from mj.game import Game
from mj.models.policy_value import ValueTransformContract, value_contract_from_json
from mj.training.policy_value_train import evaluate_value_predictions, regression_metrics
from mj.training.value_contract import (
    ValueCalibrationRequirement,
    calibration_gate,
)


class TestValueTransformContract(unittest.TestCase):
    def test_forward_and_inverse_round_trip(self):
        contract = ValueTransformContract()
        self.assertAlmostEqual(contract.forward(96.0), 1.0)
        self.assertAlmostEqual(contract.forward(-48.0), -0.5)
        self.assertAlmostEqual(contract.forward(10000.0), 1.0)
        self.assertAlmostEqual(contract.forward(-10000.0), -1.0)
        self.assertAlmostEqual(
            contract.inverse_transform_value(contract.forward(72.0)), 72.0)

    def test_fingerprint_and_json_round_trip(self):
        contract = ValueTransformContract()
        loaded = value_contract_from_json(contract.as_json())
        self.assertEqual(loaded.fingerprint, contract.fingerprint)
        self.assertNotEqual(contract.fingerprint,
                            ValueTransformContract(scale=48.0).fingerprint)
        with self.assertRaises(ValueError):
            ValueTransformContract(output_activation="relu")
        with self.assertRaises(ValueError):
            ValueTransformContract(score_units="raw")

    def test_leaf_uses_the_declared_contract(self):
        try:
            import torch
        except ImportError:
            self.skipTest("torch unavailable")

        from mj.search.leaf import ValueNetLeafEvaluator

        class _Model:
            manifest = SimpleNamespace(
                calibrated=True, belief_profile_fingerprint="b",
                search_profile_fingerprint="s",
                feature_contract_fingerprint="f", oracle=False)

            def extract_features(self, context):
                return (np.zeros((79, 34), dtype=np.float32),
                        np.zeros(12, dtype=np.float32))

            def __call__(self, planes, scalars):
                return None, torch.tensor([0.5])

        game = Game(seed=3)
        hero = game.current_seat()
        evaluator = ValueNetLeafEvaluator(_Model())
        result = evaluator.evaluate(game, hero)
        self.assertTrue(result.valid)
        self.assertAlmostEqual(result.reward, 0.5 * 96.0)
        with self.assertRaises(ValueError):
            ValueNetLeafEvaluator(_Model(), value_scale=24.0)

    def test_uncalibrated_model_is_rejected(self):
        from mj.search.leaf import LeafModelMismatch, ValueNetLeafEvaluator

        class _Model:
            manifest = SimpleNamespace(
                calibrated=False, belief_profile_fingerprint="b",
                search_profile_fingerprint="s",
                feature_contract_fingerprint="f", oracle=False)

        with self.assertRaises(LeafModelMismatch):
            ValueNetLeafEvaluator(_Model())


class TestCalibrationGate(unittest.TestCase):
    def _metrics(self, **overrides):
        values = dict(count=100, mae=4.0, rmse=6.0, ranking_accuracy=0.7,
                      bucket_calibration={
                          "-24:-8": {"count": 20, "predicted_mean": -12.0,
                                     "actual_mean": -13.0},
                      })
        values.update(overrides)
        return values

    def test_gate_passes_on_calibrated_metrics(self):
        result = calibration_gate(self._metrics())
        self.assertTrue(result["passed"], result["violations"])
        self.assertTrue(result["fingerprint"])

    def test_gate_rejects_each_violation(self):
        self.assertIn("mae", calibration_gate(self._metrics(mae=50.0))["violations"])
        self.assertIn("rmse",
                      calibration_gate(self._metrics(rmse=90.0))["violations"])
        self.assertIn("ranking_accuracy",
                      calibration_gate(
                          self._metrics(ranking_accuracy=0.1))["violations"])
        self.assertIn("insufficient_count",
                      calibration_gate(self._metrics(count=3))["violations"])
        bad_bucket = {"-24:-8": {"count": 5, "predicted_mean": 0.0,
                                 "actual_mean": -20.0}}
        self.assertIn("bucket:-24:-8",
                      calibration_gate(
                          self._metrics(bucket_calibration=bad_bucket))["violations"])

    def test_regression_metrics_feed_the_gate(self):
        metrics = regression_metrics([1.0, 2.0, 3.0, 4.0] * 8,
                                     [1.5, 2.0, 2.5, 4.0] * 8,
                                     buckets=(-24, 0, 24))
        result = calibration_gate(
            metrics, requirement=ValueCalibrationRequirement(
                max_mae=1.0, max_rmse=2.0, min_ranking_accuracy=0.4,
                max_bucket_error=24.0, min_count=1))
        self.assertTrue(result["passed"], result["violations"])


if __name__ == "__main__":
    unittest.main()
