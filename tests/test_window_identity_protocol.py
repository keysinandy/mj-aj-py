"""Fixture replay battery for the window-identity-protocol contract.

Each fixture in tests/fixtures/window_identity/ encodes one scenario from
openspec/changes/window-identity-protocol/protocol_fixture_contract.md and is
replayed through the real BotClient identity resolution.  The battery doubles
as the acceptance proof for the day the server starts carrying
``source_discard_seq`` on pending snapshots.
"""

import json
import os
from unittest import mock

import pytest

import mj.platform.bot_client as module
from mj.game import CHOW_LOW
from mj.platform.bot_client import BotClient
from scripts.window_acceptance import _logical_window_key
from test_window_recovery import (
    FakeClock, ScriptedServer, _snapshot, _finished, _choose_chi_or_draw,
)

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures",
                           "window_identity")


def _load(name):
    with open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def _bot():
    return BotClient(mock.Mock(), "b", lambda *_: -1, log=lambda _: None)


def _merge(fixture_snapshot):
    """Complete a fixture snapshot with the shared baseline fields."""
    base = _snapshot(phase=fixture_snapshot["phase"], turn=3, responding=[0],
                     discards=[[], [], [], ["6b"]], last_discard="6b",
                     window_deadline_ms=fixture_snapshot["window_deadline_ms"])
    base.update({k: v for k, v in fixture_snapshot.items() if k != "phase"})
    return base


def test_peng_chi_shared_identity_fixture_replays_and_submits():
    fx = _load("peng_chi_shared_identity.json")
    clock = FakeClock()
    peng, chi = _merge(fx["peng_snapshot"]), _merge(fx["chi_snapshot"])
    api = ScriptedServer([
        {"snapshot": peng, "seq": 5},
        {"snapshot": chi, "seq": 8},
        {"snapshot": chi, "seq": 8}, _finished(),
    ], clock, expected_seqs=[0, 0, 8, 8])
    bot = BotClient(api, "b", _choose_chi_or_draw, log=lambda _: None,
                    idle_sleep=0)
    with mock.patch.object(module, "time", clock):
        bot.play_game("g")

    key_peng = bot._window_key(
        bot._mirror_from_snapshot(peng, gid="g1"), "response_peng", snap=peng)
    key_chi = bot._window_key(
        bot._mirror_from_snapshot(chi, gid="g1"), "response_chi", snap=chi)
    assert key_peng.window_id == key_chi.window_id
    assert key_peng.window_id.identity_status == "authoritative"
    assert key_peng.window_id.identity_origin == "explicit_source_field"
    assert len(api.actions) == 1  # the chi window closed in-budget


def test_seq0_reanchor_retention_fixture():
    fx = _load("seq0_reanchor_retention.json")
    bot = _bot()
    before = bot._window_key(
        bot._mirror_from_snapshot(_merge(fx["pending_snapshot"]), gid="g1"),
        "response_chi", snap=_merge(fx["pending_snapshot"]))
    after = bot._window_key(
        bot._mirror_from_snapshot(_merge(fx["reanchor_snapshot"]), gid="g1"),
        "response_chi", snap=_merge(fx["reanchor_snapshot"]))
    assert before.window_id == after.window_id
    assert after.window_id.identity_status == "authoritative"
    assert after.window_id.identity_origin == "explicit_source_field"


def test_repeated_same_tile_discards_are_distidentities():
    fx = _load("repeated_same_tile_distinct.json")
    bot = _bot()
    first = bot._window_key(
        bot._mirror_from_snapshot(_merge(fx["first_window"]), gid="g1"),
        "response_chi", snap=_merge(fx["first_window"]))
    second = bot._window_key(
        bot._mirror_from_snapshot(_merge(fx["second_window"]), gid="g1"),
        "response_chi", snap=_merge(fx["second_window"]))
    assert first.window_id.source_discard_seq == 42
    assert second.window_id.source_discard_seq == 57
    assert first.window_id != second.window_id
    assert bot._same_window_identity(first, second) is False


