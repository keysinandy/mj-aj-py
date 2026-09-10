"""SSE 合并与十场共享配额的离线调度回归。"""

import queue

from mj.platform.bot_client import BotClient
from mj.platform.throttle import StateThrottle


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
