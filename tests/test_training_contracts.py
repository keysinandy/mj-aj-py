import unittest

from mj.training import (
    SearchDataset, SearchSample, ValueDataset, freeze_source_splits,
    iteration_versions, should_stop_iteration,
)
from mj.training.policy_iteration import IterationRecord, PolicyIterationManifest


def _sample(group="g"):
    return SearchSample(
        context_hash="c", history_hash="h",
        legal_mask=tuple([True] + [False] * 108),
        visit_counts={0: 2}, q_by_action={0: 1.0}, root_value=1.0,
        simulations=2, ambiguous=False, confidence=1.0, source_group=group,
        belief_fingerprint="b", search_fingerprint="s",
        opponent_policy_version="o", leaf_version="terminal-rollout-v1",
        terminal_reward=3.0)


class TestTrainingContracts(unittest.TestCase):
    def test_soft_target_and_independent_terminal_reward_round_trip(self):
        sample = _sample()
        self.assertEqual(sample.policy_target, {0: 1.0})
        self.assertEqual(sample.root_value, 1.0)
        self.assertEqual(sample.terminal_reward, 3.0)
        loaded = SearchSample.from_json(sample.as_json())
        self.assertEqual(loaded.terminal_reward, 3.0)
        self.assertEqual(ValueDataset.from_search_dataset(
            SearchDataset([sample])).samples[0].target, 1.0)

    def test_source_groups_are_atomic(self):
        dataset = SearchDataset([_sample("a"), _sample("b")])
        assignments = {"a": "train", "b": "final-test"}
        self.assertEqual(set(dataset.split_by_source_group(assignments)),
                         {"train", "final-test"})
        self.assertEqual(set(freeze_source_splits(["a", "b"], seed=3)),
                         {"a", "b"})

    def test_iteration_boundary_has_new_identity_and_stop_gate(self):
        boundary = iteration_versions(
            1, continuation_version="pi0", belief_fingerprint="b",
            search_fingerprint="s", policy_source_version="pi0")
        self.assertIn("dataset1-", boundary["dataset_version"])
        self.assertNotEqual(boundary["teacher_fingerprint"], "s")
        rows = [IterationRecord(i, f"pi{i}", "c", "d", "b", "s",
                                root_regret_mean=1.0,
                                paired_score_mean=0.0)
                for i in range(3)]
        manifest = PolicyIterationManifest(
            no_improvement_generations=2, records=tuple(rows))
        self.assertTrue(should_stop_iteration(manifest.records, manifest)["stop"])


if __name__ == "__main__":
    unittest.main()
