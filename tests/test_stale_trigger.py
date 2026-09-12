"""陈旧触发/窗口时序 409 回归测试(match 实测 2026-09-08,15 个 409 归因)。

服务端窗口结构(事件 ts 实证:碰窗超时事件盖 弃牌ts+1,吃窗超时事件盖
弃牌ts+2):弃牌 T → 碰窗 (T, T+1) → 吃窗 (T+1, T+2)。三类失步:
1. 网络停摆期 /state 重试 7.6s+ → 我方弃牌窗超时被服务端代打,批内
   tile_drawn+tile_discarded(自家)+timeout(discard)齐发——旧代码仍按
   tile_drawn 提交废弃牌(必 409),新代码作废触发;
2. 我方碰窗已超时(批内 timeout kind=response window=peng seat=me)
   仍提交碰/杠(必 409),新代码作废窗口触发;
3. 吃窗 gating 在"碰窗全部响应"观测上,多一轮轮询,网络抖动下错过
   T+2 截止,新代码从观察时刻固定等 window_wait 后按截止直接提交;
4. api 层 5xx(502/503/504)一次即杀场次线程(实测整房 10 局全灭),
   新代码共享退避预算重试;
5. 场次异常永久标记完成 → 服务端代打污染整局,新代码连续 3 次才放弃;
6. 过期截止滞留:窗口关闭后推进事件缺位(流局 round_ended/gap 过渡
   期),同一过期截止被轮询连发携带,空占 EDF 抢其他场次配额、虚增
   deadline_missed(match 实测 2026-09-09 b2 连发 7 次),新代码限制
   为一次性追赶拉取。
"""

import io
import json
import queue
import time
import unittest
import urllib.error
from unittest import mock

from mj.platform.api import Api, ApiError, _request
from mj.platform.bot_client import BotClient
from mj.platform.mirror import Mirror
from test_hu_failed import ScriptedApi, _ev, _resp, _snap

# 我方(seat 0)手牌:7b8b 供吃 6b;其余成搭
HAND = ["1w", "2w", "3w", "4w", "5w", "6w", "7b", "8b",
        "1t", "2t", "3t", "4t", "9w"]
HAND_AFTER_CHI = ["1w", "2w", "3w", "4w", "5w", "6w",
                  "1t", "2t", "3t", "4t", "9w"]


def _decide_drawn(g, seat):
    """弃摸到的牌;反应窗有碰必碰,有吃选吃。"""
    if g.phase == "discard":
        return g.drawn[seat]
    legal = g.legal_actions()
    for a in (-5, -2, -3, -4):
        if a in legal:
            return a
    return -1


