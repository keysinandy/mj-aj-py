"""Discard-only environment for the Mini-Suphx v1 training scope.

Only an ordinary hero discard is exposed as an RL step.  Opponent actions and
hero HU/KONG/CHOW/PONG/PASS decisions are advanced by the legacy layer and are
recorded in ``audit_log`` for replay/debugging.
"""

from __future__ import annotations

import numpy as np

from ..bot import choose_action
from ..features import N_ACTIONS, extract, legal_mask
from ..game import Game
from ..hybrid_policy import HybridDecision, HybridPolicy
from ..shanten import shanten

DISCARD_FLATS = tuple(range(34))
DISCARD_MASK = np.zeros(N_ACTIONS, dtype=bool)
DISCARD_MASK[:34] = True


def normalize_round_score(points: float) -> float:
    """Return the versioned ``round-score-v2-normalized`` value target."""
    return float(np.clip(float(points) / 96.0, -1.0, 1.0))


def mask_discards(legal_mask_value: np.ndarray) -> np.ndarray:
    """Restrict a full 109-action mask to ordinary discard actions."""
    return DISCARD_MASK & np.asarray(legal_mask_value, dtype=bool)


def _potential(game: Game, seat: int) -> float:
    """A small shanten potential used only by optional early shaping."""
    hand = game.hands[seat]
    locked = len(game.melds[seat])
    need = 13 - 3 * locked
    if sum(hand) == need:
        return float(8 - shanten(list(hand), locked))
    best = min(
        (shanten([v - (i == tile) for i, v in enumerate(hand)], locked)
         for tile in range(34) if hand[tile]),
        default=8,
    )
    return float(8 - best)


