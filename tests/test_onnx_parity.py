"""Task 5.2 验收:对拍门禁(torch vs onnx 逐动作一致 + 负面拦截)。

BC/PPO 用真实 ckpt,先导出 onnx 再对拍;负面用例注入坏权重断言拦截。
"""

import inspect
import os

import pytest

from mj.tools.export_onnx import export_checkpoint
from mj.tools.onnx_parity import run_parity

BC_CKPT = "runs/bc0/best.pt"
PPO_CKPT = "runs/ppo4/ckpt_350000.pt"


@pytest.mark.parametrize("ckpt", [BC_CKPT, PPO_CKPT])
def test_parity_real_checkpoints(tmp_path, ckpt):
    if not os.path.exists(ckpt):
        pytest.skip(f"checkpoint missing: {ckpt}")
    onnx_path = str(tmp_path / f"{os.path.basename(ckpt)}.onnx")
    export_checkpoint(ckpt, onnx_path)
    report = run_parity(ckpt, onnx_path, n_random=40, n_games=3, seed=7)
    assert report["matched"] == report["checked"]
    assert report["all_match"] is True
    assert not report["mismatches"]


def test_parity_detects_corruption(tmp_path):
    if not os.path.exists(BC_CKPT):
        pytest.skip("no bc ckpt")
    onnx_path = str(tmp_path / "bc.onnx")
    export_checkpoint(BC_CKPT, onnx_path)
    # 导出一个随机权重模型作为"坏权重"——结构合法但权重不同
    # → torch 原模型 vs onnx 坏模型在同批观测上动作应不一致(检出篡改)
    import torch
    import onnxruntime as ort
    import numpy as np
    from mj.model import Net
    from mj.features import N_PLANES, N_SCALARS, N_ACTIONS
    ck = torch.load(BC_CKPT, map_location="cpu", weights_only=True)
    net = Net(n_planes=N_PLANES, n_scalars=N_SCALARS,
              blocks=ck.get("blocks", 2), width=ck.get("width", 64))
    torch.nn.init.normal_(net.p_fc.weight, std=1.0)
    for p in net.v_fc.parameters():
        torch.nn.init.normal_(p, std=1.0)
    net.eval()
    bad_path = str(tmp_path / "bad.onnx")
    planes = torch.randn(1, N_PLANES, 34)
    scalars = torch.randn(1, N_SCALARS)
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
    if "dynamo" in inspect.signature(torch.onnx.export).parameters:
        export_kwargs["dynamo"] = False
    torch.onnx.export(net, (planes, scalars), bad_path, **export_kwargs)
    # 比较:原 onnx vs 随机 onnx 在同一批观测上选牌应不一致
    orig_sess = ort.InferenceSession(onnx_path)
    bad_sess = ort.InferenceSession(bad_path)
    rng = np.random.default_rng(42)
    mismatches = 0
    for _ in range(60):
        p = rng.standard_normal((N_PLANES, 34)).astype(np.float32)
        s = rng.standard_normal((N_SCALARS,)).astype(np.float32)
        mask = np.zeros(N_ACTIONS, dtype=bool)
        mask[rng.choice(N_ACTIONS, size=max(1, rng.integers(1, 20)),
                        replace=False)] = True
        ol = orig_sess.run(None, {"planes": p[None], "scalars": s[None]})[0][0]
        bl = bad_sess.run(None, {"planes": p[None], "scalars": s[None]})[0][0]
        a_orig = int(np.argmax(np.where(mask, ol, -np.inf)))
        a_bad = int(np.argmax(np.where(mask, bl, -np.inf)))
        if a_orig != a_bad:
            mismatches += 1
    assert mismatches > 0, "随机权重 onnx 与原 onnx 应产生不同动作"
