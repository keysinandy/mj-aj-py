"""Information-set search contract tests."""

import copy
import unittest

from mj.belief import BeliefState
from mj.decision.context import PublicDecisionContext
from mj.game import Game
from mj.search import InformationSetSearch, SearchProfile, search_game
from mj.search.tree import HeroInfoNodeKey
from mj.rollout.simulator import build_world_game


class _FailedLeaf:
    version = "terminal-rollout-v1"

    def evaluate(self, game, hero, *, depth=0):
        from mj.search.leaf import LeafEvaluation
        return LeafEvaluation("failed", None, self.version, error="fixture")


class TestInformationSearch(unittest.TestCase):
    def test_determinization_does_not_change_node_key_or_prior(self):
        game = Game(seed=41)
        context = PublicDecisionContext.from_game_complete(game, 0)
        belief = BeliefState(context, history=game.public_history,
                             profile={"particle_count": 4, "seed": 5})
        left = build_world_game(context, belief.worlds[0])
        right = build_world_game(context, belief.worlds[1])
        key_left = HeroInfoNodeKey.from_game(left, 0, game.public_history)
        key_right = HeroInfoNodeKey.from_game(right, 0, game.public_history)
        self.assertEqual(key_left, key_right)

    def test_fixed_simulation_ids_are_worker_independent(self):
        game = Game(seed=42)
        profile = SearchProfile(simulation_budget=3, max_depth=2)
        first = search_game(game, profile=profile, workers=1)
        second = search_game(copy.deepcopy(game), profile=profile, workers=4)
        self.assertEqual(first.visit_counts, second.visit_counts)
        self.assertEqual(first.q_by_action, second.q_by_action)
        self.assertEqual(first.best_action, second.best_action)

    def test_failed_simulations_are_not_zero_reward_successes(self):
        game = Game(seed=43)
        context = PublicDecisionContext.from_game_complete(game, 0)
        result = InformationSetSearch(
            context, history=game.public_history,
            profile=SearchProfile(simulation_budget=2, max_depth=2),
            leaf_evaluator=_FailedLeaf()).run()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failed_simulations, 2)
        self.assertEqual(result.visit_counts[context.legal_actions[0]], 0)
        self.assertIsNone(result.best_action)
        self.assertFalse(result.as_json()["real_wall_optimal"])


if __name__ == "__main__":
    unittest.main()
