"""Regret-aware active distillation tests (active sampling / replay / cache)."""

import unittest
import tempfile
from dataclasses import replace
from pathlib import Path

import numpy as np

from mj.training.active_sampling import (
    ActiveSamplingProfile,
    CandidateState,
    active_sampling_profile_from_json,
    classify_candidate,
    pool_manifest,
    read_candidate_pool,
    sample_pool,
    write_candidate_pool,
)
from mj.training.hard_states import (
    HardStateEntry,
    HardStateRegistry,
    hard_set_regression,
)
from mj.training.regret_training import (
    RegretAwareLossProfile,
    regret_aware_policy_value_loss,
    regret_aware_weight,
)
from mj.training.replay_buffer import ReplayBuffer, ReplayProfile
from mj.training.search_data import SearchDataset, SearchSample, state_identity
from mj.training.teacher_cache import TeacherCache
from mj.search.report import SearchResult


def _candidate(seed=0, *, source_group="game:1", action=0, probs=None,
               tags=(), q=None, cheap_action=None, previously_hard=False):
    mask = tuple([True, True] + [False] * 107)
    return CandidateState(
        state_id=state_identity(f"c{seed}", f"h{seed}"),
        source_group=source_group, generation=0,
        policy_version_source="pi0", context={"seed": seed},
        history=None, legal_mask=mask,
        planes=np.zeros((79, 34), dtype=np.float32),
        scalars=np.zeros(12, dtype=np.float32),
        policy_action=action,
        policy_prob_by_action=probs or {0: 0.6, 1: 0.4},
        special_state_tags=tags, state_source="normal")


class TestActiveSamplingProfile(unittest.TestCase):
    def test_fingerprint_and_validation(self):
        profile = ActiveSamplingProfile()
        loaded = active_sampling_profile_from_json(profile.as_json())
        self.assertEqual(loaded.fingerprint, profile.fingerprint)
        with self.assertRaises(ValueError):
            ActiveSamplingProfile(ratios=(("normal", 1.0),))
        with self.assertRaises(ValueError):
            ActiveSamplingProfile(ratios=(("normal", 0.5), ("disagreement", 0.3),
                                          ("hard", 0.1), ("special", 0.05),
                                          ("random", 0.04)))

    def test_classification_rules(self):
        profile = ActiveSamplingProfile()
        # expensive mistake -> hard
        source, importance, regret = classify_candidate(
            legal_mask=[True, True] + [False] * 107, policy_action=1,
            policy_prob_by_action={0: 0.1, 1: 0.9},
            q_by_action={0: 20.0, 1: 0.0}, profile=profile)
        self.assertEqual(source, "hard")
        self.assertEqual(regret, 20.0)
        # genuine disagreement -> disagreement
        source, _, _ = classify_candidate(
            legal_mask=[True, True] + [False] * 107, policy_action=1,
            policy_prob_by_action={0: 0.1, 1: 0.9},
            q_by_action={0: 3.0, 1: 0.0}, profile=profile)
        self.assertEqual(source, "disagreement")
        # special tag
        source, _, _ = classify_candidate(
            legal_mask=[True, True] + [False] * 107, policy_action=0,
            policy_prob_by_action={0: 0.9, 1: 0.1},
            q_by_action={0: 3.0, 1: 0.0}, special_tags=("hu",),
            critical_tags=("hu",), profile=profile)
        self.assertEqual(source, "special")
        # forced
        source, _, _ = classify_candidate(
            legal_mask=[True] + [False] * 108, policy_action=0,
            policy_prob_by_action={0: 1.0}, profile=profile)
        self.assertEqual(source, "forced")

    def test_sampling_is_deterministic_and_ratio_aware(self):
        pool = []
        for index in range(10):
            pool.append(_candidate(index, q={0: 1.0, 1: 0.9}))
        for index in range(10, 20):
            pool.append(replace(_candidate(index), state_source="hard",
                                policy_regret=20.0))
        for index in range(20, 30):
            pool.append(replace(_candidate(index),
                                state_source="disagreement",
                                policy_regret=2.0))
        for index in range(30, 40):
            pool.append(replace(_candidate(index), state_source="special",
                                special_state_tags=("hu",)))
        profile = ActiveSamplingProfile()
        first = sample_pool(pool, batch_size=20, profile=profile, seed=3)
        second = sample_pool(pool, batch_size=20, profile=profile, seed=3)
        self.assertEqual([item.state_id for item in first],
                         [item.state_id for item in second])
        self.assertEqual(len(first), 20)
        by_source = {}
        for item in first:
            by_source[item.state_source] = by_source.get(item.state_source, 0) + 1
        self.assertGreaterEqual(by_source["hard"], 4)
        self.assertGreaterEqual(by_source["special"], 3)
        self.assertGreaterEqual(by_source["normal"], 1)

    def test_short_bucket_is_redistributed(self):
        pool = [_candidate(index, q={0: 1.0, 1: 0.9})
                for index in range(10)]
        selected = sample_pool(pool, batch_size=8,
                               profile=ActiveSamplingProfile(), seed=0)
        self.assertEqual(len(selected), 8)
        self.assertEqual(len({item.state_id for item in selected}), 8)

    def test_candidate_pool_round_trip(self):
        pool = [_candidate(0), _candidate(1)]
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "pool.jsonl"
            write_candidate_pool(path, pool)
            loaded = read_candidate_pool(path)
        self.assertEqual([item.state_id for item in loaded],
                         [item.state_id for item in pool])
        manifest = pool_manifest(pool, profile=ActiveSamplingProfile())
        self.assertEqual(manifest["count"], 2)
        self.assertFalse(manifest["oracle"])