def test_claimed_discard_identity_is_not_reused():
    fx = _load("claimed_discard_not_reused.json")
    bot = _bot()
    snap0 = _snapshot(phase="response_chi", turn=3, responding=[0],
                      discards=[[], [], [], ["6b"]], last_discard="6b")
    snap0["source_discard_seq"] = fx["claimed_seq"]
    mirror = bot._mirror_from_snapshot(snap0, gid="g1")
    old_key = bot._window_key(mirror, "response_chi", snap=snap0)

    mirror.apply_event(fx["claim_event"])
    mirror.apply_event(fx["next_discard_event"])
    new_key = bot._window_key(
        mirror, "response_chi", ev=fx["next_discard_event"])
    assert new_key.window_id.source_discard_seq == 57
    assert new_key.window_id != old_key.window_id
    assert bot._same_window_identity(old_key, new_key) is False


def test_identity_mismatch_is_downgraded_not_promoted():
    fx = _load("identity_mismatch_downgrade.json")
    bot = _bot()
    snap = _merge(fx["snapshot"])
    mirror = bot._mirror_from_snapshot(snap, gid="g1")
    event_key = bot._window_key(mirror, "response_chi", ev=fx["event"])
    snap_key = bot._window_key(mirror, "response_chi", snap=snap)
    assert event_key.window_id.source_discard_seq == 42
    assert snap_key.window_id.source_discard_seq == 43
    assert event_key.window_id != snap_key.window_id
    # The disagreement must never be merged into one strong window.
    assert bot._same_window_identity(event_key, snap_key) is False


def test_snapshot_only_window_remains_legacy_and_weak():
    fx = _load("snapshot_only_remains_legacy.json")
    bot = _bot()
    snap = _merge(fx["snapshot"])
    key = bot._window_key(
        bot._mirror_from_snapshot(snap, gid="g1"), "response_chi", snap=snap)
    assert key.window_id.identity_status == "legacy_unresolved"
    assert key.window_id.identity_origin == "legacy_snapshot"
    assert bot._strong_window_key(key) is False

    row = {"phase": "response_chi", "window_id": {
        "game_id": "g1", "round_id": 1, "discard_owner": 3,
        "source_discard_seq": None, "tile": key.window_id.tile,
        "identity_status": "legacy_unresolved",
        "fallback": (1, 0, 0, 0)}}
    logical = _logical_window_key(row)
    assert logical is not None and logical[0] == "legacy"


# ---------- window-snapshot-identity-decision: 弱键决策契约 ----------


def test_snapshot_first_unresolved_chi_window_decides_and_submits():
    """快照首见、无协议身份的 legacy 吃窗:弱键授权后直接决策提交。

    复现线上损失链的脚本化对局:碰窗快照无 source 字段 → 吃窗等待态
    携带 legacy key 进入 WINDOW_CHI 确认 → 确认解析 UNKNOWN → 旧实现
    永远 PENDING 直到预算耗尽;新契约在首个权威快照上弱键决策。
    """
    clock = FakeClock()
    fx = _load("snapshot_only_remains_legacy.json")
    chi = _merge(fx["snapshot"])
    peng = _snapshot(phase="response_peng", turn=3, responding=[0],
                     discards=[[], [], [], ["6b"]], last_discard="6b",
                     window_deadline_ms=1001000)
    api = ScriptedServer([
        {"snapshot": peng, "seq": 5},
        {"snapshot": chi, "seq": 8},
        {"snapshot": chi, "seq": 8}, _finished(),
    ], clock, expected_seqs=[0, 0, 8, 8])
    bot = BotClient(api, "b", _choose_chi_or_draw, log=lambda _: None,
                    idle_sleep=0)
    with mock.patch.object(module, "time", clock):
        bot.play_game("g")

    assert len(api.actions) == 1
    assert api.actions[0][1]["action"] == "chi"
    # 弱键授权与弱键决策各自计数,验收可与 authoritative open 区分。
    assert bot.stats["window_confirm_weak_open"] == 1
    assert bot.stats["weak_key_decisions"] == 1
    assert bot.stats["window_confirm_miss"] == 0
    assert bot.stats["confirmation_observation_budget_exhausted"] == 0


