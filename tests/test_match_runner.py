"""自由对战(/api/match)离线测试:FakeMatchApi 按房回放 synth 局。

FakeMatchApi 模拟 auto 房生命周期:match() 开新房(每房一场 synth
局,复用 FakeApi 的单座位 /state 回放),打完 finished → 下一房;
tournament 404 分支模拟房间关停。覆盖 run_match 挂机循环、整房退出
粒度、403 永久退出、MATCH_BUSY 退避、mode 落盘与 log2data 过滤。
"""

import json
import os
import queue
import tempfile
import time
import unittest

from mj.log2data import collect
from mj.platform.api import ApiError
from mj.platform.bot_client import BotClient
from mj.platform.recorder import Recorder
from mj.platform.synth import synth_game
from test_platform_client import FakeApi

PORTAL_BINDING = json.dumps(
    {"code": "PORTAL_BINDING_REQUIRED", "message": "匿名令牌禁入"})
MATCH_BUSY = json.dumps({"code": "MATCH_BUSY", "message": "在途房满 50"})


class FakeMatchApi:
    """auto 房序列假服务器:match() 开新房,每房恰一场 synth 局。

    room_gone=True 时房内局打完不走 finished,而是 tournament 直接
    404(模拟关停后玩家 API 404 的正常生命周期)。busy_before=n 时
    前 n 次 match() 抛 409 MATCH_BUSY(退避重试路径)。
    """

    def __init__(self, results, seat, busy_before=0, room_gone=False,
                 refuse_403=False):
        self.results = list(results)
        self.seat = seat
        self.busy_before = busy_before
        self.room_gone = room_gone
        self.refuse_403 = refuse_403
        self.match_calls = 0
        self.room_idx = -1
        self.room_id = None
        self.game = None        # 当前房 FakeApi(单场回放)
        self._it = iter(())     # 当前房决策迭代器(decide 消费)

    def match(self):
        self.match_calls += 1
        if self.refuse_403:
            raise ApiError(403, PORTAL_BINDING)
        if self.match_calls <= self.busy_before:
            raise ApiError(409, MATCH_BUSY)
        self.room_idx += 1
        res = self.results[self.room_idx]
        self.room_id = f"auto_r{self.room_idx}"
        self.game = FakeApi(res, self.seat)
        self._rel = [d for d in res["decisions"]
                     if d["seat"] == self.seat
                     and (d["mode"] == "draw" or d["legal"] != [-1])]
        self._it = iter(self._rel)
        return {"room_id": self.room_id, "config": res["config"],
                "round_no": 1}

    def _check_room(self, tid):
        assert tid == self.room_id, (tid, self.room_id)

    def me(self):
        return self.game.me()

    def tournament(self, tid):
        self._check_room(tid)
        st = self.game.tournament(tid)
        if self.room_gone and st["status"] == "finished":
            raise ApiError(404, json.dumps({"message": "room closed"}))
        return st

    def game_state(self, gid, seq):
        return self.game.game_state(gid, seq)

    def game_action(self, gid, payload):
        return self.game.game_action(gid, payload)


class FakeSseStream:
    """按队列推送行的假 SSE 响应(FakeApi 每次发事件后 push 一帧)。"""

    def __init__(self):
        self.q = queue.Queue()

    def push(self, seq, closed=False):
        payload = {"seq": seq}
        if closed:
            payload["closed"] = True
        self.q.put(json.dumps(payload).encode())

    def close(self):
        self.q.put(None)

    def __iter__(self):
        while True:
            item = self.q.get()
            if item is None:
                return
            yield b"data: " + item + b"\n"


class FakeSseMatchApi(FakeMatchApi):
    """/notify 可用的 FakeMatchApi:game_state 返回事件后推 SSE 帧;
    close_every=N 模拟服务端周期断流(监听须自动重连)。"""

    def __init__(self, *a, sse_error=None, close_every=None, **kw):
        super().__init__(*a, **kw)
        self.sse_error = sse_error
        self.close_every = close_every
        self.sse = FakeSseStream()
        self.notify_opens = 0
        self._served = 0
        self._await_opens = None   # 断流后要求监听先重连(确定性,免调度竞态)

    def match(self):
        res = super().match()
        self.sse = FakeSseStream()  # 新房新流
        return res

    def open_notify(self, gid):
        if self.sse_error is not None:
            raise self.sse_error
        self.notify_opens += 1
        self.sse = FakeSseStream()  # 每次连接 = 新流(重连场景换流)
        return self.sse

    def game_state(self, gid, seq):
        if self._await_opens is not None:
            # 断流后服务端不给状态,直到监听重连(否则用例依赖线程调度)
            deadline = time.time() + 2.0
            while self.notify_opens <= self._await_opens \
                    and time.time() < deadline:
                time.sleep(0.001)
            self._await_opens = None
        res = self.game.game_state(gid, seq)
        self.sse.push(res.get("seq", seq), closed=bool(res.get("finished")))
        self._served += 1
        if self.close_every and self._served % self.close_every == 0:
            self.sse.close()  # 模拟断流:迭代器返回 → 监听重连
            self._await_opens = self.notify_opens
        return res