class TestStaleTriggerCancel(unittest.TestCase):
    def test_auto_discard_echo_cancels_draw_trigger(self):
        """批内 弃牌窗代打回声 → 不提交废弃牌(409 根源 1)。"""
        api = ScriptedApi(
            [[_ev(1, "tile_drawn", 0, "6w"),
              _ev(2, "tile_discarded", 0, "6w"),
              _ev(3, "timeout", 0, data={"kind": "discard"})]],
            [_snap(HAND, turn=1), _snap(HAND, turn=1)])
        bot = BotClient(api, "bot0", _decide_drawn, log=lambda m: None,
                        window_wait=0, idle_sleep=0)
        bot.play_game("g1")
        self.assertEqual(api.submitted, [])       # 不提交废弃牌
        self.assertEqual(bot.stats["auto_played"], 1)  # 代打记账
        self.assertEqual(bot.stats["err409"], 0)
        self.assertEqual(bot.stats["games"], 1)   # 正常 finished

    def test_discard_timeout_cancels_draw_trigger(self):
        """批内 timeout kind=discard(无回声先行)→ 同样作废。"""
        api = ScriptedApi(
            [[_ev(1, "tile_drawn", 0, "4t"),
              _ev(2, "timeout", 0, data={"kind": "discard"})]],
            [_snap(HAND, turn=1), _snap(HAND, turn=1)])
        bot = BotClient(api, "bot0", _decide_drawn, log=lambda m: None,
                        window_wait=0, idle_sleep=0)
        bot.play_game("g1")
        self.assertEqual(api.submitted, [])
        self.assertEqual(bot.stats["auto_played"], 1)

    def test_my_peng_timeout_cancels_window_trigger(self):
        """批内我方碰窗超时 → 不提交碰/杠(409 根源 2,实测 gang 409)。"""
        # 手牌带 5b5b 供碰,服务端已判我超时
        hand = ["5b", "5b", "1w", "2w", "3w", "4w", "5w", "6w",
                "1t", "2t", "3t", "4t", "9w"]
        api = ScriptedApi(
            [[_ev(1, "tile_drawn", 1),
              _ev(2, "tile_discarded", 1, "5b"),
              _ev(3, "timeout", 0, data=_resp("peng"))]],
            [_snap(hand, turn=1), _snap(hand, turn=1)])
        bot = BotClient(api, "bot0", _decide_drawn, log=lambda m: None,
                        window_wait=0, idle_sleep=0)
        bot.play_game("g1")
        self.assertEqual(api.submitted, [])       # 不提交注定 409 的碰
        self.assertEqual(bot.stats["auto_played"], 1)  # 真有选项:记代打

    def test_my_peng_timeout_without_claim_silent(self):
        """无碰/杠选项的碰窗超时:作废触发但不记代打(与自选过等价)。"""
        api = ScriptedApi(
            [[_ev(1, "tile_drawn", 1),
              _ev(2, "tile_discarded", 1, "5b"),  # 手牌无 5b
              _ev(3, "timeout", 0, data=_resp("peng"))]],
            [_snap(HAND, turn=1), _snap(HAND, turn=1)])
        bot = BotClient(api, "bot0", _decide_drawn, log=lambda m: None,
                        window_wait=0, idle_sleep=0)
        bot.play_game("g1")
        self.assertEqual(api.submitted, [])
        self.assertEqual(bot.stats["auto_played"], 0)

    def test_round_ended_same_batch_cancels_draw_trigger(self):
        """批内 我方摸牌+round_ended(服务端自动结算杠开)→ 不再按陈旧
        摸牌触发提交动作(实测 2026-09-11 b5:hu 409 "hu only after draw")。"""
        api = ScriptedApi(
            [[_ev(1, "tile_drawn", 0, "4t",
                  data={"gang_replenish": True}),
              _ev(2, "round_ended", 0,
                  data={"round_no": 1, "detail": ["平胡", "杠开"],
                        "scores": [48, -16, -16, -16]})]],
            [_snap(HAND, turn=1), _snap(HAND, turn=1)])
        bot = BotClient(api, "bot0", _decide_drawn, log=lambda m: None,
                        window_wait=0, idle_sleep=0)
        bot.play_game("g1")
        self.assertEqual(api.submitted, [])       # 轮已结算:不提交陈旧动作
        self.assertEqual(bot.stats["auto_played"], 0)  # 自动结算非代打损失
        self.assertEqual(bot.stats["err409"], 0)
        self.assertEqual(bot.stats["games"], 1)


class TestChiDeadline(unittest.TestCase):
    def test_chi_fires_at_deadline_without_all_responses(self):
        """吃窗不依赖「碰窗响应观测齐」,由权威快照相位推进驱动提交。

        旧代码 seen<needed 时继续等下一批,吃窗被无谓推迟错过截止;
        现在无碰/杠可做时直接抓 seq=0 快照(带吃窗截止→EDF 优先):先看到
        response_peng,相位一到 response_chi 立即决策提交,不等全部响应。
        """
        from mj.platform.proto import tidx

        def decide(g, seat):
            if g.phase == "discard":
                d = g.drawn[seat]
                return d if d is not None else tidx("9w")  # 吃后固定弃 9w
            legal = g.legal_actions()
            for a in (-5, -2, -3, -4):
                if a in legal:
                    return a
            return -1

        base = dict(_snap(HAND, turn=3), last_discard="6b",
                    discards=[["4t"], [], [], ["6b"]])
        peng_snap = dict(base, phase="response_peng",
                         responding_seats=[0, 1, 2],
                         window_deadline_ms=(time.time() + 0.5) * 1000)
        chi_snap = dict(base, phase="response_chi", responding_seats=[0],
                        window_deadline_ms=(time.time() + 1.0) * 1000)
        api = ScriptedApi(
            [[_ev(1, "tile_drawn", 3),
              _ev(2, "tile_discarded", 3, "6b")],
             [_ev(3, "pass", 1)],                # 响应迟到:仅一家
             [_ev(4, "pass", 2)],                # 我方碰窗响应始终未观测到
             [_ev(5, "chi", 0, "6b",
                  data={"tiles": ["6b", "7b", "8b"]})],  # 吃回声→弃牌回合
             [_ev(6, "tile_discarded", 0, "9w"),
              _ev(7, "pass", 1), _ev(8, "pass", 2), _ev(9, "pass", 3)]],
            [_snap(HAND, turn=3)],
            reanchor_snaps=[peng_snap, chi_snap])
        bot = BotClient(api, "bot0", decide, log=lambda m: None,
                        window_wait=0, idle_sleep=0)
        bot.play_game("g1")
        # 吃 6b 先于弃 9w 提交:相位驱动,不等全部碰窗响应
        self.assertEqual(
            api.submitted,
            [{"action": "chi", "tile": "6b", "tiles": ["7b", "8b"]},
             {"action": "discard", "tile": "9w"}])
        self.assertEqual(bot.stats["err409"], 0)

    def test_chi_cancelled_by_claim_observed(self):
        """倒计时期间观察到他家碰 → 吃窗作废,不提交。"""
        api = ScriptedApi(
            [[_ev(1, "tile_drawn", 3),
              _ev(2, "tile_discarded", 3, "6b")],
             [_ev(3, "pass", 1),
              _ev(4, "peng", 2, "6b")]],         # 他家碰走
            [_snap(HAND, turn=3), _snap(HAND, turn=3)])
        bot = BotClient(api, "bot0", _decide_drawn, log=lambda m: None,
                        window_wait=0, idle_sleep=0)
        bot.play_game("g1")
        self.assertEqual(api.submitted, [])


