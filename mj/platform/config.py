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
