"""本地配置加载(local/platform.json,gitignored,含令牌)。"""

import json
import os

DEFAULT_PATH = os.path.join("local", "platform.json")


class TournamentConfigError(ValueError):
    """Invalid formal-tournament configuration without secret values."""


def _read_json(path, purpose):
    if not os.path.exists(path):
        raise TournamentConfigError(f"缺少配置 {path}:{purpose}")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise TournamentConfigError(
            f"无法读取配置 {path}:{type(exc).__name__}") from None


def _validate_token_label(label):
    if not isinstance(label, str) or not label.strip():
        raise TournamentConfigError("tokens 的 label 必须是非空字符串")
    if label in (".", "..") or "\x00" in label \
            or "/" in label or "\\" in label:
        raise TournamentConfigError("tokens 的 label 含有非法路径字符")
    return label


def load_tournament_config(path=None):
    """Load a scoped-token configuration for formal tournaments.

    Unlike the test-room loader this function deliberately accepts one token;
    it never infers tournament size from the number of configured identities.
    """
    p = path or DEFAULT_PATH
    cfg = _read_json(p, "正式锦标赛需含 server 与 tokens")
    if not isinstance(cfg, dict):
        raise TournamentConfigError("config 顶层必须是对象")
    if not isinstance(cfg.get("server"), str) or not cfg["server"].strip():
        raise TournamentConfigError("config 需含非空 server")
    tokens = cfg.get("tokens")
    if not isinstance(tokens, dict) or not tokens:
        raise TournamentConfigError("正式锦标赛需含一个或多个 tokens")
    for label, token in tokens.items():
        _validate_token_label(label)
        if not isinstance(token, str) or not token:
            raise TournamentConfigError(
                f"token {label!r} 必须是非空字符串")
    return cfg


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