class TestGatewayRetry(unittest.TestCase):
    def test_502_retried_until_success(self):
        """502 网关瞬断按退避重试,不炸调用方(409 根源 4)。"""
        calls = []

        def fake_urlopen(req, timeout=None, context=None):
            calls.append(req.full_url)
            if len(calls) < 3:
                raise urllib.error.HTTPError(
                    req.full_url, 502, "Bad Gateway", {},
                    io.BytesIO(b'{"code": ""}'))
            return io.BytesIO(json.dumps({"ok": 1}).encode())

        with mock.patch("urllib.request.urlopen", fake_urlopen), \
                mock.patch("time.sleep"):
            res = _request("GET", "https://x/api/me", token="t")
        self.assertEqual(res, {"ok": 1})
        self.assertEqual(len(calls), 3)           # 2 次 502 + 1 次成功

    def test_502_gives_up_after_budget(self):
        """持续 502 超过预算后抛 ApiError(502),不无限重试。"""
        def fake_urlopen(req, timeout=None, context=None):
            raise urllib.error.HTTPError(
                req.full_url, 502, "Bad Gateway", {},
                io.BytesIO(b'{"code": "BAD_GATEWAY"}'))

        with mock.patch("urllib.request.urlopen", fake_urlopen), \
                mock.patch("time.sleep"):
            with self.assertRaises(ApiError) as cm:
                _request("GET", "https://x/api/me", token="t")
        self.assertEqual(cm.exception.status, 502)

    def test_other_http_errors_not_retried(self):
        """400/409 等语义错误不重试,原样抛出。"""
        n = []

        def fake_urlopen(req, timeout=None, context=None):
            n.append(1)
            raise urllib.error.HTTPError(
                req.full_url, 409, "Conflict", {},
                io.BytesIO(b'{"code": "INVALID_ACTION"}'))

        with mock.patch("urllib.request.urlopen", fake_urlopen), \
                mock.patch("time.sleep"):
            with self.assertRaises(ApiError):
                _request("GET", "https://x/api/me", token="t")
        self.assertEqual(len(n), 1)

    def test_api_get_uses_request(self):
        """Api.get 透传 5xx 重试(mocker 冒烟)。"""
        calls = []

        def fake_urlopen(req, timeout=None, context=None):
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.HTTPError(
                    req.full_url, 503, "Unavailable", {}, io.BytesIO(b"{}"))
            return io.BytesIO(b'{"data": 7}')

        api = Api("https://x", "tok")
        with mock.patch("urllib.request.urlopen", fake_urlopen), \
                mock.patch("time.sleep"):
            self.assertEqual(api.me(), {"data": 7})


