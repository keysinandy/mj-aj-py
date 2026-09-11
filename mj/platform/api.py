"""平台 HTTP 层:urllib + 自签证书姿态 + 429 退避 + /state 限速。

姿态与 tests/fancalc_parity.py 一致:自签证书按请求关闭校验,不改
全局 SSL 配置。一个 Api 实例对应一个令牌，可被该令牌的多个场次线程
共享；/state 配额由实例内的限速器统一仲裁。免认证测试房端点为模块级函数。
"""

import http.client
import json
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .throttle import StateThrottle, ThrottleTicket

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# 每请求尝试计数(含 429/网络退避重试);线程局部无竞态,供记录器读取
_TLS = threading.local()


class ApiError(Exception):
    """HTTP 错误;code 为平台错误码(INVALID_ACTION 等),body 留底。"""

    def __init__(self, status: int, body: str, *, uncertain=False,
                 timed_out=False, deadline_exceeded=False, attempts=1):
        self.status = status
        self.body = body
        # 对普通 HTTP 错误这些字段保持 False。动作 POST 的传输失败可能
        # 已经到达服务端，调用方必须据此选择重锚快照，而不是重发旧动作。
        self.uncertain = bool(uncertain)
        self.timed_out = bool(timed_out)
        self.deadline_exceeded = bool(deadline_exceeded)
        self.attempts = attempts
        # result_unknown 是更直白的兼容别名，便于调用方按语义读取。
        self.result_unknown = self.uncertain
        try:
            j = json.loads(body)
            self.code = j.get("code", "")
            self.message = j.get("message", "")
        except Exception:
            self.code, self.message = "", body[:200]
        super().__init__(f"HTTP {status} {self.code}: {self.message[:160]}")


class ActionSubmissionError(ApiError):
    """动作提交失败。

    ``uncertain`` 为真表示请求结果未知（例如 POST 后连接断开或网关
    5xx），动作可能已经执行，不能自动重试；为假表示服务端明确拒绝
    或请求尚未发出。该异常仍继承 :class:`ApiError`，兼容原有错误处理。
    """


class ActionDeadlineExceeded(ActionSubmissionError):
    """动作调用开始前已没有足够的本地 deadline 余量。"""


class StateSnapshotError(ApiError):
    """有界 seq=0 快照失败；该 GET 不会修改对局状态。"""


class StateSnapshotDeadlineExceeded(StateSnapshotError):
    """限速许可取得后已经越过截止，因而没有发起快照请求。"""


