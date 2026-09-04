"""MaskablePPO 自博弈环境:每家一条独立轨迹。

设计(与训练方案一致):
- 单 agent 视角:本家决策点产出 (obs, mask);其余三家由注入的
  opponents 策略驱动((g, seat)->action,默认启发式 bot)。
  reset 随机庄家与 agent 座位(消除座位偏差)。
- obs = 99×34:features.extract(oracle=True) 的 91 平面 + 8 行
  标量广播(写在第 0 列,其余列 0)。oracle dropout 通过把
  [75:91) 段置零实现,维度恒定,推理/训练同构。
- reward:终局一次性 = clip(本家得分/24, ±4),γ=1;过程 0
  (无 shaping,计分线性无需 GRP)。可选势函数 shaping(shape_k>0,
  理论上不改变最优策略,给弃牌/吃碰决策密集向听信号——稀疏终局
  reward 下 PPO 噪声大,50k 实测无 shaping 学不动牌效)。

与 bc_data 的差异:那边采全部决策点做监督样本;这里只暴露 agent
座位的决策点,其余座位走 opponents——同局内 agent 座位的对手就是
"本局真实动作的回放",天然自博弈。
"""

import numpy as np
import gymnasium as gym
from gymnasium import spaces

from .bot import choose_action
from .features import (
    N_ACTIONS, N_PLANES, N_PLANES_ORACLE, N_SCALARS, extract, legal_mask,
)
from .game import Game
from .shanten import shanten

N_OBS_ROWS = N_PLANES_ORACLE + N_SCALARS

_STAND_CACHE = {}


def _potential(g, seat):
    """势函数 Φ = 8 − shanten(站立手),弃牌决策给密集效率信号。

    拿满 need+1 张(弃牌决策点)取最优弃张后的手——与特征
    post-discard 平面同口径。结果按 (手牌字节串, locked) 记忆化。
    """
    hand = g.hands[seat]
    locked = len(g.melds[seat])
    if sum(hand) == 13 - 3 * locked:
        return 8 - shanten(list(hand), locked)
    key = (bytes(hand), locked)
    best = _STAND_CACHE.get(key)
    if best is None:
        best = min(
            (shanten([v - 1 if i == t else v for i, v in enumerate(hand)], locked)
             for t in range(34) if hand[t]),
            default=8)
        if len(_STAND_CACHE) >= 1 << 17:
            _STAND_CACHE.clear()
        _STAND_CACHE[key] = best
    return 8 - best


class MahjongEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, opponents=None, oracle_p=1.0, shape_k=0.0,
                 you_cai_bi_kao=False, seed=None):
        """opponents: [(g, seat)->action] ×3 或 None(全启发式 bot);
        oracle_p: oracle 通道保留概率(1.0=全量,0=无);
        shape_k: 势函数 shaping 系数(0=关);
        you_cai_bi_kao: 有财必拷响(手有财神须爆头/杠开才可胡)。"""
        super().__init__()
        self.opponents = opponents or [choose_action] * 3
        self.oracle_p = oracle_p
        self.shape_k = shape_k
        self.you_cai_bi_kao = you_cai_bi_kao
        self.rng = np.random.default_rng(seed)
        self.action_space = spaces.Discrete(N_ACTIONS)
        self.observation_space = spaces.Box(
            low=0.0, high=1.0, shape=(N_OBS_ROWS, 34), dtype=np.float32)
        self._g = None
        self._agent = 0
        self._oracle_keep = False
        self._phi = 0.0

    def _obs(self):
        planes, scalars = extract(self._g, self._agent, oracle=True)
        if not self._oracle_keep:
            planes[N_PLANES:] = 0.0
        obs = np.zeros((N_OBS_ROWS, 34), dtype=np.float32)
        obs[:N_PLANES_ORACLE] = planes
        obs[N_PLANES_ORACLE:, 0] = scalars
        return obs

    def _advance_to_agent(self):
        """推进对局直到轮到 agent 座位决策;返回是否有待决策点。"""
        while not self._g.done:
            seat = self._g.current_seat()
            if seat == self._agent:
                return True
            # 下一个对手 = 座位 (agent+1)%4,依此类推
            opp_idx = (seat - self._agent - 1) % 4
            acts = self._g.legal_actions()
            act = self.opponents[opp_idx](self._g, seat)
            if act not in acts:  # 对手非法动作兜底:随机
                act = acts[self.rng.integers(len(acts))]
            self._g.step(act)
        return False

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self._g = Game(seed=int(self.rng.integers(2 ** 31)),
                       dealer=int(self.rng.integers(4)),
                       you_cai_bi_kao=self.you_cai_bi_kao)
        self._agent = int(self.rng.integers(4))
        self._oracle_keep = bool(self.rng.random() < self.oracle_p)
        self._advance_to_agent()
        self._phi = _potential(self._g, self._agent)
        return self._obs(), {}

    def step(self, action):
        from .features import flat_to_action

        g = self._g
        act = flat_to_action(int(action))
        acts = g.legal_actions()
        assert act in acts, f"非法动作 {action}->{act}"
        g.step(act)
        self._advance_to_agent()
        reward = 0.0
        if self.shape_k:
            phi = _potential(self._g, self._agent) if not g.done else 0.0
            reward = self.shape_k * (phi - self._phi)
            self._phi = phi
        if g.done:
            reward += float(np.clip(g.scores[self._agent] / 24.0, -4, 4))
            return (self._obs(), reward, True, False,
                    {"result": g.result, "scores": g.scores})
        return self._obs(), reward, False, False, {}

    def action_masks(self):
        return np.asarray(legal_mask(self._g), dtype=bool)

    def set_oracle_p(self, p):
        """SubprocVecEnv 远程更新 oracle 保留概率(train_ppo 退火用)。"""
        self.oracle_p = float(p)

    def get_oracle_p(self):
        return self.oracle_p
