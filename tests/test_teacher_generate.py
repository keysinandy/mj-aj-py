"""Search-teacher generator contract tests (P1)."""

import json
import tempfile
import unittest
from pathlib import Path

from mj.belief import BeliefProfile, BeliefState
from mj.decision.context import PublicDecisionContext
from mj.game import Game
from mj.models.policy_value import extract_value_features
from mj.rollout.simulator import build_world_game
from mj.search import SearchProfile
from mj.training.distillation_profile import OpponentPopulationProfile
from mj.training.search_data import SearchSample, read_search_dataset, work_identity
from mj.training.teacher_budget import TeacherBudgetProfile
from mj.training.teacher_generate import (
    GenerationConfig,
    SourceGameSpec,
    generate_dataset,
    generation_manifest,
    read_reference_contexts,
    resume_dataset,
    scheduled_specs,
    write_reference_contexts,
)


def _config(**overrides):
    values = dict(
        generation=0, policy_source="heuristic:legacy",
        population=OpponentPopulationProfile(members=(("legacy", 1.0),)),
        budget_profile=TeacherBudgetProfile(
            tiers=(2, 4), default_tier=0, hard_state_tier=1, reference_tier=1,
            min_q_gap=100.0, min_visit_share=0.99),
        search_profile=SearchProfile(simulation_budget=2, seed=0),
        belief_profile=BeliefProfile(particle_count=4, seed=0))
    values.update(overrides)
    return GenerationConfig(**values)


class TestScheduledSpecs(unittest.TestCase):
    def test_balanced_schedule_and_split(self):
        specs = scheduled_specs(seed_start=240000, games=8)
        self.assertEqual([spec.hero_seat for spec in specs], [0, 1, 2, 3] * 2)
        self.assertEqual([spec.dealer for spec in specs][:4], [0, 0, 0, 0])
        self.assertEqual([spec.dealer for spec in specs][4:], [1, 1, 1, 1])
        self.assertEqual(specs[0].source_group, "game:240000")
        self.assertEqual(specs[0].split, "train")
        self.assertIsNone(SourceGameSpec(seed=1).split)


