"""Complete-world restoration and fixed-policy offline simulation."""

from __future__ import annotations

from dataclasses import dataclass
import copy
import random
from typing import Optional

from ..game import DEAD_WALL, Game
from ..bot import choose_action
from ..decision.context import ContextError, PublicDecisionContext
from .belief import SampledWorld


class RolloutFailure(RuntimeError):
    """A world could not be executed to a legal terminal state."""


def _world_seed(world):
    return int(world.fingerprint[:16], 16)


def _phase_for_game(context):
    if context.phase in ("draw", "discard"):
        return "discard"
    if context.phase.startswith("response") or context.phase == "react":
        return "react"
    raise RolloutFailure(f"unsupported phase {context.phase}")


def _expected_hidden(context, seat):
    value = context.concealed_counts[seat]
    if value is None:
        raise RolloutFailure(f"concealed count unknown for seat {seat}")
    return int(value)


def build_world_game(context: PublicDecisionContext, world: SampledWorld):
    """Build a fully independent ``Game`` from public context + sample.

    This is intentionally not ``Mirror.build_game``.  Every Game field is
    assigned, and the resulting policy views are made separately with hidden
    data zeroed before each continuation decision.
    """
    try:
        context.validate_for("rollout")
    except ContextError as exc:
        raise RolloutFailure(str(exc)) from exc
    if world.context_hash != context.context_hash:
        raise RolloutFailure("world/context fingerprint mismatch")
    if len(world.hidden_hands) != 4:
        raise RolloutFailure("world must contain four hidden hand rows")
    if any(len(row) != 34 for row in world.hidden_hands):
        raise RolloutFailure("hidden hand row must have 34 tiles")
    if any(world.hidden_hands[context.hero_seat]):
        raise RolloutFailure("hero hidden hand slot must be empty")
    if len(world.wall) != (context.full_wall or -1):
        raise RolloutFailure("sampled wall length differs from context")
    if world.dead_wall != context.dead_wall:
        raise RolloutFailure("sampled dead wall differs from context")
    if (any(x is None for x in context.chain_counts) or
            any(x is None for x in context.chain_piao_counts)):
        raise RolloutFailure("four-player chain state is incomplete")
    phase = _phase_for_game(context)
    g = Game.__new__(Game)
    g.dealer = context.dealer
    g.base = context.base
    g.you_cai_bi_kao = bool(context.you_cai_bi_kao)
    g._kong_draw = bool(context.kong_draw)
    g.rng = random.Random(_world_seed(world))
    g.hands = [[0] * 34 for _ in range(4)]
    g.hands[context.hero_seat] = list(context.hand)
    for seat in range(4):
        if seat != context.hero_seat:
            g.hands[seat] = list(world.hidden_hands[seat])
    g.wall = list(world.wall)
    g.melds = [[(kind, int(tile)) for kind, tile in row]
               for row in context.melds]
    g.discards = [list(row) for row in context.discards]
    g.drawn = [None] * 4
    g.drawn[context.hero_seat] = context.drawn
    g.chows = list(context.chows)
    g.turn = context.turn if context.turn is not None else context.hero_seat
    g.phase = phase
    g.pending = ((context.pending_owner, context.pending_tile)
                 if context.pending_owner is not None and
                 context.pending_tile is not None else None)
    g.freeze = int(context.freeze)
    g.freezer = context.freezer
    g.chain = [int(x) for x in context.chain_counts]
    g.chain_piao = [int(x) for x in context.chain_piao_counts]
    g.scores = [0] * 4
    g.done = False
    g.result = None
    g.react_seq = list(context.react_seq)
    g.react_idx = int(context.react_index or 0)
    g._n_claim = (int(context.react_claim_count)
                  if context.react_claim_count is not None else 0)
    if phase == "react":
        if not g.react_seq or context.react_claim_count is None:
            raise RolloutFailure("response order is not fully specified")
        if g.pending is None:
            raise RolloutFailure("react phase has no pending discard")
    # Verify the state through both public material and the actual root legal
    # set.  The latter catches a partially restored freeze/drawn/meld field.
    if tuple(g.visible_counts(context.hero_seat)) != tuple(context.visible):
        raise RolloutFailure("restored visible material differs from context")
    for seat in range(4):
        if seat == context.hero_seat:
            continue
        if sum(g.hands[seat]) != _expected_hidden(context, seat):
            raise RolloutFailure(f"hidden count mismatch for seat {seat}")
    if context.legal_actions:
        actual = tuple(g.legal_actions())
        if actual != tuple(context.legal_actions):
            # Action order is part of a deterministic stable tie break, but a
            # set comparison gives a useful error for logs with only order
            # normalization differences.
            if set(actual) != set(context.legal_actions):
                raise RolloutFailure(
                    f"root legal set mismatch: actual={actual} context={context.legal_actions}")
    return g


