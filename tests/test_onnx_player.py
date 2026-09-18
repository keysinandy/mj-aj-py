"""Task 5.3 验收:OnnxPolicyPlayer + strategies.onnx 路径。

测试:
- 合法 onnx 加载,契约校验通过;
- play() 返回合法动作;
- 与 torch policy_player 在同一批局上逐动作一致;
- 非法契约(planes 过少/scalars 不符)拒载;
- make_policy_player 正常路由到 onnx。
"""

import os
import tempfile

import numpy as np
import pytest
import torch

from mj.features import N_ACTIONS, N_PLANES, N_SCALARS, extract, legal_mask, flat_to_action
from mj.clientd.onnx_player import OnnxPolicyPlayer, load_onnx_player
from mj.clientd.errors import ValidationError

BC_CKPT = "runs/bc0/best.pt"
PPO_CKPT = "runs/ppo4/ckpt_350000.pt"


def _export_bc(onnx_path):
    from mj.tools.export_onnx import export_checkpoint
    export_checkpoint(BC_CKPT, onnx_path)


def _export_ppo(onnx_path):
    from mj.tools.export_onnx import export_checkpoint
    export_checkpoint(PPO_CKPT, onnx_path)


def _export_bad_planes(onnx_path, n_planes=40):
    """导出一个 planes 维度过小的模型:export_checkpoint 设元数据,再改写。"""
    from mj.tools.export_onnx import export_checkpoint, PLANES_KEY, SCALARS_KEY, ACTIONS_KEY
    import onnx
    export_checkpoint(BC_CKPT, onnx_path)
    graph = onnx.load(onnx_path, load_external_data=False)
    for prop in graph.metadata_props:
        if prop.key == PLANES_KEY:
            prop.value = str(n_planes)
    onnx.save(graph, onnx_path)


def _export_bad_scalars(onnx_path):
    """导出一个 scalars 维度不符的模型。"""
    from mj.tools.export_onnx import export_checkpoint, PLANES_KEY, SCALARS_KEY, ACTIONS_KEY
    import onnx
    export_checkpoint(BC_CKPT, onnx_path)
    graph = onnx.load(onnx_path, load_external_data=False)
    for prop in graph.metadata_props:
        if prop.key == SCALARS_KEY:
            prop.value = "12"
    onnx.save(graph, onnx_path)


class TestOnnxPolicyPlayerBasic:
    def test_load_bc(self, tmp_path):
        onnx_path = str(tmp_path / "bc.onnx")
        _export_bc(onnx_path)
        player = OnnxPolicyPlayer(onnx_path)
        assert player.n_planes == N_PLANES
        assert player.n_scalars == N_SCALARS
        assert player.n_actions == N_ACTIONS

    def test_load_ppo(self, tmp_path):
        onnx_path = str(tmp_path / "ppo.onnx")
        _export_ppo(onnx_path)
        player = OnnxPolicyPlayer(onnx_path)
        assert player.n_planes == 91  # PPO exported with 91 planes

    def test_play_returns_legal_action(self, tmp_path):
        onnx_path = str(tmp_path / "bc.onnx")
        _export_bc(onnx_path)
        from mj.game import Game
        player = OnnxPolicyPlayer(onnx_path)
        g = Game(seed=42)
        seat = g.current_seat()
        action = player(g, seat)
        assert action in g.legal_actions()

    def test_load_onnx_player_rejects_non_onnx(self):
        with pytest.raises(ValidationError, match="expected .onnx"):
            load_onnx_player("model.pt")

    def test_load_onnx_player_rejects_missing(self):
        with pytest.raises(ValidationError, match="not found"):
            OnnxPolicyPlayer("/nonexistent/model.onnx")

    def test_contract_rejects_too_few_planes(self, tmp_path):
        onnx_path = str(tmp_path / "bad.onnx")
        _export_bad_planes(onnx_path, n_planes=40)
        with pytest.raises(ValidationError, match="contract_planes"):
            OnnxPolicyPlayer(onnx_path)

    def test_contract_rejects_wrong_scalars(self, tmp_path):
        onnx_path = str(tmp_path / "bad.onnx")
        _export_bad_scalars(onnx_path)
        with pytest.raises(ValidationError, match="contract_scalars"):
            OnnxPolicyPlayer(onnx_path)


class TestOnnxPlayerParity:
    """与 torch policy_player 在同一批局上逐动作一致。"""

    def test_bc_parity(self, tmp_path):
        if not os.path.exists(BC_CKPT):
            pytest.skip("no bc ckpt")
        onnx_path = str(tmp_path / "bc.onnx")
        _export_bc(onnx_path)
        from mj.evaluate import policy_player
        from mj.game import Game
        torch_player = policy_player(BC_CKPT)
        onnx_player = OnnxPolicyPlayer(onnx_path)
        rng = np.random.default_rng(99)
        mismatches = 0
        for gi in range(6):
            g = Game(seed=int(rng.integers(0, 100000)), dealer=gi % 4)
            guard = 0
            while not g.done and guard < 500:
                seat = g.current_seat()
                legal = g.legal_actions()
                ta = torch_player(g, seat)
                oa = onnx_player(g, seat)
                if ta != oa:
                    mismatches += 1
                g.step(ta)
                guard += 1
        assert mismatches == 0, f"BC onnx vs torch mismatches: {mismatches}"


class TestStrategyFactoryOnnx:
    def test_make_policy_player_routes_to_onnx(self, tmp_path):
        onnx_path = str(tmp_path / "bc.onnx")
        _export_bc(onnx_path)
        from mj.clientd.strategies import make_policy_player
        player = make_policy_player({"strategy": "policy",
                                     "ckpt": onnx_path})
        from mj.game import Game
        g = Game(seed=123)
        action = player(g, g.current_seat())
        assert action in g.legal_actions()

    def test_make_policy_player_routes_to_torch(self):
        if not os.path.exists(BC_CKPT):
            pytest.skip("no bc ckpt")
        from mj.clientd.strategies import make_policy_player
        player = make_policy_player({"strategy": "policy", "ckpt": BC_CKPT})
        from mj.game import Game
        g = Game(seed=123)
        action = player(g, g.current_seat())
        assert action in g.legal_actions()
