"""clientd 入口:python -m mj.clientd [--host] [--http-port] [--ws-port]。

默认端口 0(占用空闲端口,端口写入 local/clientd.ports.json)。
Ctrl+C 触发优雅停机。
"""

from __future__ import annotations

import argparse
import signal
import sys

from .service import Service, DEFAULT_DISCOVERY, health_router, \
    DEFAULT_CORS_ORIGINS
from .api import api_router, session_router
from .arena import make_arena_session_manager


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mj.clientd",
                                 description="clientd sidecar 服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--http-port", type=int, default=0)
    ap.add_argument("--ws-port", type=int, default=0)
    ap.add_argument("--discovery", default=DEFAULT_DISCOVERY)
    ap.add_argument("--arena-root", default=None)
    ap.add_argument("--games-root", default=None)
    ap.add_argument("--seed-root", default=None)
    ap.add_argument("--settings-path", default=None,
                    help="本机平台配置路径(默认 local/platform.json)")
    ap.add_argument("--model-root", default=None,
                    help="本机 ONNX 模型目录(默认 local/models)")
    ap.add_argument("--cors-origins", nargs="*", default=None,
                    help="放行 CORS 的额外 Origin;缺省用本地开发默认集")
    args = ap.parse_args(argv)

    cors_origins = list(DEFAULT_CORS_ORIGINS)
    if args.cors_origins:
        cors_origins.extend(args.cors_origins)

    health = health_router()
    api = api_router(arena_root=args.arena_root,
                     games_root=args.games_root,
                     seed_root=args.seed_root,
                     settings_path=args.settings_path,
                     model_root=args.model_root)
    sessions = make_arena_session_manager(
        arena_root=args.arena_root, settings_path=args.settings_path)
    # api_router 内部持有同一份 ModelStore；会话控制面通过它读取当前
    # 选择，仅对创建时尚未显式指定模型的新会话注入路径。
    from .settings import ModelStore, PlatformSettings
    model_store = ModelStore(
        args.model_root or "local/models",
        settings=PlatformSettings(args.settings_path or "local/platform.json"),
    )
    api_sessions = session_router(sessions,
                                  model_resolver=model_store.resolve_selected)
    for route in api.get_all() + api_sessions.get_all():
        health.add(*route)

    service = Service(host=args.host, http_port=args.http_port,
                      ws_port=args.ws_port, discovery=args.discovery,
                      router=health, cors_origins=cors_origins)
    service.start()
    print(f"clientd listening http={service.ports['http']} "
          f"ws={service.ports['ws']} discovery={args.discovery}", flush=True)

    stop = {"flag": False}

    def _handler(signum, frame):
        stop["flag"] = True
        try:
            service.stop()
        except Exception:  # noqa: BLE001
            pass
        print("clientd stopped", flush=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)

    while True:
        import time
        time.sleep(10)


if __name__ == "__main__":
    main()
