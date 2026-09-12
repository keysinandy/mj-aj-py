"""claim_miss 诊断日志的归因回归。

规则允许吃/碰/杠但未成功时写 claim_miss,并按本地决策分三类:
- 已决策为吃/碰/杠但未落地 → chosen=动作,client_decision=true
- 尚未决策即被服务端 timeout → chosen=null,client_decision=false
- 已本地决策为过(-1) → 不算机会损失,不写记录(碰窗也不计代打)

用假时钟 + 脚本化假服务器复现;断言写入内容而不是只数条数。
"""

import json
import os
import sys
import time
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mj.platform.bot_client import BotClient
from mj.platform.recorder import Recorder
import mj.platform.bot_client as module
from test_window_recovery import (
    FakeClock, ScriptedServer, _snapshot, _ev, _finished, _choose_chi_or_draw,
)

HAND_CHI = ["1w", "2w", "3w", "4w", "5w", "6w", "7b", "8b",
            "1t", "2t", "3t", "4t", "9w"]
HAND_PENG = ["6b", "6b", "1w", "2w", "3w", "4w", "5w",
             "1t", "2t", "3t", "4t", "7t", "8t"]


def _play(tmp_path, hand, decide, responses, expected, *, gid, advance=None):
    """打一局并返回 (bot, api, 该局全部日志记录)。"""
    rec = Recorder(root=str(tmp_path))
    clock = FakeClock(monotonic=100.0, epoch=1000.0)
    api = ScriptedServer(responses, clock, expected_seqs=expected,
                         before_response=advance or {})
    bot = BotClient(api, "b", decide, log=lambda _: None, idle_sleep=0,
                    recorder=rec, window_wait=0)
    with mock.patch.object(module, "time", clock):
        bot.play_game(gid)
    path = os.path.join(str(tmp_path), time.strftime("%Y%m%d"),
                        f"b_{gid}.jsonl")
    with open(path, encoding="utf-8") as f:
        recs = [json.loads(line) for line in f if line.strip()]
    return bot, api, recs


def test_decided_but_abandoned_records_chosen(tmp_path):
    """策略已选吃、快照截止已过 → 记 chosen=吃(已决策)。"""
    chi = _snapshot(HAND_CHI, phase="response_chi", turn=3, responding=[0],
                    discards=[[], [], [], ["6b"]], last_discard="6b",
                    window_deadline_ms=1001000)
    bot, api, recs = _play(
        tmp_path, HAND_CHI, _choose_chi_or_draw,
        [{"snapshot": chi, "seq": 5}, _finished()], [0, 5],
        advance={0: 1.2}, gid="gA")
    miss = [r for r in recs if r["type"] == "claim_miss"]
    assert len(miss) == 1
    assert miss[0]["phase"] == "response_chi"
    assert miss[0]["chosen"] == -2  # CHOW_LOW
    assert miss[0]["client_decision"] is True
    assert miss[0]["chosen_legal"] is True
    assert miss[0]["legal_check"] == "current_mirror"
    assert api.actions == []
    assert bot.stats["client_deadline_abandons"] == 1


def test_undecided_server_timeout_records_null_chosen(tmp_path):
    """未收到权威吃窗快照即被 timeout → 记 chosen=null(未决策)。"""
    peng = _snapshot(HAND_CHI, phase="response_peng", turn=3,
                     responding=[2, 3], discards=[[], [], [], ["6b"]],
                     last_discard="6b")
    timeout = _ev(6, "timeout", 0, data={"kind": "response",
                                         "window": "chi"})
    bot, api, recs = _play(
        tmp_path, HAND_CHI, _choose_chi_or_draw,
        [{"snapshot": peng, "seq": 5},
         {"events": [timeout], "seq": 6}, _finished()],
        [0, 5, 6], gid="gB")
    miss = [r for r in recs if r["type"] == "claim_miss"]
    assert len(miss) == 1
    assert miss[0]["reason"] == "server_timeout_chi"
    assert miss[0]["chosen"] is None
    assert miss[0]["client_decision"] is False
    assert miss[0]["legal_check"] == "not_decided"
    assert miss[0]["legal"] == [-2]  # 规则确实可吃
    # 窗口关联字段:离线分类据此把 miss 关联到同窗 decision/action/echo
    key = miss[0]["window_attempt_key"]
    assert key["phase"] == "response_chi"
    assert key["window_id"]["game_id"] == "gB"
    assert key["window_id"]["round_id"] == 1
    assert miss[0]["window_id"]["game_id"] == "gB"
    assert miss[0]["logical_request_id"].startswith("window:gB:")
    assert miss[0]["action_posted"] is False
    assert "observed_at" in miss[0]
    assert bot.stats["my_timeout_chi"] == 1
    assert api.actions == []


def test_decided_pass_chi_timeout_writes_nothing(tmp_path):
    """策略已决策为过 → timeout 不算机会损失,不写 claim_miss。"""
    chi = _snapshot(HAND_CHI, phase="response_chi", turn=3, responding=[0],
                    discards=[[], [], [], ["6b"]], last_discard="6b",
                    window_deadline_ms=1001000)
    timeout = _ev(6, "timeout", 0, data={"kind": "response",
                                         "window": "chi"})
    bot, _, recs = _play(
        tmp_path, HAND_CHI, lambda g, s: -1,
        [{"snapshot": chi, "seq": 5},
         {"events": [timeout], "seq": 6}, _finished()],
        [0, 5, 6], gid="gC")
    assert [r for r in recs if r["type"] == "claim_miss"] == []
    assert bot.stats["my_timeout_chi"] == 1  # 服务端事件仍如实计数


def test_decided_pass_peng_timeout_is_not_auto_played(tmp_path):
    """碰窗已决策为过 → 不写 claim_miss,也不计服务端代打。"""
    draw = _snapshot(HAND_PENG, phase="draw", turn=0)
    # A discard event is only a wake-up.  The decision is made after the
    # authoritative response_peng snapshot confirms the same window.
    peng = _snapshot(
        HAND_PENG, phase="response_peng", turn=1, responding=[0],
        discards=[[], ["6b"], [], []], last_discard="6b",
        window_deadline_ms=1001000)
    disc = _ev(5, "tile_discarded", 1, "6b", ts=1000.0)
    timeout = _ev(6, "timeout", 0, data={"kind": "response",
                                         "window": "peng"})
    bot, api, recs = _play(
        tmp_path, HAND_PENG, lambda g, s: -1,
        [{"snapshot": draw, "seq": 4},
         {"events": [disc], "seq": 5},
         {"snapshot": peng, "seq": 5},
         {"events": [timeout], "seq": 6}, _finished()],
        [0, 4, 0, 5, 6], gid="gD")
    assert [r for r in recs if r["type"] == "claim_miss"] == []
    assert bot.stats["auto_played"] == 0
    assert any(a[1].get("action") == "pass" for a in api.actions)
