#!/usr/bin/env bash
# setup 引导(macOS/Linux):保证存在 Python >= 3.10,再执行 scripts/setup.py。
# 用法:scripts/setup.sh [setup.py 的任意参数,如 --install --venv]
#
# 顺序:
#   1. 依次探测 python3.11 / python3.12 / python3.13 / python3.10 / python3 /
#      python,取第一个「满足 >= 3.10 且不超出 Rust 内核支持上限」的;
#   2. 只有「能跑但超出内核上限」的解释器 → 不询问,直接 brew install
#      python@3.11 后再用它;
#   3. 全都低于 3.10 → 同样直接尝试安装;
#   4. exec 找到的解释器运行 scripts/setup.py(参数透传)。
#
# 版本判据只在 scripts/setup.py 的 RUST_KERNEL_MAX_PY 一处维护:本脚本只读
# --interpreter-check 的退出码,不要再抄一份版本号过来。
#
# 不想让它自动安装 Python:设 MJ_SETUP_NO_AUTO_INSTALL=1

set -euo pipefail

Root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SetupPy="$Root/scripts/setup.py"
MIN_MSG="需要 Python >= 3.10"
CANDIDATES="python3.11 python3.12 python3.13 python3.10 python3 python"

no_auto_install() {
    case "${MJ_SETUP_NO_AUTO_INSTALL:-}" in
        ""|0|false|no|False|No) return 1 ;;
        *) return 0 ;;
    esac
}

# 0 = 可用且支持 Rust 内核;2 = 可用但超出内核上限;1 = 版本过低;
# -1 = 这个候选根本不是能用的 Python。
py_verdict() {
    local rc=0
    "$1" "$SetupPy" --interpreter-check >/dev/null 2>&1 || rc=$?
    case "$rc" in
        0|1|2) printf '%s' "$rc" ;;
        *) printf '%s' -1 ;;
    esac
}

find_python311() {
    local path p
    path="$(command -v python3.11 2>/dev/null || true)"
    if [ -n "$path" ] && [ "$(py_verdict "$path")" = "0" ]; then
        printf '%s' "$path"
        return 0
    fi
    # brew 装完新 PATH 未必立刻生效,兜一下固定前缀(与 setup.ps1 兜
    # winget 的 Python311 安装位置对应)。
    for p in /opt/homebrew/bin/python3.11 /usr/local/bin/python3.11; do
        if [ -x "$p" ] && [ "$(py_verdict "$p")" = "0" ]; then
            printf '%s' "$p"
            return 0
        fi
    done
    return 1
}

# 成功则把 3.11 的路径打到 stdout;失败返回非零,由调用方兜底。
install_python311() {
    if ! command -v brew >/dev/null 2>&1; then
        echo "未找到 brew,无法自动安装 Python 3.11。" >&2
        return 1
    fi
    echo "安装:brew install python@3.11(可能需数分钟)…" >&2
    if ! brew install python@3.11 >&2; then
        echo "brew 安装失败。" >&2
        return 1
    fi
    find_python311
}

Usable=""
Degraded=""
for cand in $CANDIDATES; do
    path="$(command -v "$cand" 2>/dev/null || true)"
    [ -n "$path" ] || continue
    verdict="$(py_verdict "$path")"
    if [ "$verdict" = "0" ]; then
        Usable="$path"
        break
    fi
    if [ "$verdict" = "2" ] && [ -z "$Degraded" ]; then
        Degraded="$path"
    fi
done

Chosen="$Usable"

if [ -z "$Chosen" ] && [ -n "$Degraded" ]; then
    desc="$("$Degraded" "$SetupPy" --interpreter-check 2>/dev/null || true)"
    echo "当前解释器 $Degraded :$desc" >&2
    TriedInstall=0
    if no_auto_install; then
        echo "已设 MJ_SETUP_NO_AUTO_INSTALL,不自动安装 Python 3.11。" >&2
    else
        TriedInstall=1
        echo "自动安装 Python 3.11(Rust 内核需要更低的版本)…" >&2
        Chosen="$(install_python311 || true)"
    fi
    if [ -z "$Chosen" ]; then
        if [ "$TriedInstall" = "1" ]; then
            echo "3.11 未装成或本会话找不到。" >&2
        fi
        echo "Rust 内核装不上,shanten/ukeire 将走纯 Python(可跑但稍慢)。" >&2
        Chosen="$Degraded"
    fi
fi

if [ -z "$Chosen" ]; then
    echo "未找到满足版本的 Python($MIN_MSG)。" >&2
    if ! no_auto_install; then
        echo "自动安装 Python 3.11…" >&2
        Chosen="$(install_python311 || true)"
    fi
    if [ -z "$Chosen" ]; then
        echo "手动安装任选其一:" >&2
        echo "  1) brew install python@3.11(https://brew.sh 先装 brew)" >&2
        echo "  2) 系统包管理器:apt install python3.11 / dnf install python3.11" >&2
        echo "  3) https://www.python.org/downloads/ 装官方包" >&2
        if [ "$(uname)" = "Darwin" ]; then
            echo "  4) xcode-select --install 后用 CLT 的 python3(注意版本需 >= 3.10)" >&2
        fi
        exit 1
    fi
fi

exec "$Chosen" "$SetupPy" "$@"
