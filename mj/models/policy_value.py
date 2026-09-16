"""Policy/value network and its public-information feature contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping

try:  # NumPy/Torch are optional for rules, belief and fallback consumers.
    import numpy as np
except ImportError:  # pragma: no cover - exercised in minimal deployments
    np = None

try:
    import torch
    from ..model import Net
except ImportError:  # pragma: no cover - exercised in minimal deployments
    torch = None

    class Net:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise ImportError(
                "PolicyValueNet requires optional numpy and torch dependencies")

from ..decision.profile import fingerprint
from ..decision.context import PublicDecisionContext
try:
    from ..features import N_ACTIONS, N_PLANES, N_SCALARS
except ImportError:  # Keep manifests loadable in a dependency-free process.
    N_ACTIONS, N_PLANES, N_SCALARS = 109, 75, 8


def _feature_module():
    from .. import features
    return features


class ModelInferenceError(RuntimeError):
    """The model could not produce a finite legal-action distribution."""


@dataclass(frozen=True)
class ValueFeatureContract:
    schema: str = "value-feature-contract-v1"
    n_planes: int = N_PLANES + 4
    n_scalars: int = N_SCALARS + 4
    action_space: int = N_ACTIONS
    oracle: bool = False
    belief_summary: bool = True
    history_encoding: str = "history-length-and-integrity-v1"
    rules_version: str = "hangzhou-platform-guide-v34"

    def __post_init__(self):
        if self.schema != "value-feature-contract-v1":
            raise ValueError(f"unsupported value feature schema: {self.schema}")
        if bool(self.oracle):
            raise ValueError("value feature contract must set oracle=False")
        if int(self.action_space) != N_ACTIONS:
            raise ValueError("value feature action space must be 109")
        if int(self.n_planes) < N_PLANES or int(self.n_scalars) < N_SCALARS:
            raise ValueError("value feature dimensions cannot shrink base features")
        object.__setattr__(self, "n_planes", int(self.n_planes))
        object.__setattr__(self, "n_scalars", int(self.n_scalars))
        object.__setattr__(self, "action_space", int(self.action_space))

    def payload(self):
        return asdict(self)

    @property
    def fingerprint(self):
        return fingerprint(self.payload(), 24)

    def as_json(self):
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value


@dataclass(frozen=True)
class PolicyValueModelManifest:
    """Loadable model identity; every semantic input is explicit."""

    schema: str = "policy-value-model-manifest-v1"
    model_version: str = "policy-v1"
    feature_contract_fingerprint: str = ""
    belief_profile_fingerprint: str = ""
    search_profile_fingerprint: str = ""
    opponent_policy_version: str = ""
    leaf_version: str = "terminal-rollout-v1"
    rules_version: str = "hangzhou-platform-guide-v34"
    action_space: int = N_ACTIONS
    oracle: bool = False
    calibrated: bool = False
    calibration_fingerprint: str = ""
    training_source: str = "search-distillation"
    policy_iteration: int = 0
    blocks: int = 2
    width: int = 64

    def __post_init__(self):
        if self.schema != "policy-value-model-manifest-v1":
            raise ValueError(f"unsupported model manifest schema: {self.schema}")
        if not str(self.model_version):
            raise ValueError("model_version must not be empty")
        if int(self.action_space) != N_ACTIONS:
            raise ValueError("model action_space must be 109")
        if bool(self.oracle):
            raise ValueError("policy/value model manifest must set oracle=False")
        if int(self.policy_iteration) < 0 or int(self.blocks) < 0 or int(self.width) <= 0:
            raise ValueError("invalid model architecture/iteration fields")
        if bool(self.calibrated) and not self.calibration_fingerprint:
            raise ValueError("calibrated model must carry calibration fingerprint")
        object.__setattr__(self, "action_space", int(self.action_space))
        object.__setattr__(self, "policy_iteration", int(self.policy_iteration))
        object.__setattr__(self, "blocks", int(self.blocks))
        object.__setattr__(self, "width", int(self.width))

    def payload(self):
        return asdict(self)

    @property
    def fingerprint(self):
        return fingerprint(self.payload(), 24)

    def as_json(self):
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value


def policy_value_manifest_from_json(data: Mapping[str, Any]) -> PolicyValueModelManifest:
    value = dict(data)
    supplied = value.pop("fingerprint", None)
    allowed = set(PolicyValueModelManifest.__dataclass_fields__)
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("unknown policy/value manifest fields: " +
                         ", ".join(sorted(unknown)))
    manifest = PolicyValueModelManifest(**value)
    if supplied is not None and supplied != manifest.fingerprint:
        raise ValueError(
            f"model manifest fingerprint mismatch: supplied={supplied} "
            f"actual={manifest.fingerprint}")
    return manifest


VALUE_CONTRACT_SCHEMA = "value-transform-contract-v1"


@dataclass(frozen=True)
class ValueTransformContract:
    """Frozen score transform for the tanh value head.

    ``forward`` normalizes raw round-score points into the declared target
    range; ``inverse_transform_value`` maps a network prediction back to
    points.  Training, calibration reporting and the search leaf must all use
    this single contract (a mismatch was the historical value_scale=24 bug).
    """

    schema: str = VALUE_CONTRACT_SCHEMA
    version: str = "value-v2"
    score_units: str = "hero_round_score_points"
    scale: float = 96.0
    clip_normalized: float = 1.0
    output_activation: str = "tanh"
    inverse_transform: str = "multiply-scale-v1"

    def __post_init__(self):
        if self.schema != VALUE_CONTRACT_SCHEMA:
            raise ValueError(f"unsupported value contract schema: {self.schema}")
        if not str(self.version):
            raise ValueError("value contract version must not be empty")
        if self.score_units != "hero_round_score_points":
            raise ValueError("value contract must target round-score points")
        scale = float(self.scale)
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError("value contract scale must be finite and positive")
        clip = float(self.clip_normalized)
        if not math.isfinite(clip) or clip <= 0:
            raise ValueError("value contract clip must be finite and positive")
        if self.output_activation != "tanh":
            raise ValueError("value contract supports only the tanh head")
        if self.inverse_transform != "multiply-scale-v1":
            raise ValueError("unsupported value inverse transform")
        object.__setattr__(self, "scale", scale)
        object.__setattr__(self, "clip_normalized", clip)

    @property
    def output_range(self):
        return (-float(self.clip_normalized), float(self.clip_normalized))

    def forward(self, score):
        """Raw round-score points -> declared normalized target."""
        value = float(score) / self.scale
        clip = float(self.clip_normalized)
        return min(clip, max(-clip, value))

    def inverse_transform_value(self, prediction):
        """Declared normalized prediction -> raw round-score points."""
        prediction = float(prediction)
        if not math.isfinite(prediction):
            raise ValueError("value prediction must be finite")
        return prediction * self.scale

    def payload(self):
        return asdict(self)

    @property
    def fingerprint(self):
        return fingerprint(self.payload(), 24)

    def as_json(self):
        value = self.payload()
        value["fingerprint"] = self.fingerprint
        return value


def value_contract_from_json(data: Mapping[str, Any]) -> ValueTransformContract:
    value = dict(data)
    supplied = value.pop("fingerprint", None)
    allowed = set(ValueTransformContract.__dataclass_fields__)
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("unknown value contract fields: " +
                         ", ".join(sorted(unknown)))
    contract = ValueTransformContract(**value)
    if supplied is not None and supplied != contract.fingerprint:
        raise ValueError("value contract fingerprint mismatch")
    return contract


def _public_game_from_context(context):
    """Construct a feature-only Game with all hidden material zeroed."""
    from ..game import Game

    g = Game.__new__(Game)
    g.dealer = context.dealer if context.dealer is not None else 0
    g.base = context.base if context.base is not None else 1
    g.you_cai_bi_kao = bool(context.you_cai_bi_kao)
    g._kong_draw = bool(context.kong_draw)
    g.hands = [[0] * 34 for _ in range(4)]
    g.hands[context.hero_seat] = list(context.hand)
    full_wall = context.full_wall
    if full_wall is None:
        full_wall = context.dead_wall
    g.wall = [0] * max(0, int(full_wall))
    g.melds = [[tuple(m) for m in row] for row in context.melds]
    g.discards = [list(row) for row in context.discards]
    g.drawn = [None] * 4
    g.drawn[context.hero_seat] = context.drawn
    g.chows = list(context.chows)
    g.turn = context.turn if context.turn is not None else context.hero_seat
    g.phase = "discard" if context.phase in ("draw", "discard") else "react"
    g.pending = ((context.pending_owner, context.pending_tile)
                 if context.pending_owner is not None and
                 context.pending_tile is not None else None)
    g.freeze = int(context.freeze)
    g.freezer = context.freezer
    g.chain = [0 if value is None else int(value)
               for value in context.chain_counts]
    g.chain_piao = [0 if value is None else int(value)
                    for value in context.chain_piao_counts]
    g.scores = [0] * 4
    g.done = False
    g.result = None
    g.react_seq = list(context.react_seq)
    g.react_idx = int(context.react_index or 0)
    g._n_claim = int(context.react_claim_count or 0)
    return g


def _belief_planes_and_scalars(belief, history):
    if belief is None:
        planes = [[0.0] * 34 for _ in range(4)]
        scalars = [0.0, 0.0, 0.0, 0.0]
        return planes, scalars
    summary = belief.summary() if hasattr(belief, "summary") else dict(belief)
    marginals = summary.get("marginals") or {}
    expected = marginals.get("opponent_hand_expected_count") or {}
    opponent = [0.0] * 34
    for row in expected.values():
        for tile, value in enumerate(row[:34]):
            opponent[tile] += float(value) / 4.0
    live = [float(value) for value in
            (marginals.get("live_wall_probability") or [0.0] * 34)]
    dead = [float(value) for value in
            (marginals.get("dead_wall_probability") or [0.0] * 34)]
    occupancy = [0.0] * 34
    probability = marginals.get("opponent_hand_probability") or {}
    for row in probability.values():
        for tile, value in enumerate(row[:34]):
            occupancy[tile] += float(value) / 3.0
    count = max(1, int(summary.get("particle_count") or 1))
    entropy_value = float(summary.get("entropy") or 0.0)
    entropy_scale = max(1.0, math.log(count))
    scalars = [
        min(1.0, max(0.0, float(summary.get("ess") or 0.0) / count)),
        min(1.0, max(0.0, entropy_value / entropy_scale)),
        1.0 if summary.get("belief_degraded") else 0.0,
        min(1.0, len(getattr(history, "events", ())) / 256.0)
        if history is not None else 0.0,
    ]
    return [opponent, live, dead, occupancy], scalars


def extract_value_features(context, *, history=None, belief=None):
    """Extract only public/hero information for value or policy training."""
    if np is None:
        raise ImportError("extract_value_features requires optional numpy")
    history = history or getattr(belief, "history", None)
    game = _public_game_from_context(context)
    features = _feature_module()
    planes, scalars = features.extract(game, context.hero_seat, oracle=False)
    extra_planes, extra_scalars = _belief_planes_and_scalars(belief, history)
    return (np.concatenate([planes, np.asarray(extra_planes, dtype=np.float32)], axis=0),
            np.concatenate([scalars, np.asarray(extra_scalars, dtype=np.float32)]))


def extract_game_value_features(game, seat):
    """Feature extraction for an actor view; never requests oracle planes."""
    if np is None:
        raise ImportError("extract_game_value_features requires optional numpy")
    features = _feature_module()
    planes, scalars = features.extract(game, seat, oracle=False)
    return (np.concatenate([planes, np.zeros((4, 34), dtype=np.float32)], axis=0),
            np.concatenate([scalars, np.zeros(4, dtype=np.float32)]))


class PolicyValueNet(Net):
    """109-action policy/value network with legal-mask-safe inference."""

    def __init__(self, *, blocks=2, width=64, n_planes=None,
                 n_scalars=None, n_actions=N_ACTIONS, manifest=None):
        if torch is None or np is None:
            raise ImportError(
                "PolicyValueNet requires optional numpy and torch dependencies")
        feature_contract = ValueFeatureContract(
            n_planes=n_planes or ValueFeatureContract().n_planes,
            n_scalars=n_scalars or ValueFeatureContract().n_scalars,
            action_space=n_actions)
        if manifest is None:
            manifest = PolicyValueModelManifest(
                feature_contract_fingerprint=feature_contract.fingerprint,
                blocks=blocks, width=width)
        elif isinstance(manifest, Mapping):
            manifest = policy_value_manifest_from_json(manifest)
        if not isinstance(manifest, PolicyValueModelManifest):
            raise TypeError("manifest must be a PolicyValueModelManifest or mapping")
        if manifest.action_space != int(n_actions):
            raise ValueError("model manifest/action-space mismatch")
        if manifest.feature_contract_fingerprint and (
                manifest.feature_contract_fingerprint != feature_contract.fingerprint):
            raise ValueError("model manifest/feature contract mismatch")
        if manifest.blocks != int(blocks) or manifest.width != int(width):
            raise ValueError("model manifest/architecture mismatch")
        super().__init__(n_planes=feature_contract.n_planes,
                         n_scalars=feature_contract.n_scalars,
                         n_actions=n_actions, blocks=blocks, width=width)
        self.feature_contract = feature_contract
        self.manifest = manifest
        self.model_version = manifest.model_version

    def forward_masked(self, planes, scalars, legal_mask):
        logits, value = self.forward(planes, scalars)
        mask = torch.as_tensor(legal_mask, dtype=torch.bool, device=logits.device)
        if mask.ndim == 1:
            mask = mask.unsqueeze(0)
        if not mask.any(dim=-1).all():
            raise ModelInferenceError("legal mask contains no action")
        return logits.masked_fill(~mask, float("-inf")), value

    def policy_distribution(self, planes, scalars, legal_mask):
        from .opponent_policy import ActionDistribution

        with torch.no_grad():
            logits, _ = self.forward_masked(planes, scalars, legal_mask)
            row = logits[0]
            mask = torch.as_tensor(legal_mask, dtype=torch.bool,
                                   device=row.device)
            if mask.ndim > 1:
                mask = mask[0]
            legal_logits = row[mask]
            if legal_logits.numel() == 0 or not torch.isfinite(legal_logits).all():
                raise ModelInferenceError("model produced a non-finite legal logit")
            probs = torch.softmax(row, dim=-1)
            if not torch.isfinite(probs[mask]).all():
                raise ModelInferenceError("model produced a non-finite distribution")
        mask = np.asarray(legal_mask, dtype=bool)
        if mask.ndim > 1:
            mask = mask[0]
        features = _feature_module()
        actions = tuple(features.flat_to_action(i)
                       for i, value in enumerate(mask) if value)
        values = tuple(float(probs[i].item()) for i, value in enumerate(mask) if value)
        raw_logits = tuple(float(row[i].item()) for i, value in enumerate(mask) if value)
        return ActionDistribution(actions, values, raw_logits,
                                  version=self.model_version)

    def predict_game(self, game, seat, *, legal_actions=None,
                     distribution_version=None, history=None, belief=None):
        if legal_actions is None:
            legal_actions = tuple(game.legal_actions())
        legal_actions = tuple(int(action) for action in legal_actions)
        mask = [False] * N_ACTIONS
        for action in legal_actions:
            mask[_feature_module().action_to_flat(action)] = True
        if history is not None or belief is not None:
            context_factory = (PublicDecisionContext.from_game_complete
                               if hasattr(game, "_public_history") else
                               PublicDecisionContext.from_game)
            context = context_factory(game, seat)
            planes, scalars = self.extract_features(
                context, history=history, belief=belief)
        else:
            planes, scalars = extract_game_value_features(game, seat)
        distribution = self.policy_distribution(
            torch.as_tensor(planes[None], dtype=torch.float32),
            torch.as_tensor(scalars[None], dtype=torch.float32), mask)
        if distribution_version and distribution.version != distribution_version:
            distribution = type(distribution)(
                distribution.actions, distribution.probabilities,
                distribution.logits, distribution_version)
        return distribution

    def extract_features(self, context, *, history=None, belief=None):
        return extract_value_features(context, history=history, belief=belief)

    def select_action(self, planes, scalars, legal_mask):
        """Return the highest masked logit; failures remain explicit."""
        with torch.no_grad():
            logits, _ = self.forward_masked(planes, scalars, legal_mask)
            row = logits[0]
            index = int(torch.argmax(row).item())
            if not torch.isfinite(row[index]):
                raise ModelInferenceError("no finite masked action")
        return _feature_module().flat_to_action(index)
