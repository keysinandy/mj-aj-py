"""Weighted hidden-world particles conditioned on public events."""

from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import math
import random
from typing import Any, Iterable, Mapping

from ..decision.context import ContextError, PublicDecisionContext
from ..decision.profile import fingerprint
from ..rollout.belief import BeliefError as UniformBeliefError
from ..rollout.belief import SampledWorld, BeliefSampler
from ..rollout.simulator import actor_view, build_world_game
from ..models.opponent_policy import ActorPolicy, HeuristicLikelihoodPolicy
from .events import InformationHistory, PublicEvent
from .likelihood import smoothed_likelihood
from .profile import BeliefProfile, belief_profile_from_json
from .resample import (effective_sample_size, entropy, normalize_log_weights,
                       systematic_resample, weighted_index)


class BeliefError(ContextError):
    """The public context or event cannot support belief-v2."""


@dataclass
class Particle:
    """A posterior particle.

    ``state`` is an offline-only mutable Game restoration cache.  It is never
    returned by ``as_json`` and is deliberately not part of the public belief
    fingerprint payload.
    """

    world: SampledWorld
    weight: float
    log_weight: float
    state: Any = None

    def safe_metadata(self) -> dict[str, Any]:
        return {
            "context_hash": self.world.context_hash,
            "belief_version": self.world.belief_version,
            "sample_id": self.world.sample_id,
            "world_fingerprint": self.world.fingerprint,
            "weight": self.weight,
        }


def _seed_for(*values) -> int:
    raw = "|".join(str(value) for value in values).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def _zero_hero_world(game, context) -> SampledWorld:
    """Rebind a fully restored Game to a new public context safely."""
    hands = []
    for seat in range(4):
        hands.append(tuple(0 for _ in range(34)) if seat == context.hero_seat
                     else tuple(int(value) for value in game.hands[seat]))
    wall = tuple(int(value) for value in game.wall)
    payload = {
        "context_hash": context.context_hash,
        "belief_version": "belief-v2-state",
        "hidden_hands": hands, "wall": wall,
        "dead_wall": int(context.dead_wall),
    }
    return SampledWorld(
        context_hash=context.context_hash,
        belief_version="belief-v2-state", seed=0, sample_id=0,
        hidden_hands=tuple(hands), wall=wall,
        dead_wall=int(context.dead_wall), fingerprint=fingerprint(payload, 24),
    )


