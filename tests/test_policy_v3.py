import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from mj.belief.events import PublicEvent
from mj.decision.policy_v3 import PolicyV3Profile, PolicyV3Runtime
from mj.game import Game
from mj.models.opponent_policy import ActionDistribution
from mj.platform.recorder import Recorder


class _Model:
    manifest = SimpleNamespace(
        oracle=False, calibrated=True, model_version="policy-test",
        feature_contract_fingerprint="", belief_profile_fingerprint="",
        fingerprint="model-test")

    def __init__(self, fail=False):
        self.fail = fail

    def predict_game(self, game, seat, legal_actions=None, **kwargs):
        if self.fail:
            raise ValueError("non-finite test output")
        actions = tuple(legal_actions)
        return ActionDistribution(
            actions, tuple(1.0 if i == len(actions) - 1 else 0.0
                            for i in range(len(actions))), version="policy-test")


class TestPolicyV3(unittest.TestCase):
    def test_network_is_masked_and_shadow_is_bounded(self):
        game = Game(seed=9)
        runtime = PolicyV3Runtime(
            _Model(),
            profile=PolicyV3Profile(model_version="policy-test",
                                    max_shadow_particles=2),)
        action, explanation = runtime.choose(
            game, game.current_seat(), return_evaluation=True)
        self.assertIn(action, game.legal_actions())
        self.assertEqual(explanation["level"], "network")
        self.assertLessEqual(explanation["belief_ess"], 2)
        self.assertFalse(explanation["search_enabled"])

    def test_model_failure_uses_declared_fallback(self):
        game = Game(seed=10)
        runtime = PolicyV3Runtime(
            _Model(fail=True),
            profile=PolicyV3Profile(model_version="policy-test",
                                    max_shadow_particles=2),)
        action, explanation = runtime.choose(
            game, game.current_seat(), return_evaluation=True)
        self.assertIn(action, game.legal_actions())
        self.assertTrue(explanation["fallback_reason"].startswith("model_error"))
        self.assertEqual(explanation["level"], "shape-v2")

    def test_snapshot_history_is_explicitly_degraded(self):
        runtime = PolicyV3Runtime(profile=PolicyV3Profile(max_shadow_particles=2))
        history = runtime.observe_snapshot(gid="g1")
        self.assertTrue(history.history_incomplete)
        self.assertIn("full_snapshot", history.incomplete_reasons[0])
        history = runtime.observe_event(
            PublicEvent("ROUND_START", actor=0), gid="g1")
        self.assertEqual(len(history.events), 1)
        runtime.reset(gid="g2")
        self.assertIsNone(runtime._observed_history)

    def test_recorder_drops_hidden_explanation_fields(self):
        with tempfile.TemporaryDirectory() as root:
            recorder = Recorder(root=root)
            recorder.meta("g", "bot")
            recorder.decision(
                "g", "draw", [1], 1, 1.0,
                evaluation={
                    "history_hash": "h", "network_confidence": .5,
                    "hidden_hands": [[1]], "particles": [{"wall": [1]}],
                })
            recorder.close_all()
            day = __import__("time").strftime("%Y%m%d")
            path = os.path.join(root, day, "bot_g.jsonl")
            with open(path, encoding="utf-8") as stream:
                rows = [json.loads(line) for line in stream]
            decision = rows[1]
            self.assertEqual(decision["history_hash"], "h")
            self.assertNotIn("hidden_hands", decision["evaluation"])
            self.assertNotIn("particles", decision["evaluation"])


if __name__ == "__main__":
    unittest.main()
