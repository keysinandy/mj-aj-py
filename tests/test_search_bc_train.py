"""Search-BC trainer contract tests (P3)."""

import json
import unittest
from dataclasses import replace
import tempfile
from pathlib import Path

import numpy as np

from mj.training.distillation_profile import SearchDistillationProfile
from mj.training.policy_value_train import (
    is_usable_sample,
    search_policy_target,
    unit_safe_weight,
)
from mj.training.search_bc_train import (
    SearchBCTrainProfile,
    augment_parity_row,
    build_training_rows,
    train_search_bc,
)
from mj.training.search_data import SearchDataset, SearchSample


def _sample(*, status="ok", simulations=4096, ambiguous=False, reset=0,
            visits=(3, 1), q=(0.0, -4.0), q_gap=4.0, variance=1.0,
            source_group="g", work_seed=0, planes=True):
    mask = [True, True] + [False] * 107
    return SearchSample(
        context_hash=f"c{work_seed}", history_hash=f"h{work_seed}",
        legal_mask=tuple(mask), visit_counts={0: visits[0], 1: visits[1]},
        q_by_action={0: q[0], 1: q[1]}, root_value=1.0,
        simulations=simulations, ambiguous=ambiguous, confidence=1.0,
        source_group=source_group, belief_fingerprint="b",
        search_fingerprint="s", opponent_policy_version="o",
        leaf_version="terminal-rollout-v1", teacher_status=status,
        teacher_q_gap=q_gap, search_variance=variance, reset_count=reset,
        teacher_seed=work_seed,
        planes=(np.random.RandomState(work_seed).rand(79, 34).astype(np.float32)
                if planes else None),
        scalars=(np.random.RandomState(100 + work_seed).rand(12).astype(np.float32)
                 if planes else None))


def _profile(**overrides):
    values = dict(generation=1, target_mode="visit",
                  belief_profile_fingerprint="bp",
                  search_profile_fingerprint="sp",
                  full_evidence_simulations=2048)
    values.update(overrides)
    return SearchDistillationProfile(**values)


class TestPolicyTargets(unittest.TestCase):
    def test_visit_distribution_is_normalized(self):
        sample = _sample()
        target = search_policy_target(sample, _profile())
        self.assertAlmostEqual(target[0], 0.75)
        self.assertAlmostEqual(target[1], 0.25)
        self.assertAlmostEqual(sum(target.values()), 1.0)

    def test_q_soft_uses_versioned_temperature(self):
        sample = _sample(q=(0.0, -4.0))
        target = search_policy_target(
            sample, _profile(target_mode="q-soft", lambda_visit=0.0,
                             lambda_q=1.0, tau_q=4.0))
        self.assertAlmostEqual(target[0] / target[1], np.e, places=6)

    def test_hybrid_mixes_both_targets(self):
        sample = _sample()
        target = search_policy_target(
            sample, _profile(target_mode="hybrid", lambda_visit=0.7,
                             lambda_q=0.3, tau_q=4.0))
        visit = 0.75
        q_soft = 1.0 / (1.0 + np.exp(-1.0))
        self.assertAlmostEqual(target[0], 0.7 * visit + 0.3 * q_soft, places=6)

    def test_q_soft_requires_complete_q_evidence(self):
        sample = replace(_sample(), q_by_action={0: 1.0})
        with self.assertRaises(ValueError):
            search_policy_target(
                sample, _profile(target_mode="q-soft", lambda_visit=0.0,
                                 lambda_q=1.0))


class TestUnitSafeWeight(unittest.TestCase):
    def test_raw_variance_does_not_suppress_without_declared_scale(self):
        quiet = _sample(variance=0.0)
        volatile = _sample(variance=1e9)
        profile = _profile()
        self.assertEqual(unit_safe_weight(quiet, profile),
                         unit_safe_weight(volatile, profile))

    def test_declared_scale_softens_but_never_zeroes(self):
        profile = _profile(variance_scale=24.0)
        volatile = _sample(variance=24.0 * 999.0)
        weight = unit_safe_weight(volatile, profile)
        self.assertGreater(weight, 0.0)
        self.assertLess(weight, unit_safe_weight(_sample(variance=0.0),
                                                 profile))

    def test_ambiguity_reset_and_importance_factors(self):
        profile = _profile()
        base = unit_safe_weight(_sample(), profile)
        self.assertAlmostEqual(unit_safe_weight(_sample(ambiguous=True), profile),
                               base * 0.5)
        self.assertAlmostEqual(unit_safe_weight(_sample(reset=2), profile),
                               base * 0.25)
        small_gap = unit_safe_weight(_sample(q_gap=0.05), profile)
        self.assertAlmostEqual(small_gap, base * 0.25)

    def test_failed_or_empty_search_is_not_usable(self):
        self.assertFalse(is_usable_sample(_sample(status="failed")))
        self.assertFalse(is_usable_sample(_sample(status="unsupported")))
        self.assertFalse(is_usable_sample(_sample(simulations=0)))