def test_snapshot_first_unresolved_peng_window_decides_and_submits():
    """快照首见碰窗(持对子):_act_window 不再 raise 无限确认,直接弱键提交。"""
    clock = FakeClock()
    hand = ["5b", "5b", "1w", "2w", "3w", "4w", "5w", "6w",
            "1t", "2t", "3t", "7t", "8t"]
    peng = _snapshot(hand, phase="response_peng", turn=1, responding=[0],
                     discards=[[], ["5b"], [], []], last_discard="5b",
                     window_deadline_ms=1001800)
    api = ScriptedServer([
        {"snapshot": peng, "seq": 1},
        _finished(),
    ], clock, expected_seqs=[0, 1])
    bot = BotClient(api, "b",
                    lambda g, _: next(a for a in g.legal_actions()
                                      if a != -1),
                    log=lambda _: None)
    with mock.patch.object(module, "time", clock):
        bot.play_game("g")

    assert len(api.actions) == 1
    assert api.actions[0][1]["action"] == "peng"
    assert bot.stats["weak_key_decisions"] == 1
    assert bot.stats["window_confirm_requests"] == 0


def test_weak_submission_409_recovers_without_reposting():
    """弱键提交被 409 驳回时走既有同环恢复,不重复 POST。"""
    from mj.platform.api import ApiError
    clock = FakeClock()
    fx = _load("snapshot_only_remains_legacy.json")
    chi = _merge(fx["snapshot"])
    peng = _snapshot(phase="response_peng", turn=3, responding=[0],
                     discards=[[], [], [], ["6b"]], last_discard="6b",
                     window_deadline_ms=1001000)
    api = ScriptedServer([
        {"snapshot": peng, "seq": 5},
        {"snapshot": chi, "seq": 8},
        {"snapshot": chi, "seq": 8}, _finished(),
    ], clock, expected_seqs=[0, 0, 0, 8],
        action_errors=[ApiError(409, "{}")])
    bot = BotClient(api, "b", _choose_chi_or_draw, log=lambda _: None,
                    idle_sleep=0)
    with mock.patch.object(module, "time", clock):
        bot.play_game("g")

    assert len(api.actions) == 1
    assert bot.stats["err409"] == 1
    assert bot.stats["decide_errors"] == 0
    assert bot.stats["window_confirm_weak_open"] == 1


def test_weak_key_does_not_collide_for_same_tile_rediscard():
    """同家同牌重弃:认领弹牌河后弱键必须变化,旧决策不得压制新窗。"""
    fx = _load("claimed_discard_not_reused.json")
    bot = _bot()
    snap0 = _snapshot(phase="response_chi", turn=3, responding=[0],
                      discards=[[], [], [], ["6b"]], last_discard="6b")
    mirror = bot._mirror_from_snapshot(snap0, gid="g1")
    first = bot._window_key(mirror, "response_chi", snap=snap0)
    assert first.window_id.identity_status == "legacy_unresolved"
    mirror._legacy_epoch = 1
    mirror._legacy_attempts = {first: 1}

    # 另一家碰走 6b(牌河弹顶 + 副露 +1),同一座再弃 6b:牌河长度回到 1,
    # 但副露计数已变 → 弱键不同,新窗不被旧提交压制。
    mirror.apply_event(fx["claim_event"])
    mirror.apply_event(fx["next_discard_event"])
    second = bot._window_key(mirror, "response_chi")
    assert second.window_id.identity_status == "legacy_unresolved"
    assert first != second
    assert bot._window_was_attempted(mirror, second, set()) is False

    # 跨重锚 carry 仅在牌河尾位置一致时生效;牌河增长(不一致)不携带。
    grown = _snapshot(phase="response_chi", turn=3, responding=[0],
                      discards=[[], [], [], ["9t", "6b"]], last_discard="6b")
    mirror2 = bot._mirror_from_snapshot(grown, gid="g1")
    mirror2._legacy_epoch = 2
    mirror2._legacy_attempts = {first: 1}
    assert bot._weak_key_still_pinned(mirror2, first) is False
    assert bot._window_was_attempted(mirror2, first, set()) is False

    # 一致的牌河尾(同窗重锚)允许携带,防止重锚后重复 POST。
    same = _snapshot(phase="response_chi", turn=3, responding=[0],
                     discards=[[], [], [], ["6b"]], last_discard="6b")
    mirror3 = bot._mirror_from_snapshot(same, gid="g1")
    mirror3._legacy_epoch = 2
    mirror3._legacy_attempts = {first: 1}
    assert bot._weak_key_still_pinned(mirror3, first) is True
    assert bot._window_was_attempted(mirror3, first, set()) is True