def _request(method, url, body=None, token=None, timeout=35.0, max_retry=5,
             deadline=None, retry=True, retry_429=True,
             retry_transient=True, error_cls=ApiError, before_attempt=None,
             state_diagnostics=False):
    """执行 HTTP 请求并把本次传输明细留在线程局部供记录器读取。

    deadline 使用 monotonic 秒；/state 用它参与限速与退避调度，动作
    POST 还用它限制单次等待。临近窗口时不再进行注定跨过截止的完整
    退避，立即尝试下一次请求，让服务端作最终裁定。

    ``retry`` 等开关只改变当前请求，不改变普通 GET/POST 的兼容行为。
    动作 POST 传入 ``retry=False`` 和 ``error_cls=ActionSubmissionError``，
    因而 429、网关错误和网络错误都只尝试一次；传输失败会标记结果
    ``uncertain``，供客户端快照重锚。
    """
    data = json.dumps(body).encode() if body is not None else None
    delay, failures = 0.5, 0
    attempts = retry_429_count = retry_gateway = retry_network = 0
    backoff_ms = 0.0
    deadline_fast_retry = False
    # Keep this deliberately small: one dictionary per physical HTTP attempt,
    # without copying the URL, headers, or response body.  It is enabled only
    # by Api.game_state so ordinary endpoints retain their old metadata shape.
    state_attempts = [] if state_diagnostics else None
    allow_retry_429 = bool(retry and retry_429)
    allow_retry_transient = bool(retry and retry_transient)
    is_action = issubclass(error_cls, ActionSubmissionError)
    wraps_transport = is_action or issubclass(error_cls, StateSnapshotError)

    def set_meta():
        _TLS.attempts = attempts
        meta = {
            "attempts": attempts, "retry_429": retry_429_count,
            "retry_gateway": retry_gateway,
            "retry_network": retry_network,
            "backoff_ms": round(backoff_ms, 1),
        }
        if state_diagnostics:
            # Copy the list so a caller observing _TLS after the request gets
            # a stable snapshot even while another retry is being prepared.
            meta["state_attempts"] = [dict(item) for item in state_attempts]
            meta["state_physical_attempts"] = len(state_attempts)
            meta["state_429"] = sum(
                item.get("status") == 429 for item in state_attempts)
        _TLS.request_meta = meta

    def record_state_attempt(started_epoch, started_mono, status=None,
                             error=None):
        if not state_diagnostics:
            return
        item = {
            "started_epoch": round(started_epoch, 3),
            "status": status,
            "latency_ms": round((time.monotonic() - started_mono) * 1000.0,
                                 1),
        }
        if error:
            item["error"] = error
        state_attempts.append(item)
        # Keep the thread-local metadata current on every terminal outcome;
        # this matters for callers that inspect it after an ApiError.
        set_meta()

    def response_status(response):
        status = getattr(response, "status", None)
        if status is None:
            getcode = getattr(response, "getcode", None)
            if getcode is not None:
                try:
                    status = getcode()
                except Exception:
                    status = None
        # Test doubles and custom response wrappers sometimes expose a
        # MagicMock or another object here.  Keep diagnostics JSON-safe and
        # use null for an unavailable HTTP status.
        return status if isinstance(status, int) else None

    def make_error(status, body, **kwargs):
        kwargs.setdefault("attempts", attempts)
        return error_cls(status, body, **kwargs)

    def request_timeout():
        """给有 deadline 的动作请求设置单次等待上限。

        动作 deadline 在 monotonic 时钟上计算；至少保留 1ms，避免把
        urllib 的 timeout 设成 0 而在真正发包前直接 ValueError。普通
        无 deadline 请求仍使用原有 timeout。
        """
        # /state 的 deadline 参与限速器/重试排序，但它本身仍可能是
        # 一次服务端长轮询；只给动作 POST 限制 socket 单次等待，避免
        # 把合法的 state 挂起语义改成短轮询超时。
        if deadline is None or not is_action:
            return timeout
        left = deadline - time.monotonic()
        return min(timeout, max(0.001, left))

    def is_timeout(exc):
        reason = getattr(exc, "reason", None)
        return isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError)

    def sleep_retry(sec):
        nonlocal backoff_ms, deadline_fast_retry
        if deadline is not None and not deadline_fast_retry:
            # 给一次网络往返留出极小余量；若已无等待空间则只立刻重试
            # 一次，避免持续 429 时退化为热循环。
            capped = max(0.0, deadline - time.monotonic() - 0.05)
            if capped < sec:
                sec = capped
                deadline_fast_retry = True
        if sec > 0:
            time.sleep(sec)
            backoff_ms += sec * 1000.0

    # Initialize metadata before the first throttle callback.  If acquiring a
    # state permit itself fails, the caller still sees a fresh zero-attempt
    # diagnostic instead of metadata left by the previous request in the same
    # worker thread.
    if state_diagnostics:
        set_meta()
    while True:
        if before_attempt is not None:
            before_attempt()
        if is_action and deadline is not None and time.monotonic() >= deadline:
            set_meta()
            raise ActionDeadlineExceeded(
                0, "action deadline exceeded", deadline_exceeded=True,
                attempts=attempts)
        attempts += 1
        # retry_429_count 与 retry_429(是否允许) 分开，避免线程局部诊断
        # 被布尔开关覆盖。
        set_meta()
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        started_epoch = time.time()
        started_mono = time.monotonic()
        attempt_status = None
        try:
            with urllib.request.urlopen(req, timeout=request_timeout(),
                                        context=_CTX) as r:
                if state_diagnostics:
                    attempt_status = response_status(r)
                    # A successful response wrapper without status metadata
                    # still represents an HTTP 200 from this call.  Keep the
                    # summary useful while retaining JSON-safe values for
                    # test doubles and custom wrappers.
                    if attempt_status is None:
                        attempt_status = 200
                raw = r.read()
                result = json.loads(raw.decode())
                record_state_attempt(started_epoch, started_mono,
                                     attempt_status)
                set_meta()
                return result
        except urllib.error.HTTPError as e:
            # Record before reading the body: an HTTPError body is not part of
            # the diagnostic contract and a broken body stream must not erase
            # evidence that a physical attempt happened.
            record_state_attempt(started_epoch, started_mono, e.code)
            if e.code == 429:
                retry_429_count += 1
                set_meta()
                if not allow_retry_429:
                    raise make_error(e.code, e.read().decode(errors="replace"),
                                     uncertain=False) from None
                sleep_retry(delay)
                delay = min(delay * 2, 10.0)
                continue
            if e.code in (502, 503, 504):
                # 网关瞬断(实测 2026-09-08:502 风暴一次即杀全场次线程,
                # 房间 10 局全被服务端代打):与网络错误共享退避预算重试
                failures += 1
                retry_gateway += 1
                set_meta()
                if allow_retry_transient and failures <= max_retry:
                    sleep_retry(delay)
                    delay = min(delay * 2, 10.0)
                    continue
                # 网关错误发生在 POST 之后时，上游是否执行未知；动作
                # 提交必须把它交给客户端重锚，不能再次发送旧 payload。
                raise make_error(e.code, e.read().decode(errors="replace"),
                                 uncertain=is_action) from None
            if 500 <= e.code < 600:
                # 500/501/505 等非网关 5xx 也可能发生在上游已执行
                # 动作之后；动作调用方同样必须把结果视为未知。
                raise make_error(e.code, e.read().decode(errors="replace"),
                                 uncertain=is_action) from None
            if e.code == 408:
                # HTTP 层明确超时同样不能证明动作未到达上游。
                raise make_error(e.code, e.read().decode(errors="replace"),
                                 uncertain=is_action, timed_out=wraps_transport) \
                    from None
            # 400/401/403/404/409 等是服务端已经明确作出的语义裁定，
            # 不应进入下面的瞬态重试路径。
            raise make_error(e.code, e.read().decode(errors="replace"),
                             uncertain=False) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            record_state_attempt(started_epoch, started_mono, None,
                                 type(e).__name__ if state_diagnostics else None)
            failures += 1
            retry_network += 1
            timed_out = is_timeout(e)
            set_meta()
            if not allow_retry_transient or failures > max_retry:
                if wraps_transport:
                    raise make_error(0, str(e), uncertain=True,
                                     timed_out=timed_out) from e
                raise
            sleep_retry(delay)
            delay = min(delay * 2, 10.0)
        except (http.client.HTTPException, ValueError) as e:
            record_state_attempt(started_epoch, started_mono, attempt_status,
                                 type(e).__name__ if state_diagnostics else None)
            # 连接在响应头或 JSON body 中途断开时，服务端可能已经执
            # 行了动作；这类「响应丢失」不能按普通 GET 的重试规则处理。
            if wraps_transport:
                raise make_error(0, str(e), uncertain=True,
                                 timed_out=is_timeout(e)) from e
            raise


