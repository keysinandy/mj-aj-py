"""平台连接设置与 ONNX 模型管理的服务层契约。"""

import pytest

from mj.clientd.api import api_router, session_router
from mj.clientd.errors import ValidationError
from mj.clientd.service import Request
from mj.clientd.settings import ModelStore, PlatformSettings
from mj.clientd.sessions import SessionManager


def dispatch(router, method, path, body=None):
    return router.dispatch(Request(method, path, body=body))


def test_platform_settings_are_persisted_and_normalized(tmp_path):
    settings_path = tmp_path / "platform.json"
    router = api_router(settings_path=settings_path, model_root=tmp_path / "models")

    status, initial = dispatch(router, "GET", "/api/settings")
    assert status == 200
    assert initial["server"] == ""
    assert initial["tokens"]["test_room"] == []

    status, saved = dispatch(router, "PUT", "/api/settings", {
        "server": "https://mahjong.example.com/",
        "tokens": {
            "tournament": "tournament-token",
            "match": "match-token",
            "test_room": ["room-a", "room-b"],
        },
    })
    assert status == 200
    assert saved["server"] == "https://mahjong.example.com"
    assert settings_path.stat().st_mode & 0o077 == 0

    second_router = api_router(settings_path=settings_path,
                               model_root=tmp_path / "models")
    _, restored = dispatch(second_router, "GET", "/api/settings")
    assert restored["tokens"]["match"] == "match-token"
    assert restored["tokens"]["test_room"] == ["room-a", "room-b"]


def test_invalid_server_is_rejected_without_overwriting_previous_settings(tmp_path):
    router = api_router(settings_path=tmp_path / "platform.json",
                        model_root=tmp_path / "models")
    dispatch(router, "PUT", "/api/settings", {
        "server": "https://good.example.com",
        "tokens": {"match": "keep-me"},
    })
    with pytest.raises(ValidationError):
        dispatch(router, "PUT", "/api/settings", {
            "server": "file:///etc/passwd",
            "tokens": {"match": "replace-me"},
        })
    _, restored = dispatch(router, "GET", "/api/settings")
    assert restored["server"] == "https://good.example.com"
    assert restored["tokens"]["match"] == "keep-me"


def test_bad_model_is_rejected_and_selection_is_unchanged(tmp_path, monkeypatch):
    import mj.clientd.settings as settings_module

    settings = PlatformSettings(tmp_path / "platform.json")
    store = ModelStore(tmp_path / "models", settings=settings)

    monkeypatch.setattr(
        settings_module, "validate_onnx_file",
        lambda path: {"planes": 75, "scalars": 8, "actions": 109},
    )
    first = store.import_bytes("first.onnx", b"valid-placeholder")
    assert first["selected"] is False
    store.select("first.onnx")

    def reject(path):
        raise ValidationError("ONNX 动作空间契约不匹配")

    monkeypatch.setattr(settings_module, "validate_onnx_file", reject)
    with pytest.raises(ValidationError, match="动作空间"):
        store.import_bytes("broken.onnx", b"broken")
    assert settings.get()["selected_model"] == "first.onnx"
    assert not (tmp_path / "models" / "broken.onnx").exists()


def test_selected_model_is_injected_only_into_new_session_config(tmp_path, monkeypatch):
    import mj.clientd.settings as settings_module

    settings = PlatformSettings(tmp_path / "platform.json")
    store = ModelStore(tmp_path / "models", settings=settings)
    monkeypatch.setattr(
        settings_module, "validate_onnx_file",
        lambda path: {"planes": 75, "scalars": 8, "actions": 109},
    )
    store.import_bytes("policy.onnx", b"valid-placeholder")
    store.select("policy.onnx")

    seen = []

    def factory(kind, config):
        seen.append((kind, config))
        return lambda stop, session: {"ok": True}

    manager = SessionManager(runner_factory=factory)
    router = session_router(manager, model_resolver=store.resolve_selected)
    _, payload = dispatch(router, "POST", "/api/sessions", {
        "kind": "arena",
        "config": {"seats": [
            {"strategy": "policy"}, {"strategy": "random"},
            {"strategy": "random"}, {"strategy": "random"},
        ]},
    })
    assert payload["config"]["model_name"] == "policy.onnx"
    assert payload["config"]["seats"][0]["ckpt"].endswith("policy.onnx")
    assert seen[0][1]["seats"][0]["ckpt"].endswith("policy.onnx")
