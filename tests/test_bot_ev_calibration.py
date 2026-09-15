"""Calibration, evidence, and versioned BC metadata contracts."""

import unittest

import numpy as np

from mj.bc_data import generate_game
from mj.decision.calibration import (
    FEATURE_NAMES, candidate_features, fit_global_linear, fit_sparse_lut,
    calibrated_profile, freeze_split_manifest, paired_q_regret, release_gate,
)
from mj.decision.context import PublicDecisionContext
from mj.decision.report import (associate_counterfactual, compact_evaluation,
                                build_offline_report, sanitize_public)
from mj.decision.profile import ProfileSpec


class BotEvCalibrationTests(unittest.TestCase):
    def _context(self):
        hand = [0] * 34
        for tile in (0, 1, 2, 0, 1, 2, 0, 1, 2, 3, 3, 3, 4, 4):
            hand[tile] += 1
        return PublicDecisionContext(
            dealer=0, base=1, you_cai_bi_kao=False, hand=hand,
            legal_actions=(0, 1), live_wall=8, chain_count=0, chain_piao=0)

    def test_features_require_explicit_missing_values(self):
        context = self._context()
        with self.assertRaises(ValueError):
            candidate_features({"shanten": 1, "u1": 2, "p1": .1,
                                "visible_unknown": 10}, context)
        values, missing = candidate_features(
            {"shanten": 1, "u1": 2, "p1": .1, "visible_unknown": 10},
            context, allow_missing=True)
        self.assertIn("EV2", missing)
        self.assertEqual(values["EV2_missing"], 1.0)

        values, missing = candidate_features(
            {"shanten": 1, "u1": 2, "p1": .1,
             "visible_unknown": 10, "risk": 7}, context,
            allow_missing=True)
        self.assertEqual(values["risk"], 7)
        self.assertNotIn("risk", missing)

    def test_global_and_sparse_models_are_deterministic(self):
        rows, targets = [], []
        for i in range(8):
            row = {name: float(i + j) for j, name in enumerate(FEATURE_NAMES)}
            rows.append(dict(row, bucket=("small",)))
            targets.append(float(2 * i - 3))
        a = fit_global_linear(rows, targets,
                              constraints={"shanten": (None, 0)})
        b = fit_global_linear(rows, targets,
                              constraints={"shanten": (None, 0)})
        self.assertEqual(a.as_json(), b.as_json())
        lut = fit_sparse_lut(rows, targets, min_bucket_samples=2)
        self.assertEqual(len(lut.bucket_models), 1)
        self.assertEqual(lut.predict(rows[0], ("missing",)),
                         lut.global_model.predict(rows[0]))

    def test_split_and_signed_regret_gate(self):
        manifest = freeze_split_manifest()
        self.assertEqual(manifest["splits"]["train"]["games"], 1024)
        result = paired_q_regret([{
            "source_group": "g0",
            "q": {"legacy": 1, "shape-v1": 2, "shape-v2": 2,
                   "teacher": 1},
        }])
        self.assertEqual(result["regret"]["shape-v1"], [-1.0])
        gate = release_gate([1.0, -1.0], ["a", "b"], required_pairs=4096,
                            rounds=10)
        self.assertFalse(gate["passed"])
        self.assertTrue(gate["legacy_default"])

    def test_evidence_sanitization_and_compaction(self):
        value = sanitize_public({"opponent_hands": [[1]], "wall_order": [2],
                                 "context_hash": "abc", "Q": 1})
        self.assertNotIn("opponent_hands", value)
        self.assertNotIn("wall_order", value)
        evaluation = {"selected": 3, "legacy_best": 1,
                      "candidates": [{"tile": i, "Q": float(i)}
                                     for i in range(8)]}
        compact = compact_evaluation(evaluation, limit=2)
        self.assertEqual(compact["candidate_count"], 8)
        self.assertTrue(compact["candidates_truncated"])
        self.assertIn(3, [x["tile"] for x in compact["candidates"]])
        self.assertIn(1, [x["tile"] for x in compact["candidates"]])

        same = compact_evaluation({"selected": 3, "legacy_best": 3,
                                   "candidates": [
                                       {"tile": i, "Q": float(i)}
                                       for i in range(8)]}, limit=3)
        self.assertEqual(len(same["candidates"]), 4)

    def test_bc_metadata_is_non_oracle_and_missing_teacher_is_nan(self):
        data = generate_game(123)
        self.assertTrue(np.all(data["oracle"] == False))
        self.assertTrue(np.all(data["counterfactual"] == False))
        self.assertTrue(np.isnan(data["teacher_ev"]).all())
        self.assertEqual(len(data["context_hash"]), len(data["action"]))

    def test_counterfactual_join_requires_full_window_identity(self):
        evaluation = {"candidates": [{"tile": 3, "Q": 4.0}]}
        decision = {"type": "decision", "gid": "g1", "round_no": 1,
                    "seq": 7, "id": 2, "input_hash": "ctx",
                    "scope": "discard", "action": 3,
                    "evaluation": evaluation, "identity_status": "authoritative"}
        offline = {"gid": "g2", "round_no": 1, "source_seq": 7,
                   "decision": 2, "input_hash": "ctx", "scope": "discard",
                   "action": 3, "evaluation": evaluation}
        joined = associate_counterfactual([decision], [offline])
        self.assertFalse(joined[0]["actual"]["present"])
        self.assertIsNone(joined[0]["actual"]["regret"])

    def test_offline_report_does_not_cross_gid_on_reused_decision_id(self):
        records = [
            {"type": "decision", "gid": "g1", "id": 1, "seq": 2,
             "action": 3, "legal": [3], "evaluation": {}},
            {"type": "action", "gid": "g2", "decision": 1,
             "ok": True, "status": 200},
        ]
        report = build_offline_report(records)
        self.assertIsNone(report["rows"][0]["submitted"])

    def test_calibrated_profile_owns_omitted_coefficients(self):
        rows = [{"EV2": 0.0}, {"EV2": 2.0}]
        model = fit_global_linear(rows, [1.0, 5.0], feature_names=("EV2",))
        profile = calibrated_profile(ProfileSpec.shape_v2_discard(), model)
        self.assertEqual(profile.q0_shanten_weight, 0.0)
        self.assertEqual(profile.q0_u1_weight, 0.0)
        self.assertEqual(profile.q0_ev1_weight, 0.0)
        self.assertNotEqual(profile.q0_ev2_weight, 0.0)


if __name__ == "__main__":
    unittest.main()
