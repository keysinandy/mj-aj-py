#!/usr/bin/env python3
"""环境初始化脚本(macOS / Windows 通用):保障正式锦标赛能跑。

分层检查,先保命再锦上添花:

  [核心] 锦标赛默认路径(bot + legacy/shape-v1 评价器)
         —— 纯标准库,零三方依赖,只要 Python 3.10+ 能 import 就能跑;
  [策略] policy/policy-v3 策略所需(torch + checkpoint);
  [内核] Rust shanten 内核(未装自动回退纯 Python,不影响正确性);
  [开发] 测试套件(pytest/numpy)与 ONNX 导出(onnx/onnxruntime);
  [配置] local/platform.json 模板与令牌提示;
  [验证] 引擎冒烟测试 + tournament.py --dry-run 预检。

用法:
    python3 scripts/setup.py               # 只检查,给出报告
    python3 scripts/setup.py --install     # 缺的组件装进当前 Python 环境
    python3 scripts/setup.py --install --venv
                                          # 推荐:创建 .venv 并把整套环境装进去
    python3 scripts/setup.py --skip-tests  # 跳过引擎冒烟测试(快速)

退出码:锦标赛核心路径就绪 = 0,否则 1。
装好后启动锦标赛(在 .venv 里):
    .venv/bin/python scripts/tournament.py            # macOS
    .venv\\Scripts\\python.exe scripts\\tournament.py  # Windows
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

MIN_PY = (3, 10)  # mj/platform 使用 `float | None` 联合类型语法

DEFAULT_SERVER = "https://10.240.169.190:18080"
PLATFORM_JSON = os.path.join("local", "platform.json")

# 可选组件:名字 → pip 包名列表 + 说明
OPTIONAL = {
    "policy": {"pkgs": ["torch"],
               "hint": "policy/policy-v3 策略与 BC/PPO 评估"},
    "tests": {"pkgs": ["pytest", "numpy"],
              "hint": "全量单测(约 800 个)"},
    "onnx": {"pkgs": ["onnx", "onnxruntime"],
             "hint": "ONNX 导出/对拍/桌面 clientd 玩家"},
}

# 引擎冒烟测试:核心规则路径,纯 Python 几秒内跑完
SMOKE_TESTS = ["tests/test_shanten.py", "tests/test_game.py"]


def _ok(msg):
    print(f"  [ok]   {msg}", flush=True)


def _warn(msg):
    print(f"  [skip] {msg}", flush=True)


def _fail(msg):
    print(f"  [FAIL] {msg}", flush=True)


def _pip_install(pkgs):
    cmd = [sys.executable, "-m", "pip", "install", *pkgs]
    print(f"  安装:{' '.join(cmd)}", flush=True)
    return subprocess.call(cmd, cwd=_ROOT) == 0


def venv_python(root=None):
    """仓库 .venv 里解释器的路径(跨平台)。"""
    root = _ROOT if root is None else root
    sub = "Scripts" if os.name == "nt" else "bin"
    exe = "python.exe" if os.name == "nt" else "python"
    return os.path.join(root, ".venv", sub, exe)


def in_venv():
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def bootstrap_venv(skip_tests):
    """创建 .venv(若缺)并把安装流程委托给 venv 内的解释器重跑本脚本。

    web_client.sh / web_client.ps1 已优先选用 .venv 解释器;
    tournament.py 用 sys.executable,从 venv 里启动即落在 venv。
    """
    print("\n[0] 创建隔离环境 .venv")
    py = venv_python()
    if in_venv():
        _ok(f"已在 venv 中({sys.prefix})")
        return None  # 直接继续本进程的安装流程
    if os.path.exists(py):
        _ok(f".venv 已存在({py}),复用")
    else:
        cmd = [sys.executable, "-m", "venv", os.path.join(_ROOT, ".venv")]
        print(f"  创建:{' '.join(cmd)}", flush=True)
        if subprocess.call(cmd, cwd=_ROOT) != 0:
            _fail("venv 创建失败(需要 python3-venv / pyenv 等补齐 venv 模块)")
            return 1
        if not os.path.exists(py):
            _fail(f"venv 已建但找不到解释器:{py}")
            return 1
        _ok(f".venv 已创建({py})")
    # 旧版内置 pip 可能拉不动新 wheel,先升级
    subprocess.call([py, "-m", "pip", "install", "--upgrade", "--quiet",
                     "pip"], cwd=_ROOT)
    inner = [py, os.path.abspath(__file__), "--install"]
    if skip_tests:
        inner.append("--skip-tests")
    print(f"  在 venv 内继续安装:{' '.join(inner)}\n", flush=True)
    return subprocess.call(inner, cwd=_ROOT)


def check_python():
    print("\n[1/6] Python 版本")
    if sys.version_info >= MIN_PY:
        _ok(f"{sys.version_info.major}.{sys.version_info.minor}"
            f" ({sys.executable})")
        return True
    _fail(f"需要 Python >={'.'.join(map(str, MIN_PY))},"
          f"当前 {sys.version_info.major}.{sys.version_info.minor}")
    return False


def check_core_imports():
    """锦标赛默认路径(bot + legacy)的 import 冒烟——全部标准库。"""
    print("\n[2/6] 锦标赛核心路径(bot 策略,零三方依赖)")
    ok = True
    for module, attr in [
            ("mj.platform.tournament_runner", None),
            ("mj.platform.bot_client", None),
            ("mj.bot", "choose_action"),
            ("mj.win", None),
            ("mj.shanten", None),
            ("mj.scoring", None)]:
        try:
            mod = __import__(module, fromlist=[attr] if attr else [])
            if attr and not hasattr(mod, attr):
                raise AttributeError(attr)
            _ok(f"import {module}")
        except Exception as exc:  # noqa: BLE001
            _fail(f"import {module}: {type(exc).__name__} {exc}")
            ok = False
    return ok


def check_rust_kernel(install):
    print("\n[3/6] Rust shanten 内核(可选,缺省回退纯 Python)")
    if importlib.util.find_spec("mj_kernels") is not None:
        try:
            import mj_kernels  # noqa: F401
            import mj.shanten as shanten
            using_rust = any(getattr(shanten, name, None) is not None
                             for name in ("_rust_shanten",
                                          "_rust_baotou_ukeire"))
            _ok("mj_kernels 已安装,shanten 走 Rust 内核" if using_rust
                else "mj_kernels 已安装(但 shanten 未挂载,检查 MJ_KERNELS)")
            return True
        except Exception as exc:  # noqa: BLE001
            _warn(f"mj_kernels 导入失败,回退纯 Python:{exc}")
            return False
    _warn("未安装 mj_kernels —— shanten/ukeire 走纯 Python,"
          "锦标赛可跑但决策稍慢")
    if install:
        if importlib.util.find_spec("maturin") is None:
            if not _pip_install(["maturin"]):
                _warn("maturin 安装失败,跳过 Rust 内核")
                return False
        cmd = [sys.executable, "-m", "pip", "install", "-e", "./rust"]
        print(f"  构建:{' '.join(cmd)}(需 Rust 工具链,首次编译数分钟)",
              flush=True)
        if subprocess.call(cmd, cwd=_ROOT) == 0:
            _ok("Rust 内核已构建")
            return True
        _warn("Rust 内核构建失败(缺 cargo?),继续用纯 Python")
    return False


def check_optional(install):
    print("\n[4/6] 可选组件")
    status = {}
    for name, spec in OPTIONAL.items():
        missing = [p for p in spec["pkgs"]
                   if importlib.util.find_spec(p) is None]
        if not missing:
            _ok(f"{name}({'/'.join(spec['pkgs'])})—— {spec['hint']}")
        else:
            _warn(f"{name} 缺 {missing} —— {spec['hint']}")
            if install and _pip_install(missing):
                # 装完复验:importlib 需重新探测(安装后 find_spec 即时生效)
                missing = [p for p in spec["pkgs"]
                           if importlib.util.find_spec(p) is None]
                if not missing:
                    _ok(f"{name} 安装并复验通过")
                else:
                    _fail(f"{name} 装后仍缺 {missing}")
            else:
                _warn(f"{name} 未安装(--install 可自动补)")
        status[name] = not missing
    # policy 策略还需要 checkpoint
    ckpt = os.path.join(_ROOT, "runs", "bc0", "best.pt")
    if os.path.isfile(ckpt):
        _ok(f"checkpoint runs/bc0/best.pt 存在")
    else:
        _warn("runs/bc0/best.pt 不存在 —— policy 策略不可用"
              "(bot 策略不受影响)")
        status["policy"] = False
    return status


def ensure_platform_config(root=None):
    print("\n[5/6] 平台配置 local/platform.json")
    root = _ROOT if root is None else root
    path = os.path.join(root, PLATFORM_JSON)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        template = {"server": DEFAULT_SERVER, "tournament_token": "",
                    "match_token": ""}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(template, f, ensure_ascii=False, indent=2)
        _warn(f"已创建模板 {path} —— 请把报名令牌填入 tournament_token")
        return False
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"读取失败:{exc}")
        return False
    if not cfg.get("server"):
        _fail("server 字段为空")
        return False
    token = cfg.get("tournament_token")
    if isinstance(token, str) and token.strip():
        _ok(f"server={cfg['server']},tournament_token 已配置")
        return True
    _warn("tournament_token 未配置 —— 拿到报名令牌后填入,"
          "然后跑 python3 scripts/tournament.py --dry-run 验证")
    return False


def run_smoke_tests(skip):
    print("\n[6/6] 引擎冒烟测试")
    if skip:
        _warn("--skip-tests,跳过")
        return True
    cmd = [sys.executable, "-m", "pytest", *SMOKE_TESTS, "-q"]
    result = subprocess.run(cmd, cwd=_ROOT, capture_output=True, text=True)
    tail = (result.stdout or "").strip().splitlines()
    summary = tail[-1] if tail else "(无输出)"
    if result.returncode == 0:
        _ok(f"pytest {SMOKE_TESTS} 通过:{summary}")
        return True
    _fail(f"冒烟测试未通过:{summary}")
    for line in tail[-15:-1]:
        print(f"         {line}", flush=True)
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="环境初始化(锦标赛保障)")
    ap.add_argument("--install", action="store_true",
                    help="缺的组件自动 pip 安装(含 Rust 内核构建)")
    ap.add_argument("--venv", action="store_true",
                    help="创建/复用 .venv 并在其中完成 --install(推荐)")
    ap.add_argument("--skip-tests", action="store_true",
                    help="跳过引擎冒烟测试")
    args = ap.parse_args(argv)

    print(f"仓库根:{_ROOT}")
    if args.venv and not in_venv():
        rc = bootstrap_venv(args.skip_tests)
        if rc is not None:
            if rc == 0:
                print("\n===== 结论 =====")
                print("环境已安装到 .venv。之后请用 venv 解释器启动:")
                py = venv_python()
                print(f"  {py} scripts/tournament.py --dry-run")
                print(f"  {py} scripts/tournament.py")
            return rc
        # in_venv 刚变化不会发生;此处为防御分支
        print("已在 venv 内,继续当前进程安装流程。", flush=True)

    core_ok = check_python() and check_core_imports()
    check_rust_kernel(args.install)
    check_optional(args.install)
    config_ok = ensure_platform_config()
    smoke_ok = run_smoke_tests(args.skip_tests)

    print("\n===== 结论 =====")
    if core_ok and smoke_ok:
        print("锦标赛核心路径就绪(bot 策略可跑)。")
        py = venv_python()
        prefix = f"{py} " if os.path.exists(py) else "python3 "
        if config_ok:
            print(f"下一步:{prefix}scripts/tournament.py --dry-run "
                  "确认探活,然后去掉 --dry-run 启动。")
        else:
            print("补上 local/platform.json 的 tournament_token 即可上场。")
        return 0
    print("核心路径未就绪,按上方 [FAIL] 项修复后重跑。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
