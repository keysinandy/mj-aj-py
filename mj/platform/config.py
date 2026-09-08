"""本地配置加载(local/platform.json,gitignored,含令牌)。"""

import json
import os

DEFAULT_PATH = os.path.join("local", "platform.json")


def load_config(path=None):
    p = path or DEFAULT_PATH
    if not os.path.exists(p):
        raise SystemExit(
            f"缺少配置 {p}:请按如下格式创建(令牌来自门户测试房间,勿入库)\n"
            '{\n  "server": "https://10.240.169.190:18080",\n'
            '  "tokens": {"青龙": "<token>", "白虎": "<token>",\n'
            '             "朱雀": "<token>", "玄武": "<token>"}\n}')
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg.get("server") and cfg.get("tokens"), "config 需含 server 与 tokens"
    return cfg


def load_match_config(path=None):
    """自由对战配置:server + match_token(门户绑定全局令牌)。

    match_token 来自门户「我的 AI 身份」(OpenID 绑定)——v24 后旧
    匿名全局令牌调 /api/match 永久 403;测试房 4 令牌是 scoped 的,
    调 /api/match 会 400 TOKEN_NOT_SCOPED,都不能当 match_token 用。
    """
    p = path or DEFAULT_PATH
    if not os.path.exists(p):
        raise SystemExit(
            f"缺少配置 {p}:自由对战需在 config 中加\n"
            '  "match_token": "<门户『我的 AI 身份』签发的绑定全局令牌>"')
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    if not (cfg.get("server") and cfg.get("match_token")):
        raise SystemExit("config 需含 server 与 match_token")
    return cfg
