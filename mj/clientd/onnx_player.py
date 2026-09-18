"""ONNX 推理玩家(task 5.3):在无 torch 环境运行 policy/policy-v3 策略。

- 从 onnx 元数据读取契约(planes/scalars/actions),契约不符拒载;
- 掩码/argmax 留在 numpy 侧(与 policy_player 同口径);
- 提供 (g,seat)->action 的 Player 兼容接口,以及供 PolicyV3Runtime
  鸭子注入的 distribution()/predict_game()。
"""

from __future__ import annotations

import os

import numpy as np

from ..features import (
    N_ACTIONS, N_PLANES, N_SCALARS, extract, legal_mask, action_to_flat,
    flat_to_action,
)
from .errors import ValidationError

__all__ = ["OnnxPolicyPlayer", "load_onnx_player"]


class _Distribution:
    """PolicyV3Runtime._pick_distribution 期望的最小形状。"""

    def __init__(self, actions, probabilities):
        self.actions = tuple(int(a) for a in actions)
        self.probabilities = tuple(float(p) for p in probabilities)


class OnnxPolicyPlayer:
    def __init__(self, onnx_path):
        onnx_path = str(onnx_path)
        if not os.path.exists(onnx_path):
            raise ValidationError(f"onnx model not found: {onnx_path}")
        import onnx
        import onnxruntime as ort
        self.path = onnx_path
        graph = onnx.load(onnx_path, load_external_data=False)
        props = {p.key: p.value for p in graph.metadata_props}
        try:
            self.n_planes = int(props.get("contract_planes", N_PLANES))
            self.n_scalars = int(props.get("contract_scalars", N_SCALARS))
            self.n_actions = int(props.get("contract_actions", N_ACTIONS))
        except (TypeError, ValueError):
            raise ValidationError("onnx model missing contract metadata")
        self._validate_contract()
        self._session = ort.InferenceSession(onnx_path)

    def _validate_contract(self):
        if self.n_scalars != N_SCALARS:
            raise ValidationError(
                f"onnx contract_scalars={self.n_scalars} != {N_SCALARS}")
        if self.n_actions != N_ACTIONS:
            raise ValidationError(
                f"onnx contract_actions={self.n_actions} != {N_ACTIONS}")
        if self.n_planes < N_PLANES:
            raise ValidationError(
                f"onnx contract_planes={self.n_planes} < {N_PLANES}")

    def _logits(self, planes, scalars):
        out = self._session.run(None, {
            "planes": np.asarray(planes, dtype=np.float32)[None],
            "scalars": np.asarray(scalars, dtype=np.float32)[None],
        })[0]
        return out[0]

    def _obs(self, game, seat):
        planes, scalars = extract(game, seat)
        if self.n_planes > planes.shape[0]:
            pad = np.zeros((self.n_planes - planes.shape[0], 34),
                           dtype=np.float32)
            planes = np.concatenate([planes, pad])
        return planes, scalars

    def play(self, game, seat):
        planes, scalars = self._obs(game, seat)
        mask = legal_mask(game)
        logits = self._logits(planes, scalars)
        a = _masked_argmax(logits, mask)
        return flat_to_action(a)

    def __call__(self, game, seat):
        return self.play(game, seat)

    def distribution(self, game, legal_actions):
        legal_actions = tuple(int(a) for a in legal_actions)
        seats = _any_seat(game)
        planes, scalars = self._obs(game, seats)
        mask = [False] * N_ACTIONS
        for a in legal_actions:
            mask[action_to_flat(a)] = True
        logits = self._logits(planes, scalars)
        exp = np.exp(np.where(np.asarray(mask), logits, -1e9) -
                     np.max(logits))
        probs = exp / exp.sum()
        return _Distribution(legal_actions, [float(probs[action_to_flat(a)])
                                             for a in legal_actions])

    def predict_game(self, game, seat, *, legal_actions=None, **kwargs):
        if legal_actions is None:
            legal_actions = game.legal_actions()
        return self.distribution(game, legal_actions)


def _any_seat(game):
    return game.current_seat()


def _masked_argmax(logits, mask):
    logs = np.asarray(logits, dtype=np.float64)
    masked = np.where(np.asarray(mask), logs, -np.inf)
    return int(np.argmax(masked))


def load_onnx_player(ckpt):
    """按路径加载 onnx 玩家;非 .onnx 抛 ValidationError。"""
    if not str(ckpt).endswith(".onnx"):
        raise ValidationError(f"expected .onnx model, got {ckpt!r}")
    return OnnxPolicyPlayer(ckpt)