"""平台 HTTP 层:urllib + 自签证书姿态 + Bearer 认证 + 429 退避。

姿态与 tests/fancalc_parity.py 一致:自签证书按请求关闭校验,不改
全局 SSL 配置。一个 Api 实例 = 一个令牌 = 一个 bot 线程,不做跨线程
共享;免认证的测试房数据端点为模块级函数。
"""

import json
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# 每请求尝试计数(含 429/网络退避重试);线程局部无竞态,供记录器读取
_TLS = threading.local()


class ApiError(Exception):
    """HTTP 错误;code 为平台错误码(INVALID_ACTION 等),body 留底。"""

    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        try:
            j = json.loads(body)
            self.code = j.get("code", "")
            self.message = j.get("message", "")
        except Exception:
            self.code, self.message = "", body[:200]
        super().__init__(f"HTTP {status} {self.code}: {self.message[:160]}")


def _request(method, url, body=None, token=None, timeout=35.0, max_retry=5):
    data = json.dumps(body).encode() if body is not None else None
    delay, failures = 0.5, 0
    attempts = 0
    while True:
        attempts += 1
        _TLS.attempts = attempts
    while True:
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(delay)
                delay = min(delay * 2, 10.0)
                continue
            raise ApiError(e.code, e.read().decode(errors="replace")) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            failures += 1
            if failures > max_retry:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 10.0)


class Api:
    """一个参赛令牌的玩家 API(线程封闭)。"""

    def __init__(self, server: str, token: str, timeout: float = 35.0):
        self.base = server.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _url(self, path, params=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return url

    def get(self, path, params=None):
        return _request("GET", self._url(path, params), token=self.token,
                        timeout=self.timeout)

    def post(self, path, body=None):
        return _request("POST", self._url(path), body=body, token=self.token,
                        timeout=self.timeout)

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

    def game_state(self, gid, seq):
        return self.get(f"/api/games/{gid}/state", {"seq": seq})

    def game_action(self, gid, payload):
        return self.post(f"/api/games/{gid}/action", payload)


def room_games(server: str, room_id: str) -> list:
    """免认证:测试房局列表 [{batch, game_id, status}](batch 升序)。"""
    return _request("GET",
                    server.rstrip("/") + f"/api/test-rooms/{room_id}/games")


def room_events(server: str, room_id: str, batch: int) -> dict:
    """免认证:某局完整事件流(blocks 含四家手牌 + rounds 结果 + seats)。"""
    return _request(
        "GET",
        server.rstrip("/") + f"/api/test-rooms/{room_id}/games/{batch}/events")
