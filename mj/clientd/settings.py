"""本地 clientd 设置与 ONNX 模型仓库。

敏感配置只写入 ``local/`` 下的 JSON 文件。这个模块不把 token 写入
日志、异常或模型清单；HTTP API 只在本机 clientd 控制面上返回配置，供
桌面设置页恢复表单。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..features import N_ACTIONS, N_PLANES, N_SCALARS
from .errors import ConflictError, NotFoundError, ValidationError

__all__ = [
    "DEFAULT_MODEL_DIR",
    "DEFAULT_SETTINGS_PATH",
    "ModelStore",
    "PlatformSettings",
    "probe_platform_connection",
    "validate_onnx_file",
]


DEFAULT_SETTINGS_PATH = "local/platform.json"
DEFAULT_MODEL_DIR = "local/models"
MAX_MODEL_BYTES = 512 * 1024 * 1024
_MODES = {"tournament", "match", "test_room"}
_MODEL_NAME = re.compile(r"^[^/\\\x00]+\.onnx$", re.IGNORECASE)


def _harden_fd(fd):
    """把(临时)文件的权限收到 0600 —— 仅 POSIX 可用。

    Windows 的 ``os`` 模块**没有** ``fchmod`` 属性,直接调用抛的是
    ``AttributeError`` —— 不是调用方 ``os.chmod`` 那句所防的 ``OSError``,
    两层兜底都接不住(实测:保存平台设置与导入 ONNX 模型在 Windows 上必抛,
    经 service 边界一律变成 500 INTERNAL)。
    Windows 的权限模型是 ACL、``os.chmod`` 也只认只读位,"收紧到 0600"在那里
    没有对应物,跳过即可。
    """
    fchmod = getattr(os, "fchmod", None)
    if fchmod is None:
        return
    try:
        fchmod(fd, 0o600)
    except OSError:
        # 与调用方 os.chmod 那句同口径:权限收紧是尽力而为,失败不该让设置/
        # 模型写入整体失败(例如不支持权限位的文件系统)。
        pass


def _default_settings():
    return {
        "server": "",
        "tokens": {"tournament": "", "match": "", "test_room": []},
        "selected_model": None,
    }


def _atomic_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        _harden_fd(fd)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError) as exc:
        raise ValidationError(f"无法读取本地配置 {path.name}: {exc}") from exc


def _validate_server(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError("服务器地址必须是字符串")
    server = value.strip().rstrip("/")
    if not server:
        return ""
    parsed = urllib.parse.urlsplit(server)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValidationError("服务器地址必须是 http(s) URL")
    if parsed.username or parsed.password:
        raise ValidationError("服务器地址不能包含用户名或密码")
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValidationError("服务器地址只能填写平台根地址")
    return server


def _validate_token(value, field):
    if not isinstance(value, str):
        raise ValidationError(f"{field} 必须是字符串")
    token = value.strip()
    if len(token) > 4096:
        raise ValidationError(f"{field} 过长")
    if "\n" in token or "\r" in token:
        raise ValidationError(f"{field} 不能包含换行")
    return token


def _normalize_tokens(value):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValidationError("tokens 必须是对象")
    # 兼容已有平台配置中可能出现的 test/test-room 命名，但持久化时只
    # 保留设置页使用的稳定字段名。
    test_values = value.get("test_room", value.get("test", []))
    if test_values is None:
        test_values = []
    if isinstance(test_values, str):
        test_values = [test_values] if test_values.strip() else []
    if not isinstance(test_values, (list, tuple)):
        raise ValidationError("test_room 令牌必须是数组")
    if len(test_values) > 64:
        raise ValidationError("test_room 令牌最多 64 枚")
    normalized = {
        "tournament": _validate_token(
            value.get("tournament", ""), "tournament token"),
        "match": _validate_token(value.get("match", ""), "match token"),
        "test_room": [
            _validate_token(token, f"test_room token #{index + 1}")
            for index, token in enumerate(test_values)
        ],
    }
    return normalized


class PlatformSettings:
    """读写本机平台连接设置与当前默认模型名。"""

    def __init__(self, path=DEFAULT_SETTINGS_PATH):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _normalize(self, raw):
        if not isinstance(raw, dict):
            raise ValidationError("平台配置必须是对象")
        selected = raw.get("selected_model")
        if selected:
            selected = _safe_model_name(str(selected))
        return {
            "server": _validate_server(raw.get("server", "")),
            "tokens": _normalize_tokens(raw.get("tokens", {})),
            "selected_model": selected,
        }

    def get(self):
        with self._lock:
            return self._normalize(_read_json(self.path, _default_settings()))

    def update(self, payload):
        if not isinstance(payload, dict):
            raise ValidationError("平台配置必须是对象")
        with self._lock:
            current = self.get()
            merged = {
                "server": payload.get("server", current["server"]),
                "tokens": payload.get("tokens", current["tokens"]),
                "selected_model": payload.get(
                    "selected_model", current["selected_model"]),
            }
            normalized = self._normalize(merged)
            _atomic_json(self.path, normalized)
            return normalized

    def select_model(self, name):
        with self._lock:
            current = self.get()
            current["selected_model"] = name
            normalized = self._normalize(current)
            _atomic_json(self.path, normalized)
            return normalized


def _contract_value(props, key):
    raw = props.get(key)
    if raw is None:
        raise ValidationError(f"ONNX 缺少 {key} 元数据")
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"ONNX {key} 元数据不是整数") from exc


def validate_onnx_file(path):
    """解析并校验可由 ``OnnxPolicyPlayer`` 使用的 ONNX 契约。

    这里只做导入阶段需要的图和元数据校验，不创建 onnxruntime session，
    避免导入一个坏模型时影响当前已选择模型，也让错误保持可读。
    """
    try:
        import onnx
    except ImportError as exc:
        raise ValidationError(
            "当前运行环境缺少 ONNX 校验依赖，请安装 onnx") from exc
    try:
        graph = onnx.load(str(path), load_external_data=False)
    except Exception as exc:  # noqa: BLE001 - onnx exposes several error types
        raise ValidationError(f"ONNX 文件无法解析: {exc}") from exc
    props = {item.key: item.value for item in graph.metadata_props}
    planes = _contract_value(props, "contract_planes")
    scalars = _contract_value(props, "contract_scalars")
    actions = _contract_value(props, "contract_actions")
    if scalars != N_SCALARS:
        raise ValidationError(
            f"ONNX 观测标量契约不匹配: {scalars} != {N_SCALARS}")
    if planes < N_PLANES:
        raise ValidationError(
            f"ONNX 观测平面数不足: {planes} < {N_PLANES}")
    if actions != N_ACTIONS:
        raise ValidationError(
            f"ONNX 动作空间契约不匹配: {actions} != {N_ACTIONS}")
    inputs = {item.name for item in graph.graph.input}
    missing = {"planes", "scalars"} - inputs
    if missing:
        raise ValidationError(
            f"ONNX 输入契约缺少: {', '.join(sorted(missing))}")
    outputs = {item.name for item in graph.graph.output}
    if "logits" not in outputs:
        raise ValidationError("ONNX 输出契约缺少 logits")
    return {
        "planes": planes,
        "scalars": scalars,
        "actions": actions,
    }


def _safe_model_name(name):
    if not isinstance(name, str):
        raise ValidationError("模型文件名必须是字符串")
    name = name.strip()
    if (not name or name != os.path.basename(name) or "/" in name
            or "\\" in name or not _MODEL_NAME.fullmatch(name)
            or name in (".", "..")):
        raise ValidationError("模型文件必须是单层路径且以 .onnx 结尾")
    return name


class ModelStore:
    """本地 ONNX 仓库；先校验临时文件，再原子移动到模型目录。"""

    def __init__(self, root=DEFAULT_MODEL_DIR, settings=None):
        self.root = Path(root)
        self.registry_path = self.root / "models.json"
        self.settings = settings or PlatformSettings()
        self._lock = threading.RLock()

    def _read_registry(self):
        raw = _read_json(self.registry_path, {})
        if not isinstance(raw, dict):
            raise ValidationError("模型清单必须是对象")
        return raw

    def _write_registry(self, registry):
        _atomic_json(self.registry_path, registry)

    def _record(self, name, metadata):
        if not isinstance(metadata, dict):
            raise ValidationError(f"模型清单条目无效: {name}")
        path = self.root / name
        stat = path.stat()
        return {
            "name": name,
            "size": stat.st_size,
            "sha256": metadata.get("sha256"),
            "imported_at": metadata.get("imported_at"),
            "contract": metadata.get("contract", {}),
            "selected": self.settings.get().get("selected_model") == name,
        }

    def list(self):
        with self._lock:
            registry = self._read_registry()
            records = []
            for name, metadata in sorted(registry.items()):
                try:
                    name = _safe_model_name(name)
                    record = self._record(name, metadata or {})
                except (OSError, ValidationError):
                    continue
                records.append(record)
            return records

    def import_bytes(self, name, content):
        name = _safe_model_name(name)
        if not isinstance(content, (bytes, bytearray)):
            raise ValidationError("模型内容必须是二进制数据")
        content = bytes(content)
        if not content:
            raise ValidationError("不能导入空模型")
        if len(content) > MAX_MODEL_BYTES:
            raise ValidationError("模型文件超过 512 MiB 限制")
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            target = self.root / name
            registry = self._read_registry()
            if target.exists() or name in registry:
                raise ConflictError(f"模型已存在: {name}")
            fd, temporary = tempfile.mkstemp(
                prefix=f".{name}.", suffix=".tmp", dir=str(self.root))
            try:
                _harden_fd(fd)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                contract = validate_onnx_file(temporary)
                metadata = {
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "imported_at": time.time(),
                    "contract": contract,
                }
                os.replace(temporary, target)
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    pass
                registry[name] = metadata
                try:
                    self._write_registry(registry)
                except Exception:
                    try:
                        target.unlink()
                    except OSError:
                        pass
                    raise
            except Exception:
                try:
                    os.close(fd)
                except OSError:
                    pass
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise
            return self._record(name, metadata)

    def select(self, name):
        name = _safe_model_name(name)
        with self._lock:
            registry = self._read_registry()
            if name not in registry or not (self.root / name).is_file():
                raise NotFoundError(f"model not found: {name}")
            # 重新验证文件，避免模型文件被外部替换后仍能进入新会话。
            contract = validate_onnx_file(self.root / name)
            registry[name]["contract"] = contract
            self._write_registry(registry)
            settings = self.settings.select_model(name)
            return self._record(name, registry[name]) | {
                "selected": True,
                "settings": settings,
            }

    def delete(self, name):
        name = _safe_model_name(name)
        with self._lock:
            current = self.settings.get()
            if current.get("selected_model") == name:
                raise ConflictError("不能删除当前已选模型，请先切换默认模型")
            registry = self._read_registry()
            path = self.root / name
            if name not in registry or not path.is_file():
                raise NotFoundError(f"model not found: {name}")
            path.unlink()
            registry.pop(name, None)
            self._write_registry(registry)
            return {"name": name, "deleted": True}

    def resolve_selected(self):
        """返回给新会话注入的绝对路径；未选择模型时返回 None。"""
        selected = self.settings.get().get("selected_model")
        if not selected:
            return None
        name = _safe_model_name(selected)
        path = self.root / name
        if not path.is_file():
            return None
        return {"name": name, "path": str(path.resolve())}


def _auth_message(status, mode):
    if status == 401:
        return "令牌无效或已过期"
    if status == 403:
        labels = {
            "tournament": "锦标赛",
            "match": "匹配房",
            "test_room": "测试房",
        }
        return (f"该令牌不能用于{labels.get(mode, '当前')}模式："
                "令牌类型不匹配或权限不足")
    return f"平台返回 HTTP {status}"


def probe_platform_connection(server, token, mode, *, timeout=6.0):
    """验证一个令牌能否访问平台身份端点，不创建房间、不启动对局。"""
    if mode not in _MODES:
        raise ValidationError("连接测试模式必须是 tournament/match/test_room")
    server = _validate_server(server)
    token = _validate_token(token, f"{mode} token")
    if not server:
        raise ValidationError("请先填写服务器地址")
    if not token:
        raise ValidationError("请先填写对应模式的令牌")
    request = urllib.request.Request(
        f"{server}/api/me", method="GET",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    context = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            status = int(response.status)
            response.read(4096)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        try:
            exc.read(4096)
        except Exception:
            pass
        return {"ok": False, "status": status, "mode": mode,
                "code": "AUTH_REJECTED" if status in (401, 403) else "HTTP_ERROR",
                "message": _auth_message(status, mode)}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "status": 0, "mode": mode,
                "code": "NETWORK_ERROR", "message": f"无法连接服务器: {exc}"}
    if status in (401, 403):
        return {"ok": False, "status": status, "mode": mode,
                "code": "AUTH_REJECTED", "message": _auth_message(status, mode)}
    return {"ok": True, "status": status, "mode": mode,
            "code": "OK", "message": "连接成功，令牌可访问平台"}
