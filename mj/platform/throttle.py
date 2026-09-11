"""每令牌 /state 请求的主动限速与截止优先仲裁。"""

from dataclasses import dataclass
import threading
import time


@dataclass(frozen=True)
class ThrottleTicket:
    """一次 /state 许可的调度诊断信息。"""

    waited_ms: float
    urgent: bool
    deadline_missed: bool
    deadline_left_ms: float | None


class StateThrottle:
    """线程安全的 /state 许可器。

    一个 Api(一个令牌)共享一个实例。普通刷新按到达顺序运行；有截止时间
    的刷新按 EDF 运行，避免 10 局并发时普通 SSE 唤醒挤掉反应窗口。
    """

    def __init__(self, rate=15.0, burst=2, clock=time.monotonic,
                 max_normal_wait=0.5):
        if rate <= 0:
            raise ValueError("rate 必须大于 0")
        if burst < 1:
            raise ValueError("burst 必须至少为 1")
        self.rate = float(rate)
        self.interval = 1.0 / self.rate
        self.burst = int(burst)
        self.clock = clock
        self.max_normal_wait = float(max_normal_wait)
        self._cv = threading.Condition()
        self._waiters = []
        self._serial = 0
        # 允许冷启动 burst 个请求；之后维持匀速发起。
        self._next = self.clock() - (self.burst - 1) * self.interval
        self._stats = {"grants": 0, "urgent": 0, "deadline_missed": 0,
                       "waited_ms": 0.0, "waited_ms_max": 0.0,
                       "feedback_429": 0, "feedback_cooldown_ms": 0.0,
                       "feedback_cooldown_ms_max": 0.0}

    def _head(self, now):
        def key(w):
            deadline = w["deadline"]
            # normal 等待过久时提升，但仍让真正的最近截止优先。
            aged = now - w["arrived"] >= self.max_normal_wait
            # 已过期请求仍需拉状态追赶，但不能抢占尚可完成的窗口。
            # 在队列中等待期间也可能过期，因此每次仲裁重新判断。
            if deadline is not None and deadline > now:
                return (0, deadline, w["serial"])
            if aged:
                return (1, w["arrived"], w["serial"])
            return (2, w["serial"], w["serial"])
        return min(self._waiters, key=key)

    def acquire(self, gid=None, deadline=None):
        """等待一个许可；deadline 使用 monotonic 秒，None 表示普通刷新。"""
        arrived = self.clock()
        with self._cv:
            waiter = {"gid": gid, "deadline": deadline, "arrived": arrived,
                      "serial": self._serial}
            self._serial += 1
            self._waiters.append(waiter)
            self._cv.notify_all()
            while True:
                now = self.clock()
                head = self._head(now)
                grant_at = max(self._next, now)
                if head is waiter and now >= self._next:
                    self._waiters.remove(waiter)
                    self._next = now + self.interval
                    waited_ms = round((now - arrived) * 1000.0, 1)
                    left = None if deadline is None else round((deadline - now) * 1000.0, 1)
                    missed = left is not None and left <= 0
                    self._stats["grants"] += 1
                    self._stats["waited_ms"] += waited_ms
                    self._stats["waited_ms_max"] = max(self._stats["waited_ms_max"], waited_ms)
                    if deadline is not None:
                        self._stats["urgent"] += 1
                    if missed:
                        self._stats["deadline_missed"] += 1
                    self._cv.notify_all()
                    return ThrottleTicket(waited_ms, deadline is not None,
                                          missed, left)
                # 被更早 deadline 插队时应马上重算；否则睡到下一个许可或
                # normal 老化点，避免普通请求在持续窗口流量中永久不醒。
                waits = [max(0.0, grant_at - now)]
                if deadline is None:
                    waits.append(max(0.0, waiter["arrived"] + self.max_normal_wait - now))
                timeout = min(x for x in waits if x > 0) if any(waits) else self.interval
                self._cv.wait(timeout=timeout)

    def note_429(self, cooldown_intervals=1.0):
        """把服务端 429 反馈折算成下一许可的短暂冷却。

        名义速率和 EDF 排序不变；只有收到真实 429 后才把下一个许可
        向后平移一个间隔，避免网络抖动把匀速的客户端请求在服务端的
        滑动窗口里挤成一簇。调用方通常在 429 重试退避前调用一次，
        因而不会影响没有 429 的正常路径。
        """
        try:
            intervals = float(cooldown_intervals)
        except (TypeError, ValueError):
            intervals = 1.0
        intervals = max(0.0, intervals)
        now = self.clock()
        with self._cv:
            target = now + self.interval * (1.0 + intervals)
            previous = self._next
            self._next = max(self._next, target)
            added_ms = max(0.0, (self._next - previous) * 1000.0)
            self._stats["feedback_429"] += 1
            self._stats["feedback_cooldown_ms"] += added_ms
            self._stats["feedback_cooldown_ms_max"] = max(
                self._stats["feedback_cooldown_ms_max"], added_ms)
            self._cv.notify_all()

    def stats(self):
        with self._cv:
            return dict(self._stats, rate=self.rate)
