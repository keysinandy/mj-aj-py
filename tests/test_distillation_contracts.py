"""Search-teacher distillation contract tests (P0/P2)."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from mj.game import Game
from mj.training import (
    BASELINE_GIT,
    RULES_VERSION,
    FROZEN_SPLITS,
    OpponentPopulationProfile,
    PolicyCandidate,
    SearchDataset,
    SearchDistillationProfile,
    SearchSample,
    TeacherBudgetProfile,
    TeacherEvidence,
    baseline_freeze,
    coverage_gate,
    dataset_report,
    duplicate_report,
    feature_fingerprint,
    forced_decision,
    merge_split_assignments,
    next_decision,
    run_decision,
    search_distillation_profile_from_json,
    select_strongest_fast_policy,
    special_state_tags,
    start_decision,
    teacher_budget_profile_from_json,
    validate_split_integrity,
    verify_feature_fingerprints,
    write_baseline_freeze,
)


def _sample(group="g", *, context="c", tags=(), forced=False, status="ok",
            simulations=2048, tier=1, generation=0, planes=None, scalars=None,
            q=None, q_gap=None):
    mask = [True] + [False] * 108
    if not forced:
        mask[1] = True
    resolved_q = q or ({0: 1.0} if forced else {0: 1.0, 1: 0.5})
    ranked = sorted(resolved_q.values(), reverse=True)
    top1 = ranked[0]
    top2 = ranked[1] if len(ranked) > 1 else None
    q_gap = top1 - top2 if top2 is not None else q_gap
    return SearchSample(
        context_hash=context, history_hash="h", legal_mask=tuple(mask),
        visit_counts={0: 3, 1: 1} if not forced else {0: 1},
        q_by_action=resolved_q,
        root_value=1.0,
        simulations=simulations, ambiguous=False, confidence=1.0,
        source_group=group, belief_fingerprint="b", search_fingerprint="s",
        opponent_policy_version="o", leaf_version="terminal-rollout-v1",
        generation=generation, forced=forced, special_state_tags=tags,
        teacher_budget_tier=tier, teacher_requested_simulations=simulations,
        teacher_completed_simulations=simulations, teacher_status=status,
        teacher_seed=0, teacher_top1_q=top1, teacher_top2_q=top2,
        teacher_q_gap=q_gap, planes=planes, scalars=scalars,
        terminal_reward=3.0)


class TestTeacherBudgetProfile(unittest.TestCase):
    def test_fingerprint_is_immutable_and_sensitive(self):
        base = TeacherBudgetProfile()
        self.assertEqual(base.fingerprint, TeacherBudgetProfile().fingerprint)
        changed = TeacherBudgetProfile(min_q_gap=3.0)
        self.assertNotEqual(base.fingerprint, changed.fingerprint)
        self.assertEqual(teacher_budget_profile_from_json(
            base.as_json()).fingerprint, base.fingerprint)

    def test_invalid_ladders_and_ratios_rejected(self):
        with self.assertRaises(ValueError):
            TeacherBudgetProfile(tiers=(512, 512))
        with self.assertRaises(ValueError):
            TeacherBudgetProfile(default_tier=9)
        with self.assertRaises(ValueError):
            TeacherBudgetProfile(forced_sanity_ratio=0.5)
        with self.assertRaises(ValueError):
            TeacherBudgetProfile(min_visit_share=0.0)

    def test_easy_state_stops_at_default_tier(self):
        profile = TeacherBudgetProfile()
        first = start_decision(profile)
        self.assertEqual(first.tier, profile.default_tier)
        evidence = TeacherEvidence(status="ok", q_gap=8.0,
                                   top1_visit_share=0.9, mean_variance=1.0)
        final = run_decision(profile, first, evidence)
        self.assertFalse(final.escalated)
        self.assertEqual(final.status, "ok")
        self.assertEqual(final.stop_reason, "stable")
        self.assertEqual(final.tier, profile.default_tier)

    def test_ambiguous_state_escalates_one_tier_then_stops(self):
        profile = TeacherBudgetProfile()
        first = start_decision(profile)
        hard = TeacherEvidence(status="ok", ambiguous=True, q_gap=0.1,
                               top1_visit_share=0.4, mean_variance=900.0)
        promoted = run_decision(profile, first, hard)
        self.assertTrue(promoted.escalated)
        self.assertEqual(promoted.status, "pending")
        self.assertEqual(promoted.tier, first.tier + 1)
        easy = TeacherEvidence(status="ok", q_gap=9.0, top1_visit_share=0.8,
                               mean_variance=1.0)
        final = run_decision(profile, promoted, easy)
        self.assertFalse(final.escalated)
        self.assertEqual(final.tier, promoted.tier)
        self.assertEqual(final.status, "ok")

    def test_critical_state_starts_at_hard_tier_and_caps_at_top(self):
        profile = TeacherBudgetProfile()
        first = start_decision(profile, critical=True)
        self.assertEqual(first.tier, profile.hard_state_tier)
        self.assertIn("critical_state", first.stop_reason)
        top = profile.max_tier
        decision = next_decision(profile, top, TeacherEvidence(
            status="ok", ambiguous=True, q_gap=0.0, top1_visit_share=0.1))
        self.assertFalse(decision.escalated)
        self.assertTrue(decision.stop_reason.startswith("max_tier:"))

    def test_forced_state_skips_high_budget_search(self):
        profile = TeacherBudgetProfile()
        decision = forced_decision(profile, source_key="game:1")
        self.assertEqual(decision.tier, -1)
        self.assertEqual(decision.simulations, 0)
        self.assertIn(decision.status, ("forced", "forced-sanity"))
        skipped = start_decision(profile, forced=True, source_key="game:1")
        self.assertEqual(skipped.tier, -1)
        self.assertEqual(skipped.simulations, 0)

    def test_forced_sanity_subset_within_declared_ratio(self):
        profile = TeacherBudgetProfile()
        selected = [index for index in range(2000)
                    if forced_decision(profile, source_key=f"g:{index}").sanity]
        ratio = len(selected) / 2000.0
        self.assertGreater(ratio, 0.0)
        self.assertLess(ratio, 0.10)
        again = [index for index in range(2000)
                 if forced_decision(profile, source_key=f"g:{index}").sanity]
        self.assertEqual(selected, again)


class TestDistillationProfile(unittest.TestCase):
    def test_fingerprint_round_trip_and_oracle_rejected(self):
        profile = SearchDistillationProfile(
            generation=2, policy_version_source="pi1",
            teacher_budget_fingerprint=TeacherBudgetProfile().fingerprint)
        loaded = search_distillation_profile_from_json(profile.as_json())
        self.assertEqual(loaded.fingerprint, profile.fingerprint)
        with self.assertRaises(ValueError):
            SearchDistillationProfile(oracle=True)

    def test_target_modes_are_versioned(self):
        with self.assertRaises(ValueError):
            SearchDistillationProfile(target_mode="visit", lambda_q=0.5)
        with self.assertRaises(ValueError):
            SearchDistillationProfile(target_mode="hybrid", lambda_q=0.0)
        q_soft = SearchDistillationProfile(
            target_mode="q-soft", lambda_visit=0.0, lambda_q=1.0, tau_q=2.0)
        self.assertEqual(q_soft.lambda_q, 1.0)

    def test_value_weight_requires_contract(self):
        with self.assertRaises(ValueError):
            SearchDistillationProfile(value_weight=1.0)
        profile = SearchDistillationProfile(
            value_weight=1.0, value_contract_fingerprint="vc")
        self.assertEqual(profile.value_weight, 1.0)

    def test_coverage_requirements_are_frozen(self):
        profile = SearchDistillationProfile()
        self.assertIn(("hu", 128), profile.critical_bucket_minimums)
        with self.assertRaises(ValueError):
            SearchDistillationProfile(critical_bucket_minimums=(("hu", 0),))


class TestOpponentPopulation(unittest.TestCase):
    def test_member_selection_is_deterministic_and_weighted(self):
        population = OpponentPopulationProfile(
            members=(("legacy", 1.0), ("shape-v2", 3.0)))
        first = population.member_for(generation=0, source_group="g", seat=0)
        second = population.member_for(generation=0, source_group="g", seat=0)
        self.assertEqual(first, second)
        self.assertIn(first, ("legacy", "shape-v2"))
        picks = {population.member_for(generation=0, source_group=f"g{i}",
                                       seat=i % 4)
                 for i in range(400)}
        self.assertEqual(picks, {"legacy", "shape-v2"})

    def test_invalid_members_rejected(self):
        with self.assertRaises(ValueError):
            OpponentPopulationProfile(members=())
        with self.assertRaises(ValueError):
            OpponentPopulationProfile(members=(("a", 1.0), ("a", 2.0)))
        with self.assertRaises(ValueError):
            OpponentPopulationProfile(members=(("a", 0.0),))


class TestPi0Selection(unittest.TestCase):
    def test_regret_not_name_selects_pi0(self):
        result = select_strongest_fast_policy([
            PolicyCandidate("aaa-baseline", 4.0, 8.0, (1.0, 2.0), 10.0),
            PolicyCandidate("zzz-strong", 1.0, 3.0, (0.5, 1.5), 30.0),
        ], max_latency_ms=36.0)
        self.assertEqual(result["selected"], "zzz-strong")
        self.assertEqual(result["ranking"][0]["name"], "zzz-strong")

    def test_latency_and_p95_gates_reject(self):
        result = select_strongest_fast_policy([
            PolicyCandidate("fast-accurate", 2.0, 9.0, (1.0, 2.0), 10.0),
            PolicyCandidate("slow", 0.5, 1.0, (2.0, 3.0), 500.0),
        ], max_latency_ms=36.0)
        self.assertEqual(result["selected"], "fast-accurate")
        result = select_strongest_fast_policy([
            PolicyCandidate("spiky", 0.1, 100.0, (5.0, 6.0), 10.0),
        ], max_latency_ms=36.0, max_p95_regret=24.0)
        self.assertIsNone(result["selected"])

    def test_illegal_or_nonfinite_candidates_excluded(self):
        result = select_strongest_fast_policy([
            PolicyCandidate("broken", 0.0, 0.0, (5.0, 6.0), 1.0,
                            illegal_actions=1),
            PolicyCandidate("ok", 3.0, 6.0, (1.0, 2.0), 1.0),
        ], max_latency_ms=36.0)
        self.assertEqual(result["selected"], "ok")


class TestSpecialStateTags(unittest.TestCase):
    def test_tags_are_deterministic_and_public(self):
        game = Game(seed=7)
        tags_first = special_state_tags(game, 0)
        self.assertEqual(tags_first, special_state_tags(game, 0))
        self.assertIn("dealer", tags_first)
        self.assertIn("non-dealer", special_state_tags(game, 1))
        self.assertIn("ycbk-off", tags_first)
        self.assertEqual(tuple(sorted(tags_first)), tags_first)

    def test_ycbk_and_wall_tail_tags(self):
        game = Game(seed=8, you_cai_bi_kao=True)
        tags = special_state_tags(game, game.current_seat())
        self.assertIn("ycbk-on", tags)


class TestDatasetQuality(unittest.TestCase):
    def test_work_id_is_stable_and_generation_independent(self):
        first = _sample(generation=0)
        second = _sample(generation=3)
        self.assertEqual(first.work_id, second.work_id)
        self.assertNotEqual(first.work_id, _sample(context="c2").work_id)

    def test_split_integrity_rejects_missing_and_conflicts(self):
        dataset = SearchDataset([_sample("a"), _sample("b")])
        report = validate_split_integrity(
            dataset, {"a": "train", "b": "final-test"})
        self.assertEqual(report["counts"]["train"], 1)
        with self.assertRaises(ValueError):
            validate_split_integrity(dataset, {"a": "train"})
        with self.assertRaises(ValueError):
            merge_split_assignments({"a": "train"}, {"a": "validation"})
        with self.assertRaises(ValueError):
            merge_split_assignments({"a": "test-x"})
        merged = merge_split_assignments({"a": "train"}, {"b": "validation"})
        self.assertEqual(merged, {"a": "train", "b": "validation"})

    def test_report_counts_buckets_and_coverage_gate(self):
        dataset = SearchDataset([
            _sample("a", tags=("hu", "piao"), q={0: 4.0, 1: 1.0}),
            _sample("a", tags=("wall-tail",), forced=False),
            _sample("b", tags=("hu", "kong-add")),
        ])
        report = dataset_report(dataset)
        self.assertEqual(report["count"], 3)
        self.assertEqual(report["special_state_tags"]["hu"], 2)
        self.assertEqual(report["legal_action_count"]["2"], 3)
        self.assertEqual(report["q_gap"]["count"], 3)
        gate = coverage_gate(report, (("hu", 2), ("piao", 1)))
        self.assertTrue(gate["passed"])
        gate = coverage_gate(report, (("hu", 3),))
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["missing"], ["hu"])

    def test_duplicates_and_feature_fingerprints(self):
        shared = _sample("a", context="same", tags=("hu",))
        collision = replace(shared, feature_fingerprint="different")
        dataset = SearchDataset([shared, collision, shared])
        report = duplicate_report(dataset)
        self.assertEqual(len(report["context_collisions"]), 1)
        self.assertTrue(report["exact_duplicates"])
        self.assertTrue(report["repeated_work_ids"])

    def test_feature_fingerprint_verification(self):
        import numpy as np

        planes = np.zeros((2, 3), dtype=np.float32)
        scalars = np.ones(2, dtype=np.float32)
        digest = feature_fingerprint(planes, scalars)
        self.assertEqual(digest, feature_fingerprint(planes.copy(), scalars))
        self.assertNotEqual(digest, feature_fingerprint(planes + 1, scalars))
        sample = replace(_sample(planes=planes, scalars=scalars),
                         feature_fingerprint=digest)
        self.assertTrue(verify_feature_fingerprints(
            SearchDataset([sample]))["passed"])
        bad = replace(sample, feature_fingerprint="wrong")
        self.assertFalse(verify_feature_fingerprints(
            SearchDataset([bad]))["passed"])


class TestBaselineFreeze(unittest.TestCase):
    def test_freeze_records_git_rules_fingerprints_and_splits(self):
        value = baseline_freeze()
        self.assertEqual(value["git_revision"], BASELINE_GIT)
        self.assertEqual(value["rules_version"], RULES_VERSION)
        self.assertEqual(value["splits"]["final_test"]["games"],
                         FROZEN_SPLITS["final_test"]["games"])
        self.assertFalse(value["oracle"])
        for name in ("teacher_budget", "search_distillation",
                     "opponent_population", "feature_contract"):
            self.assertTrue(value["fingerprints"][name])
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "baseline_freeze.json"
            write_baseline_freeze(path)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["fingerprint"], value["fingerprint"])


if __name__ == "__main__":
    unittest.main()