class MahjongDiscardEnv:
    """A minimal gym-independent environment for custom rollout workers."""

    def __init__(self, opponents=None, you_cai_bi_kao=False,
                 seed: int = 0, hero: int = 0, evaluator: str = "legacy",
                 hybrid_policy: HybridPolicy | None = None,
                 shape_k: float = 0.0, strict_learned: bool = False):
        self.opponents = list(opponents or [choose_action] * 3)
        if len(self.opponents) != 3:
            raise ValueError("opponents must contain exactly three policies")
        self.you_cai_bi_kao = bool(you_cai_bi_kao)
        self.seed = int(seed)
        self.hero = int(hero)
        if self.hero not in range(4):
            raise ValueError("hero must be in range(4)")
        self.evaluator = evaluator
        self.hybrid = hybrid_policy or HybridPolicy(evaluator=evaluator)
        self.shape_k = float(shape_k)
        self.strict_learned = bool(strict_learned)
        self.rng = np.random.default_rng(self.seed)
        self._g: Game | None = None
        self._discard_mask: np.ndarray | None = None
        self._pending_decision: HybridDecision | None = None
        self._phi = 0.0
        self.discard_decisions = 0
        self.invalid_learned_actions = 0
        self.audit_log: list[dict] = []

    @property
    def game(self) -> Game:
        if self._g is None:
            raise RuntimeError("environment has not been reset")
        return self._g

    @property
    def pending_decision(self) -> HybridDecision | None:
        return self._pending_decision

    def _obs(self):
        planes, scalars = extract(self.game, self.hero, oracle=False)
        return planes, scalars

    def _audit(self, item: dict) -> None:
        event = dict(item)
        event["index"] = len(self.audit_log)
        self.audit_log.append(event)

    def _opponent_action(self, game: Game, seat: int) -> tuple[int, str]:
        opp_idx = (seat - self.hero - 1) % 4
        legal = tuple(int(a) for a in game.legal_actions())
        action = self.opponents[opp_idx](game, seat)
        if isinstance(action, tuple):
            action = action[0]
        action = int(action)
        if action not in legal:
            action = int(legal[self.rng.integers(len(legal))])
            return action, "invalid_opponent_fallback"
        return action, "opponent_legacy_action"

    def _advance_to_discard(self) -> bool:
        """Advance until a hero ordinary discard or terminal state is reached."""
        self._pending_decision = None
        self._discard_mask = None
        while not self.game.done:
            seat = self.game.current_seat()
            if seat != self.hero:
                phase = str(self.game.phase)
                action, reason = self._opponent_action(self.game, seat)
                legal = tuple(int(a) for a in self.game.legal_actions())
                self._audit({
                    "seat": int(seat), "phase": phase,
                    "legal_actions": list(legal),
                    "legacy_action": action, "selected_action": action,
                    "learned_action": None, "route": "legacy",
                    "reason": reason,
                    "fallback": reason != "opponent_legacy_action",
                    "executed": True,
                })
                self.game.step(action)
                continue

            decision = self.hybrid.gate(self.game, self.hero)
            if decision.learned_allowed:
                self._pending_decision = decision
                self._discard_mask = mask_discards(
                    np.asarray(legal_mask(self.game), dtype=bool))
                self._audit(decision.as_audit(executed=False))
                if not bool(self._discard_mask.any()):
                    raise RuntimeError(
                        "legacy classified a state as discard but no legal discard exists")
                return True

            self._audit(decision.as_audit(executed=True))
            self.game.step(decision.selected_action)
        return False

    def reset(self, *, seed: int | None = None, dealer: int | None = None):
        """Start a deterministic episode and return ``(planes, scalars)``."""
        episode_seed = self.seed if seed is None else int(seed)
        self.seed = episode_seed
        # Derive all episode-local choices from the supplied seed.  Reusing an
        # env with reset(seed=x) therefore reproduces the same trajectory.
        self.rng = np.random.default_rng(episode_seed ^ 0x5EED5EED)
        chosen_dealer = (int(dealer) if dealer is not None else
                         int(self.rng.integers(4)))
        if chosen_dealer not in range(4):
            raise ValueError("dealer must be in range(4)")
        self._g = Game(seed=episode_seed, dealer=chosen_dealer,
                       you_cai_bi_kao=self.you_cai_bi_kao)
        self.discard_decisions = 0
        self.invalid_learned_actions = 0
        self.audit_log = []
        ready = self._advance_to_discard()
        self._phi = _potential(self.game, self.hero) if ready else 0.0
        return self._obs()

    def step(self, action: int, *, strict: bool | None = None):
        """Execute one learned discard decision.

        ``action`` is the flat discard index 0..33.  An invalid learned action
        follows the explicit legacy fallback unless strict mode is requested.
        """
        if self.game.done:
            raise RuntimeError("cannot step a completed game")
        if self._pending_decision is None or self._discard_mask is None:
            raise RuntimeError("environment is not waiting for a discard")
        decision = self.hybrid.resolve(
            self.game, self.hero, int(action),
            strict=self.strict_learned if strict is None else bool(strict),
            decision=self._pending_decision)
        if decision.fallback:
            self.invalid_learned_actions += 1
        self._audit(decision.as_audit(executed=True))
        old_phi = self._phi
        self._pending_decision = None
        self._discard_mask = None
        self.discard_decisions += 1
        self.game.step(decision.selected_action)
        ready = self._advance_to_discard()
        done = bool(self.game.done)
        next_phi = _potential(self.game, self.hero) if ready else 0.0
        reward = self.shape_k * (next_phi - old_phi) if self.shape_k else 0.0
        terminal_score = None
        if done:
            terminal_score = float(self.game.scores[self.hero])
            reward += normalize_round_score(terminal_score)
        self._phi = next_phi
        info = {
            "result": self.game.result,
            "scores": list(self.game.scores),
            "discard_decisions": self.discard_decisions,
            "terminal_score": terminal_score,
            "invalid_learned_actions": self.invalid_learned_actions,
            "audit_log": list(self.audit_log),
        }
        mask = None if done else self.action_mask()
        return self._obs(), mask, float(reward), done, info

    def action_mask(self) -> np.ndarray:
        """Return the current 109-action mask, restricted to discards."""
        if self.game.done:
            return np.zeros(N_ACTIONS, dtype=bool)
        if self._discard_mask is None:
            return np.zeros(N_ACTIONS, dtype=bool)
        return self._discard_mask.copy()

    # SB3-style spelling is useful to callers migrating from ``rl_env``.
    action_masks = action_mask


__all__ = [
    "DISCARD_FLATS", "DISCARD_MASK", "MahjongDiscardEnv",
    "mask_discards", "normalize_round_score",
]
