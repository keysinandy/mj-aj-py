"""Hero information-history tree and PUCT statistics."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping

from ..belief.events import InformationHistory, public_state_hash
from ..decision.profile import fingerprint


def _hero_state_payload(source, hero: int):
    """Build a semantic hero/public state without sampled hidden material."""
    if hasattr(source, "hand") and hasattr(source, "discards"):
        hand = tuple(source.hand)
        melds = source.melds
        discards = source.discards
        drawn = source.drawn
        phase = source.phase
        turn = source.turn
        pending = (source.pending_owner, source.pending_tile)
        if pending == (None, None):
            pending = None
        freeze, freezer = source.freeze, source.freezer
        live_wall = source.live_wall
        legal = source.legal_actions
        locked = source.locked
    else:
        hand = tuple(source.hands[hero])
        melds = tuple(tuple(row) for row in source.melds)
        discards = tuple(tuple(row) for row in source.discards)
        drawn = source.drawn[hero]
        phase = source.phase
        turn = source.turn
        pending = tuple(source.pending) if source.pending is not None else None
        freeze, freezer = source.freeze, source.freezer
        live_wall = source.live_wall_left()
        legal = tuple(source.legal_actions()) if turn == hero else ()
        locked = len(source.melds[hero])
    return {
        "hero": int(hero), "hand": hand, "drawn": drawn,
        "locked": locked, "melds": melds, "discards": discards,
        "phase": phase, "turn": turn, "pending": pending,
        "freeze": freeze, "freezer": freezer, "live_wall": live_wall,
        "legal_actions": legal,
    }


@dataclass(frozen=True)
class HeroInfoNodeKey:
    """Key for one hero information state; world identity is absent by design."""

    history_hash: str
    hero_state_hash: str
    schema: str = "hero-info-node-key-v1"

    @classmethod
    def from_context(cls, context, history: InformationHistory | None = None):
        history_hash = (history.history_hash if history is not None
                        else getattr(context, "history_hash", context.context_hash))
        return cls(str(history_hash), fingerprint(
            _hero_state_payload(context, context.hero_seat), 24))

    @classmethod
    def from_game(cls, game, hero: int, history: InformationHistory | None = None):
        if history is None:
            existing = getattr(game, "_public_history", None)
            if isinstance(existing, InformationHistory):
                history = existing
        if history is None:
            history_hash = public_state_hash(game)
        else:
            history_hash = history.history_hash
        return cls(str(history_hash), fingerprint(
            _hero_state_payload(game, hero), 24))

    @property
    def value(self):
        return f"{self.history_hash}:{self.hero_state_hash}"

    @property
    def fingerprint(self):
        return fingerprint({"schema": self.schema,
                            "history_hash": self.history_hash,
                            "hero_state_hash": self.hero_state_hash}, 24)

    def as_json(self):
        return {"schema": self.schema, "history_hash": self.history_hash,
                "hero_state_hash": self.hero_state_hash,
                "fingerprint": self.fingerprint}

    def __str__(self):
        return self.value


@dataclass
class ActionStats:
    action: int
    prior: float = 0.0
    visits: int = 0
    value_sum: float = 0.0
    returns: list[float] = field(default_factory=list)

    @property
    def N(self):
        return self.visits

    @property
    def W(self):
        return self.value_sum

    @property
    def P(self):
        return self.prior

    @property
    def Q(self):
        return self.value_sum / self.visits if self.visits else 0.0

    @property
    def variance(self):
        if len(self.returns) < 2:
            return 0.0
        mean = self.Q
        return sum((value - mean) ** 2 for value in self.returns) / len(self.returns)

    def update(self, value: float):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("tree backup value must be finite")
        self.visits += 1
        self.value_sum += value
        self.returns.append(value)

    def as_json(self):
        return {"action": int(self.action), "N": self.N, "W": self.W,
                "Q": self.Q, "P": self.P, "variance": self.variance}


@dataclass
class TreeNode:
    key: HeroInfoNodeKey
    legal_actions: tuple[int, ...] = ()
    actions: dict[int, ActionStats] = field(default_factory=dict)
    visits: int = 0

    @property
    def N(self):
        return self.visits

    def expand(self, legal_actions, priors: Mapping[int, float] | None = None):
        actions = tuple(int(action) for action in legal_actions)
        if not actions:
            raise ValueError("cannot expand a hero node with no legal actions")
        if len(set(actions)) != len(actions):
            raise ValueError("legal actions must be unique")
        priors = priors or {}
        total = math.fsum(max(0.0, float(priors.get(action, 0.0)))
                          for action in actions)
        uniform = 1.0 / len(actions)
        self.legal_actions = actions
        self.actions = {
            action: ActionStats(action, (max(0.0, float(priors.get(action, 0.0))) /
                                total if total > 0 else uniform))
            for action in actions
        }
        return self

    def select(self, c_puct: float) -> ActionStats:
        if not self.actions:
            raise ValueError("node has not been expanded")
        root_scale = math.sqrt(max(1, self.visits))
        best = None
        best_key = None
        for order, action in enumerate(self.legal_actions):
            stat = self.actions[action]
            score = stat.Q + float(c_puct) * stat.P * root_scale / (1 + stat.N)
            # Earlier legal action wins exact ties; the key makes this rule
            # explicit and independent of dict insertion behavior.
            key = (score, -order)
            if best_key is None or key > best_key:
                best, best_key = stat, key
        return best

    def backup(self, action: int, value: float):
        if action not in self.actions:
            raise KeyError(f"action {action} was not expanded")
        self.visits += 1
        self.actions[action].update(value)

    def as_json(self):
        return {
            "key": self.key.as_json(), "N": self.N,
            "legal_actions": list(self.legal_actions),
            "actions": [self.actions[action].as_json()
                        for action in self.legal_actions],
        }


class SearchTree:
    def __init__(self):
        self.nodes: dict[str, TreeNode] = {}

    def get(self, key: HeroInfoNodeKey):
        return self.nodes.get(key.value)

    def get_or_create(self, key: HeroInfoNodeKey):
        node = self.nodes.get(key.value)
        if node is None:
            node = TreeNode(key)
            self.nodes[key.value] = node
        return node

    def as_json(self):
        return {"schema": "search-tree-v1",
                "nodes": [node.as_json() for node in self.nodes.values()]}
