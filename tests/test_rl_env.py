"""RL 环境测试:obs/mask 语义、oracle dropout、独立轨迹与终局奖励。"""

import unittest

import numpy as np

from mj.features import (
    N_PLANES, N_PLANES_ORACLE, N_SCALARS, flat_to_action, legal_mask,
)
from mj.rl_env import MahjongEnv, N_OBS_ROWS


def _rollout(env, rng):
    """随机合法动作跑完一局,返回 (steps, total_reward, info)。"""
    total = 0.0
    steps = 0
    done = False
    env.reset(seed=int(rng.integers(2 ** 31)))
    while not done:
        mask = env.action_masks()
        a = int(rng.choice(np.flatnonzero(mask)))
        obs, r, done, trunc, info = env.step(a)
        assert not trunc
        total += r
        steps += 1
    return steps, total, info


class TestMahjongEnv(unittest.TestCase):
    def test_spaces_and_obs(self):
        env = MahjongEnv(seed=0)
        obs, _ = env.reset()
        self.assertEqual(obs.shape, (N_OBS_ROWS, 34))
        self.assertTrue(obs.dtype == np.float32)
        self.assertEqual(env.observation_space.shape, (N_OBS_ROWS, 34))
        self.assertEqual(env.action_space.n, 109)

    def test_mask_matches_engine(self):
        env = MahjongEnv(seed=1)
        env.reset()
        # 内部状态与 legal_actions 一致
        g, agent = env._g, env._agent
        self.assertEqual(g.current_seat(), agent)
        expected = {a for a in g.legal_actions()}
        got = {flat_to_action(i) for i, v in enumerate(env.action_masks()) if v}
        self.assertEqual(got, expected)

    def test_oracle_dropout(self):
        # oracle_p=0:oracle 段 [75:91) 全零;标量段 [91:99) 第 0 列有效
        env = MahjongEnv(oracle_p=0.0, seed=2)
        obs, _ = env.reset()
        self.assertEqual(float(np.abs(obs[N_PLANES:N_PLANES_ORACLE]).max()), 0.0)
        seg = obs[N_PLANES_ORACLE:, :]
        self.assertEqual(float(seg[:, 1:].max()), 0.0)
        self.assertGreater(float(np.abs(seg[:, 0]).max()), 0.0)
        # oracle_p=1:oracle 平面段有非零(对手手牌非空)
        env2 = MahjongEnv(oracle_p=1.0, seed=2)
        obs2, _ = env2.reset()
        self.assertGreater(float(np.abs(obs2[N_PLANES:N_PLANES_ORACLE]).max()), 0.0)
        # 同 seed 同 agent 座位下,两环境的基础 75 平面应一致
        self.assertTrue((obs[:N_PLANES] == obs2[:N_PLANES]).all())

    def test_reward_and_done(self):
        rng = np.random.default_rng(3)
        for _ in range(5):
            env = MahjongEnv(seed=int(rng.integers(2 ** 31)))
            steps, total, info = _rollout(env, rng)
            self.assertGreater(steps, 10)
            self.assertIn("scores", info)
            # 终局奖励 = 本家 clip(score/24, ±4),过程 0 → 总奖励即终局
            s = info["scores"][env._agent]
            expect = float(np.clip(s / 24.0, -4, 4))
            self.assertAlmostEqual(total, expect)

    def test_agent_decides_only_own_seat(self):
        # 对手全 pass:agent 座位外的决策不产 obs;到 agent 必是当前行动者
        env = MahjongEnv(opponents=[lambda g, s: g.legal_actions()[0]] * 3,
                         seed=4)
        env.reset()
        for _ in range(200):
            self.assertEqual(env._g.current_seat(), env._agent)
            mask = env.action_masks()
            if not mask.any() or env._g.done:
                break
            env.step(int(np.flatnonzero(mask)[0]))

    def test_illegal_action_raises(self):
        env = MahjongEnv(seed=5)
        env.reset()
        mask = env.action_masks()
        illegal = int(np.flatnonzero(~mask)[0])
        with self.assertRaises(AssertionError):
            env.step(illegal)

    def test_you_cai_bi_kao_propagates(self):
        """开关传入 env → 底层 Game 生效:胡牌掩码受门禁约束。"""
        from mj.game import HU
        from mj.features import flat_to_action

        for ycbk in (False, True):
            env = MahjongEnv(seed=11, you_cai_bi_kao=ycbk)
            env.reset()
            self.assertEqual(env._g.you_cai_bi_kao, ycbk)
        # 随机 rollout:开关开启时对局照常终局且动作全合法
        env = MahjongEnv(seed=3, you_cai_bi_kao=True)
        env.reset()
        rng = np.random.default_rng(0)
        done, steps = False, 0
        while not done:
            mask = env.action_masks()
            done = env.step(int(rng.choice(np.flatnonzero(mask))))[2]
            steps += 1
            self.assertLess(steps, 2000)
        self.assertTrue(env._g.done)

    def test_deterministic_given_seed(self):
        # 相同 env seed + 相同动作序列 → 相同结果
        rng = np.random.default_rng(6)
        a_env = MahjongEnv(seed=42)
        b_env = MahjongEnv(seed=42)
        a_obs, _ = a_env.reset()
        b_obs, _ = b_env.reset()
        self.assertTrue((a_obs == b_obs).all())
        done = False
        while not done:
            mask = a_env.action_masks()
            a = int(rng.choice(np.flatnonzero(mask)))
            a_obs, a_r, a_done, _, a_info = a_env.step(a)
            b_obs, b_r, b_done, _, b_info = b_env.step(a)
            self.assertTrue((a_obs == b_obs).all())
            self.assertEqual(a_r, b_r)
            self.assertEqual(a_done, b_done)
            done = a_done
        self.assertEqual(list(a_info["scores"]), list(b_info["scores"]))


if __name__ == "__main__":
    unittest.main()
