"""Policy-v3 release manifest, kill switch and workload gates (P9)."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from mj.decision.policy_v3 import PolicyV3Profile, PolicyV3Runtime
from mj.decision.release import (
    ReleaseManifest,
    build_release_manifest,
    load_release_runtime,
    release_manifest_from_json,
    release_gate_report,
    sha256_file,
    write_release_manifest,
)
from mj.game import Game
from mj.models.opponent_policy import ActionDistribution


class _DummyModel:
    manifest = SimpleNamespace(
        oracle=False, calibrated=True, model_version="dummy-v1",
        feature_contract_fingerprint="", belief_profile_fingerprint="",
        search_profile_fingerprint="", fingerprint="dummy-model")

    def __init__(self, probabilities=(0.51, 0.49), fail=False):
        self.probabilities = probabilities
        self.fail = fail

    def predict_game(self, game, seat, legal_actions=None, **kwargs):
        if self.fail:
            raise ValueError("non-finite test output")
        actions = tuple(int(action) for action in legal_actions)
        values = list(self.probabilities) + [0.0] * (len(actions) - 2)
        return ActionDistribution(actions, tuple(values[:len(actions)]),
                                  version="dummy-v1")


class _NonFiniteModel:
    manifest = SimpleNamespace(
        oracle=False, calibrated=True, model_version="nan",
        feature_contract_fingerprint="", belief_profile_fingerprint="",
        search_profile_fingerprint="", fingerprint="nan-model")

    def predict_game(self, game, seat, legal_actions=None, **kwargs):
        actions = tuple(int(action) for action in legal_actions)
        values = [float("nan")] + [0.0] * (len(actions) - 1)
        return SimpleNamespace(actions=actions, probabilities=tuple(values),
                               version="nan-model")


class TestRuntimeStats(unittest.TestCase):
    def test_every_fallback_is_recorded(self):
        runtime = PolicyV3Runtime(
            _DummyModel(fail=True),
            profile=PolicyV3Profile(model_version="dummy-v1",
                                    max_shadow_particles=2))
        game = Game(seed=21)
        runtime.choose(game, game.current_seat(), return_evaluation=True)
        self.assertEqual(runtime.stats["decisions"], 1)
        self.assertEqual(runtime.stats["fallbacks"], 1)
        self.assertEqual(runtime.stats["network"], 0)
        self.assertTrue(runtime.stats["fallback_by_reason"])

    def test_nonfinite_output_is_counted_then_falls_back(self):
        runtime = PolicyV3Runtime(
            _NonFiniteModel(),
            profile=PolicyV3Profile(model_version="nan",
                                    max_shadow_particles=2))
        game = Game(seed=22)
        _, explanation = runtime.choose(game, game.current_seat(),
                                        return_evaluation=True)
        self.assertEqual(runtime.stats["model_output_errors"], 1)
        self.assertEqual(
            runtime.stats["model_output_error_kinds"]["nonfinite_probability"],
            1)
        self.assertEqual(explanation["level"], "shape-v2")

    def test_confidence_threshold_zero_keeps_low_margin_network_action(self):
        runtime = PolicyV3Runtime(
            _DummyModel(probabilities=(0.51, 0.49)),
            profile=PolicyV3Profile(model_version="dummy-v1",
                                    confidence_threshold=0.0,
                                    max_shadow_particles=2))
        game = Game(seed=23)
        _, explanation = runtime.choose(game, game.current_seat(),
                                        return_evaluation=True)
        self.assertEqual(explanation["level"], "network")
        self.assertIsNone(explanation["fallback_reason"])
        self.assertEqual(runtime.stats["network"], 1)
        self.assertEqual(runtime.stats["fallbacks"], 0)


class TestReleaseManifest(unittest.TestCase):
    def _model(self):
        try:
            import torch
        except ImportError:
            self.skipTest("torch unavailable")
        from mj.models.policy_value import PolicyValueNet

        torch.manual_seed(0)
        return PolicyValueNet(blocks=1, width=8)

    def _checkpoint(self, root):
        import torch

        model = self._model()
        path = Path(root) / "policy.pt"
        from mj.training.search_data import SearchDataset
        from mj.training.search_bc_train import (
            SearchBCTrainProfile, save_checkpoint)
        from mj.training.distillation_profile import SearchDistillationProfile

        save_checkpoint(
            path, model, profile=SearchDistillationProfile(),
            train=SearchBCTrainProfile(), dataset=SearchDataset(),
            epoch=1, generation=0, model_version="release-test",
            history=[], blocks=1, width=8)
        from mj.decision.policy_v3 import load_policy_value_model
        return load_policy_value_model(path), path

    def test_active_release_round_trip_and_runtime(self):
        with tempfile.TemporaryDirectory() as root:
            model, checkpoint = self._checkpoint(root)
            manifest = write_release_manifest(
                Path(root) / "release.json", checkpoint=checkpoint, model=model,
                evidence_fingerprint="evidence-1")
            loaded = release_manifest_from_json(
                json.loads((Path(root) / "release.json").read_text()))
            self.assertEqual(loaded.fingerprint, manifest.fingerprint)
            self.assertEqual(loaded.checkpoint_sha256, sha256_file(checkpoint))
            self.assertEqual(loaded.value_mode, "policy-only")
            runtime, active = load_release_runtime(loaded)
            self.assertTrue(active)
            self.assertIsNone(runtime.model_error)
            game = Game(seed=24)
            _, explanation = runtime.choose(game, game.current_seat(),
                                            return_evaluation=True)
            self.assertEqual(explanation["level"], "network")
            gate = release_gate_report(runtime.stats)
            self.assertTrue(gate["passed"], gate["checks"])

    def test_checkpoint_hash_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            model, checkpoint = self._checkpoint(root)
            manifest = build_release_manifest(checkpoint=checkpoint,
                                              model=model)
            Path(checkpoint).write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                load_release_runtime(manifest)

    def test_inactive_release_uses_kill_switch(self):
        with tempfile.TemporaryDirectory() as root:
            model, checkpoint = self._checkpoint(root)
            manifest = build_release_manifest(
                checkpoint=checkpoint, model=model, active=False,
                rollback_checkpoint=str(checkpoint))
            runtime, active = load_release_runtime(manifest)
            self.assertFalse(active)
            game = Game(seed=25)
            _, explanation = runtime.choose(game, game.current_seat(),
                                            return_evaluation=True)
            self.assertEqual(explanation["level"], "shape-v2")
            self.assertGreaterEqual(runtime.stats["fallbacks"], 1)
            gate = release_gate_report(runtime.stats)
            self.assertFalse(gate["passed"])
            self.assertFalse(gate["checks"]["no_fallbacks"])

    def test_value_mode_requires_calibration(self):
        with tempfile.TemporaryDirectory() as root:
            model, checkpoint = self._checkpoint(root)
            with self.assertRaises(ValueError):
                build_release_manifest(checkpoint=checkpoint, model=model,
                                       value_mode="value-v2",
                                       calibration_fingerprint="cal")
            with self.assertRaises(ValueError):
                ReleaseManifest(value_mode="value-v2", calibration_fingerprint="")

    def test_release_gate_flags_illegal_and_emergency(self):
        stats = {"decisions": 10, "illegal_selected": 1}
        gate = release_gate_report(stats)
        self.assertFalse(gate["passed"])
        self.assertFalse(gate["checks"]["illegal_selected"])
        emergency = release_gate_report({
            "decisions": 10, "emergency": 1, "fallbacks": 2})
        self.assertFalse(emergency["passed"])
        self.assertFalse(emergency["checks"]["emergency_fallbacks"])


if __name__ == "__main__":
    unittest.main()
