"""Deterministic root-sampling POMCP/ISMCTS for Mahjong information sets."""

from __future__ import annotations

import hashlib
import math
import random
import time
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from ..belief import BeliefProfile, BeliefState, InformationHistory
from ..belief.events import history_from_game
from ..decision.context import ContextError, PublicDecisionContext
from ..models.opponent_policy import ActorPolicy, HeuristicLikelihoodPolicy
from ..rollout.simulator import actor_view, build_world_game
from .leaf import TerminalRolloutEvaluator, make_leaf_evaluator
from .profile import SearchProfile
from .report import SearchResult
from .tree import HeroInfoNodeKey, SearchTree


class SearchError(RuntimeError):
    """The search context or simulation cannot be used safely."""


def _seed_for(*values):
    raw = "|".join(str(value) for value in values).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


class InformationSetSearch:
    """Root-sampling search with only hero decision nodes."""

    def __init__(self, context: PublicDecisionContext, *, belief=None,
                 history: InformationHistory | None = None,
                 actor_policy: ActorPolicy | None = None,
                 profile: SearchProfile | dict | None = None,
                 leaf_evaluator=None):
        if not isinstance(context, PublicDecisionContext):
            raise TypeError("search requires a PublicDecisionContext")
        self.context = context
        self.history = history or InformationHistory()
        if profile is None:
            profile = SearchProfile()
        elif isinstance(profile, Mapping):
            from .profile import search_profile_from_json
            profile = search_profile_from_json(profile)
        if not isinstance(profile, SearchProfile):
            raise TypeError("profile must be a SearchProfile or mapping")
        self.profile = profile
        self.actor_policy = actor_policy or HeuristicLikelihoodPolicy(
            temperature=1.0, version=profile.actor_policy_version)
        self.belief = belief or BeliefState(
            context, history=self.history,
            profile=BeliefProfile(seed=profile.seed),
            actor_policy=self.actor_policy)
        if not isinstance(self.belief, BeliefState):
            raise TypeError("belief must be a BeliefState")
        if history is not None and history.history_hash != self.belief.history_hash:
            raise SearchError("search/history and belief/history mismatch")
        if not profile.belief_profile_fingerprint:
            # Bind the effective default belief to the search artifact.  The
            # caller may omit the fingerprint for convenience, but the
            # resulting report/profile must never remain unbound.
            profile = replace(
                profile,
                belief_profile_fingerprint=self.belief.profile.fingerprint)
            self.profile = profile
        if profile.belief_profile_fingerprint and (
                profile.belief_profile_fingerprint != self.belief.profile.fingerprint):
            raise SearchError("search/belief profile fingerprint mismatch")
        self.history = self.belief.history if history is None else history
        self.tree = SearchTree()
        self.failures: list[dict[str, Any]] = []
        self.terminal_simulations = 0
        self.leaf_simulations = 0
        self.failed_simulations = 0
        self.successful_simulations = 0
        self.max_depth_seen = 0
        self._started = None
        self._root_game = None
        self._root_key = HeroInfoNodeKey.from_context(context, self.history)
        self.root = self.tree.get_or_create(self._root_key)
        self.legal_actions = self._root_legal_actions()
        self.root.expand(self.legal_actions, self._root_priors())
        self.leaf = leaf_evaluator or make_leaf_evaluator(
            profile.leaf_version,
            continuation=_PolicyContinuation(self.actor_policy))
        if getattr(self.leaf, "version", "") != profile.leaf_version:
            # The profile is authoritative; fail before simulations rather
            # than producing an artifact with an unannounced evaluator.
            raise SearchError(
                f"leaf/profile mismatch: {getattr(self.leaf, 'version', None)} "
                f"!= {profile.leaf_version}")

    def _root_legal_actions(self):
        if self.context.turn is not None and self.context.turn != self.context.hero_seat:
            raise SearchError("search root is not a hero decision")
        actions = tuple(int(action) for action in self.context.legal_actions)
        if not actions:
            raise SearchError("search root legal_actions are missing")
        if len(set(actions)) != len(actions):
            raise SearchError("search root legal_actions contain duplicates")
        try:
            world = self.belief.sample_world(0)
            game = build_world_game(self.context, world)
            actual = tuple(int(action) for action in game.legal_actions())
            if set(actual) != set(actions):
                raise SearchError(f"root legal set mismatch: {actual} != {actions}")
            self._root_game = game
        except Exception as exc:
            if isinstance(exc, SearchError):
                raise
            raise SearchError(str(exc)) from exc
        return actions

    def _root_priors(self):
        view = actor_view(self._root_game, self.context.hero_seat)
        try:
            distribution = self.actor_policy.distribution(view, self.legal_actions)
            return {action: distribution.probability(action)
                    for action in self.legal_actions}
        except Exception as exc:
            self.failures.append({"stage": "prior", "error": f"{type(exc).__name__}:{exc}"})
            # Priors use no hidden field; uniform is the explicit safe fallback.
            value = 1.0 / len(self.legal_actions)
            return {action: value for action in self.legal_actions}

    def _node_for(self, game):
        history = history_from_game(game)
        key = HeroInfoNodeKey.from_game(game, self.context.hero_seat, history)
        node = self.tree.get_or_create(key)
        if not node.actions:
            legal = tuple(int(action) for action in game.legal_actions())
            if not legal:
                raise SearchError("hero state has no legal actions")
            view = actor_view(game, self.context.hero_seat)
            try:
                distribution = self.actor_policy.distribution(view, legal)
                priors = {action: distribution.probability(action) for action in legal}
            except Exception:
                value = 1.0 / len(legal)
                priors = {action: value for action in legal}
            node.expand(legal, priors)
        return node

    def _simulate(self, simulation_id):
        world = self.belief.sample_for_simulation(simulation_id)
        game = build_world_game(self.context, world)
        # A Game created from a posterior world is still only carrying the
        # semantic public history; its private hand/wall remain in the offline
        # simulation object and never reach a node key or report.
        game._public_history = self.history
        node_path = []
        depth = 0
        try:
            while not game.done and depth < self.profile.max_depth:
                self.max_depth_seen = max(self.max_depth_seen, depth)
                actor = game.current_seat()
                legal = tuple(int(action) for action in game.legal_actions())
                if not legal:
                    raise SearchError("non-terminal state has no legal actions")
                if actor == self.context.hero_seat:
                    node = self._node_for(game)
                    selected = node.select(self.profile.c_puct)
                    action = int(selected.action)
                    node_path.append((node, action))
                else:
                    view = actor_view(game, actor)
                    rng = random.Random(_seed_for(
                        self.profile.seed, self.belief.history_hash,
                        int(simulation_id), depth, actor))
                    action = self.actor_policy.choose(view, legal, rng)
                    if action not in legal:
                        raise SearchError("opponent policy selected illegal action")
                game.step(action)
                depth += 1
            if game.done:
                reward = float(game.scores[self.context.hero_seat])
                if not math.isfinite(reward):
                    raise SearchError("terminal reward is non-finite")
                self.terminal_simulations += 1
            else:
                leaf = self.leaf.evaluate(game, self.context.hero_seat, depth=depth)
                if not leaf.valid:
                    raise SearchError(leaf.error or "leaf evaluation failed")
                reward = float(leaf.reward)
                if not math.isfinite(reward):
                    raise SearchError("leaf reward is non-finite")
                self.leaf_simulations += 1
            for node, action in node_path:
                node.backup(action, reward)
            self.successful_simulations += 1
            return True
        except Exception as exc:
            self.failed_simulations += 1
            self.failures.append({
                "simulation_id": int(simulation_id), "depth": depth,
                "error": f"{type(exc).__name__}:{exc}",
            })
            return False

    def run(self, *, simulation_budget=None, workers=1, reference=False):
        """Run fixed simulation ids; ``workers`` cannot affect their streams."""
        if reference and self.profile.root_noise:
            raise SearchError("reference search cannot use root exploration noise")
        requested = (self.profile.simulation_budget if simulation_budget is None
                     else max(0, int(simulation_budget)))
        self._started = time.monotonic()
        completed = 0
        incomplete = False
        for simulation_id in range(requested):
            if (self.profile.wall_clock_ms is not None and
                    (time.monotonic() - self._started) * 1000.0 >=
                    self.profile.wall_clock_ms):
                incomplete = True
                break
            self._simulate(simulation_id)
            completed += 1
        if completed < requested:
            incomplete = True
        stats = self.root.actions
        counts = {action: stats[action].visits for action in self.legal_actions}
        total_visits = sum(counts.values())
        visits = {action: (counts[action] / total_visits if total_visits else 0.0)
                  for action in self.legal_actions}
        q_values = {action: stats[action].Q for action in self.legal_actions
                    if stats[action].visits}
        variance = {action: stats[action].variance for action in self.legal_actions
                    if stats[action].visits}
        # Stable legal-order tie break applies to both PUCT selection and
        # result extraction; failed simulations never contribute zero rewards.
        best = None
        best_key = None
        for order, action in enumerate(self.legal_actions):
            key = (q_values.get(action, -math.inf), counts[action], -order)
            if best_key is None or key > best_key:
                best, best_key = action, key
        # A failed run has no evidence for recommending any action.  Keep the
        # legal set in the report, but do not turn the deterministic legal
        # order into an apparently evaluated recommendation.
        if self.successful_simulations == 0:
            best = None
        observed = [q_values[action] for action in self.legal_actions
                    if action in q_values]
        ambiguous = (len(observed) > 1 and
                     max(observed) - sorted(observed)[-2] <=
                     self.profile.ambiguity_margin)
        status = "incomplete_budget" if incomplete else "ok"
        if requested > 0 and self.successful_simulations == 0:
            status = "failed"
        visited = [stats[action] for action in self.legal_actions
                   if stats[action].visits]
        root_visits = sum(stat.visits for stat in visited)
        root_value = (sum(stat.value_sum for stat in visited) / root_visits
                      if root_visits else None)
        return SearchResult(
            status=status, context_hash=self.context.context_hash,
            history_hash=self.belief.history_hash,
            belief_fingerprint=self.belief.fingerprint,
            search_profile_fingerprint=self.profile.fingerprint,
            root_key=self._root_key.value, legal_actions=self.legal_actions,
            best_action=best, visit_policy=visits, q_by_action=q_values,
            visit_counts=counts, variance_by_action=variance,
            simulations=completed, requested_simulations=requested,
            terminal_simulations=self.terminal_simulations,
            leaf_simulations=self.leaf_simulations,
            failed_simulations=self.failed_simulations,
            max_depth=self.max_depth_seen, incomplete_budget=incomplete,
            ambiguous=ambiguous, failures=tuple(self.failures),
            node_count=len(self.tree.nodes), root_value=root_value,
            leaf_fallback_reason=getattr(self.leaf, "fallback_reason", None),
            leaf_version=self.profile.leaf_version)


class _PolicyContinuation:
    def __init__(self, policy):
        self.policy = policy
        self.version = "terminal-rollout-v1"

    def action(self, game, actor):
        view = actor_view(game, actor)
        return self.policy.choose(view, tuple(game.legal_actions()), random.Random(0))


POMCP = InformationSetSearch


def run_search(context, *, belief=None, history=None, actor_policy=None,
               profile=None, leaf_evaluator=None, **kwargs):
    search = InformationSetSearch(
        context, belief=belief, history=history, actor_policy=actor_policy,
        profile=profile, leaf_evaluator=leaf_evaluator)
    return search.run(**kwargs)


def search_game(game, seat=None, *, profile=None, actor_policy=None,
                leaf_evaluator=None, **kwargs):
    """Convenience adapter for an offline self-play ``Game`` root."""
    seat = game.current_seat() if seat is None else int(seat)
    context = PublicDecisionContext.from_game_complete(game, seat)
    history = history_from_game(game)
    return run_search(context, history=history, actor_policy=actor_policy,
                      profile=profile, leaf_evaluator=leaf_evaluator, **kwargs)