class TestRegretAwareLoss(unittest.TestCase):
    def test_profile_validation_and_weights(self):
        profile = RegretAwareLossProfile()
        self.assertEqual(profile.fingerprint,
                         RegretAwareLossProfile().fingerprint)
        with self.assertRaises(ValueError):
            RegretAwareLossProfile(ranking_mode="all_pairs")
        with self.assertRaises(ValueError):
            RegretAwareLossProfile(policy_weight=0, ranking_weight=0)
        sample = _sample(q_gap=0.0, regret=None)
        self.assertAlmostEqual(regret_aware_weight(sample, profile), 0.5)
        sample = _sample(q_gap=8.0, regret=24.0)
        self.assertGreater(regret_aware_weight(sample, profile), 1.0)
        extreme = replace(sample, importance_factor=99.0)
        self.assertLessEqual(regret_aware_weight(extreme, profile),
                             profile.weight_max)

    def test_ranking_and_catastrophic_terms(self):
        try:
            import torch
        except ImportError:
            self.skipTest("torch unavailable")
        logits = torch.tensor([[2.0, 1.0] + [-10.0] * 107], dtype=torch.float32)
        mask = torch.tensor([[True, True] + [False] * 107])
        q_values = torch.zeros(1, 109)
        q_values[0, 0], q_values[0, 1] = 20.0, 0.0
        q_valid = torch.zeros(1, 109, dtype=torch.bool)
        q_valid[0, 0] = q_valid[0, 1] = True
        profile = RegretAwareLossProfile()
        loss, _ = regret_aware_policy_value_loss(
            logits, torch.zeros(1), torch.tensor([[1.0, 0.0] + [0.0] * 107]),
            torch.zeros(1), mask, q_values=q_values, q_valid=q_valid,
            profile=profile)
        self.assertTrue(torch.isfinite(loss))
        # Flipping the logits to prefer the bad action must increase the loss.
        bad_logits = torch.tensor([[1.0, 2.0] + [-10.0] * 107],
                                  dtype=torch.float32)
        bad_loss, _ = regret_aware_policy_value_loss(
            bad_logits, torch.zeros(1), torch.tensor([[1.0, 0.0] + [0.0] * 107]),
            torch.zeros(1), mask, q_values=q_values, q_valid=q_valid,
            profile=profile)
        self.assertGreater(float(bad_loss), float(loss))
        disabled = regret_aware_policy_value_loss(
            bad_logits, torch.zeros(1), torch.tensor([[1.0, 0.0] + [0.0] * 107]),
            torch.zeros(1), mask, q_values=q_values, q_valid=q_valid,
            profile=RegretAwareLossProfile(catastrophic_weight=0.0))
        self.assertNotIn("catastrophic", disabled[1])


class TestReplayBuffer(unittest.TestCase):
    def test_buckets_and_deterministic_batch(self):
        old = SearchDataset([_sample(seed=index, generation=0)
                             for index in range(6)])
        new = SearchDataset([_sample(seed=10 + index, generation=1)
                             for index in range(6)])
        hard = SearchDataset([replace(_sample(seed=20, generation=1),
                                      policy_regret=30.0)])
        special = SearchDataset([replace(_sample(seed=30, generation=1),
                                         special_state_tags=("hu",))])
        buffer = ReplayBuffer(ReplayProfile())
        buffer.add_generation(old, generation=1)
        buffer.add_generation(new, generation=1)
        buffer.add_generation(hard, generation=1)
        buffer.add_generation(special, generation=1)
        manifest = buffer.manifest()
        self.assertEqual(manifest["counts"]["recent"], 8)
        self.assertEqual(manifest["counts"]["historical"], 6)
        self.assertEqual(manifest["counts"]["hard"], 1)
        self.assertEqual(manifest["counts"]["special"], 1)
        first = buffer.sample_batch(size=8, seed=5)
        second = buffer.sample_batch(size=8, seed=5)
        self.assertEqual([sample.work_id for _, sample in first],
                         [sample.work_id for _, sample in second])
        self.assertEqual(len(first), 8)

    def test_split_filter_and_reservoir_cap(self):
        dataset = SearchDataset([_sample(seed=index) for index in range(4)])
        buffer = ReplayBuffer(ReplayProfile(historical_cap=2))
        added = buffer.add_generation(
            dataset, generation=1,
            assignments={"game:240000": "train", "game:240001": "validation"})
        self.assertGreaterEqual(added, 1)
        for index in range(10):
            buffer.add_generation(
                SearchDataset([_sample(seed=100 + index, generation=0)]),
                generation=2)
        self.assertLessEqual(len(buffer.buckets["historical"]), 2)