def _drive_match(n_rooms, seat=0, seed0=0, max_games=None, recorder=None,
                 api_cls=FakeMatchApi, use_notify=False, **api_kwargs):
    """打 n_rooms 个 auto 房(每房 1 场),返回 (api, stats)。

    api_kwargs 透传 FakeMatchApi(busy_before/room_gone/refuse_403…);
    use_notify=True 时用 FakeSseMatchApi(SSE 帧驱动)。
    """
    results = [synth_game(seed0 + k) for k in range(n_rooms)]
    cls = FakeSseMatchApi if use_notify else api_cls
    api = cls(results, seat, **api_kwargs)

    def decide(g, s):
        d = next(api._it)
        api.game.deciding(d)      # 通知夹具:该决策点已消费
        act = d["action"]
        assert act in g.legal_actions()
        return act

    bot = BotClient(api, "bot0", decide, log=lambda m: None,
                    window_wait=0, idle_sleep=0, recorder=recorder,
                    mode="match", match_retry_wait=0, use_notify=use_notify,
                    notify_fallback_wait=0.05, notify_retry_wait=0.01)
    stats = bot.run_match(max_games=max_games if max_games is not None
                          else n_rooms, room_close_wait=0)
    return api, stats


class TestRunMatch(unittest.TestCase):
    def test_multi_room_loop(self):
        """/api/match → 打完 → re-match 循环:每房一场,决策逐点合法。"""
        api, stats = _drive_match(2)
        self.assertEqual(stats["games"], 2)
        self.assertEqual(stats["rooms"], 2)
        self.assertEqual(api.match_calls, 2)
        self.assertEqual(stats["err409"], 0)
        self.assertEqual(stats["mirror_resets"], 0)
        self.assertEqual(len(stats["scores"]), 2)

    def test_room_granularity(self):
        """max_games=1 仍打完整房(整房为退出粒度,不中途弃房)。"""
        api, stats = _drive_match(3, max_games=1)
        self.assertEqual(stats["rooms"], 1)
        self.assertEqual(stats["games"], 1)
        self.assertEqual(api.match_calls, 1)

    def test_room_404_close(self):
        """房间关停(tournament 404)视作本房结束,继续 re-match。"""
        api, stats = _drive_match(2, room_gone=True)
        self.assertEqual(stats["games"], 2)
        self.assertEqual(stats["rooms"], 2)

    def test_match_busy_backoff(self):
        """409 MATCH_BUSY 退避重试后正常入席。"""
        api, stats = _drive_match(1, busy_before=2)
        self.assertEqual(api.match_calls, 3)
        self.assertEqual(stats["games"], 1)

    def test_403_permanent(self):
        """403 PORTAL_BINDING_REQUIRED 为永久错误,直接抛出不重试。"""
        with self.assertRaises(ApiError):
            _drive_match(1, refuse_403=True)


class TestMatchRecording(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_mode_in_meta_and_log2data_filter(self):
        """,meta 带 mode=match;log2data --mode match 只收自由对战日志。"""
        rec = Recorder(root=self._tmp.name)
        _, stats = _drive_match(1, seed0=0, recorder=rec)
        self.assertEqual(stats["games"], 1)

        # 同目录再落一份无 mode 的测试房日志(走 run(),gid 同为 g1,
        # 令牌名区分文件)
        res = synth_game(9)
        fake = FakeApi(res, 0)
        it = iter([d for d in res["decisions"]
                   if d["seat"] == 0
                   and (d["mode"] == "draw" or d["legal"] != [-1])])

        def decide(g, s):
            d = next(it)
            fake.deciding(d)      # 通知夹具:该决策点已消费
            return d["action"]

        bot = BotClient(fake, "t0", decide,
                        log=lambda m: None, window_wait=0, idle_sleep=0,
                        recorder=rec)
        bot.run(max_games=1)

        import time as _t
        day = _t.strftime("%Y%m%d")
        m_path = os.path.join(self._tmp.name, day, "bot0_g1.jsonl")
        t_path = os.path.join(self._tmp.name, day, "t0_g1.jsonl")
        with open(m_path, encoding="utf-8") as f:
            meta = json.loads(f.readline())
        self.assertEqual(meta["mode"], "match")
        with open(t_path, encoding="utf-8") as f:
            self.assertNotIn("mode", json.loads(f.readline()))

        # log2data 过滤:match 只收 bot0 日志,test 只收 t0 日志
        out_m, skip_m = collect([m_path, t_path], mode="match")
        self.assertEqual(len(out_m), 1)
        self.assertTrue(all("mode 不符" in why for _, why in skip_m))
        out_t, skip_t = collect([m_path, t_path], mode="test")
        self.assertEqual(len(out_t), 1)
        self.assertEqual(len(skip_t), 1)
        out_a, _ = collect([m_path, t_path])
        self.assertEqual(len(out_a), 2)


class TestSseNotify(unittest.TestCase):
    def test_sse_frames_drive_game(self):
        """SSE 帧驱动对弈:多房循环决策序列与非 SSE 路径一致。"""
        api, stats = _drive_match(2, use_notify=True)
        self.assertEqual(stats["games"], 2)
        self.assertEqual(stats["rooms"], 2)
        self.assertEqual(stats["err409"], 0)
        self.assertEqual(stats["mirror_resets"], 0)

    def test_sse_unavailable_falls_back(self):
        """SSE 不可用(404)→ 监听退出,自动退回轮询节奏,对弈照常完成。"""
        err = ApiError(404, json.dumps({"message": "game gone"}))
        api, stats = _drive_match(1, use_notify=True, sse_error=err)
        self.assertEqual(stats["games"], 1)

    def test_sse_stream_reconnects(self):
        """断流(流被服务端关闭)后监听自动重连,对弈不中断。"""
        api, stats = _drive_match(1, use_notify=True, close_every=4)
        self.assertEqual(stats["games"], 1)
        self.assertGreaterEqual(api.notify_opens, 2)


if __name__ == "__main__":
    unittest.main()
