"""Policy/Value 残差 1D 卷积网络(BC 预训练与 RL 共用骨架)。

输入:特征平面 [B, C, 34] + 标量 [B, S](广播为平面拼接)。
输出:policy logits [B, 109](未 softmax,训练侧配合法 mask)、
value [B](tanh,目标 = clip(得分/24, -4, 4)/4)。

Suphx 配置为 15 块 × 256 通道;CPU 演练用 --blocks/--width 调小。
"""

import torch
import torch.nn as nn
import torch.nn.functional as F

from .features import N_ACTIONS, N_PLANES, N_SCALARS


class ResBlock(nn.Module):
    def __init__(self, ch):
        super().__init__()
        self.conv1 = nn.Conv1d(ch, ch, 3, padding=1, bias=False)
        self.bn1 = nn.BatchNorm1d(ch)
        self.conv2 = nn.Conv1d(ch, ch, 3, padding=1, bias=False)
        self.bn2 = nn.BatchNorm1d(ch)

    def forward(self, x):
        y = F.relu(self.bn1(self.conv1(x)))
        y = self.bn2(self.conv2(y))
        return F.relu(x + y)


class Net(nn.Module):
    def __init__(self, n_planes=N_PLANES, n_scalars=N_SCALARS,
                 n_actions=N_ACTIONS, blocks=15, width=256):
        super().__init__()
        self.stem = nn.Sequential(
            nn.Conv1d(n_planes + n_scalars, width, 3, padding=1, bias=False),
            nn.BatchNorm1d(width),
            nn.ReLU(),
        )
        self.blocks = nn.Sequential(*[ResBlock(width) for _ in range(blocks)])
        self.p_conv = nn.Conv1d(width, 2, 1)
        self.p_fc = nn.Linear(2 * 34, n_actions)
        self.v_conv = nn.Conv1d(width, 1, 1)
        self.v_fc = nn.Sequential(
            nn.Linear(34, 256),
            nn.ReLU(),
            nn.Linear(256, 1),
        )

    def forward(self, planes, scalars):
        """planes [B,C,34], scalars [B,S] → (policy_logits [B,109], value [B])。"""
        b = scalars.unsqueeze(-1).expand(-1, -1, planes.shape[-1])
        x = self.blocks(self.stem(torch.cat([planes, b], dim=1)))
        p = self.p_fc(F.relu(self.p_conv(x)).flatten(1))
        v = self.v_fc(F.relu(self.v_conv(x)).flatten(1))
        return p, v.squeeze(-1).tanh()


def masked_ce(logits, mask, action):
    """合法动作约束下的交叉熵(非法位 -inf)。"""
    return F.cross_entropy(logits.masked_fill(~mask, float("-inf")), action)


def masked_policy(logits, mask):
    """合法动作上的 softmax 概率。"""
    return F.softmax(logits.masked_fill(~mask, float("-inf")), dim=-1)