class TestHardStates(unittest.TestCase):
    def test_dedupe_and_regression_report(self):
        registry = HardStateRegistry()
        sample = _sample(seed=0, regret=30.0, tags=("kong-add",))
        registry.add_sample(sample, trigger="catastrophic_action")
        registry.add_sample(sample, trigger="special_failure",
                            failure_tag="kong-add")
        self.assertEqual(len(registry), 1)
        entry = next(iter(registry))
        self.assertIn("kong-add", entry.failure_tag)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "hard.json"
            registry.save(path)
            loaded = HardStateRegistry.load(path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded.failure_modes(), registry.failure_modes())

    def test_auto_registration_from_dataset(self):
        dataset = SearchDataset([
            _sample(seed=0, regret=30.0, tags=("hu",)),
            _sample(seed=1, regret=1.0),
        ])
        registry = HardStateRegistry()
        added = registry.add_from_dataset(dataset, regret_threshold=24.0)
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].trigger, "catastrophic_action")

    def test_regression_report_marks_fixed_and_regressed(self):
        previous = {"mean": 10.0, "p95": 20.0, "catastrophic_regret_rate": 0.2,
                    "rows": [{"context_hash": "a", "regret": 10.0},
                             {"context_hash": "b", "regret": 30.0}]}
        current = {"mean": 8.0, "p95": 18.0, "catastrophic_regret_rate": 0.1,
                   "rows": [{"context_hash": "a", "regret": 5.0},
                            {"context_hash": "b", "regret": 40.0}]}
        report = hard_set_regression(previous, current)
        self.assertTrue(report["passed"])
        self.assertEqual(len(report["fixed"]), 1)
        self.assertEqual(len(report["regressed"]), 1)
        worse = dict(current)
        worse["p95"] = 50.0
        self.assertFalse(hard_set_regression(previous, worse)["passed"])


class TestTeacherCache(unittest.TestCase):
    def _result(self, simulations=2048):
        return SearchResult(
            status="ok", context_hash="c", history_hash="h",
            belief_fingerprint="b", search_profile_fingerprint="s",
            root_key="k", legal_actions=(0, 1), best_action=0,
            visit_policy={0: 0.8, 1: 0.2}, q_by_action={0: 2.0, 1: 1.0},
            visit_counts={0: 8, 1: 2}, variance_by_action={0: 0.1, 1: 0.2},
            simulations=simulations, requested_simulations=simulations,
            terminal_simulations=simulations, leaf_simulations=0,
            failed_simulations=0, max_depth=10, incomplete_budget=False,
            ambiguous=False, root_value=1.5)

    def test_reuse_rule_and_persistence(self):
        with tempfile.TemporaryDirectory() as root:
            cache = TeacherCache.load_dir(root)
            cache.open_shard(root)
            self.assertIsNone(cache.lookup(
                state_hash="st", teacher_version="v1",
                teacher_config_hash="cfg", requested_simulations=512))
            cache.store(state_hash="st", teacher_version="v1",
                        teacher_config_hash="cfg", requested_simulations=2048,
                        result=self._result(2048))
            self.assertIsNotNone(cache.lookup(
                state_hash="st", teacher_version="v1",
                teacher_config_hash="cfg", requested_simulations=512))
            self.assertIsNone(cache.lookup(
                state_hash="st", teacher_version="v1",
                teacher_config_hash="cfg", requested_simulations=8192))
            self.assertIsNone(cache.lookup(
                state_hash="st", teacher_version="v2",
                teacher_config_hash="cfg", requested_simulations=512))
            cache.flush()
            reloaded = TeacherCache.load_dir(root)
            self.assertEqual(len(reloaded), 1)


def _sample(seed=0, *, generation=0, regret=None, tags=(), q_gap=4.0):
    mask = (True, True) + (False,) * 107
    return SearchSample(
        context_hash=f"c{seed}", history_hash=f"h{seed}",
        legal_mask=mask, visit_counts={0: 3, 1: 1},
        q_by_action={0: 2.0, 1: 1.0}, root_value=1.0, simulations=2048,
        ambiguous=False, confidence=1.0, source_group="game:240000",
        belief_fingerprint="b", search_fingerprint="s",
        opponent_policy_version="o", leaf_version="terminal-rollout-v1",
        teacher_status="ok", teacher_q_gap=q_gap, generation=generation,
        special_state_tags=tags, policy_regret=regret,
        planes=np.zeros((79, 34), dtype=np.float32),
        scalars=np.zeros(12, dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
