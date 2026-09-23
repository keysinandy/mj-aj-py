"""clientd 策略工厂:把策略配置解析为 (g, seat) -> action 的 Player。

策略:
- policy:    policy_player(ckpt)(神经网络 argmax);
 - bot:       启发式,评价器 legacy(线上 v2) / legacy-v1(回滚) / legacyV2
             (兼容 weighted-two-ply-frontier-v1) / shape-v1 / shape-v2;
             shape-v2 经 ProfileSpec 注入回退窗口;
- policy-v3: PolicyV3Runtime,置信度阈值高级项。

Player.__call__ 统一返回动作 int(内部元组 (action, evaluation) 自动解包),
供竞技场/回放驱动。非法参数在入口抛 ValidationError,不落会话。

"永不回退"以大型有限哨兵实现(见 design.md D4 修正):time_budget_ms=10**9,
node_budget=10**12——JSON(allow_nan=False)与 int() 转换均安全,等效不限。
"""

from __future__ import annotations

import math
import os

from ..game import Game
from ..bot import choose_action, choose_shape_v2_action
from ..legacy_eval import (
    DEFAULT_BOT_EVALUATOR,
    LEGACY_V2_EVALUATORS,
    LegacyTwoPlyProfile,
    canonical_evaluator,
)
from ..decision.profile import ProfileSpec
from .errors import ValidationError

__all__ = ["Player", "make_player", "resolve_budget", "NEVER_TIME_MS",
           "NEVER_NODE_BUDGET"]

NEVER_TIME_MS = float(10 ** 9)     # ~11.6 天,远超任何对局
NEVER_NODE_BUDGET = 10 ** 12

VALID_EVALUATORS = ("legacy", "legacy-two-ply-v1", "legacy_v1", "legacy-v1",
                    *LEGACY_V2_EVALUATORS,
                    "shape-v1", "shape_v1", "shape",
                    "shape-v2", "shape_v2", "ev2", "policy-v3", "policy_v3")
_SUPPORTED_STRATEGIES = ("policy", "bot", "policy-v3")


def resolve_budget(window):
    """回退窗口 → (time_budget_ms, node_budget)。

    window: "never"/"inf"/None → 不限(哨兵);数值(可 "10.0"/10) → 时间预算,
    节点预算留默认。负数/非数值/非有限 → ValidationError。
    """
    if window is None or window == "" :
        return NEVER_TIME_MS, NEVER_NODE_BUDGET
    if isinstance(window, str):
        low = window.strip().lower()
        if low in ("never", "inf", "infinite", "none", "-"):
            return NEVER_TIME_MS, NEVER_NODE_BUDGET
    try:
        value = float(window)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"invalid fallback window {window!r}: expected ms number, "
            f"'never', or 'inf'") from exc
    if not math.isfinite(value) or value < 0:
        raise ValidationError(
            f"invalid fallback window {window!r}: must be finite and >= 0")
    return float(value), None


def _validate_confidence(value):
    try:
        c = float(value if value is not None else 0.0)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"invalid confidence_threshold {value!r}: must be in [0, 1]") from exc
    if not math.isfinite(c) or not 0 <= c <= 1:
        raise ValidationError(
            f"invalid confidence_threshold {value!r}: must be in [0, 1]")
    return c


class Player:
    """统一玩家封装;__call__ 返回动作 int,元组结果自动解包。"""

    def __init__(self, play, *, strategy, evaluator=None, profile=None):
        self._play = play
        self.strategy = strategy
        self.evaluator = evaluator
        self.profile = profile

    def __call__(self, game, seat):
        result = self._play(game, seat)
        return result[0] if isinstance(result, tuple) else result

    @property
    def meta(self):
        return {"strategy": self.strategy, "evaluator": self.evaluator,
                "profile_fingerprint": (self.profile.fingerprint
                                        if self.profile is not None else None)}


def make_bot_player(config):
    evaluator = canonical_evaluator(
        config.get("evaluator") or DEFAULT_BOT_EVALUATOR)
    if evaluator not in VALID_EVALUATORS:
        raise ValidationError(f"unknown bot evaluator {evaluator!r}")
    profile = None
    if evaluator in ("shape-v2", "shape_v2", "ev2"):
        time_ms, node_budget = resolve_budget(config.get("fallback_ms"))
        if node_budget is None:
            profile = ProfileSpec.shape_v2_discard(time_budget_ms=time_ms)
        else:
            profile = ProfileSpec.shape_v2_discard(
                time_budget_ms=time_ms, node_budget=node_budget)

        def _play(game, seat, _p=profile):
            return choose_shape_v2_action(game, seat, profile=_p)
    else:
        _ev = evaluator
        if _ev in (("legacy-two-ply-v1", "legacy_v1", "legacy-v1")
                   + LEGACY_V2_EVALUATORS):
            profile = (LegacyTwoPlyProfile.weighted_online()
                       if _ev in LEGACY_V2_EVALUATORS else
                       LegacyTwoPlyProfile.default())

        def _play(game, seat, _e=_ev):
            return choose_action(game, seat, evaluator=_e,
                                 return_evaluation=True)
    return Player(_play, strategy="bot", evaluator=evaluator,
                  profile=profile)


def make_policy_player(config):
    ckpt = config.get("ckpt")
    if not ckpt or not os.path.exists(str(ckpt)):
        raise ValidationError(
            f"policy strategy requires an existing checkpoint, got {ckpt!r}")
    if str(ckpt).endswith(".onnx"):
        from .onnx_player import OnnxPolicyPlayer
        player = OnnxPolicyPlayer(ckpt)
    else:
        from ..evaluate import policy_player  # lazy: imports torch
        player = policy_player(ckpt)

    def _play(game, seat, _p=player):
        return _p(game, seat)
    return Player(_play, strategy="policy", evaluator="policy")


def make_policy_v3_player(config):
    threshold = _validate_confidence(config.get("confidence_threshold"))
    from ..decision.policy_v3 import PolicyV3Runtime, PolicyV3Profile
    model = config.get("model")
    if isinstance(model, str) and model.endswith(".onnx"):
        from .onnx_player import OnnxPolicyPlayer
        model = OnnxPolicyPlayer(model)
    profile = PolicyV3Profile(confidence_threshold=threshold)
    runtime = PolicyV3Runtime(model=model, profile=profile)

    def _play(game, seat, _r=runtime):
        return _r.choose(game, seat, return_evaluation=True)
    return Player(_play, strategy="policy-v3", evaluator="policy-v3",
                  profile=profile)


def make_player(config):
    if not isinstance(config, dict):
        raise ValidationError("strategy config must be a mapping")
    strategy = config.get("strategy")
    if strategy not in _SUPPORTED_STRATEGIES:
        raise ValidationError(
            f"unknown strategy {strategy!r}; expected {list(_SUPPORTED_STRATEGIES)}")
    if strategy == "bot":
        return make_bot_player(config)
    if strategy == "policy":
        return make_policy_player(config)
    return make_policy_v3_player(config)