def test_near_deadline_unresolved_window_stops_confirming_and_decides():
    """临近截止:停止确认调度改为弱键决策;连决策余量都没有时仍落预算记录。"""
    bot = _bot()
    fx = _load("snapshot_only_remains_legacy.json")
    snap = _merge(fx["snapshot"])
    mirror = bot._mirror_from_snapshot(snap, gid="g1")
    legacy_key = bot._window_key(mirror, "response_chi", snap=snap)
    confirm = module._WindowConfirm(
        phase="response_chi", pending=mirror.pending, round_no=1,
        legal=[CHOW_LOW], window_key=legacy_key)

    # 剩余 ~0.2s:只够 decide+POST,不再排下一轮确认拉取 → 直接弱键决策。
    near = dict(snap, window_deadline_ms=1000200)
    with mock.patch.object(module.time, "time", return_value=1000.0):
        assert bot._resolve_window_confirm(
            "g1", mirror, near, confirm, seq=42) == "confirmed"
    assert bot.stats["window_confirm_weak_open"] == 1
    assert bot.stats["window_confirm_unconfirmed"] == 0

    # 剩余 50ms ≤ SUBMIT_EPS:连决策余量都没有 → 观测预算记录 + 终态。
    bot2 = _bot()
    mirror2 = bot2._mirror_from_snapshot(snap, gid="g1")
    legacy_key2 = bot2._window_key(mirror2, "response_chi", snap=snap)
    confirm2 = module._WindowConfirm(
        phase="response_chi", pending=mirror2.pending, round_no=1,
        legal=[CHOW_LOW], window_key=legacy_key2)
    gone = dict(snap, window_deadline_ms=1000050)
    with mock.patch.object(module.time, "time", return_value=1000.0):
        assert bot2._resolve_window_confirm(
            "g1", mirror2, gone, confirm2, seq=42) == "expired"
    assert bot2.stats["confirmation_observation_budget_exhausted"] == 1
    assert bot2.stats["window_confirm_miss"] == 0


def test_cross_reanchor_carry_requires_meld_lengths_to_pin():
    """跨重锚携带必须同时钉住副露计数。

    漏看他家 claim 事件后陈旧 fallback 的 (河长, 副露元组) 可能与重建
    镜像的牌河恰好一致(同牌重弃弹回同长度);只查牌河会把真实新窗
    当作已尝试静默跳过(此方向无 409 兜底)。
    """
    fx = _load("claimed_discard_not_reused.json")
    bot = _bot()
    snap0 = _snapshot(phase="response_chi", turn=3, responding=[0],
                      discards=[[], [], [], ["6b"]], last_discard="6b")
    mirror = bot._mirror_from_snapshot(snap0, gid="g1")
    first = bot._window_key(mirror, "response_chi", snap=snap0)
    assert first.window_id.fallback == (1, (0, 0, 0, 0))

    # 他家已碰走 6b 后同一座再弃 6b:牌河长度回到 1,但副露计数已变。
    drifted = _snapshot(phase="response_chi", turn=3, responding=[0],
                        discards=[[], [], [], ["6b"]], last_discard="6b",
                        melds=[[], [], [], [{"kind": "pong",
                                             "tiles": ["6b", "6b"]}]])
    mirror2 = bot._mirror_from_snapshot(drifted, gid="g1")
    mirror2._legacy_epoch = 2
    mirror2._legacy_attempts = {first: 1}
    assert bot._weak_key_still_pinned(mirror2, first) is False
    assert bot._window_was_attempted(mirror2, first, set()) is False


