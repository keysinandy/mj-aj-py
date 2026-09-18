"""Task 5.1 验收:ONNX 导出器(BC/PPO/policy-v3 三源 + 契约元数据)。

BC/PPO 用真实 checkpoint(runs/bc0/best.pt, runs/ppo4/ckpt_350000.pt);
policy-v3 无现成 ckpt,合成一个最小 PolicyValueNet + manifest 验证路径;
缺 checkpoint / 坏 checkpoint 明确报错。
"""

import os

import pytest

from mj.tools.export_onnx import (
    export_checkpoint, detect_format, PLANES_KEY, SCALARS_KEY, ACTIONS_KEY,
)
from mj.features import N_PLANES, N_SCALARS, N_ACTIONS

BC_CKPT = "runs/bc0/best.pt"
PPO_CKPT = "runs/ppo4/ckpt_350000.pt"


def _load(path):
    import torch
    return torch.load(path, map_location="cpu", weights_only=True)


def test_detect_format():
    assert detect_format(_load(BC_CKPT)) == "bc"
    assert detect_format(_load(PPO_CKPT)) == "ppo"


@pytest.mark.parametrize("ckpt,expected_planes", [
    (BC_CKPT, N_PLANES), (PPO_CKPT, 91),
])
def test_export_bc_ppo(tmp_path, ckpt, expected_planes):
    if not os.path.exists(ckpt):
        pytest.skip(f"checkpoint missing: {ckpt}")
    out = tmp_path / (os.path.basename(ckpt) + ".onnx")
    meta = export_checkpoint(ckpt, str(out))
    assert meta[PLANES_KEY] == expected_planes
    assert meta[SCALARS_KEY] == N_SCALARS
    assert meta[ACTIONS_KEY] == N_ACTIONS
    assert os.path.exists(out)
    import onnx
    m = onnx.load(str(out), load_external_data=False)
    props = {p.key: p.value for p in m.metadata_props}
    assert props[PLANES_KEY] == str(expected_planes)


def test_export_policy_v3_synthetic(tmp_path):
    """无现成 policy-v3 ckpt:合成最小模型 + manifest 验证导出路径。"""
    import torch
    from mj.models.policy_value import (
        PolicyValueNet, PolicyValueModelManifest,
        policy_value_manifest_from_json,
    )
    net = PolicyValueNet(blocks=1, width=8)
    manifest = net.manifest
    ck = {
        "manifest": manifest.as_json(),
        "state_dict": net.state_dict(),
    }
    import json as _json
    cpath = tmp_path / "pv3.pt"
    torch.save(ck, str(cpath))
    out = tmp_path / "pv3.onnx"
    meta = export_checkpoint(str(cpath), str(out))
    assert meta[PLANES_KEY] == int(net.feature_contract.n_planes)
    assert meta[SCALARS_KEY] == int(net.feature_contract.n_scalars)
    assert os.path.exists(out)


def test_missing_ckpt_errors():
    with pytest.raises((FileNotFoundError, RuntimeError)):
        export_checkpoint("runs/does_not_exist.pt", "x.onnx")


def test_bad_ckpt_errors(tmp_path):
    import torch
    bad = tmp_path / "bad.pt"
    torch.save({"noise": torch.zeros(3)}, str(bad))
    with pytest.raises(ValueError):
        export_checkpoint(str(bad), str(tmp_path / "bad.onnx"))