class TestTrainingRowsAndRows(unittest.TestCase):
    def test_rows_skip_reasons_are_audited(self):
        dataset = SearchDataset([
            _sample(work_seed=0),
            _sample(work_seed=1, status="failed"),
            _sample(work_seed=2, status="unsupported"),
            _sample(work_seed=3, simulations=0, status="ok"),
            replace(_sample(work_seed=4), q_by_action={0: 1.0}),
            _sample(work_seed=5, planes=False),
        ])
        rows = build_training_rows(
            dataset, profile=_profile(target_mode="q-soft",
                                      lambda_visit=0.0, lambda_q=1.0),
            train=SearchBCTrainProfile())
        self.assertEqual(len(rows.rows), 1)
        self.assertEqual(rows.skipped["status"], 3)
        self.assertEqual(rows.skipped["missing_q"], 1)
        self.assertEqual(rows.skipped["features"], 1)
        ordered = [row.work_id for row in rows.rows]
        self.assertEqual(ordered, sorted(ordered))

    def test_suit_augmentation_parity(self):
        from mj.features import SUIT_PERMS

        profile = _profile()
        rows = build_training_rows(
            SearchDataset([_sample()]), profile=profile,
            train=SearchBCTrainProfile())
        row = rows.rows[0]
        variants = augment_parity_row(row)
        self.assertEqual(len(variants), 6)
        self.assertTrue(np.array_equal(variants[0].planes, row.planes))
        for (q_index, action_map), variant in zip(SUIT_PERMS, variants):
            self.assertTrue(np.array_equal(
                variant.mask[action_map], row.mask))
            self.assertTrue(np.allclose(
                variant.target[action_map], row.target))
            self.assertTrue(np.allclose(
                variant.q_values[action_map], row.q_values))
            # Hand planes move with the same sigma as the discard action.
            for plane in range(4):
                self.assertTrue(np.array_equal(
                    variant.planes[plane][action_map[:34]],
                    row.planes[plane][:34]))
            self.assertTrue(np.array_equal(variant.scalars, row.scalars))
            self.assertEqual(variant.value, row.value)
            self.assertEqual(variant.weight, row.weight)


class TestSearchBCTraining(unittest.TestCase):
    def setUp(self):
        try:
            import torch  # noqa: F401
        except ImportError:
            self.skipTest("torch unavailable")

    def test_every_epoch_checkpoint_has_provenance_and_loads(self):
        from mj.decision.policy_v3 import load_policy_value_model
        from mj.models.policy_value import PolicyValueNet

        dataset = SearchDataset([_sample(work_seed=index) for index in range(4)])
        profile = _profile()
        train = SearchBCTrainProfile(epochs=2, batch_size=2, lr=1e-3,
                                     seed=3, augmentation="suit")
        model = PolicyValueNet(blocks=1, width=8)
        with tempfile.TemporaryDirectory() as root:
            history, rows = train_search_bc(
                model, dataset, profile=profile, train=train,
                output_dir=root, generation=1, model_version="test-bc",
                blocks=1, width=8)
            self.assertEqual(len(history), 2)
            for record in history:
                self.assertTrue(np.isfinite(record["loss"]))
            checkpoint = Path(root) / "epoch_002.pt"
            self.assertTrue(checkpoint.exists())
            loaded = load_policy_value_model(checkpoint)
            self.assertFalse(loaded.manifest.calibrated)
            self.assertFalse(loaded.manifest.oracle)
            import torch
            raw = torch.load(checkpoint, map_location="cpu",
                             weights_only=True)
            provenance = raw["provenance"]
            self.assertEqual(provenance["generation"], 1)
            self.assertEqual(provenance["distillation_profile"]["fingerprint"],
                             profile.fingerprint)
            self.assertEqual(provenance["train_profile"]["fingerprint"],
                             train.fingerprint)
            self.assertFalse(provenance["oracle"])
            self.assertEqual(raw["manifest"]["model_version"], "test-bc")

    def test_non_suit_augmentation_is_byte_stable(self):
        from mj.models.policy_value import PolicyValueNet
        import torch

        dataset = SearchDataset([_sample(work_seed=index) for index in range(4)])
        profile = _profile()
        train = SearchBCTrainProfile(epochs=1, batch_size=4, lr=1e-3,
                                     seed=7, augmentation="none")
        with tempfile.TemporaryDirectory() as root:
            torch.manual_seed(11)
            first = PolicyValueNet(blocks=1, width=8)
            history_a, _ = train_search_bc(
                first, dataset, profile=profile, train=train,
                output_dir=root, generation=0, model_version="det",
                blocks=1, width=8)
            torch.manual_seed(11)
            second = PolicyValueNet(blocks=1, width=8)
            history_b, _ = train_search_bc(
                second, dataset, profile=profile, train=train,
                output_dir=root, generation=0, model_version="det",
                blocks=1, width=8)
        self.assertEqual(history_a, history_b)


if __name__ == "__main__":
    unittest.main()