def test_act_window_snapshot_authorization_requires_last_discard_match():
    """快照授权门必须校验 last_discard 与 pending 牌一致。

    已结算旧窗的快照(phase/deadline/responding 齐备但 last_discard
    已翻页)不得直接授权决策提交,必须回到确认环重新拉取。
    """
    hand = ["5b", "5b", "1w", "2w", "3w", "4w", "5w", "6w",
            "1t", "2t", "3t", "7t", "8t"]
    snap = _snapshot(hand, phase="response_peng", turn=1, responding=[0],
                     discards=[[], ["5b"], [], []], last_discard="5b",
                     window_deadline_ms=1001800)
    bot = BotClient(mock.Mock(), "b",
                    lambda g, _: next(a for a in g.legal_actions()
                                      if a != -1),
                    log=lambda _: None)
    mirror = bot._mirror_from_snapshot(snap, gid="g1")
    assert mirror.pending == (1, 13)  # owner=1, tile 5b

    settled = dict(snap, last_discard="9t")
    with mock.patch.object(module.time, "time", return_value=1000.0):
        with pytest.raises(module._WindowConfirm):
            bot._act_window(mirror, settled, "g", snapshot_seq=1)


def test_set_window_authorization_records_weak_outcome():
    """弱键授权在对局日志中记为 weak_key_open,不得伪装成权威开启。"""
    recorder = mock.Mock()
    recorder.window_authorization = mock.Mock()
    bot = BotClient(mock.Mock(), "b", lambda *_: -1, log=lambda _: None,
                    recorder=recorder)
    snap = _snapshot(phase="response_chi", turn=3, responding=[0],
                     discards=[[], [], [], ["6b"]], last_discard="6b")
    mirror = bot._mirror_from_snapshot(snap, gid="g1")
    weak_key = bot._window_key(mirror, "response_chi", snap=snap)
    assert weak_key.window_id.identity_status == "legacy_unresolved"

    bot._set_window_authorization(mirror, weak_key, snap, seq=7)
    outcome = recorder.window_authorization.call_args.kwargs["outcome"]
    assert outcome == "weak_key_open"
    # 弱键授权在授权收口处计数(直达腿同样覆盖),不混入权威开启桶。
    assert bot.stats["window_confirm_weak_open"] == 1
    assert bot.stats["window_confirm_open"] == 0


def test_resolve_window_confirm_accounts_under_authorized_weak_key():
    """弱键确认记账挂在实际授权的 key 上,而非 confirm 携带的旧实例。"""
    bot = _bot()
    fx = _load("snapshot_only_remains_legacy.json")
    snap = _merge(fx["snapshot"])
    mirror = bot._mirror_from_snapshot(snap, gid="g1")
    stale_key = bot._window_key(mirror, "response_chi", snap=snap)
    confirm = module._WindowConfirm(
        phase="response_chi", pending=mirror.pending, round_no=1,
        legal=[CHOW_LOW], window_key=stale_key)

    rows = []
    recorder = mock.Mock()
    recorder.window_confirm = (
        lambda gid, **fields: rows.append(fields))
    bot.recorder = recorder
    near = dict(snap, window_deadline_ms=1000200)
    with mock.patch.object(module.time, "time", return_value=1000.0):
        assert bot._resolve_window_confirm(
            "g1", mirror, near, confirm, seq=42) == "confirmed"
    weak_rows = [row for row in rows if row.get("outcome") == "open"]
    assert len(weak_rows) == 1
    assert weak_rows[0]["reason"] == "weak_key_open"
    # 记账身份 = 实际授权的弱键(本例与 confirm 同 key;断言防回退)。
    assert weak_rows[0]["window_id"]["identity_status"] == "legacy_unresolved"
    assert bot.stats["window_confirm_weak_open"] == 1
    assert bot.stats["window_confirm_open"] == 0
