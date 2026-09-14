"""Mirror 属性测试:synth 自博弈 × 4 座位,逐决策点与引擎真值对拍。

关卡(实施计划第 4 步):每个本人决策点
- build_game(phase).legal_actions() 集合 == 引擎真值
- live_wall_left / freeze / chows / melds / discards == 引擎真值
- 快照锚定:apply_snapshot(my_hand) 与引擎手牌一致
"""

import unittest

from mj.platform.mirror import Mirror, MirrorInconsistent
from mj.platform.proto import tname, tidx, EV_DISCARDED
from mj.platform.synth import synth_game, view_for

N_GAMES = 40  # 全量 200 局在 CI 代价高,日常 40 局;关键回归再加量


def _run_games(n, policy=None):
    checked = 0
    for seed in range(n):
        res = synth_game(seed, you_cai_bi_kao=(seed % 3 == 0),
                         policy=policy)
        for seat in range(4):
            checked += _check_view(res, seat)
    return checked


def _initial_snapshot(res, seat):
    """开局锚点快照(模拟 /state seq=0 的第一发)。"""
    snap = {
        "seat": seat,
        "phase": "draw" if seat == res["game"].dealer else "deal",
        "turn": res["game"].dealer,
        "responding_seats": [],
        "drawn_tile": None,
        "my_hand": res["start_hands"][seat],
        "god": {"baotou": False, "chain_count": 0, "catch_play": False},
        "round_no": 1,
    }
    if seat == res["game"].dealer:
        snap["phase"] = "draw"
        snap["drawn_tile"] = res["dealer_first_draw"]
    return snap


def _check_view(res, seat):
    view = view_for(res, seat)
    mir = Mirror(my_seat=seat, dealer=res["game"].dealer,
                base=1, you_cai_bi_kao=res["config"]["YouCaiBiKao"],
                round_no=1)
    mir.apply_snapshot(_initial_snapshot(res, seat))
    events, cursor, checked = view["events"], 0, 0
    for d in view["prompts"]:
        while cursor < d["events_before"]:
            mir.apply_event(events[cursor])
            cursor += 1
        prompt = d["prompt"]
        phase = prompt["phase"]
        if phase == "draw":
            mir.apply_snapshot(prompt)  # 锚定自有手牌(快照是真相)
        g = mir.build_game(phase)
        assert g.turn == seat
        assert sorted(g.legal_actions()) == d["legal"], (
            f"座位 {seat} 阶段 {phase} 合法集分歧:\nprompt={prompt}\n"
            f"mirror 手牌={mir.my_hand}")
        # 公共状态逐项对拍
        assert mir.live_wall_left() == d["live_wall"], \
            f"墙长分歧 seat={seat}: {mir.live_wall_left()} != {d['live_wall']}"
        assert mir.freeze == d["freeze"], f"冻结分歧 seat={seat}"
        assert mir.chows == d["chows"], f"吃摊数分歧 seat={seat}"
        assert mir.melds == d["melds"], f"副露分歧 seat={seat}"
        assert mir.discards == d["discards"], f"牌河分歧 seat={seat}"
        checked += 1
    # 收尾:吃完全部事件应能对上终局(无异常即通过)
    for ev in events[cursor:]:
        mir.apply_event(ev)
    return checked


class TestMirrorProperties(unittest.TestCase):
    def test_random_policy_all_seats(self):
        checked = _run_games(N_GAMES)
        self.assertGreater(checked, 1000,
                           "决策点样本过少,检查 synth 是否正常产出")

    def test_heuristic_policy(self):
        from mj.bot import choose_action
        checked = _run_games(8, policy=choose_action)
        self.assertGreater(checked, 100)

    def test_window_keys_distinguish(self):
        res = synth_game(0)
        mir = Mirror(my_seat=0, dealer=res["game"].dealer)
        k1 = mir.window_key("response_peng")
        k2 = mir.window_key("response_chi")
        self.assertNotEqual(k1, k2)
        # 同一 pending 重复询问:键不变(防重)
        self.assertEqual(k1, mir.window_key("response_peng"))

    def test_dirty_stream_detected(self):
        res = synth_game(1)
        view = view_for(res, 0)
        mir = Mirror(my_seat=0, dealer=res["game"].dealer)
        mir.apply_snapshot(_initial_snapshot(res, 0))
        with self.assertRaises(MirrorInconsistent):
            for ev in view["events"]:
                if ev["type"] == EV_DISCARDED and ev["seat"] == 0 \
                        and ev["tile"] is not None:
                    ev = dict(ev, tile=tname((tidx(ev["tile"]) + 1) % 34))
                mir.apply_event(ev)

    def test_snapshot_rebuild_restores_catch_play_circle(self):
        """gap 快照位于抓打圈中时，后续冻结不能在首张弃牌后丢失。

        复现 match a_19d56a5bc8ac 的唯一 409：seat 0 打白，快照落在
        seat 1 已摸待弃；seat 1 弃后 seat 2 摸牌只能弃刚摸的 5w。
        """
        hand = ["1w", "2w", "3w", "4w", "6w", "7w", "8w",
                "9w", "1b", "2b", "3b", "1t", "2t"]
        snap = {
            "seat": 2, "phase": "draw", "turn": 1, "drawn_tile": "",
            "my_hand": hand, "round_no": 1, "dealer": 0,
            "wall_remaining": 82,
            "discards": [["白"], [], [], []], "melds": [[], [], [], []],
            "god": {"catch_play": True, "god_discarder_seat": 0},
        }
        mir = Mirror(my_seat=2, dealer=0)
        mir.apply_snapshot(snap)
        self.assertEqual((mir.freeze, mir.freezer), (3, 0))
        mir.apply_event({"type": "tile_discarded", "seat": 1,
                         "tile": "6b", "seq": 1})
        self.assertEqual(mir.freeze, 2)
        mir.apply_event({"type": "tile_drawn", "seat": 2,
                         "tile": "5w", "seq": 2})
        self.assertEqual(mir.build_game("draw").legal_actions(), [4])

    def test_snapshot_catch_play_without_freezer_is_conservative(self):
        """旧快照缺发起者时，不得把抓打圈错误降级成任意弃牌。"""
        mir = Mirror(my_seat=2, dealer=0)
        mir.apply_snapshot({
            "seat": 2, "phase": "draw", "turn": 1, "my_hand": [],
            "god": {"catch_play": True},
        })
        self.assertEqual((mir.freeze, mir.freezer), (3, -1))

    def test_react_on_own_pending_rejected(self):
        """自家打出的牌没有自家反应窗:build_game 拒绝(防陈旧窗口
        构建出"吃自己弃牌"的假合法集——2026-09-08 实弹 409 根因)。"""
        res = synth_game(2)
        mir = Mirror(my_seat=0, dealer=res["game"].dealer)
        mir.apply_snapshot(_initial_snapshot(res, 0))
        mir.pending = (0, 3)  # 强行把 pending 设成自家弃牌
        for phase in ("response_peng", "response_chi"):
            with self.assertRaises(MirrorInconsistent):
                mir.build_game(phase)

    def test_chi_requires_discarders_next_seat(self):
        """A stale pending discard cannot manufacture a chi window."""
        mir = Mirror(my_seat=0, dealer=0)
        mir.my_hand = [0] * 34
        mir.my_hand[tidx("3w")] = 1
        mir.my_hand[tidx("4w")] = 1
        mir.pending = (2, tidx("2w"))  # seat 0 is not seat 2's下家
        with self.assertRaises(MirrorInconsistent):
            mir.build_game("response_chi")


if __name__ == "__main__":
    unittest.main()
