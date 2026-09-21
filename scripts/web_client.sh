#!/usr/bin/env bash
# 一键启动本地 Web 对战客户端:拉起 clientd sidecar + Vite 前端,打开浏览器。
# (scripts/web_client.ps1 的 macOS/Linux 等价物)
#
# 用法:
#   scripts/web_client.sh          # 前台跑 Vite,Ctrl+C 退出并清理 clientd
#   scripts/web_client.sh --headless   # 只起后端,不启前端(复用逻辑同下)
#
# 约定端口(与 client/src/service/config.ts 回退一致):
#   HTTP  = 127.0.0.1:17320   (REST 控制面)
#   WS    = 127.0.0.1:17321   (数据面;当前前端仅用作连接状态)
# 若 17320 已被某个 clientd 占用(health 应答),则复用而不重复启动。
# clientd 输出落在 local/web-clientd.{out,err}.log。

set -euo pipefail

Root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$Root"

HttpPort=17320
WsPort=17321
HealthUrl="http://127.0.0.1:${HttpPort}/health"
LogDir="$Root/local"
mkdir -p "$LogDir"
OutLog="$LogDir/web-clientd.out.log"
ErrLog="$LogDir/web-clientd.err.log"

# 1) 选 Python(优先仓库 .venv,其次 python3)
Py="$Root/.venv/bin/python"
if [ ! -x "$Py" ]; then
    Py="$(command -v python3 || true)"
    if [ -z "$Py" ]; then
        echo "错误:未找到 Python。请安装 python3,或在仓库根创建 .venv。" >&2
        exit 1
    fi
fi

# 1.5) clientd 运行依赖自检(mj/clientd:纯标准库 + numpy + websockets)
# .venv 存在但为空(缺依赖)时,给出可执行的修复命令,而不是让 clientd 抛 traceback。
if ! "$Py" -c 'import numpy, websockets' >/dev/null 2>&1; then
    Missing=""
    for mod in numpy websockets; do
        "$Py" -c "import $mod" >/dev/null 2>&1 || Missing="$Missing $mod"
    done
    [ -n "$Missing" ] || Missing=" numpy websockets"
    echo "错误:${Py} 缺少 clientd 运行依赖:${Missing# }" >&2
    echo "修复:\"$Py\" -m pip install${Missing}" >&2
    exit 1
fi

# 2) 前端依赖检查 + 包管理器选择(锁文件优先 pnpm)
PM="npm"
if [ -f "$Root/client/pnpm-lock.yaml" ] && command -v pnpm >/dev/null 2>&1; then
    PM="pnpm"
fi
if [ ! -x "$Root/client/node_modules/.bin/vite" ]; then
    echo "client/node_modules 缺 vite,正在 $PM install(首次会较慢)…"
    (cd "$Root/client" && "$PM" install)
fi

# 3) 启动 / 复用 clientd
StartedPid=""
if curl -sf --max-time 2 "$HealthUrl" >/dev/null 2>&1; then
    echo "clientd 已在 ${HttpPort} 运行,直接复用。"
else
    echo "启动 clientd(http=${HttpPort} ws=${WsPort})…"
    "$Py" -m mj.clientd --host 127.0.0.1 \
        --http-port "$HttpPort" --ws-port "$WsPort" \
        >>"$OutLog" 2>>"$ErrLog" &
    StartedPid=$!
    echo "  pid=${StartedPid} stdout=${OutLog} stderr=${ErrLog}"

    cleanup() {
        if [ -n "$StartedPid" ] && kill -0 "$StartedPid" 2>/dev/null; then
            echo "停止 clientd(pid=${StartedPid})…"
            kill "$StartedPid" 2>/dev/null || true
        fi
    }
    trap cleanup EXIT INT TERM

    ready=0
    for _ in $(seq 1 30); do
        sleep 0.5
        if ! kill -0 "$StartedPid" 2>/dev/null; then
            echo "错误:clientd 已退出。请查看 ${ErrLog}" >&2
            exit 1
        fi
        if curl -sf --max-time 1 "$HealthUrl" >/dev/null 2>&1; then
            ready=1
            break
        fi
    done
    if [ "$ready" -ne 1 ]; then
        echo "错误:clientd 在 15s 内未就绪,请查看 ${ErrLog}" >&2
        exit 1
    fi
    echo "clientd 就绪。"
fi

# 4) Vite 前端(前台;Ctrl+C 退出触发 trap 清理 clientd)
if [ "${1:-}" = "--headless" ]; then
    echo "后端就绪:${HealthUrl}(--headless,不启动前端)"
    echo "按 Ctrl+C 停止。"
    while true; do sleep 10; done
fi

echo "启动前端开发服务器并在浏览器打开…"
cd "$Root/client"
if [ "$PM" = "pnpm" ]; then
    "$PM" run dev --open
else
    "$PM" run dev -- --open
fi
