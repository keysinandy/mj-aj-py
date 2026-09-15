"""Uniform unseen-card belief sampling for offline teacher data."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import random

from ..decision.context import ContextError, PublicDecisionContext
from ..decision.profile import canonical_json, fingerprint


class BeliefError(ContextError):
    """The public context cannot generate a legal hidden world."""


@dataclass(frozen=True)
class SampledWorld:
    context_hash: str
    belief_version: str
    seed: int
    sample_id: int
    hidden_hands: tuple
    wall: tuple
    dead_wall: int
    fingerprint: str

    @property
    def live_wall(self):
        return self.wall[self.dead_wall:]

    @property
    def world_fingerprint(self):
        return self.fingerprint

    def public_metadata(self):
        """Metadata safe to put in a teacher report; no hidden material."""
        return {
            "context_hash": self.context_hash,
            "belief_version": self.belief_version,
            "seed": self.seed, "sample_id": self.sample_id,
            "world_fingerprint": self.fingerprint,
            "wall_length": len(self.wall), "dead_wall": self.dead_wall,
        }


def _derived_seed(context_hash, belief_version, seed, sample_id):
    payload = f"{context_hash}|{belief_version}|{int(seed)}|{int(sample_id)}"
    return int.from_bytes(hashlib.sha256(payload.encode()).digest()[:8], "big")


class BeliefSampler:
    """Deterministic, uniform assignment from public unseen material."""

    def __init__(self, context: PublicDecisionContext,
                 *, belief_version="uniform_unseen-v1", seed=0,
                 dead_wall=None):
        self.context = context
        self.belief_version = str(belief_version)
        self.seed = int(seed)
        self.dead_wall = int(context.dead_wall if dead_wall is None else dead_wall)
        if self.dead_wall < 0:
            raise BeliefError("dead_wall must be non-negative")

    def sample(self, sample_id: int) -> SampledWorld:
        context = self.context
        try:
            context.validate_for("rollout")
        except ContextError as exc:
            raise BeliefError(str(exc)) from exc
        if context.live_wall is None:
            raise BeliefError("live_wall is unknown")
        if self.dead_wall != context.dead_wall:
            raise BeliefError("sampler dead_wall differs from context")
        if any(x is None for x in context.concealed_counts):
            raise BeliefError("opponent concealed counts are unknown")
        hidden_sizes = [0 if s == context.hero_seat else
                        int(context.concealed_counts[s]) for s in range(4)]
        wall_size = int(context.live_wall) + self.dead_wall
        unseen = []
        for tile, count in enumerate(context.remaining):
            if count < 0:
                raise BeliefError(f"visible count exceeds four at tile {tile}")
            unseen.extend([tile] * count)
        expected = sum(hidden_sizes) + wall_size
        if len(unseen) != expected:
            raise BeliefError(
                f"unseen size {len(unseen)} != hidden+wall {expected}")
        rng = random.Random(_derived_seed(
            context.context_hash, self.belief_version, self.seed, sample_id))
        rng.shuffle(unseen)
        cursor = 0
        hands = []
        for s, size in enumerate(hidden_sizes):
            picked = unseen[cursor:cursor + size]
            cursor += size
            counts = [0] * 34
            for tile in picked:
                counts[tile] += 1
            hands.append(tuple(counts))
        wall = tuple(unseen[cursor:cursor + wall_size])
        if len(wall) != wall_size:
            raise BeliefError("wall assignment is incomplete")
        # Game._draw pops from the end, so the sampled order is stored as a
        # complete wall with its final live card at the end.  The first
        # ``dead_wall`` entries remain reserved and are never popped.
        by_tile = [0] * 34
        for row in hands:
            for tile, count in enumerate(row):
                by_tile[tile] += count
        for tile in wall:
            by_tile[tile] += 1
        for tile, count in enumerate(context.visible):
            if by_tile[tile] + count != 4:
                raise BeliefError(f"material conservation failed at tile {tile}")
        fp_payload = {
            "context_hash": context.context_hash,
            "belief_version": self.belief_version,
            "seed": self.seed, "sample_id": int(sample_id),
            "hidden_hands": hands, "wall": wall, "dead_wall": self.dead_wall,
        }
        return SampledWorld(
            context_hash=context.context_hash,
            belief_version=self.belief_version, seed=self.seed,
            sample_id=int(sample_id), hidden_hands=tuple(hands), wall=wall,
            dead_wall=self.dead_wall, fingerprint=fingerprint(fp_payload, 24))

    def iter_samples(self, start=0, count=1):
        for sample_id in range(int(start), int(start) + int(count)):
            yield self.sample(sample_id)


def sample_world(context, sample_id, seed=0, belief_version="uniform_unseen-v1"):
    return BeliefSampler(context, seed=seed,
                         belief_version=belief_version).sample(sample_id)
