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
    root = str(tmp_path)
    assert s.venv_python(root) == os.path.join(
        root, ".venv", "bin", "python")
    monkeypatch.setattr(s.os, "name", "nt")
    assert s.venv_python(root) == os.path.join(
        root, ".venv", "Scripts", "python.exe")


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
