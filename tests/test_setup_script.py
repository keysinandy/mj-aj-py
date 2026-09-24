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
