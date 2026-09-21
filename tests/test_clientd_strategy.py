"""Task 2.2 验收:clientd 策略工厂(预算注入/永不回退/非法参数)。

不依赖 torch:policy 仅测 ckpt 缺失校验(policy_player 需 torch,测试
不实例化其网络)。预算注入与"永不回退"以 shape-v2 eval 的 budget 摘要、
ProfileSpec 值与小黑盒对局断言。
"""

import math

import pytest

from mj.game import Game
from mj.bot import choose_shape_v2_action
from mj.decision.profile import ProfileSpec
from mj.clientd.strategies import (
    make_player, resolve_budget, NEVER_TIME_MS, NEVER_NODE_BUDGET,
)
from mj.clientd.errors import ValidationError


def test_resolve_budget_finite():
    tb, nb = resolve_budget(10)
    assert tb == 10.0 and nb is None


def test_resolve_budget_never():
    for w in ("never", "inf", "infinite", "none", None, "-"):
        tb, nb = resolve_budget(w)
        assert tb == NEVER_TIME_MS
        assert nb == NEVER_NODE_BUDGET


def test_resolve_budget_rejects_bad():
    for bad in ("abc", -5, float("nan")):
        with pytest.raises(ValidationError):
            resolve_budget(bad)


def test_bot_shape_v2_budget_injection():
    player = make_player({"strategy": "bot", "evaluator": "shape-v2",
                          "fallback_ms": 10})
    assert player.profile is not None
    assert player.profile.time_budget_ms == 10.0
    # 决策记录(shape-v2 eval)的 budget 摘要反映注入预算
    g = Game(seed=7)
    _, ev = choose_shape_v2_action(g, 0, profile=player.profile)
    assert ev["budget"]["time_limit_ms"] == 10.0


def test_bot_default_uses_legacy_v2():
    player = make_player({"strategy": "bot"})
    assert player.evaluator == "legacyV2"
    assert player.profile is not None
    assert player.profile.name == "legacyV2"


def test_bot_legacy_v2_alias_is_accepted_and_normalized():
    player = make_player({"strategy": "bot", "evaluator": "legacy-v2"})
    assert player.evaluator == "legacyV2"
    assert player.profile.name == "legacyV2"


def test_bot_never_fallbacks_no_budget_fallback():
    player = make_player({"strategy": "bot", "evaluator": "shape-v2",
                          "fallback_ms": "never"})
    assert player.profile.time_budget_ms == NEVER_TIME_MS
    assert player.profile.node_budget == NEVER_NODE_BUDGET
    # 小黑盒:带"永不回退"的 bot 跑一局不抛错
    g = Game(seed=11)
    guard = 0
    while not g.done and guard < 500:
        seat = g.current_seat()
        if seat == 0:
            act = player(g, seat)
        else:
            from mj.bot import choose_action
            act = choose_action(g, seat)
        assert act in list(g.legal_actions())
        g.step(act)
        guard += 1


def test_bot_invalid_evaluator():
    with pytest.raises(ValidationError):
        make_player({"strategy": "bot", "evaluator": "nope"})


def test_unknown_strategy():
    with pytest.raises(ValidationError):
        make_player({"strategy": "random"})  # random 由竞技场按座位配置


def test_policy_requires_ckpt_without_torch():
    with pytest.raises(ValidationError):
        make_player({"strategy": "policy"})
    with pytest.raises(ValidationError):
        make_player({"strategy": "policy", "ckpt": "/does/not/exist.pt"})


def test_policy_v3_threshold_validation():
    for bad in (-0.1, 1.5, "high"):
        with pytest.raises(ValidationError):
            make_player({"strategy": "policy-v3",
                         "confidence_threshold": bad})
    player = make_player({"strategy": "policy-v3",
                          "confidence_threshold": 0.5})
    assert player.strategy == "policy-v3"
    assert player.profile is not None
    assert player.profile.confidence_threshold == 0.5


def test_player_unwraps_tuple_result():
    # shape-v2 的 play 返回 (action, eval) 元组,Player.__call__ 解包为 int
    player = make_player({"strategy": "bot", "evaluator": "shape-v2",
                          "fallback_ms": 50})
    g = Game(seed=13)
    act = player(g, g.current_seat())
    assert isinstance(act, int)