def actor_view(game: Game, actor: int):
    """Return a detached policy input with only ``actor`` hand visible."""
    view = Game.__new__(Game)
    view.dealer = game.dealer
    view.base = game.base
    view.you_cai_bi_kao = game.you_cai_bi_kao
    view._kong_draw = game._kong_draw
    view.rng = random.Random(0)
    view.hands = [[0] * 34 for _ in range(4)]
    view.hands[actor] = list(game.hands[actor])
    view.wall = [0] * len(game.wall)
    view.melds = [[tuple(m) for m in row] for row in game.melds]
    view.discards = [list(row) for row in game.discards]
    view.drawn = [None] * 4
    view.drawn[actor] = game.drawn[actor]
    view.chows = list(game.chows)
    view.turn = game.turn
    view.phase = game.phase
    view.pending = tuple(game.pending) if game.pending is not None else None
    view.freeze = game.freeze
    view.freezer = game.freezer
    view.chain = [0] * 4
    view.chain[actor] = game.chain[actor]
    view.chain_piao = [0] * 4
    view.chain_piao[actor] = game.chain_piao[actor]
    view.scores = [0] * 4
    view.done = game.done
    view.result = None
    view.react_seq = list(getattr(game, "react_seq", ()))
    view.react_idx = getattr(game, "react_idx", 0)
    view._n_claim = getattr(game, "_n_claim", 0)
    return view


class FixedContinuation:
    """Frozen non-rollout continuation policy used by teacher worlds."""

    def __init__(self, evaluator="shape-v1"):
        if evaluator not in ("legacy", "shape-v1"):
            raise ValueError("continuation must be legacy or shape-v1")
        self.evaluator = evaluator
        self.version = f"frozen_{evaluator}"

    def action(self, game, actor):
        view = actor_view(game, actor)
        visible_legal = tuple(view.legal_actions())
        actual_legal = tuple(game.legal_actions())
        if set(visible_legal) != set(actual_legal):
            raise RolloutFailure("actor view legal set differs from world")
        action = choose_action(view, actor, evaluator=self.evaluator)
        if isinstance(action, tuple):
            action = action[0]
        if action not in actual_legal:
            raise RolloutFailure(
                f"continuation produced illegal action {action}; legal={actual_legal}")
        return action

    def as_json(self):
        return {"version": self.version, "evaluator": self.evaluator,
                "recursive_teacher": False,
                "actor_view": "private_actor_plus_public"}


@dataclass(frozen=True)
class RolloutOutcome:
    status: str
    reward: Optional[float]
    sample_id: int
    world_fingerprint: str
    steps: int = 0
    winner: Optional[int] = None
    multiplier: Optional[int] = None
    draw: bool = False
    error: Optional[str] = None

    @property
    def valid(self):
        return self.status == "ok" and self.reward is not None

    def as_json(self):
        return {
            "status": self.status, "reward": self.reward,
            "sample_id": self.sample_id,
            "world_fingerprint": self.world_fingerprint,
            "steps": self.steps, "winner": self.winner,
            "multiplier": self.multiplier, "draw": self.draw,
            "error": self.error,
        }


def run_rollout(context: PublicDecisionContext, world: SampledWorld,
                root_action: int, continuation=None, *, max_steps=4096,
                raise_on_failure=False):
    """Execute one root candidate to Game.done without silent repair."""
    continuation = continuation or FixedContinuation()
    steps = 0
    try:
        game = build_world_game(context, world)
        if root_action not in game.legal_actions():
            raise RolloutFailure(f"root action {root_action} is not legal")
        game.step(root_action)
        steps += 1
        while not game.done:
            if steps >= max_steps:
                raise RolloutFailure("incomplete_rollout:max_steps")
            actor = game.current_seat()
            legal = game.legal_actions()
            if not legal:
                raise RolloutFailure("non-terminal state has no legal actions")
            action = continuation.action(game, actor)
            if action not in legal:
                raise RolloutFailure(f"illegal continuation action {action}")
            game.step(action)
            steps += 1
        winner = game.result[0] if game.result else None
        mult = game.result[1] if game.result else None
        reward = float(game.scores[context.hero_seat])
        return RolloutOutcome(
            "ok", reward, world.sample_id, world.fingerprint, steps,
            winner, mult, game.result is None)
    except Exception as exc:
        if raise_on_failure:
            raise
        return RolloutOutcome(
            "failed", None, world.sample_id, world.fingerprint, steps,
            error=f"{type(exc).__name__}:{exc}")