class BeliefState:
    """Particle posterior over the hidden material of one public context."""

    def __init__(self, context: PublicDecisionContext, *, history=None,
                 profile: BeliefProfile | Mapping[str, Any] | None = None,
                 actor_policy: ActorPolicy | None = None, seed=None,
                 strict: bool = True, particles: Iterable[Particle] | None = None):
        if not isinstance(context, PublicDecisionContext):
            raise TypeError("BeliefState requires a PublicDecisionContext")
        self.context = context
        self.history = (history if isinstance(history, InformationHistory)
                        else InformationHistory())
        if profile is None:
            profile = BeliefProfile(seed=0 if seed is None else int(seed))
        elif isinstance(profile, Mapping):
            profile = belief_profile_from_json(profile)
        if not isinstance(profile, BeliefProfile):
            raise TypeError("profile must be a BeliefProfile or mapping")
        self.profile = profile
        self.actor_policy = actor_policy or HeuristicLikelihoodPolicy(
            evaluator=profile.likelihood_policy,
            temperature=profile.temperature,
            version=profile.likelihood_policy,
        )
        self.strict = bool(strict)
        self.reset_count = 0
        self.resample_count = 0
        self.update_count = 0
        self.last_reset_reason = None
        self.last_resample_reason = None
        self.degraded = bool(self.history.history_incomplete)
        self._particles: list[Particle] = []
        if particles is not None:
            self._particles = list(particles)
            self._normalize_existing()
        else:
            self._initialize_or_degrade()

    @classmethod
    def from_uniform(cls, context, *, particle_count=512, seed=0,
                     history=None, strict=True):
        profile = BeliefProfile(particle_count=particle_count, seed=seed)
        return cls(context, history=history, profile=profile, strict=strict)

    @property
    def particles(self) -> tuple[Particle, ...]:
        return tuple(self._particles)

    @property
    def worlds(self) -> tuple[SampledWorld, ...]:
        return tuple(particle.world for particle in self._particles)

    @property
    def weights(self) -> tuple[float, ...]:
        return tuple(particle.weight for particle in self._particles)

    @property
    def particle_count(self) -> int:
        return len(self._particles)

    @property
    def ess(self) -> float:
        return effective_sample_size(self.weights)

    @property
    def weight_entropy(self) -> float:
        return entropy(self.weights)

    @property
    def history_hash(self) -> str:
        return self.history.history_hash

    @property
    def fingerprint(self) -> str:
        return fingerprint({
            "schema": "belief-state-v2",
            "context_hash": self.context.context_hash,
            "history_hash": self.history_hash,
            "profile_fingerprint": self.profile.fingerprint,
            "actor_policy_fingerprint": self.actor_policy.fingerprint,
            "weights": [round(float(value), 15) for value in self.weights],
            "world_fingerprints": [particle.world.fingerprint
                                   for particle in self._particles],
            "reset_count": self.reset_count,
            "resample_count": self.resample_count,
        }, 32)

    def _sampler(self):
        # History is part of the sampling stream.  This makes a new event
        # history a new belief artifact without consulting hidden truth.
        version = (f"{self.profile.version}|{self.profile.fingerprint}|"
                   f"{self.history_hash}")
        return BeliefSampler(self.context, belief_version=version,
                             seed=self.profile.seed)

    def _initialize_or_degrade(self):
        try:
            self.context.validate_for("rollout")
            sampler = self._sampler()
            self._particles = [
                Particle(world=sampler.sample(i),
                         weight=1.0 / self.profile.particle_count,
                         log_weight=-math.log(self.profile.particle_count))
                for i in range(self.profile.particle_count)
            ]
        except (ContextError, UniformBeliefError, ValueError) as exc:
            if self.strict:
                raise BeliefError(str(exc)) from exc
            self._particles = []
            self.degraded = True
            self.last_reset_reason = f"unsupported_context:{type(exc).__name__}"

    def _normalize_existing(self):
        if not self._particles:
            return
        values = normalize_log_weights(p.log_weight for p in self._particles)
        if values is None:
            self._particles = []
            return
        weights, _ = values
        for particle, weight in zip(self._particles, weights):
            particle.weight = weight
            particle.log_weight = math.log(weight) if weight > 0 else -math.inf

    def _state_for(self, particle: Particle):
        if particle.state is None:
            try:
                particle.state = build_world_game(self.context, particle.world)
            except Exception:
                return None
        return particle.state

    def _reset_public(self, reason: str):
        self.reset_count += 1
        self.last_reset_reason = str(reason)
        self.degraded = True
        self._initialize_or_degrade()

    def _apply_draw_observation(self, game, actor):
        """Advance a draw only when the action transition did not already do so."""
        if actor is None:
            return
        actor = int(actor)
        if game.current_seat() == actor and game.drawn[actor] is None and not game.done:
            game._draw(actor)

    def update(self, event: PublicEvent | Mapping[str, Any], *, context=None):
        """Condition particles on one observed event.

        ``context`` is the optional authoritative post-event public context.
        Supplying it is required before using the posterior for a later root
        search; without it the method still provides diagnostics and keeps an
        offline Game cache for the next event.
        """
        event = (event if isinstance(event, PublicEvent)
                 else PublicEvent.from_json(event))
        if context is not None and not isinstance(context, PublicDecisionContext):
            raise TypeError("context must be a PublicDecisionContext")
        old_context = self.context
        new_history = self.history.append(event)
        if not self._particles:
            self.history = new_history
            if context is not None:
                self.context = context
            self._reset_public("no_particles_before_update")
            return self

        action = event.action
        new_particles: list[Particle] = []
        for particle in self._particles:
            game = self._state_for(particle)
            if game is None:
                new_particles.append(Particle(particle.world, 0.0, -math.inf))
                continue
            candidate = game
            log_weight = particle.log_weight
            try:
                if event.event_type == "DRAW_PUBLIC":
                    self._apply_draw_observation(candidate, event.actor)
                    likelihood = 1.0
                elif action is None or event.event_type in ("ROUND_START", "ROUND_END"):
                    likelihood = 1.0
                else:
                    legal = tuple(int(value) for value in candidate.legal_actions())
                    if event.actor is not None and candidate.current_seat() != int(event.actor):
                        likelihood = 0.0
                    elif (event.legal_actions_before is not None and
                          set(legal) != set(event.legal_actions_before)):
                        likelihood = 0.0
                    elif int(action) not in legal:
                        # Hard rule constraint: epsilon is never applied here.
                        likelihood = 0.0
                    else:
                        view = actor_view(candidate, int(event.actor))
                        likelihood = smoothed_likelihood(
                            self.actor_policy, action, view, legal,
                            self.profile.epsilon)
                    if likelihood > 0:
                        candidate.step(int(action))
                if likelihood <= 0 or not math.isfinite(likelihood):
                    new_particles.append(Particle(particle.world, 0.0, -math.inf))
                else:
                    new_particles.append(Particle(
                        particle.world, 0.0, log_weight + math.log(likelihood), candidate))
            except Exception:
                new_particles.append(Particle(particle.world, 0.0, -math.inf))

        self.history = new_history
        self.update_count += 1
        values = normalize_log_weights(p.log_weight for p in new_particles)
        if values is None:
            self.context = context or old_context
            self._particles = []
            self._reset_public("posterior_zero_mass")
            return self
        weights, _ = values
        for particle, weight in zip(new_particles, weights):
            particle.weight = weight
            particle.log_weight = math.log(weight) if weight > 0 else -math.inf
        self._particles = new_particles
        if context is not None:
            self._rebind_context(context)
        self.degraded = self.degraded or new_history.history_incomplete
        threshold = self.profile.ess_ratio * max(1, len(self._particles))
        if self.ess < threshold:
            self._resample(reason="ess_below_threshold")
        if len(self._particles) < self.profile.min_effective_particles:
            self._reset_public("effective_particles_below_minimum")
        return self

    def _rebind_context(self, context: PublicDecisionContext):
        previous = self.context
        self.context = context
        for particle in self._particles:
            if particle.state is not None:
                try:
                    particle.world = _zero_hero_world(particle.state, context)
                except Exception:
                    particle.state = None
            if particle.world.context_hash != context.context_hash:
                particle.state = None
        # If an event was applied to an old context and the resulting Game was
        # unavailable, rebuilding from the new public context is safer than
        # pretending that the old hidden state is still aligned.
        if previous.context_hash != context.context_hash and any(
                p.state is None for p in self._particles):
            for particle in self._particles:
                try:
                    particle.state = build_world_game(context, particle.world)
                except Exception:
                    particle.state = None

    def _resample(self, *, reason):
        if not self._particles:
            return
        indexes = systematic_resample(
            self.weights, len(self._particles),
            seed=_seed_for(self.profile.seed, self.history_hash,
                           self.resample_count, reason))
        count = len(indexes)
        cloned = []
        log_weight = -math.log(count)
        for index in indexes:
            source = self._particles[index]
            cloned.append(Particle(source.world, 1.0 / count, log_weight,
                                   copy.deepcopy(source.state)))
        self._particles = cloned
        self.resample_count += 1
        self.last_resample_reason = str(reason)

    def sample_world(self, sample_id: int) -> SampledWorld:
        """Root-sample one current posterior world deterministically."""
        if not self._particles:
            raise BeliefError("cannot sample from a degraded empty posterior")
        index = weighted_index(
            self.weights,
            seed=_seed_for(self.profile.seed, self.history_hash,
                           self.profile.fingerprint, int(sample_id)))
        world = self._particles[index].world
        if world.context_hash != self.context.context_hash:
            raise BeliefError("particle/context fingerprint mismatch")
        return world

    # Alias used by search adapters.
    sample_for_simulation = sample_world

    def _state_or_world(self, particle):
        return particle.state if particle.state is not None else particle.world

    def marginals(self) -> dict[str, Any]:
        """Return aggregate hidden-material marginals only."""
        if not self._particles:
            return {
                "opponent_hand_probability": {},
                "opponent_hand_expected_count": {},
                "live_wall_probability": [0.0] * 34,
                "dead_wall_probability": [0.0] * 34,
            }
        opponent_probability = {str(seat): [0.0] * 34 for seat in range(4)
                                if seat != self.context.hero_seat}
        opponent_expected = {str(seat): [0.0] * 34 for seat in range(4)
                             if seat != self.context.hero_seat}
        live = [0.0] * 34
        dead = [0.0] * 34
        for particle in self._particles:
            weight = float(particle.weight)
            # After an observed transition the mutable restoration cache has
            # the hidden material at the new information state.  Falling
            # back to the immutable sampled world is correct only before the
            # first update; using it afterwards would report stale wall and
            # opponent-hand marginals.
            source = particle.state
            for seat in opponent_probability:
                row = (source.hands[int(seat)] if source is not None
                       else particle.world.hidden_hands[int(seat)])
                for tile, count in enumerate(row):
                    if count:
                        opponent_probability[seat][tile] += weight
                    opponent_expected[seat][tile] += weight * count
            wall = (tuple(source.wall) if source is not None
                    else particle.world.wall)
            dead_wall = (int(particle.world.dead_wall) if source is None
                         else min(int(particle.world.dead_wall), len(wall)))
            for tile in wall[dead_wall:]:
                live[tile] += weight
            for tile in wall[:dead_wall]:
                dead[tile] += weight
        return {
            "opponent_hand_probability": opponent_probability,
            "opponent_hand_expected_count": opponent_expected,
            "live_wall_probability": live,
            "dead_wall_probability": dead,
        }

    def summary(self) -> dict[str, Any]:
        value = {
            "schema": "belief-state-v2",
            "context_hash": self.context.context_hash,
            "history_hash": self.history_hash,
            "profile_fingerprint": self.profile.fingerprint,
            "actor_policy_fingerprint": self.actor_policy.fingerprint,
            "particle_count": self.particle_count,
            "ess": self.ess,
            "entropy": self.weight_entropy,
            "reset_count": self.reset_count,
            "resample_count": self.resample_count,
            "history_incomplete": self.history.history_incomplete,
            "belief_degraded": self.degraded,
            "last_reset_reason": self.last_reset_reason,
            "last_resample_reason": self.last_resample_reason,
            "marginals": self.marginals(),
            "fingerprint": self.fingerprint,
        }
        return value

    def as_json(self):
        return self.summary()
