"""对拍门禁(task 5.2):torch 原实现 vs ONNX 运行时,逐动作一致。

覆盖两个层面:
- 随机观测:同 (planes, scalars) → 同 logits → 合法掩码下 argmax 同动作;
- 随机自博弈真实决策点:在一局对弈中用 extract() 采集每个决策点的观测,
  torch 与 onnx 在合法集内的选择逐动作一致。

断言标准:最终所选动作一致(非 logits 数值),容忍后端浮点差异。
负面用例:对拍能拦截"权重被篡改"导致的动作不一致(测试用坏权重注入)。
"""

from __future__ import annotations

import os

import numpy as np

from .export_onnx import (
    export_checkpoint, _build_wrapper, PLANES_KEY, SCALARS_KEY, ACTIONS_KEY,
)
from ..features import N_ACTIONS, N_PLANES, N_SCALARS, legal_mask, extract


def _masked_argmax(logits, mask):
    logits = np.asarray(logits, dtype=np.float64)
    masked = np.where(mask, logits, -np.inf)
    return int(np.argmax(masked))


def _torch_logits(ck_path, planes, scalars):
    import torch
    ck = torch.load(ck_path, map_location="cpu", weights_only=True)
    wrapper, n_planes, n_scalars = _build_wrapper(ck)
    with torch.no_grad():
        logits = wrapper(
            torch.as_tensor(planes[None], dtype=torch.float32),
            torch.as_tensor(scalars[None], dtype=torch.float32))
    return np.asarray(logits[0].detach()).astype(np.float64)


def _onnx_logits(onnx_path, planes, scalars, session=None):
    if session is None:
        import onnxruntime as ort
        session = ort.InferenceSession(onnx_path)
    out = session.run(None, {
        "planes": np.asarray(planes, dtype=np.float32)[None],
        "scalars": np.asarray(scalars, dtype=np.float32)[None],
    })[0]
    return out[0]


def run_parity(ckpt, onnx_path, n_random=120, n_games=6, seed=0):
    """返回 {checked, matched, mismatches:[...]}。"""
    import onnxruntime as ort
    import onnx
    from ..game import Game
    from mj.bot import choose_action

    meta_graph = onnx.load(onnx_path)
    props = {p.key: p.value for p in meta_graph.metadata_props}
    n_planes = int(props[PLANES_KEY])
    scalars_dim = int(props.get(SCALARS_KEY, N_SCALARS))
    session = ort.InferenceSession(onnx_path)
    rng = np.random.default_rng(seed)
    checked = 0
    matched = 0
    mismatches = []

    def _cmp(planes, scalars, mask, tag):
        nonlocal checked, matched
        tl = _torch_logits(ckpt, planes, scalars)
        ol = _onnx_logits(onnx_path, planes, scalars, session)
        a = _masked_argmax(tl, mask)
        b = _masked_argmax(ol, mask)
        checked += 1
        if a == b:
            matched += 1
        else:
            mismatches.append({"tag": tag, "torch": int(a), "onnx": int(b)})

    # 随机观测
    for _ in range(n_random):
        planes = rng.standard_normal((n_planes, 34)).astype(np.float32)
        scalars = rng.standard_normal((scalars_dim,)).astype(np.float32)
        mask = np.zeros(N_ACTIONS, dtype=bool)
        mask[rng.choice(N_ACTIONS, size=max(1, rng.integers(1, 30)),
                        replace=False)] = True
        _cmp(planes, scalars, mask, "random")

    # 随机自博弈真实决策点(BC/PPO 的平面数下,extract 可能不足 → 补零)
    for gi in range(n_games):
        g = Game(seed=seed + gi, dealer=gi % 4)
        players = [choose_action] * 4
        guard = 0
        while not g.done and guard < 2000:
            seat = g.current_seat()
            planes, scalars = extract(g, seat)
            if n_planes > planes.shape[0]:
                pad = np.zeros((n_planes - planes.shape[0], 34),
                               dtype=np.float32)
                planes = np.concatenate([planes, pad])
            mask = legal_mask(g)
            _cmp(planes, scalars, mask, f"game{gi}-seed{seed+gi}")
            a = players[seat](g, seat)
            g.step(a)
            guard += 1

    return {
        "checked": checked, "matched": matched,
        "all_match": matched == checked,
        "mismatches": mismatches[:20],
    }


def run_parity_on_dir(ckpt, onnx_path):
    """供 CLI/CI 便捷入口,无 torch 时抛清晰错误。"""
    try:
        import torch  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("对拍需开发机 torch 环境") from exc
    return run_parity(ckpt, onnx_path)