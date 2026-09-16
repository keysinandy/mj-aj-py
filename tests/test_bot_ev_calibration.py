"""Calibration, evidence, and versioned BC metadata contracts."""

import json
import tempfile
import unittest

import numpy as np

from mj.bc_data import generate_game
from mj.decision.calibration import (
    FEATURE_NAMES, ablation_report, candidate_features, fit_global_linear,
    fit_sparse_lut,
    calibrated_profile, freeze_split_manifest, paired_q_regret,
    paired_q_regret_report, release_gate, split_calibration_artifact,
    evidence_contract, calibration_evidence_manifest,
)
from mj.decision.context import PublicDecisionContext
from mj.decision.report import (associate_counterfactual, compact_evaluation,
                                build_offline_report, sanitize_public)
from mj.decision.profile import ProfileSpec
from scripts import bot_ev_calibrate


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

    def test_candidate_features_prefer_post_discard_state(self):
        values, missing = candidate_features({
            "shanten": 1, "u1": 2, "p1": .1, "visible_unknown": 10,
            "I": 1, "EV1": 2, "EV2": 3, "B": 4, "C": 5,
            "risk": 6, "post_chain": 0, "post_chain_piao": 0,
            "post_locked": 2, "is_piao": False, "discarded_wild": True,
        }, self._context())
        self.assertFalse(missing)
        self.assertEqual(values["chain"], 0)
        self.assertEqual(values["chain_piao"], 0)
        self.assertEqual(values["locked"], 2)
        self.assertEqual(values["feature_provenance"]["chain"],
                         "candidate.post_chain")
        self.assertTrue(values["discarded_wild"])

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

    def test_policy_ablation_requires_explicit_eligibility_provenance(self):
        report = ablation_report(
            [{"EV2": 1.0, "hard_gate_pass": True,
              "action_threshold_pass": False},
             {"EV2": 2.0, "hard_gate_pass": False,
              "action_threshold_pass": True}],
            [3.0, 5.0], feature_names=("EV2",))
        self.assertEqual(
            report["models"]["hard_gate"]["policy"]["status"], "available")
        self.assertEqual(
            report["models"]["hard_gate"]["policy"]["with_policy"]["n"], 1)
        self.assertEqual(
            report["models"]["threshold"]["policy"]["with_policy"]["n"], 1)

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

    def test_paired_q_regret_report_is_clustered_and_signed(self):
        result = paired_q_regret_report([
            {"source_group": "g0", "q": {"legacy": 5, "shape-v1": 4,
                                             "shape-v2": 6, "teacher": 5}},
            {"source_group": "g1", "q": {"legacy": 1, "shape-v1": 3,
                                             "shape-v2": 2, "teacher": 1}},
        ], rounds=20, seed=9)
        self.assertEqual(result["valid_source_groups"], 2)
        self.assertEqual(result["source_groups"], 2)
        self.assertFalse(result["independent_worlds_verified"])
        self.assertTrue(result["counterfactual_evaluation"])
        self.assertFalse(result["online_decision"])
        self.assertFalse(result["oracle"])
        self.assertEqual(result["signed_regret"]["shape-v1"]["values"],
                         [1.0, -2.0])
        self.assertEqual(result["signed_regret"]["shape-v1"]["negative_count"],
                         1)
        self.assertEqual(
            result["paired_policy_delta"]["shape-v2"]["interval"]["clusters"],
            2)

        strict = paired_q_regret_report([
            {"source_group": "g0", "independent_worlds": True,
             "q": {"legacy": 1, "shape-v1": 2, "shape-v2": 2,
                    "teacher": 3}},
            {"source_group": "g1",
             "q": {"legacy": 1, "shape-v1": 2, "shape-v2": 2,
                    "teacher": 3}},
        ], rounds=20, require_independent_worlds=True)
        self.assertEqual(strict["valid_source_groups"], 1)
        self.assertEqual(strict["invalid"][0]["reason"],
                         "independent_worlds_unverified")
        self.assertTrue(strict["independent_worlds_required"])

        invalid = paired_q_regret_report([
            {"source_group": "g0", "strategy": "legacy", "value": 1},
            {"source_group": "g0", "strategy": "legacy", "value": 2},
            {"source_group": "g0", "strategy": "teacher", "value": 2},
        ])
        self.assertEqual(invalid["valid_source_groups"], 0)
        self.assertEqual(invalid["invalid"][0]["reason"],
                         "duplicate_strategy")

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

    def test_split_calibration_fits_train_and_falls_back_sparse_lut(self):
        manifest = freeze_split_manifest(
            train_start=10, train_games=2,
            validation_start=20, validation_games=2,
            final_test_start=30, final_test_games=2)
        rows = []
        for seed, target, bucket in (
                (10, 1.0, ("train",)), (11, 3.0, ("train",)),
                (20, 2.0, ("validation",)), (21, 4.0, ("validation",)),
                (30, 5.0, ("final_test",)), (31, 7.0, ("final_test",))):
            rows.append({"seed": seed, "source_group": f"g{seed}",
                         "features": {"EV2": float(seed - 10)},
                         "target": target, "bucket": bucket})
        artifact = split_calibration_artifact(
            rows, manifest, feature_names=("EV2",),
            min_bucket_samples=3)
        self.assertEqual(artifact["fit_split"], "train")
        self.assertEqual(artifact["rows"],
                         {"train": 2, "validation": 2, "final_test": 2})
        self.assertEqual(artifact["metrics"]["global"]["validation"]["n"], 2)
        self.assertEqual(artifact["sparse_lut"]["buckets"], {})
        self.assertEqual(artifact["ablations"]["models"]["EV2"]
                         ["model"]["feature_names"], [])
        self.assertEqual(
            artifact["ablations"]["models"]["hard_gate"]["policy"]["status"],
            "missing_policy_field")

    def test_evidence_manifest_detects_contract_drift(self):
        profile = ProfileSpec.shape_v2_discard()
        expected = evidence_contract(profile)
        manifest = calibration_evidence_manifest(
            profile,
            calibration_artifact={"contract": expected, "fingerprint": "cal"},
            teacher_artifact={"contract": dict(expected, strategy="old"),
                              "artifact_fingerprint": "teacher"})
        self.assertTrue(manifest["artifacts"]["calibration"]["valid"])
        self.assertFalse(manifest["artifacts"]["teacher"]["valid"])
        self.assertIn("strategy", manifest["artifacts"]["teacher"]["mismatch"])

    def test_calibration_cli_binds_frozen_manifest_and_writes_evidence(self):
        profile = ProfileSpec.shape_v2_discard()
        manifest = freeze_split_manifest(
            train_start=10, train_games=2,
            validation_start=20, validation_games=2,
            final_test_start=30, final_test_games=2,
            profile_fingerprint=profile.fingerprint,
            rule_version=profile.rules_version,
            kernel_version=profile.kernel_version,
            continuation_version=profile.continuation_version,
            strategy="shape-v2")
        rows = []
        for seed, target in ((10, 1.0), (11, 3.0), (20, 2.0),
                             (21, 4.0), (30, 5.0), (31, 7.0)):
            rows.append({
                "seed": seed, "source_group": f"g{seed}",
                "features": {name: float(seed + index)
                             for index, name in enumerate(FEATURE_NAMES)},
                "target": target,
            })
        with tempfile.TemporaryDirectory() as directory:
            input_path = f"{directory}/rows.jsonl"
            manifest_path = f"{directory}/split.json"
            evidence_path = f"{directory}/evidence.json"
            with open(input_path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(json.dumps(row) for row in rows) + "\n")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            artifact = bot_ev_calibrate.run(
                input_path, split_manifest=manifest,
                require_all_splits=True, profile=profile,
                require_contract=True, manifest_output=evidence_path)
            with open(evidence_path, encoding="utf-8") as handle:
                evidence = json.load(handle)
        self.assertEqual(artifact["input_mode"], "frozen_source_splits")
        self.assertTrue(evidence["artifacts"]["calibration"]["valid"])
        self.assertFalse(evidence["artifacts"]["teacher"]["present"])
        self.assertEqual(evidence["contract"]["scope"], "discard")

    def test_strict_evidence_checks_split_and_full_contract(self):
        profile = ProfileSpec.shape_v2_discard()
        split = freeze_split_manifest(
            train_start=10, train_games=1, validation_start=20,
            validation_games=1, final_test_start=30, final_test_games=1,
            profile_fingerprint=profile.fingerprint,
            rule_version=profile.rules_version,
            kernel_version=profile.kernel_version,
            continuation_version=profile.continuation_version,
            strategy="shape-v2")
        contract = evidence_contract(profile, manifest=split,
                                     strategy="shape-v2", scope="discard")
        evidence = calibration_evidence_manifest(
            profile, split_manifest=split, strict=True, require_all=True,
            calibration_artifact={"contract": dict(contract),
                                  "fingerprint": "cal"},
            teacher_artifact={"contract": dict(contract),
                             "artifact_fingerprint": "teacher"},
            score_evidence={"contract": dict(contract),
                            "fingerprint": "score"})
        self.assertTrue(evidence["split_manifest_self_check"]["valid"])
        self.assertTrue(evidence["contract_valid"])
        self.assertTrue(all(item["valid"] for item in
                            evidence["artifacts"].values()))

        drifted = dict(contract, kernel_version="other-kernel")
        invalid = calibration_evidence_manifest(
            profile, split_manifest=split, strict=True,
            calibration_artifact={"contract": drifted})
        self.assertFalse(invalid["contract_valid"])
        self.assertIn("contract_mismatch",
                      invalid["artifacts"]["calibration"]
                      ["invalidated_reasons"])


if __name__ == "__main__":
    unittest.main()