class TestSrvAnchoredWindows(unittest.TestCase):
    """服务端 ts 锚定:截止收缩/死窗 None/守卫不提交(409 根源 7)。"""

    def test_srv_deadline_shrinks_with_late_observation(self):
        """观测迟到 0.5s 的 1 秒碰窗:截止按真实剩余 0.5s 收缩。"""
        dl = BotClient._srv_deadline(time.time() - 0.5, 1.0)
        now = time.monotonic()
        self.assertIsNotNone(dl)
        self.assertGreater(dl, now)
        self.assertLess(dl - now, 0.5)   # 剩余 0.5 - margin
        self.assertGreater(dl - now, 0.3)

    def test_srv_deadline_dead_window_returns_none(self):
        """剩余不足 2×margin 的死窗不设截止(不抢 EDF、不生来过期)。"""
        self.assertIsNone(
            BotClient._srv_deadline(time.time() - 0.95, 1.0))
        self.assertIsNone(
            BotClient._srv_deadline(time.time() - 10.0, 1.0))

    def test_srv_deadline_fresh_and_missing_ts(self):
        """新鲜事件锚定满窗;ts 缺失退回观测时刻锚定(测试桩兼容)。"""
        now = time.monotonic()
        dl = BotClient._srv_deadline(time.time(), 1.0)
        self.assertGreater(dl - now, 0.8)
        dl = BotClient._srv_deadline(None, 3.0)
        self.assertGreater(dl - now, 2.8)
        self.assertLess(dl - now, 3.1)

    def test_mono_at_conversions(self):
        now = time.monotonic()
        self.assertLess(abs(BotClient._mono_at(None) - now), 0.05)
        self.assertLess(abs(BotClient._mono_at(time.time() - 1.0)
                            - (now - 1.0)), 0.05)
        # 异常 ts(未来/远古)钳制回当前时刻
        self.assertLess(abs(BotClient._mono_at(time.time() + 5.0) - now),
                        0.05)
        self.assertLess(abs(BotClient._mono_at(time.time() - 100.0) - now),
                        0.05)

    def test_next_seat_step(self):
        """动作者推进:摸/吃/碰/杠→本人,弃牌→下家,pass 不改道。"""
        self.assertEqual(BotClient._next_seat_step(None,
                         {"type": "tile_drawn", "seat": 2}), 2)
        self.assertEqual(BotClient._next_seat_step(2,
                         {"type": "tile_discarded", "seat": 3}), 0)
        self.assertEqual(BotClient._next_seat_step(0,
                         {"type": "pass", "seat": 1}), 0)
        self.assertEqual(BotClient._next_seat_step(0,
                         {"type": "gang", "seat": 1}), 1)

    def test_poll_urgent_gating(self):
        """懒轮询门:未知/我方/下家打牌/持对子/抓打圈一律急。"""
        m = Mirror(0, 0)
        self.assertTrue(BotClient(api=None, name="b", decide=None)
                        ._poll_urgent(m, None))
        bc = BotClient(api=None, name="b", decide=None)
        self.assertTrue(bc._poll_urgent(m, 0))    # 我方摸/打
        self.assertTrue(bc._poll_urgent(m, 3))    # 下家(3)打牌→我吃窗
        self.assertFalse(bc._poll_urgent(m, 1))   # 对家摸打,无对子
        self.assertFalse(bc._poll_urgent(m, 2))   # 上家之外同理
        m.my_hand[5] = 2
        self.assertTrue(bc._poll_urgent(m, 1))    # 持对子:任意弃牌可能可碰
        m.my_hand[5] = 0
        m.my_hand[33] = 2                         # 财神对子不可碰
        self.assertFalse(bc._poll_urgent(m, 1))
        m.freeze = 1
        self.assertTrue(bc._poll_urgent(m, 1))    # 抓打圈保守

    def test_peng_guard_skips_closed_window(self):
        """碰窗疑似已关→快照确认，不把本地估计记成代打。"""
        hand = ["5b", "5b", "1w", "2w", "3w", "4w", "5w", "6w",
                "1t", "2t", "3t", "7t", "8t"]
        api = ScriptedApi(
            [[dict(_ev(6, "tile_drawn", 1),
                   ts=time.time() - 2.0),
              dict(_ev(7, "tile_discarded", 1, "5b"),
                   ts=time.time() - 2.0),
              _ev(8, "pass", 2), _ev(9, "pass", 3)]],
            [_snap(hand, turn=1), _snap(hand, turn=1)])
        bot = BotClient(api, "bot0", _decide_drawn, log=lambda m: None,
                        window_wait=0, idle_sleep=0)
        bot.play_game("g1")
        self.assertEqual(api.submitted, [])
        self.assertEqual(bot.stats["auto_played"], 0)
        self.assertEqual(bot.stats["err409"], 0)

    def test_draw_guard_skips_closed_window(self):
        """自家弃牌窗已关(摸牌观测迟到 3.5s)→ 不提交,记代打。"""
        api = ScriptedApi(
            [[dict(_ev(1, "tile_drawn", 0, "4t"),
                   ts=time.time() - 3.5)]],
            [_snap(HAND, turn=1), _snap(HAND, turn=1)])
        bot = BotClient(api, "bot0", _decide_drawn, log=lambda m: None,
                        window_wait=0, idle_sleep=0)
        bot.play_game("g1")
        self.assertEqual(api.submitted, [])
        self.assertEqual(bot.stats["auto_played"], 1)


