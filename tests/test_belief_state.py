"""Belief-v2 posterior, legality and reproducibility tests."""

import unittest

from mj.belief import BeliefProfile, BeliefState, PublicEvent
from mj.decision.context import PublicDecisionContext
from mj.game import Game, PONG
from mj.models.opponent_policy import HeuristicLikelihoodPolicy


def _context(game):
    return PublicDecisionContext.from_game_complete(
        game, game.current_seat())


class TestBeliefState(unittest.TestCase):
    def test_initial_particles_are_reproducible_and_conservative(self):
        game = Game(seed=21)
        context = _context(game)
        profile = BeliefProfile(particle_count=12, seed=7)
        left = BeliefState(context, history=game.public_history,
                           profile=profile)
        right = BeliefState(context, history=game.public_history,
                            profile=profile)
        self.assertEqual([p.world.fingerprint for p in left.particles],
                         [p.world.fingerprint for p in right.particles])
        for particle in left.particles:
            world = particle.world
            counts = [0] * 34
            for row in world.hidden_hands:
                for tile, value in enumerate(row):
                    counts[tile] += value
            for tile in world.wall:
                counts[tile] += 1
            self.assertEqual([counts[tile] + context.visible[tile]
                              for tile in range(34)], [4] * 34)

    def test_illegal_observation_is_hard_zero_and_resets(self):
        game = Game(seed=22)
        belief = BeliefState(_context(game), profile={
            "particle_count": 8, "seed": 3})
        # PONG cannot be legal in a discard root for any particle.  The
        # resulting collapse is explicit and reinitializes from public state.
        belief.update(PublicEvent("PONG", actor=game.current_seat(),
                                  phase="discard", public_payload={"tile": 0}))
        self.assertGreaterEqual(belief.reset_count, 1)
        self.assertTrue(belief.degraded)
        self.assertEqual(belief.last_reset_reason, "posterior_zero_mass")
        self.assertNotIn("hidden_hands", belief.as_json())

    def test_event_update_keeps_mass_and_records_statistics(self):
        game = Game(seed=23)
        context = _context(game)
        belief = BeliefState(context, history=game.public_history,
                             profile={"particle_count": 8, "seed": 4},
                             actor_policy=HeuristicLikelihoodPolicy())
        action = game.legal_actions()[0]
        event = PublicEvent.from_action(game, action)
        game.step(action)
        next_context = PublicDecisionContext.from_game_complete(
            game, context.hero_seat)
        belief.update(event, context=next_context)
        self.assertAlmostEqual(sum(belief.weights), 1.0)
        self.assertGreaterEqual(belief.ess, 1.0)
        self.assertEqual(belief.history.events[-1].event_type, "DISCARD")
        safe = belief.summary()
        self.assertIn("marginals", safe)
        self.assertNotIn("wall", safe)


if __name__ == "__main__":
    unittest.main()
