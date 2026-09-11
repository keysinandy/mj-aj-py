"""SSE 合并与十场共享配额的离线调度回归。"""

import queue
from unittest import mock

from mj.platform.bot_client import BotClient, StateDemand
from mj.platform.throttle import StateThrottle
from mj.platform.state_demand import WindowId


def test_sse_burst_is_one_successful_wakeup():
    bot = BotClient(None, "bot", None)
    wake = queue.Queue()
    for seq in range(100):
        wake.put((seq, False))
    assert bot._wait_wake(wake, {"alive": True}, max_wait=0)
    assert wake.empty()
    assert not bot._wait_wake(wake, {"alive": True}, max_wait=0)


def test_ten_game_backlog_preserves_live_window():
    throttle = StateThrottle(rate=12.5, clock=lambda: 10.0)
    # 九场旧窗口追赶不能把第十场尚有 200ms 的窗口挤到 720ms 后。
    throttle._waiters = [
        dict(gid=str(i), deadline=9.0, arrived=9.0, serial=i)
        for i in range(9)
    ] + [dict(gid="live", deadline=10.2, arrived=10.0, serial=9)]
    order = []
    for slot in range(10):
        head = throttle._head(10.0 + slot * throttle.interval)
        order.append(head["gid"])
        throttle._waiters.remove(head)
    assert order == ["live"] + [str(i) for i in range(9)]


def test_deadline_expiring_in_queue_loses_priority():
    throttle = StateThrottle(clock=lambda: 10.0)
    stale = dict(gid="stale", deadline=10.1, arrived=10.0, serial=0)
    live = dict(gid="live", deadline=11.0, arrived=10.0, serial=1)
    throttle._waiters = [stale, live]
    assert throttle._head(10.0) is stale
    assert throttle._head(10.2) is live


def test_ten_games_merge_sse_during_state_and_preserve_new_watermark():
    demands = [StateDemand() for _ in range(10)]
    for demand in demands:
        for seq in range(1, 101):
            demand.put((seq, False))
        assert demand.qsize() == 1
        # Window confirmation covers this burst, including its deadline wake.
        demand.acknowledge(100)
        assert demand.empty()
        for seq in range(101, 201):
            demand.put((seq, False))
        demand.acknowledge(150)
        assert demand.qsize() == 1
        assert demand.get_nowait() == (200, False)


def test_demand_preserves_closed_and_unknown_watermarks():
    for frames in [[(100, True), (101, False)],
                   [(None, False), (101, False)]]:
        demand = StateDemand()
        for frame in frames:
            demand.put(frame)
        demand.acknowledge(200)
        assert demand.qsize() == 1


def test_chi_readiness_separates_local_resolution_from_server_seen():
    from test_window_recovery import _snapshot

    bot = BotClient(None, "bot", None)
    mirror = bot._mirror_from_snapshot(_snapshot(
        phase="response_peng", turn=3, last_discard="9b",
        responding=[0, 1, 2]))
    chi = bot._make_chi_pending(mirror)
    assert chi is not None
    assert mirror.me in chi["needed"]
    assert mirror.me in chi["resolved"]
    assert mirror.me not in chi["seen"]
    assert not chi["confirmed"]


def test_ten_play_loops_acknowledge_inflight_sse_with_one_state_each():
    from concurrent.futures import ThreadPoolExecutor
    import threading

    demands = {str(i): StateDemand() for i in range(10)}
    barrier = threading.Barrier(10)
    active = {gid: 0 for gid in demands}
    calls = {gid: 0 for gid in demands}

    class Api:
        def game_state(self, gid, seq):
            active[gid] += 1
            calls[gid] += 1
            assert active[gid] == 1
            barrier.wait(timeout=5)
            for watermark in range(1, 101):
                demands[gid].put((watermark, False))
            active[gid] -= 1
            return {"seq": 100, "finished": True}

    bot = BotClient(Api(), "bot", None)
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(bot._play_loop, gid, demand, {"alive": True})
                   for gid, demand in demands.items()]
        for future in futures:
            future.result(timeout=10)
    assert all(count == 1 for count in calls.values())
    assert all(demand.empty() for demand in demands.values())


def test_play_loop_passes_merged_deadline_to_physical_state_call():
    demand = StateDemand()
    demand.submit_window_confirm(
        WindowId("g", 1, 2, 7, 5), "response_peng", deadline=42.0)
    bot = BotClient(mock.Mock(), "bot", None)
    bot._state = mock.Mock(return_value={
        "finished": True, "seq": 0, "snapshot": {"scores": [0, 0, 0, 0]}})

    bot._play_loop("g", demand, {"alive": False})

    assert bot._state.call_args.args[2] == 42.0
