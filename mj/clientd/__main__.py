"""clientd 入口:python -m mj.clientd [--host] [--http-port] [--ws-port]。

默认端口 0(占用空闲端口,端口写入 local/clientd.ports.json)。
Ctrl+C 触发优雅停机。
"""

from __future__ import annotations

import argparse
import signal
import sys

from .service import Service, DEFAULT_DISCOVERY, health_router
from .api import api_router


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
    args = ap.parse_args(argv)

    health = health_router()
    api = api_router(arena_root=args.arena_root,
                     games_root=args.games_root,
                     seed_root=args.seed_root)
    for route in api.get_all():
        health.add(*route)

    service = Service(host=args.host, http_port=args.http_port,
                      ws_port=args.ws_port, discovery=args.discovery,
                      router=health)
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