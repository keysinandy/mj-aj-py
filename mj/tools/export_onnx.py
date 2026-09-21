"""ONNX 导出器(task 5.1):把 torch checkpoint 转成可分发推理的 .onnx。

三源:
- BC:    {"state_dict": Net 全量, blocks, width}             → Net 前向 logits;
- PPO:   {"net": 主干, "action_net": 独立 policy 头, ...}     → 拼接 p/v 特征 → action_net;
- policy-v3: {"manifest": PolicyValueModelManifest, state_dict} → PolicyValueNet 前向 logits。

掩码与 argmax 留在 Python 侧(导出的是纯张量前向),因此对拍标准干净:
同 (planes, scalars) → 同 logits → 同动作。导出件携带契约元数据
(planes/scalars/actions),供对拍与运行时契约校验。
"""

from __future__ import annotations

import inspect
import os

from ..features import N_ACTIONS, N_PLANES, N_SCALARS

__all__ = ["detect_format", "n_planes_from_checkpoint", "export_checkpoint",
           "PLANES_KEY", "SCALARS_KEY", "ACTIONS_KEY"]

PLANES_KEY = "contract_planes"
SCALARS_KEY = "contract_scalars"
ACTIONS_KEY = "contract_actions"


def detect_format(ck):
    """按 checkpoint 顶层键识别格式。"""
    if "manifest" in ck:
        return "policy-v3"
    if "state_dict" in ck:
        return "bc"
    if "net" in ck:
        return "ppo"
    raise ValueError(
        f"unrecognized checkpoint format; keys={sorted(ck.keys())!r}")


def n_planes_from_checkpoint(ck, fmt=None, scalars=None):
    """从 stem 卷积宽度反推输入平面数(与 policy_player 同法)。"""
    if scalars is None:
        scalars = N_SCALARS
    fmt = fmt or detect_format(ck)
    stem = (ck["state_dict"] if fmt == "bc" else ck["net"])["stem.0.weight"]
    return int(stem.shape[1]) - int(scalars)


def _make_traces(torch):
    nn = torch.nn
    F = torch.nn.functional

    class BCTrace(nn.Module):
        def __init__(self, net):
            super().__init__()
            self.net = net

        def forward(self, planes, scalars):
            logits, _value = self.net(planes, scalars)
            return logits
    BCTrace.__module__ = __name__

    class PPOTrace(nn.Module):
        def __init__(self, model, action_net):
            super().__init__()
            self.model = model
            self.action_net = action_net

        def forward(self, planes, scalars):
            b = scalars.unsqueeze(-1).expand(-1, -1, planes.shape[-1])
            x = self.model.blocks(self.model.stem(torch.cat([planes, b], dim=1)))
            feats = torch.cat([torch.relu(self.model.p_conv(x)).flatten(1),
                               torch.relu(self.model.v_conv(x)).flatten(1)],
                              dim=1)
            return self.action_net(feats)
    PPOTrace.__module__ = __name__
    return BCTrace, PPOTrace


def _build_wrapper(ck):
    import torch
    from ..model import Net
    BC, PPO = _make_traces(torch)
    fmt = detect_format(ck)
    if fmt == "bc":
        n_planes = n_planes_from_checkpoint(ck, "bc")
        net = Net(n_planes=n_planes, blocks=ck["blocks"], width=ck["width"])
        net.load_state_dict(ck["state_dict"])
        net.eval()
        return BC(net), n_planes, N_SCALARS
    if fmt == "ppo":
        n_planes = n_planes_from_checkpoint(ck, "ppo")
        net = Net(n_planes=n_planes, blocks=ck["blocks"], width=ck["width"])
        net.load_state_dict(ck["net"])
        net.eval()
        action_net = torch.nn.Linear(
            ck["action_net"]["weight"].shape[1],
            ck["action_net"]["weight"].shape[0])
        action_net.load_state_dict(ck["action_net"])
        action_net.eval()
        return PPO(net, action_net), n_planes, N_SCALARS
    # policy-v3
    from ..models.policy_value import (PolicyValueNet,
                                       policy_value_manifest_from_json)
    manifest_data = ck["manifest"]
    manifest = (manifest_data if hasattr(manifest_data, "fingerprint")
                else policy_value_manifest_from_json(manifest_data))
    net = PolicyValueNet(blocks=manifest.blocks, width=manifest.width,
                         manifest=manifest)
    state = (ck.get("state_dict") or ck.get("model_state_dict")
             or ck.get("model"))
    if state is None:
        raise ValueError("policy-v3 checkpoint missing state_dict")
    net.load_state_dict(state)
    net.eval()
    n_planes = int(net.feature_contract.n_planes)
    n_scalars = int(net.feature_contract.n_scalars)
    return BC(net), n_planes, n_scalars


def export_checkpoint(ck_path, out_path, *, model=None, n_actions=N_ACTIONS):
    """导出 ckpt → onnx 文件;model 参数适合测试注入(否则从 ck_path 读取)。

    返回写入文件的契约元数据 dict。
    """
    import onnx
    import torch

    if model is None:
        ck = torch.load(ck_path, map_location="cpu", weights_only=True)
        wrapper, n_planes, n_scalars = _build_wrapper(ck)
    else:
        wrapper, n_planes, n_scalars = model

    out_path = str(out_path)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    planes = torch.zeros(1, n_planes, 34, dtype=torch.float32)
    scalars = torch.zeros(1, n_scalars, dtype=torch.float32)
    export_kwargs = {
        "input_names": ["planes", "scalars"],
        "output_names": ["logits"],
        "dynamic_axes": {
            "planes": {0: "batch"}, "scalars": {0: "batch"},
            "logits": {0: "batch"},
        },
        "opset_version": 13,
        "do_constant_folding": True,
    }
    # Torch 2.2 的 exporter 默认就是 legacy tracer,还没有 dynamo 参数;
    # 新版仍显式关闭 dynamo,保持导出图和 onnxruntime 的契约稳定。
    if "dynamo" in inspect.signature(torch.onnx.export).parameters:
        export_kwargs["dynamo"] = False
    torch.onnx.export(wrapper, (planes, scalars), out_path, **export_kwargs)

    meta = {PLANES_KEY: int(n_planes), SCALARS_KEY: int(n_scalars),
            ACTIONS_KEY: int(n_actions)}
    m = onnx.load(out_path)
    props = m.metadata_props.add()
    props.key = PLANES_KEY
    props.value = str(int(n_planes))
    props = m.metadata_props.add()
    props.key = SCALARS_KEY
    props.value = str(int(n_scalars))
    props = m.metadata_props.add()
    props.key = ACTIONS_KEY
    props.value = str(int(n_actions))
    onnx.save(m, out_path)
    return meta