class Api:
    """一个参赛令牌的玩家 API(多场次线程共享，/state 限速按实例)。"""

    def __init__(self, server: str, token: str, timeout: float = 35.0,
                 state_rate: float | None = 15.0, state_throttle=None):
        self.base = server.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.state_throttle = state_throttle if state_throttle is not None \
            else (StateThrottle(state_rate) if state_rate is not None else None)

    def _url(self, path, params=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return url

    def get(self, path, params=None):
        return _request("GET", self._url(path, params), token=self.token,
                        timeout=self.timeout)

    def post(self, path, body=None, *, deadline=None, retry=True):
        return _request("POST", self._url(path), body=body, token=self.token,
                        timeout=self.timeout, deadline=deadline, retry=retry)

    # ---- 玩家端点包装 ----

    def me(self):
        return self.get("/api/me")

    def rules(self):
        """报名令牌直达:绑定锦标赛的 config。"""
        return self.get("/api/tournaments/me/rules")

    def tournament(self, tid):
        return self.get(f"/api/tournaments/{tid}")

    def register(self, tid):
        return self.post(f"/api/tournaments/{tid}/register")

    def ready(self, tid):
        return self.post(f"/api/tournaments/{tid}/ready")

    def match(self):
        """自由对战自动匹配入席(全局令牌唯一入口)。

        不带 body = 服务默认配置(v15:M=10/Rounds=8;显式低上限会
        永久 404)。在途 auto 房重调幂等返原房(崩溃重启可恢复);
        非门户绑定令牌 403 PORTAL_BINDING_REQUIRED(永久,勿重试)。
        """
        return self.post("/api/match")

    def game_state(self, gid, seq, deadline=None, request_timeout=None):
        """拉取状态；只有该端点消耗每令牌共享的 16/s 预算。

        ``request_timeout`` 是本次 urllib 调用的单次 timeout 上限，
        不是端到端耗时保证；默认仍使用实例 timeout。需要确认窗口
        状态而不能被服务端长轮询拖住时，可传一个较小的值，尤其是
        ``seq=0`` 的快照重锚。
        """
        _TLS.throttle_ticket = None
        def acquire():
            ticket = self.state_throttle.acquire(gid, deadline) \
                if self.state_throttle is not None else None
            previous = _TLS.throttle_ticket
            if ticket is not None and previous is not None:
                ticket = ThrottleTicket(
                    previous.waited_ms + ticket.waited_ms,
                    previous.urgent or ticket.urgent,
                    previous.deadline_missed or ticket.deadline_missed,
                    ticket.deadline_left_ms)
            _TLS.throttle_ticket = ticket
        timeout = self.timeout
        if request_timeout is not None:
            timeout = max(0.001, float(request_timeout))
            timeout = min(timeout, self.timeout)
        return _request("GET", self._url(f"/api/games/{gid}/state",
                                           {"seq": seq}), token=self.token,
                        timeout=timeout, deadline=deadline,
                        before_attempt=acquire, state_diagnostics=True)

    def game_snapshot(self, gid, deadline=None, timeout=0.5):
        """有界地请求 ``seq=0`` 全量快照。

        这是 pending chi/动作结果不确定时的确认入口。``timeout`` 只
        限制一次 urllib 调用；TLS 握手、连接、读取和慢速响应的总耗时
        不构成硬截止，调用方仍应自行保留窗口余量。
        """
        return self.game_state(gid, 0, deadline=deadline,
                               request_timeout=timeout)

    def game_action(self, gid, payload, deadline=None):
        """提交一个动作，严格单次发送。

        动作 POST 没有安全的通用重试语义：429/409 是明确拒绝，网络
        超时或 5xx 则可能已经执行。所有失败都抛出
        :class:`ActionSubmissionError`，其中 ``uncertain`` 为真时调用方
        必须先用 ``seq=0`` 快照重建，再决定下一动作。

        ``deadline`` 是本地 ``time.monotonic()`` 的绝对秒数。请求开始前
        会检查截止，已过截止则不发包；请求开始后只把本次 urllib
        timeout 截到剩余时间。它不是 TLS/连接/读取的端到端硬截止，
        慢速响应仍可能使总耗时超过 deadline。
        """
        return _request(
            "POST", self._url(f"/api/games/{gid}/action"), body=payload,
            token=self.token, timeout=self.timeout, deadline=deadline,
            retry=False, retry_429=False, retry_transient=False,
            error_cls=ActionSubmissionError)

    def open_notify(self, gid, timeout=45.0):
        """打开 /api/games/{gid}/notify SSE 通知流(v12),返回可逐行读的
        响应对象(调用方负责 close)。

        帧只含 seq(包含式水位,与 /state 同源)——不可直接当轮询游标,
        只作"状态已变"信号;30s keepalive 注释行;流终止推
        {"seq":N,"closed":true}。每用户 32 并发连接(超限 429),不占
        /state 16/s 额度。429/网络错误抛出由调用方退避;403/404 =
        场次不可访问。
        """
        req = urllib.request.Request(
            self._url(f"/api/games/{gid}/notify"), method="GET")
        req.add_header("Authorization", "Bearer " + self.token)
        req.add_header("Accept", "text/event-stream")
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=_CTX)
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, e.read().decode(errors="replace")) from None


def room_games(server: str, room_id: str) -> list:
    """免认证:测试房局列表 [{batch, game_id, status}](batch 升序)。"""
    return _request("GET",
                    server.rstrip("/") + f"/api/test-rooms/{room_id}/games")


def room_events(server: str, room_id: str, batch: int) -> dict:
    """免认证:某局完整事件流(blocks 含四家手牌 + rounds 结果 + seats)。"""
    return _request(
        "GET",
        server.rstrip("/") + f"/api/test-rooms/{room_id}/games/{batch}/events")