class TestGeneration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = generate_dataset(
            [SourceGameSpec(seed=1234, hero_seat=0, dealer=0)], _config())

    def test_samples_carry_provenance_and_legal_actions(self):
        self.assertTrue(self.result.ok, self.result.errors)
        self.assertTrue(self.result.dataset.samples)
        for sample in self.result.dataset.samples:
            self.assertFalse(sample.oracle)
            self.assertEqual(sample.source_group, "game:1234")
            self.assertEqual(sample.generation, 0)
            self.assertEqual(sample.policy_version_source, "heuristic:legacy")
            self.assertTrue(sample.opponent_policy_version.startswith(
                "opponent-population-v1:legacy"))
            self.assertEqual(sample.teacher_seed, 0)
            self.assertTrue(sample.teacher_status)
            self.assertEqual(tuple(sorted(sample.special_state_tags)),
                             sample.special_state_tags)
            self.assertTrue(sample.feature_fingerprint)
            self.assertLessEqual(sample.teacher_completed_simulations,
                                 sample.teacher_requested_simulations)
            for action, count in sample.visit_counts.items():
                self.assertTrue(sample.legal_mask[action])
            if sample.forced:
                self.assertEqual(sum(sample.legal_mask), 1)
                self.assertEqual(sample.teacher_budget_tier, -1)
                self.assertEqual(sample.teacher_status, "forced-sanity")

    def test_multi_action_samples_have_soft_evidence(self):
        multi = [sample for sample in self.result.dataset.samples
                 if not sample.forced and sample.teacher_status != "failed"
                 and sample.simulations]
        self.assertTrue(multi)
        for sample in multi:
            self.assertGreater(sum(sample.legal_mask), 1)
            self.assertTrue(sample.q_by_action)
            self.assertIsNotNone(sample.root_value)

    def test_serialization_round_trip_with_features(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "dataset.jsonl"
            from mj.training.search_data import write_search_dataset
            write_search_dataset(path, self.result.dataset)
            loaded = read_search_dataset(path)
            self.assertEqual([sample.work_id for sample in loaded.samples],
                             [sample.work_id
                              for sample in self.result.dataset.samples])
            for original, restored in zip(self.result.dataset.samples,
                                          loaded.samples):
                self.assertEqual(original.feature_fingerprint,
                                 restored.feature_fingerprint)
                self.assertEqual(original.teacher_q_gap, restored.teacher_q_gap)

    def test_manifest_records_profiles_and_coverage(self):
        manifest = generation_manifest(self.result, _config())
        self.assertEqual(manifest["count"], len(self.result.dataset.samples))
        self.assertEqual(manifest["source_groups"], ["game:1234"])
        self.assertIn("teacher_budget", manifest)
        self.assertIn("coverage", manifest)
        self.assertFalse(manifest["oracle"])
        # generation provenance: git commit + actually-loaded kernel, not just
        # the teacher config hash.
        self.assertTrue(manifest["git_commit"])
        self.assertTrue(manifest["kernel"]["weighted_kernel_version"])
        self.assertIn("weighted_kernel_compatible", manifest["kernel"])
        self.assertIn("degraded", manifest["kernel"])
        self.assertIn("baotou_kernel", manifest["kernel"])
        self.assertIn("piao_draw_mask_kernel", manifest["kernel"])

    def test_kernel_gate_fails_loud_on_degraded_runtime(self):
        from mj.training.teacher_generate import require_compatible_kernel
        degraded = {
            "shanten_kernel": "rust",
            "weighted_kernel": "rust",
            "weighted_kernel_version": "rust-weighted-two-ply-v3",
            "weighted_kernel_required": "rust-weighted-two-ply-v5",
            "weighted_kernel_compatible": False,
            "baotou_kernel": "rust",
            "piao_draw_mask_kernel": "python",
            "baotou_wait_kernel": "python",
            "degraded": True,
            "reason": "weighted_kernel_version_mismatch",
        }
        with self.assertRaises(RuntimeError) as ctx:
            require_compatible_kernel(degraded)
        self.assertIn("degraded", str(ctx.exception))
        self.assertIn("rust-weighted-two-ply-v3", str(ctx.exception))
        # explicit smoke/parity opt-out bypasses the gate but still returns the
        # diagnostic.
        self.assertIs(
            require_compatible_kernel(degraded, allow_degraded=True),
            degraded)

    def test_kernel_gate_accepts_compatible_runtime(self):
        from mj.training.teacher_generate import require_compatible_kernel
        health = {
            "shanten_kernel": "rust",
            "weighted_kernel": "rust",
            "weighted_kernel_version": "rust-weighted-two-ply-v5",
            "weighted_kernel_required": "rust-weighted-two-ply-v5",
            "weighted_kernel_compatible": True,
            "baotou_kernel": "rust",
            "piao_draw_mask_kernel": "rust",
            "baotou_wait_kernel": "rust",
            "degraded": False,
            "reason": None,
        }
        require_compatible_kernel(health)

    def test_worker_count_and_spec_order_do_not_change_rows(self):
        specs = [SourceGameSpec(seed=1234, hero_seat=0, dealer=0),
                 SourceGameSpec(seed=1235, hero_seat=1, dealer=0)]
        serial = generate_dataset(specs, _config(), workers=1)
        parallel = generate_dataset(list(reversed(specs)), _config(), workers=2)
        self.assertTrue(serial.ok, serial.errors)
        self.assertTrue(parallel.ok, parallel.errors)
        self.assertEqual(serial.dataset.fingerprint,
                         parallel.dataset.fingerprint)
        self.assertEqual([sample.work_id for sample in serial.dataset.samples],
                         [sample.work_id for sample in parallel.dataset.samples])

    def test_resume_matches_uninterrupted_run(self):
        specs = [SourceGameSpec(seed=1234, hero_seat=0, dealer=0),
                 SourceGameSpec(seed=1235, hero_seat=0, dealer=0)]
        full = generate_dataset(specs, _config())
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "partial.jsonl"
            from mj.training.search_data import write_search_dataset
            first = generate_dataset([specs[0]], _config())
            write_search_dataset(path, first.dataset)
            existing, completed = resume_dataset(path)
            remaining = generate_dataset(specs, _config(),
                                         completed_work_ids=completed)
            self.assertEqual(len(remaining.dataset.samples),
                             len(full.dataset.samples) - len(existing.samples))
            from mj.training.search_data import SearchDataset
            merged = SearchDataset(sorted(
                existing.samples + remaining.dataset.samples,
                key=lambda sample: (sample.source_group, sample.work_id)))
            self.assertEqual(merged.fingerprint, full.dataset.fingerprint)

    def test_hidden_worlds_do_not_change_the_information_state(self):
        game = Game(seed=99)
        seat = game.current_seat()
        context = PublicDecisionContext.from_game_complete(game, seat)
        history = game.public_history
        belief = BeliefState(context, history=history,
                             profile=BeliefProfile(particle_count=4, seed=1))
        left = build_world_game(context, belief.worlds[0])
        right = build_world_game(context, belief.worlds[1])
        left_context = PublicDecisionContext.from_game_complete(left, seat)
        right_context = PublicDecisionContext.from_game_complete(right, seat)
        self.assertEqual(left_context.context_hash, right_context.context_hash)
        left_planes, left_scalars = extract_value_features(
            left_context, history=history, belief=belief)
        right_planes, right_scalars = extract_value_features(
            right_context, history=history, belief=belief)
        self.assertTrue((left_planes == right_planes).all())
        self.assertTrue((left_scalars == right_scalars).all())


class TestReferenceContexts(unittest.TestCase):
    def test_reference_rows_round_trip(self):
        rows = [{
            "schema": "search-reference-context-v1",
            "source_group": "game:242048",
            "context": {"hero_seat": 0},
            "history": {"events": []},
            "sample": {"work_id": "w"},
        }]
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "reference.jsonl"
            write_reference_contexts(path, rows)
            loaded = read_reference_contexts(path)
        self.assertEqual(loaded, rows)


class TestReferenceShards(unittest.TestCase):
    """Reference rows must serialize/sort without a work_id key and shard."""

    def test_reference_generation_writes_sorted_shards(self):
        import tempfile as _tempfile
        from pathlib import Path as _Path

        import mj.training.teacher_generate as tg
        from mj.features import action_to_flat, flat_to_action
        from mj.training.teacher_generate import read_reference_shards

        spec = SourceGameSpec(seed=1234, hero_seat=0, dealer=0)
        with _tempfile.TemporaryDirectory() as root:
            config = _config(reference_mode=True, reference_simulations=8000,
                             reference_shard_dir=root)
            original = tg._reference_sample

            def fake(snapshot, config, *, source_group,
                     policy_version_source, **kwargs):
                if len(snapshot.legal_actions) <= 1:
                    return None
                mask = [False] * 109
                legal = [action_to_flat(action)
                         for action in snapshot.legal_actions]
                for action in legal:
                    mask[action] = True
                return SearchSample(
                    context_hash=snapshot.context.context_hash,
                    history_hash=snapshot.history.history_hash,
                    legal_mask=tuple(mask), visit_counts={legal[0]: 4},
                    q_by_action={legal[0]: 1.0, legal[1]: 0.5},
                    root_value=1.0, simulations=8, ambiguous=False,
                    confidence=0.5, source_group=source_group,
                    belief_fingerprint=snapshot.belief.fingerprint,
                    search_fingerprint=config.search_profile.fingerprint,
                    opponent_policy_version="o",
                    leaf_version="terminal-rollout-v1",
                    teacher_status="ok", planes=snapshot.planes,
                    scalars=snapshot.scalars)

            tg._reference_sample = fake
            try:
                result = tg.generate_dataset([spec], config, workers=1)
            finally:
                tg._reference_sample = original
            self.assertTrue(result.ok, result.errors)
            self.assertTrue(result.reference_rows)
            shard_rows = read_reference_shards(_Path(root))
            self.assertEqual(len(shard_rows), len(result.reference_rows))
            keys = [(row["source_group"], row["sample"]["fingerprint"])
                    for row in result.reference_rows]
            self.assertEqual(keys, sorted(keys))
            self.assertNotIn("work_id", result.reference_rows[0]["sample"])


class TestSampleProvenanceValidation(unittest.TestCase):
    def _base(self, **overrides):
        values = dict(
            context_hash="c", history_hash="h",
            legal_mask=tuple([True, True] + [False] * 107),
            visit_counts={0: 1}, q_by_action={0: 1.0}, root_value=1.0,
            simulations=1, ambiguous=False, confidence=1.0, source_group="g",
            belief_fingerprint="b", search_fingerprint="s",
            opponent_policy_version="o", leaf_version="terminal-rollout-v1")
        values.update(overrides)
        return SearchSample(**values)

    def test_forced_must_have_one_legal_action(self):
        with self.assertRaises(ValueError):
            self._base(forced=True)

    def test_teacher_counters_and_q_gap_are_checked(self):
        with self.assertRaises(ValueError):
            self._base(teacher_requested_simulations=2,
                       teacher_completed_simulations=3)
        with self.assertRaises(ValueError):
            self._base(teacher_top1_q=1.0, teacher_top2_q=0.5,
                       teacher_q_gap=0.9)

    def test_work_identity_is_teacher_seed_sensitive(self):
        first = self._base(teacher_seed=0)
        second = self._base(teacher_seed=1)
        self.assertNotEqual(first.work_id, second.work_id)
        self.assertEqual(first.work_id, work_identity(
            source_group="g", context_hash="c", history_hash="h",
            teacher_seed=0, search_fingerprint="s"))


if __name__ == "__main__":
    unittest.main()