class TestLazyPollSmoke(unittest.TestCase):
    def test_lazy_poll_completes_game(self):
        """懒轮询分支(SSE 在航 + 无关预测)不破坏对局推进与提交。"""
        with mock.patch("mj.platform.bot_client.LAZY_POLL_WAIT", 0.05):
            scripted = ScriptedApi(
                [[_ev(1, "tile_drawn", 1)],
                 [_ev(2, "tile_discarded", 1, "9t"),
                  _ev(3, "pass", 2), _ev(4, "pass", 3)],
                 [_ev(5, "tile_drawn", 0, "4t")]],
                [_snap(HAND, turn=1), _snap(HAND, turn=1)])
            bot = BotClient(scripted, "bot0", _decide_drawn,
                            log=lambda m: None, window_wait=0,
                            idle_sleep=0, notify_fallback_wait=0.02)
            wake, sse = queue.Queue(), {"alive": True}
            bot._play_loop("g1", wake, sse)
            # 无关他家摸打被推迟观测,我方摸牌照常决策提交
            self.assertEqual(scripted.submitted,
                             [{"action": "discard", "tile": "4t"}])
            self.assertEqual(bot.stats["games"], 1)


class TestLongPoll(unittest.TestCase):
    def test_long_poll_skips_idle_wait(self):
        """长轮询模式:空闲批次不等待直接再拉(等待由服务端挂起承担)。

        idle_sleep=5 时若仍走旧等待路径必然超时/极慢;长轮询跳过等待,
        对局在毫秒级推进且提交/计数正常。
        """
        scripted = ScriptedApi(
            [[_ev(1, "tile_drawn", 1)],
             [_ev(2, "tile_discarded", 1, "9t"),
              _ev(3, "pass", 2), _ev(4, "pass", 3)],
             [_ev(5, "tile_drawn", 0, "4t")]],
            [_snap(HAND, turn=1), _snap(HAND, turn=1)])
        bot = BotClient(scripted, "bot0", _decide_drawn,
                        log=lambda m: None, window_wait=0,
                        idle_sleep=5.0, long_poll=True)
        bot.play_game("g1")
        self.assertEqual(scripted.submitted,
                         [{"action": "discard", "tile": "4t"}])
        self.assertEqual(bot.stats["games"], 1)
        self.assertEqual(bot.stats["err409"], 0)

    def test_long_poll_incompatible_with_notify_is_disabled(self):
        """use_notify 与长轮询并存时长轮询失效(SSE 在场禁用服务端挂起)。"""
        bot = BotClient(api=None, name="b", decide=None,
                        use_notify=True, long_poll=True)
        self.assertFalse(bot.long_poll)
        bot = BotClient(api=None, name="b", decide=None, long_poll=True)
        self.assertTrue(bot.long_poll)


class TestStaleDeadlineStep(unittest.TestCase):
    def test_one_shot_catchup_then_clear(self):
        """过期截止:允许携带追赶一次,之后清除;新鲜截止重置资格。"""
        step = BotClient._stale_deadline_step
        # 首次过期:保留供一次优先追赶
        dl, used = step(100.0, False, 200.0)
        self.assertEqual(dl, 100.0)
        self.assertTrue(used)
        # 再次过期:清除(b2 流局过渡期连发根治)
        dl, used = step(100.0, True, 300.0)
        self.assertIsNone(dl)
        self.assertTrue(used)
        # 未来截止:保留并重置追赶资格
        dl, used = step(500.0, True, 300.0)
        self.assertEqual(dl, 500.0)
        self.assertFalse(used)
        # 无截止:恒 None
        dl, used = step(None, True, 300.0)
        self.assertIsNone(dl)
        self.assertFalse(used)
        # 重置后新过期截止仍可追赶一次
        dl, used = step(400.0, False, 500.0)
        self.assertEqual(dl, 400.0)
        self.assertTrue(used)


if __name__ == "__main__":
    unittest.main()
