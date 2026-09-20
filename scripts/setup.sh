#!/usr/bin/env bash
# setup 引导(macOS/Linux):保证存在 Python >= 3.10,再执行 scripts/setup.py。
# 用法:scripts/setup.sh [setup.py 的任意参数,如 --install --venv]
#
# 顺序:
#   1. 依次探测 python3.11 / python3.10 / python3 / python,取第一个满足版本的;
#   2. 都没有(或版本过老,如 macOS CLT 自带 3.9)→ 有 brew 就征求同意后
#      brew install python@3.11,否则打印手动安装指引;
#   3. exec 找到的解释器运行 scripts/setup.py(参数透传)。

set -euo pipefail

Root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIN_MSG="需要 Python >= 3.10"

find_python() {
    for cand in python3.11 python3.10 python3 python; do
        if command -v "$cand" >/dev/null 2>&1; then
            if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
                command -v "$cand"
                return 0
            fi
        fi
    done
    return 1
}

if Py="$(find_python)"; then
    exec "$Py" "$Root/scripts/setup.py" "$@"
fi

echo "未找到满足版本的 Python($MIN_MSG)。" >&2

# macOS:优先 brew;无 brew 时给两条手动路线(CLT/python.org)
if [[ "$(uname)" == "Darwin" ]]; then
    BREW="$(command -v brew || true)"
    BREW_PY=""
    for p in /opt/homebrew/bin/python3.11 /usr/local/bin/python3.11; do
        [ -x "$p" ] && BREW_PY="$p" && break
    done
    if [ -z "$BREW_PY" ] && [ -n "$BREW" ]; then
        if [ -t 0 ]; then
            read -r -p "是否用 brew install python@3.11(可能需数分钟)?[y/N] " ans
            case "$ans" in
                y|Y)
                    "$BREW" install python@3.11
                    for p in /opt/homebrew/bin/python3.11 /usr/local/bin/python3.11; do
                        [ -x "$p" ] && BREW_PY="$p" && break
                    done
                    ;;
            esac
        else
            echo "(非交互模式,不自动安装;可手动执行:brew install python@3.11)" >&2
        fi
    fi
    if [ -n "$BREW_PY" ]; then
        exec "$BREW_PY" "$Root/scripts/setup.py" "$@"
    fi
    echo "手动安装任选其一:" >&2
    echo "  1) xcode-select --install 后用 CLT 的 python3(注意版本需 >= 3.10)" >&2
    echo "  2) brew install python@3.11(https://brew.sh 先装 brew)" >&2
    echo "  3) https://www.python.org/downloads/ 装官方 pkg" >&2
    exit 1
fi

echo "手动安装 Python >= 3.10 后重跑本脚本。" >&2
exit 1
