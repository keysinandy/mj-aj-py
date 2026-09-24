#!/usr/bin/env python3
"""环境初始化脚本(macOS / Windows 通用):保障正式锦标赛能跑。

分层检查,先保命再锦上添花:

  [核心] 锦标赛默认路径(bot + legacy/shape-v1 评价器)
         —— 纯标准库,零三方依赖,只要 Python 3.10+ 能 import 就能跑;
  [策略] policy/policy-v3 策略所需(torch + checkpoint);
  [内核] Rust shanten 内核(未装自动回退纯 Python,不影响正确性);
  [前端] Node >= 22(client/ 与 web/replay_debugger/ 的构建工具链;
         缺 Node 不影响锦标赛核心路径,只是前端起不来);
  [网页] 浏览器 web 对战客户端(clientd)运行依赖:numpy + websockets;
  [开发] 测试套件(pytest/numpy)与 ONNX 导出(onnx/onnxruntime);
  [配置] local/platform.json 模板与令牌提示;
  [验证] 引擎冒烟测试 + tournament.py --dry-run 预检。

用法:
    python3 scripts/setup.py               # 只检查,给出报告
    python3 scripts/setup.py --install     # 缺的组件装进当前 Python 环境
    python3 scripts/setup.py --install --venv
                                          # 推荐:创建 .venv 并把整套环境装进去
    python3 scripts/setup.py --skip-tests  # 跳过引擎冒烟测试(快速)
    python3 scripts/setup.py --install --venv --no-rust
                                          # 缺 Rust 工具链时不构建内核
    python3 scripts/setup.py --interpreter-check
                                          # 只回答"这个解释器能不能建 Rust 内核",
                                          # 供 setup.ps1/setup.sh 判断

退出码:锦标赛核心路径就绪 = 0,否则 1。
        --interpreter-check:0 = 可用且支持 Rust 内核,
                            2 = 可用但超出内核支持范围,
                            1 = 版本过低。

环境变量:
    MJ_SETUP_NO_AUTO_INSTALL=1
        关掉全部自动改动:引导层在版本不符时不自动安装 Python,
        本脚本也不自动删除版本不一致的 .venv、不自动安装 Node(只警告)。
        CI / 他人机器上跑检查时用得上。
装好后启动锦标赛(在 .venv 里):
    .venv/bin/python scripts/tournament.py            # macOS
    .venv\\Scripts\\python.exe scripts\\tournament.py  # Windows
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

MIN_PY = (3, 10)  # mj/platform 使用 `float | None` 联合类型语法
# Rust shanten 内核(rust/Cargo.toml 的 pyo3 0.22)支持的解释器上限。
# 超出这个区间锦标赛照样能跑(纯 Python 回退),只是内核构建会失败。
# 引导层(setup.ps1/setup.sh)通过 --interpreter-check 的退出码读这个判据,
# 不要把 3.13 这个数字抄到那两个脚本里。
RUST_KERNEL_MAX_PY = (3, 13)

# Node 下界:client/(Tauri2+Vite+React+TS)与 web/replay_debugger/ 的构建
# 工具链要求。这是**下界** —— node 主版本 >= 22 一律不动,不降级。
# 判据只在本文件维护:setup.ps1/setup.sh 不碰 Node(它们的职责只是搞到一个
# 能跑本脚本的 Python)。写成元组只为与 MIN_PY 对称,眼下只有主版本故取 [0]。
MIN_NODE = (22,)

# 版本化包的标识。winget 的 LTS 通道现在是 24(OpenJS.NodeJS.LTS → 24.19.0),
# 所以"要 22"必须写死带版本号的包;brew 的 node@22 是 keg-only,装完不会进
# PATH,得靠 detect_node 的 opt 前缀兜底,且要让用户知道终端里仍敲不到。
# (brew 侧该 formula 的 deprecation_date 是 2026-10-28(upstream unsupported),
#  届时 `brew install node@22` 可能失效,需换成当时的 LTS 或 nodejs@22 继任者。)
NODE_WINGET_ID = "OpenJS.NodeJS.22"
NODE_BREW_FORMULA = "node@22"

# 设为 1 则关掉全部自动改动(自动安装 Python / 自动安装 Node / 自动删除 .venv)
NO_AUTO_INSTALL_ENV = "MJ_SETUP_NO_AUTO_INSTALL"

DEFAULT_SERVER = "https://10.240.169.190:18080"
PLATFORM_JSON = os.path.join("local", "platform.json")

# 可选组件:名字 → pip 包名列表 + 说明
OPTIONAL = {
    "web": {"pkgs": ["numpy", "websockets"],
            "hint": "浏览器 web 客户端 clientd(scripts/web_client.sh)"},
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


def venv_python_version(root=None):
    """`.venv` 里解释器的 (major, minor);读不到返回 None。

    读 pyvenv.cfg 的 version 键(3.3+ 都会写),省掉一次子进程。读不到时
    必须当成"不确定"而不是"不匹配"——调用方据此决定要不要动这个目录。
    """
    root = _ROOT if root is None else root
    cfg = os.path.join(root, ".venv", "pyvenv.cfg")
    try:
        with open(cfg, encoding="utf-8") as f:
            for line in f:
                key, sep, val = line.partition("=")
                if not sep or key.strip() not in ("version", "version_info"):
                    continue
                parts = val.strip().split(".")
                return (int(parts[0]), int(parts[1]))
    except (OSError, ValueError, IndexError):
        return None
    return None


def _no_auto_install():
    """MJ_SETUP_NO_AUTO_INSTALL 是否开启(空/0/false/no 都算关)。"""
    val = os.environ.get(NO_AUTO_INSTALL_ENV, "").strip().lower()
    return val not in ("", "0", "false", "no")


def _drop_mismatched_venv(py):
    """解释器版本对不上时删掉 `.venv`,好让调用方重建。

    返回 True = 已删除、需要重建;False = 版本一致、读不到版本(不碰),
    或设了 MJ_SETUP_NO_AUTO_INSTALL(只警告)。不交互。
    """
    if not os.path.exists(py):
        return False
    current = (sys.version_info.major, sys.version_info.minor)
    existing = venv_python_version()
    if existing is None or existing == current:
        return False
    cur_s = ".".join(map(str, current))
    old_s = ".".join(map(str, existing))
    root = os.path.join(_ROOT, ".venv")
    if _no_auto_install():
        _warn(f".venv 由 Python {old_s} 创建,与当前解释器 {cur_s} 不一致;"
              f"已设 {NO_AUTO_INSTALL_ENV},不自动删除")
        print(f"         手动删除后重跑:{root}")
        return False
    print(f"  .venv 由 Python {old_s} 创建,与当前解释器 {cur_s} 不一致,"
          f"删除重建(其中的包需重新下载)…", flush=True)
    try:
        shutil.rmtree(root)
    except OSError as exc:  # noqa: BLE001
        _fail(f"删除 .venv 失败:{exc}")
        return False
    return True


def interpreter_verdict(version_info=None):
    """当前解释器的三态判据,供引导层读退出码:

    0 = 满足 MIN_PY 且不超出 Rust 内核支持范围;
    2 = 满足 MIN_PY 但超出内核范围(锦标赛照跑,内核装不上);
    1 = 低于 MIN_PY。
    """
    vi = sys.version_info if version_info is None else version_info
    cur = (vi[0], vi[1])
    if cur < MIN_PY:
        return 1
    if cur > RUST_KERNEL_MAX_PY:
        return 2
    return 0


def node_major(version_text):
    """`node -v` 的输出 → 主版本号;拿不到或解析失败返回 None。

    实测形态有 "v22.22.0"、"22.1.0"、"v22.0.0-pre"。不抛异常:调用方用
    None 表示"这个值不可信",而不是"版本是 0"。
    """
    if not version_text:
        return None
    head = version_text.strip().lstrip("vV").split(".")[0].strip()
    if not head.isdigit():
        return None
    return int(head)


def node_verdict(major):
    """Node 三态判据。0 = 满足 MIN_NODE;1 = 主版本过低;2 = 缺失/探不到。

    形状与 interpreter_verdict 对齐,**但 `2` 的含义不同**(那边是"能跑,
    只是超出 Rust 内核上限")。两者返回值都是给各自调用点用的,不要互相
    套用判据。
    """
    if major is None:
        return 2
    if major < MIN_NODE[0]:
        return 1
    return 0


def node_install_cmd(platform=None):
    """按平台给出安装 Node >= 22 的命令(列表)。

    平台当参数注入,好让 Windows 上也能单测 darwin 分支。
    """
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        # --source winget 必须带:存在多个源时 winget 因歧义什么都不做却返回 0。
        return ["winget", "install", "--id", NODE_WINGET_ID, "-e",
                "--source", "winget"]
    if platform == "darwin":
        return ["brew", "install", NODE_BREW_FORMULA]
    # Linux 各发行版差异太大,只给一条最常见的,失败就让用户手动装。
    return ["apt", "install", "-y", "nodejs"]


def _node_version(exe):
    """跑 `node -v` 取版本串;任何失败都返回 None(不抛)。"""
    try:
        result = subprocess.run([exe, "-v"], capture_output=True, text=True,
                                timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def detect_node():
    """返回 (解释器路径, 版本串);两层探测:先 PATH,再固定前缀兜底。

    兜底那层专为 brew 的 keg-only 准备:`brew install node@22` 不会把 node
    链进 PATH,只有 /opt/homebrew/opt/node@22/bin 里才有。Windows 侧兜
    Program Files\\nodejs —— winget 装完的新 PATH 要新会话才生效,本会话仍能
    用显式路径找到它(与引导层 find_python311 是同一套路)。

    PATH 上的 node 若是 nvm/volta 的 .cmd 垫片,subprocess 可能起不来 →
    返回 None,此时兜底路径往往还能命中真正的 node.exe。
    """
    fallbacks = {
        "darwin": ["/opt/homebrew/opt/node@22/bin/node",
                   "/usr/local/opt/node@22/bin/node"],
        "win32": [os.path.join(os.environ.get("ProgramFiles",
                                              r"C:\Program Files"),
                               "nodejs", "node.exe")],
    }
    found = shutil.which("node")
    candidates = ([found] if found else []) + [
        p for p in fallbacks.get(sys.platform, []) if os.path.exists(p)]
    for exe in candidates:
        version = _node_version(exe)
        if version:
            return exe, version
    return None, None


def _npm_beside(node_exe):
    """node 同目录下的 npm 路径;找不到返回 None。

    Windows 的 npm 是 npm.cmd(批处理垫片),Linux/macOS 是无扩展名的脚本,
    所以两边候选名不同。同目录找不到再退回 PATH(发行版/包管理器可能把它
    装在别处)。
    """
    base = os.path.dirname(node_exe or "")
    names = ("npm.cmd", "npm") if os.name == "nt" else ("npm",)
    if base:
        for name in names:
            cand = os.path.join(base, name)
            if os.path.exists(cand):
                return cand
    return shutil.which("npm")


def bootstrap_venv(skip_tests, no_rust=False):
    """创建 .venv(若缺)并把安装流程委托给 venv 内的解释器重跑本脚本。

    `.venv` 已存在但其解释器版本与当前解释器不一致时删除重建——否则
    "换用 3.11"永远不会生效,后续都委托给旧解释器。
    web_client.sh / web_client.ps1 已优先选用 .venv 解释器;
    tournament.py 用 sys.executable,从 venv 里启动即落在 venv。
    """
    print("\n[0] 创建隔离环境 .venv")
    py = venv_python()
    if in_venv():
        _ok(f"已在 venv 中({sys.prefix})")
        return None  # 直接继续本进程的安装流程
    if os.path.exists(py) and not _drop_mismatched_venv(py):
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
    if no_rust:
        inner.append("--no-rust")
    print(f"  在 venv 内继续安装:{' '.join(inner)}\n", flush=True)
    return subprocess.call(inner, cwd=_ROOT)


def check_python():
    print("\n[1/7] Python 版本")
    if sys.version_info >= MIN_PY:
        _ok(f"{sys.version_info.major}.{sys.version_info.minor}"
            f" ({sys.executable})")
        return True
    _fail(f"需要 Python >={'.'.join(map(str, MIN_PY))},"
          f"当前 {sys.version_info.major}.{sys.version_info.minor}")
    return False


def check_core_imports():
    """锦标赛默认路径(bot + legacy)的 import 冒烟——全部标准库。"""
    print("\n[2/7] 锦标赛核心路径(bot 策略,零三方依赖)")
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


def check_rust_kernel(install, allow_rust=True):
    print("\n[3/7] Rust shanten 内核(可选,缺省回退纯 Python)")
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
    if not allow_rust:
        _warn("--no-rust,跳过 Rust 内核(纯 Python 回退)")
        return False
    _warn("未安装 mj_kernels —— shanten/ukeire 走纯 Python,"
          "锦标赛可跑但决策稍慢")
    if install:
        # 缺 cargo 时 maturin 会在构建元数据阶段长时间挂住,所以先查后走。
        if shutil.which("cargo") is None:
            _warn("未找到 cargo(Rust 工具链未安装),跳过构建;"
                  "装上后重跑本脚本即自动构建")
            print("         winget install --id Rustlang.Rustup -e")
            print("         winget install --id Microsoft.VisualStudio"
                  ".2022.BuildTools -e")
            if sys.version_info[:2] > RUST_KERNEL_MAX_PY:
                _warn("注意:当前 Python "
                      f"{sys.version_info.major}.{sys.version_info.minor}"
                      " 超出 Rust 内核支持范围(最高 "
                      f"{'.'.join(map(str, RUST_KERNEL_MAX_PY))});"
                      "若构建报版本不匹配,请用 3.11/3.12 重建 .venv")
                print("         py install 3.11"
                      "            # Windows(Python installation manager)")
                print("         brew install python@3.11   # macOS")
            return False
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
        _warn("Rust 内核构建失败,继续用纯 Python(见上方日志)")
    return False


def check_node(install):
    """Node 前端工具链层:client/(Tauri+Vite)与 web/replay_debugger/ 的构建。

    与其它层的关键差别:**永不 _fail,返回值也不影响退出码** —— 锦标赛
    核心路径(bot + legacy 评价器)不依赖 Node,缺它只是前端起不来。

    成败一律以「装完重探」为准,不看安装器退出码:本次会话在 setup.ps1
    已经因为把安装器 stdout 当退出码踩过一次(两次成功的安装全被判失败)。
    """
    print("\n[4/7] Node 前端工具链(client/ 与 web/replay_debugger/)")
    path, version = detect_node()
    verdict = node_verdict(node_major(version))

    if verdict == 0:
        _ok(f"node {version} ({path})")
        on_path = shutil.which("node")
        if not (on_path and os.path.abspath(on_path).lower()
                == os.path.abspath(path).lower()):
            # 靠固定前缀探到、PATH 上没有:说明终端里未必敲得到 node。
            # 两边成因不同,给的补救命令必须分开(先前无条件打 brew 的提示,
            # 在 Windows 上会让人去跑一条不存在的命令)。
            if sys.platform == "darwin":
                # brew 的 node@22 是 keg-only,装完不 link。
                _warn("以上是脚本用绝对路径探到的,你的终端里未必能直接调用 node")
                print(f"         brew link --force --overwrite "
                      f"{NODE_BREW_FORMULA}   # 或把该目录加进 PATH", flush=True)
            else:
                _warn(f"PATH 上还没有 node,以上取自 {path};"
                      "新会话的 PATH 生效后才会命中")
        npm = _npm_beside(path)
        if npm:
            _ok(f"npm ({npm})")
        else:
            # 可能被 nvm/volta 拆到了别处,也可能是装坏了。改动别人的 Node
            # 管理工具容易打架,这里只提示。
            _warn("node 同目录下没有 npm —— 装得不完整,或被 nvm/volta 接管;"
                  "本脚本不处理,请自行确认 npm 可用")
        return True

    if verdict == 1:
        _warn(f"node {version} ({path}) 低于 {MIN_NODE[0]} —— client/ 与 "
              "web/replay_debugger/ 的构建工具链要求")
    else:
        _warn(f"未找到 node(需要 >= {MIN_NODE[0]})")

    cmd = node_install_cmd()
    manual = "         " + " ".join(cmd)
    if not install:
        _warn("未加 --install,只报告;手动安装:")
        print(manual, flush=True)
        return False
    if _no_auto_install():
        _warn(f"已设 {NO_AUTO_INSTALL_ENV},不自动安装 Node;手动安装:")
        print(manual, flush=True)
        return False
    if shutil.which(cmd[0]) is None:
        _warn(f"未找到 {cmd[0]},无法自动安装 Node;手动安装:")
        print(manual, flush=True)
        return False

    if verdict == 1:
        print(f"  注意:会替换 PATH 上现有的 node({path});若它由 nvm/fnm/"
              "volta 管理,装完谁生效取决于它们的 PATH 优先级", flush=True)
    print(f"  安装:{' '.join(cmd)}", flush=True)
    if sys.platform == "win32":
        _warn("该安装包落在 Program Files,可能弹 UAC 需要点确认")
    subprocess.call(cmd, cwd=_ROOT)
    new_path, new_version = detect_node()
    if node_verdict(node_major(new_version)) == 0:
        _ok(f"重探通过:node {new_version} ({new_path})")
        return True
    # 别断言"装失败":winget 写的是系统 PATH,本进程的 PATH 是启动时的快照,
    # 新装的 node 常常要新会话才可见(detect_node 的固定路径兜底会救一部分)。
    _warn("安装命令已执行,但本会话仍探不到可用的 node —— "
          "PATH 多半只在新终端生效")
    print("         请重开终端后重跑本脚本;仍不行就手动执行上面那条命令。",
          flush=True)
    return False


def check_optional(install):
    print("\n[5/7] 可选组件")
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
    print("\n[6/7] 平台配置 local/platform.json")
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
    print("\n[7/7] 引擎冒烟测试")
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
    ap.add_argument("--no-rust", action="store_true",
                    help="跳过 Rust shanten 内核构建(纯 Python 回退)")
    ap.add_argument("--interpreter-check", action="store_true",
                    help="只报告当前解释器能否支撑 Rust 内核,以退出码返回:"
                         "0 可用 / 2 超出内核范围 / 1 版本过低")
    args = ap.parse_args(argv)

    if args.interpreter_check:
        verdict = interpreter_verdict()
        reason = {
            0: "满足全部要求",
            2: "可跑锦标赛,但超出 Rust 内核支持范围(最高 "
               + ".".join(map(str, RUST_KERNEL_MAX_PY)) + ")",
            1: "低于最低要求 " + ".".join(map(str, MIN_PY)),
        }
        print(f"Python {sys.version_info.major}.{sys.version_info.minor}."
              f"{sys.version_info.micro}:{reason[verdict]}")
        return verdict

    print(f"仓库根:{_ROOT}")
    if args.venv and not in_venv():
        rc = bootstrap_venv(args.skip_tests, args.no_rust)
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
    check_rust_kernel(args.install, allow_rust=not args.no_rust)
    # 故意不并进 core_ok:Node 只服务 client/ 与 replay_debugger/ 的构建,
    # 锦标赛核心路径不依赖它,缺了不该让整个 setup 判失败。
    check_node(args.install)
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
