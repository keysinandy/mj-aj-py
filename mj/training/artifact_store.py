"""Artifact store:不可变发布(manifest-last)。

结果发布协议(worker 本地 stage → 复制到共享发布区 → 最后原子提交
``manifest.json``):

- 只认 ``manifest.json`` 存在的目录为"已发布";
- 残留 ``.tmp`` / 未提交 manifest 的 data 文件一律忽略(可安全清理);
- ``manifest.json.tmp`` → ``os.replace`` 原子替换,进程崩溃不会留下半写 manifest;
- 文件完整性以 ``artifact_sha256`` 追踪(校验失败即拒绝 published)。
"""

from __future__ import annotations

import hashlib
import json
import os

__all__ = ["sha256_file", "sha256_bytes", "publish_result",
           "find_results", "verify_result"]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def publish_result(result_root: str, campaign_id: str, job_id: str, *,
                   files: dict, manifest: dict) -> str:
    """把 ``files``(本地绝对路径 → 子目录内相对名)发布为不可变结果。

    返回发布后的 manifest.json 绝对路径。最终 manifest 最后原子写入。
    """
    dst = os.path.join(result_root, campaign_id, job_id)
    os.makedirs(dst, exist_ok=True)
    published = {}
    for src, rel in files.items():
        target = os.path.join(dst, rel)
        tmp = target + ".tmp"
        with open(src, "rb") as r, open(tmp, "wb") as w:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                w.write(chunk)
        os.replace(tmp, target)
        published[rel] = target
    manifest_path = os.path.join(dst, "manifest.json")
    tmp = manifest_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, sort_keys=True, indent=2)
    os.replace(tmp, manifest_path)
    return manifest_path


def find_results(result_root: str, campaign_id: str, *,
                 require_manifest=True):
    """枚举 <root>/<campaign_id>/*/manifest.json;仅已提交 manifest 的计入。"""
    base = os.path.join(result_root, campaign_id)
    if not os.path.isdir(base):
        return []
    out = []
    for name in sorted(os.listdir(base)):
        mp = os.path.join(base, name, "manifest.json")
        if require_manifest:
            if os.path.exists(mp):
                out.append((name, mp))
        else:
            out.append((name, mp))
    return out


def verify_result(manifest_path: str) -> tuple[bool, str | None]:
    """校验 manifest 引用的数据文件 sha256 与 artifact_sha256 一致。

    返回 (ok, missing_file)。manifest 无 artifact_relpath 视为校验通过
    (如纯状态 job)。"""
    manifest = load_manifest(manifest_path)
    rel = manifest.get("artifact_relpath")
    if not rel:
        return True, None
    data_path = os.path.join(os.path.dirname(manifest_path), rel)
    if not os.path.exists(data_path):
        return False, data_path
    if hashlib.sha256(
            open(data_path, "rb").read()).hexdigest() != \
            manifest.get("artifact_sha256"):
        return False, data_path
    return True, None


def load_manifest(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)