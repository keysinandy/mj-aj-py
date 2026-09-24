"""scripts/setup.py(环境初始化)的单测:只测纯逻辑,不装包不触网。"""

import json
import os
import sys

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import setup as s  # noqa: E402


def test_ensure_platform_config_creates_template(tmp_path, capsys):
    assert s.ensure_platform_config(root=str(tmp_path)) is False
    path = tmp_path / "local" / "platform.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    assert cfg["server"] == s.DEFAULT_SERVER
    # 模板令牌留空:load_tournament_config 会明确报"必须是非空字符串",
    # 而不是塞一个能过校验的占位符让 dry-run 打到线上 401。
    assert cfg["tournament_token"] == ""
    assert "模板" in capsys.readouterr().out


def test_ensure_platform_config_ok(tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    (local / "platform.json").write_text(
        json.dumps({"server": "https://x",
                    "tournament_token": "abc"}), encoding="utf-8")
    assert s.ensure_platform_config(root=str(tmp_path)) is True


def test_ensure_platform_config_empty_token(tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    (local / "platform.json").write_text(
        json.dumps({"server": "https://x", "tournament_token": ""}),
        encoding="utf-8")
    assert s.ensure_platform_config(root=str(tmp_path)) is False


def test_ensure_platform_config_bad_json(tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    (local / "platform.json").write_text("not json", encoding="utf-8")
    assert s.ensure_platform_config(root=str(tmp_path)) is False


def test_optional_specs_pure_stdlib():
    # 核心保障的依据:bot+legacy 锦标赛路径不需要任何 pip 包;
    # OPTIONAL 只覆盖增强组件。
    assert "pytest" not in s.OPTIONAL.get("policy", {}).get("pkgs", [])
    assert s.MIN_PY == (3, 10)


def test_optional_web_covers_clientd_runtime():
    # clientd 的运行依赖是「纯标准库 + numpy + websockets」,
    # 缺一个 web_client.sh 就起不来;这里锁住以免再次掉队。
    assert set(s.OPTIONAL.get("web", {}).get("pkgs", [])) == {
        "numpy", "websockets"}


def test_venv_python_paths(monkeypatch, tmp_path):
    # 两个分支都要覆盖,但不能假定跑测试的机器是哪一种(原先第一句硬断言
    # bin/python,在 Windows 上必挂)。
    root = str(tmp_path)
    real_nt = os.name == "nt"
    here = ("Scripts", "python.exe") if real_nt else ("bin", "python")
    other = ("bin", "python") if real_nt else ("Scripts", "python.exe")
    assert s.venv_python(root) == os.path.join(root, ".venv", *here)
    monkeypatch.setattr(s.os, "name", "posix" if real_nt else "nt")
    assert s.venv_python(root) == os.path.join(root, ".venv", *other)


def test_in_venv_detection():
    # 两种都应可用而不崩:当前进程(可能真 venv 也可能系统)
    assert isinstance(s.in_venv(), bool)
    base = getattr(sys, "base_prefix", sys.prefix)
    assert (sys.prefix != base) == s.in_venv()


def test_bootstrap_venv_delegates_to_inner_install(monkeypatch, tmp_path):
    # 不打真 venv/不下载:fake 掉 subprocess.call 与 venv_python,
    # 验证「创建 venv → 升级 pip → 以 --install 重入本脚本」的委托链。
    assert not s.in_venv()
    py = tmp_path / "venv-python"
    monkeypatch.setattr(s, "venv_python", lambda root=None: str(py))
    calls = []

    def fake_call(cmd, cwd=None):
        calls.append(cmd)
        if "venv" in cmd[:3]:  # python -m venv .venv
            py.write_text("", encoding="utf-8")  # 模拟 venv 创建完成
        return 0

    monkeypatch.setattr(s.subprocess, "call", fake_call)
    rc = s.bootstrap_venv(skip_tests=True)
    assert rc == 0
    assert calls[0][:3] == [sys.executable, "-m", "venv"]
    assert any("--upgrade" in " ".join(c) for c in calls if "pip" in c)
    inner = calls[-1]
    assert inner[:2] == [str(py), s.os.path.abspath(s.__file__)]
    assert "--install" in inner and "--skip-tests" in inner


def _current_xy():
    return (sys.version_info[0], sys.version_info[1])


def test_interpreter_verdict_three_states():
    # 三态判据是 setup.ps1 / setup.sh 挑解释器的唯一依据,
    # 改这里的语义等于改引导行为。
    assert s.interpreter_verdict((3, 11, 0)) == 0
    assert s.interpreter_verdict((3, 13, 9)) == 0  # 上限含 3.13
    assert s.interpreter_verdict((3, 14, 5)) == 2  # 锦标赛可跑,内核装不上
    assert s.interpreter_verdict((3, 9, 18)) == 1


def test_rust_kernel_max_py_locked():
    # 与 rust/Cargo.toml 的 pyo3 0.22 声明上限绑定(本机 3.14 实测构建失败);
    # 引导层不抄这个数字,只读 --interpreter-check 的退出码。
    assert s.RUST_KERNEL_MAX_PY == (3, 13)


def test_venv_python_version_reads_pyvenv_cfg(tmp_path):
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text(
        "home = /usr/bin\ninclude-system-site-packages = false\n"
        "version = 3.11.9\n", encoding="utf-8")
    assert s.venv_python_version(str(tmp_path)) == (3, 11)


def test_venv_python_version_unreadable_is_none(tmp_path):
    # 读不到必须是 None("不确定,别碰 .venv"),不能当成不匹配而去删目录。
    assert s.venv_python_version(str(tmp_path)) is None
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    assert s.venv_python_version(str(tmp_path)) is None


def test_no_auto_install_env_parsing(monkeypatch):
    # "0"/"false"/"no" 必须是关(PowerShell 侧的 [bool] 会把 "0" 判成真,
    # 三边都要同一个口径)。
    for off in ("", "0", "false", "no", "False", "NO"):
        monkeypatch.setenv(s.NO_AUTO_INSTALL_ENV, off)
        assert s._no_auto_install() is False
    for on in ("1", "true", "yes"):
        monkeypatch.setenv(s.NO_AUTO_INSTALL_ENV, on)
        assert s._no_auto_install() is True


def test_drop_mismatched_venv_same_version_keeps(monkeypatch, tmp_path):
    py = tmp_path / "venv-python"
    py.write_text("", encoding="utf-8")
    monkeypatch.setattr(s, "venv_python_version",
                        lambda root=None: _current_xy())
    removed = []
    monkeypatch.setattr(s.shutil, "rmtree", removed.append)
    assert s._drop_mismatched_venv(str(py)) is False
    assert removed == []


def test_drop_mismatched_venv_no_auto_install_warns_only(monkeypatch, tmp_path,
                                                         capsys):
    py = tmp_path / "venv-python"
    py.write_text("", encoding="utf-8")
    cur = _current_xy()
    monkeypatch.setattr(s, "venv_python_version",
                        lambda root=None: (cur[0], cur[1] + 1))
    monkeypatch.setenv(s.NO_AUTO_INSTALL_ENV, "1")
    removed = []
    monkeypatch.setattr(s.shutil, "rmtree", removed.append)
    assert s._drop_mismatched_venv(str(py)) is False
    assert removed == []
    assert s.NO_AUTO_INSTALL_ENV in capsys.readouterr().out


def test_bootstrap_venv_recreates_on_version_mismatch(monkeypatch, tmp_path):
    # 版本不一致时必须删掉重建,否则"换成 3.11"永远不生效 —— 后面全委托
    # 给 .venv 里那个旧解释器。
    assert not s.in_venv()
    py = tmp_path / "venv-python"
    py.write_text("", encoding="utf-8")  # 模拟 .venv 已存在
    cur = _current_xy()
    monkeypatch.setattr(s, "venv_python", lambda root=None: str(py))
    monkeypatch.setattr(s, "venv_python_version",
                        lambda root=None: (cur[0], cur[1] + 1))
    monkeypatch.setattr(s, "_ROOT", str(tmp_path))  # 万一没 fake 也只删 tmp
    removed = []
    monkeypatch.setattr(s.shutil, "rmtree", removed.append)
    calls = []
    monkeypatch.setattr(s.subprocess, "call",
                        lambda cmd, cwd=None: calls.append(cmd) or 0)

    assert s.bootstrap_venv(skip_tests=True) == 0
    assert removed == [os.path.join(str(tmp_path), ".venv")]
    # 删完必须走创建流程,而不是继续复用旧环境
    assert calls[0][:3] == [sys.executable, "-m", "venv"]
    assert calls[-1][:2] == [str(py), s.os.path.abspath(s.__file__)]


def test_node_major_parses():
    assert s.node_major("v22.22.0") == 22
    assert s.node_major("22.1.0") == 22
    assert s.node_major("V9.11.2") == 9
    assert s.node_major("v22.0.0-pre") == 22
    # 拿不到一律 None(表示"不可信"),不是 0 —— 否则会被当成"版本过低"
    assert s.node_major("") is None
    assert s.node_major("node") is None
    assert s.node_major(None) is None


def test_node_verdict_three_states():
    assert s.node_verdict(22) == 0
    assert s.node_verdict(24) == 0  # 下界判据,更高版本不动
    assert s.node_verdict(21) == 1
    assert s.node_verdict(None) == 2  # 缺失按"要装"算


def test_min_node_locked():
    # 只在这里维护下界;client/ 与 web/replay_debugger/ 的构建工具链要求
    assert s.MIN_NODE == (22,)


def test_node_install_cmd_per_platform():
    win = s.node_install_cmd("win32")
    assert s.NODE_WINGET_ID in win
    # --source 必须带:多源歧义会让 winget 静默 no-op 却返回 0
    assert "--source" in win
    # 带版本号的包:LTS 通道现在是 24,不能拿 OpenJS.NodeJS.LTS 代表"要 22"
    assert "LTS" not in " ".join(win)
    assert s.node_install_cmd("darwin") == ["brew", "install", s.NODE_BREW_FORMULA]
    assert s.node_install_cmd("linux")[0] == "apt"


def test_bootstrap_scripts_do_not_touch_node():
    # 与「Rust 内核上限只在 setup.py 一处维护」同一条约定:引导层只负责把
    # Python 搞到 >= 3.10,Node 判据不要在 setup.sh / setup.ps1 里再抄一份。
    for name in ("setup.sh", "setup.ps1"):
        with open(os.path.join(_SCRIPTS, name), encoding="utf-8") as f:
            text = f.read().lower()
        for token in ("nodejs", "openjs", "node@22", "node -v"):
            assert token not in text, f"{name} 不应出现 {token}"


def _fake_node_dir(tmp_path):
    """造一个「node + npm 同目录」的落地形态,两个平台的 npm 候选名都建上。"""
    node = tmp_path / "node.exe"
    node.write_text("", encoding="utf-8")
    for name in ("npm", "npm.cmd"):
        (tmp_path / name).write_text("", encoding="utf-8")
    return node


def test_npm_beside_prefers_sibling(tmp_path, monkeypatch):
    node = _fake_node_dir(tmp_path)
    monkeypatch.setattr(s.shutil, "which", lambda name: "/elsewhere/npm")
    expected = "npm.cmd" if os.name == "nt" else "npm"
    assert s._npm_beside(str(node)) == os.path.join(str(tmp_path), expected)
    # 同目录没有时退回 PATH(发行版/包管理器常把 npm 装到别处)。
    # 注意判据是「node 所在目录」而不是 node 文件本身在不在。
    bare = tmp_path / "bare"
    bare.mkdir()
    assert s._npm_beside(str(bare / "node")) == "/elsewhere/npm"


def test_check_node_satisfied_is_ok(monkeypatch, tmp_path, capsys):
    node = _fake_node_dir(tmp_path)
    monkeypatch.setattr(s, "detect_node", lambda: (str(node), "v22.22.0"))
    monkeypatch.setattr(s.shutil, "which",
                        lambda name: str(node) if name == "node" else None)
    assert s.check_node(install=False) is True
    out = capsys.readouterr().out
    assert "v22.22.0" in out and "[FAIL]" not in out


def test_check_node_keg_only_path_warns_shell_may_lack_it(monkeypatch, tmp_path,
                                                          capsys):
    # brew 的 node@22 是 keg-only:脚本用绝对路径探得到,用户终端里未必敲得到
    node = _fake_node_dir(tmp_path)
    monkeypatch.setattr(s, "detect_node", lambda: (str(node), "v22.23.3"))
    monkeypatch.setattr(s.shutil, "which",
                        lambda name: "/opt/homebrew/bin/node" if name == "node"
                        else None)
    monkeypatch.setattr(s.sys, "platform", "darwin")
    assert s.check_node(install=False) is True
    out = capsys.readouterr().out
    assert "brew link --force --overwrite" in out
    assert s.NODE_BREW_FORMULA in out


def test_check_node_offpath_on_windows_does_not_suggest_brew(monkeypatch,
                                                             tmp_path, capsys):
    # 真机实测踩到过:Windows 上靠 Program Files 兜底探到 node(PATH 里还没有,
    # 正是 winget 刚装完的形态)时,提示语却让人去跑 brew link。
    node = _fake_node_dir(tmp_path)
    monkeypatch.setattr(s, "detect_node", lambda: (str(node), "v22.23.2"))
    monkeypatch.setattr(s.shutil, "which", lambda name: None)
    monkeypatch.setattr(s.sys, "platform", "win32")
    assert s.check_node(install=False) is True
    out = capsys.readouterr().out
    assert "brew" not in out
    assert "PATH" in out


def test_check_node_low_version_only_warns_without_install(monkeypatch, tmp_path,
                                                           capsys):
    node = _fake_node_dir(tmp_path)
    monkeypatch.setattr(s, "detect_node", lambda: (str(node), "v20.11.0"))
    calls = []
    monkeypatch.setattr(s.subprocess, "call",
                        lambda cmd, cwd=None: calls.append(cmd) or 0)
    assert s.check_node(install=False) is False
    assert calls == []  # 没 --install 就不许动手
    out = capsys.readouterr().out
    assert "v20.11.0" in out and "[FAIL]" not in out


def test_check_node_missing_warns_only(monkeypatch, capsys):
    monkeypatch.setattr(s, "detect_node", lambda: (None, None))
    assert s.check_node(install=False) is False
    out = capsys.readouterr().out
    assert " ".join(s.node_install_cmd()) in out  # 打印了手动命令
    assert "[FAIL]" not in out  # 永不 _fail:锦标赛核心路径不依赖 Node


def test_check_node_no_auto_install_does_not_install(monkeypatch, capsys):
    monkeypatch.setenv(s.NO_AUTO_INSTALL_ENV, "1")
    monkeypatch.setattr(s, "detect_node", lambda: (None, None))
    calls = []
    monkeypatch.setattr(s.subprocess, "call",
                        lambda cmd, cwd=None: calls.append(cmd) or 0)
    assert s.check_node(install=True) is False
    assert calls == []
    out = capsys.readouterr().out
    assert s.NO_AUTO_INSTALL_ENV in out
    assert " ".join(s.node_install_cmd()) in out


def test_check_node_judged_by_reprobe_not_installer_exit_code(monkeypatch,
                                                              tmp_path, capsys):
    # 安装器返回 1 但重探到了 → 必须判成功。setup.ps1 就是拿安装器的输出/
    # 退出码当判据,把 py install 与 winget 两次成功的安装都判成了失败。
    node = _fake_node_dir(tmp_path)
    replies = iter([(None, None), (str(node), "v22.23.2")])
    monkeypatch.setattr(s, "detect_node", lambda: next(replies))
    monkeypatch.setattr(s.shutil, "which",
                        lambda name: "winget" if name == "winget" else None)
    monkeypatch.setattr(s.subprocess, "call", lambda cmd, cwd=None: 1)
    assert s.check_node(install=True) is True
    out = capsys.readouterr().out
    assert "重探通过" in out and "v22.23.2" in out


def test_check_node_install_but_reprobe_fails_says_reopen_terminal(monkeypatch,
                                                                   capsys):
    # winget 写的是系统级 PATH,而本进程的 PATH 是启动时的快照 —— 装成功也
    # 可能探不到。这时不能说"装失败",要指引重开终端。
    monkeypatch.setattr(s, "detect_node", lambda: (None, None))
    monkeypatch.setattr(s.shutil, "which",
                        lambda name: "winget" if name == "winget" else None)
    monkeypatch.setattr(s.subprocess, "call", lambda cmd, cwd=None: 0)
    assert s.check_node(install=True) is False
    out = capsys.readouterr().out
    assert "重开终端" in out and "[FAIL]" not in out
