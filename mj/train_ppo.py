"""MaskablePPO 自博弈训练(Oracle Guiding 退火)。

- 网络:mj.model.Net 的卷积主干包装成 BaseFeaturesExtractor
  (obs [B,99,34] → 特征 [B, 2*34]);policy/value 头由 sb3 默认
  MlpExtractor 接管(与 BC 的双头结构略有差异,可接受——主干才是
  容量所在)。
- 动作 mask:MaskablePPO 原生支持,自动调 env.action_masks() 并在
  分布上 apply_masking,无需自定义 policy。
- Oracle guiding:环境 oracle_p 从 1 线性退火到 0(前 anneal_frac
  步),退火完成后 lr 降至 1/10(lr schedule 与退火同步表达)。
- 对手:启发式 bot(vs 固定对手 reward 语义干净,首版不引入自举
  对手池——策略互打版本见 PROGRESS P3 待办)。

用法(CPU 冒烟):
  python -m mj.train_ppo --steps 30000 --blocks 2 --width 64 --out runs/ppo0
"""

import argparse
import copy
import os
import time
from typing import Optional

import gymnasium as gym
import numpy as np
import torch
import torch.nn.functional as F
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from stable_baselines3.common.utils import explained_variance
from sb3_contrib import MaskablePPO

from .features import N_PLANES, N_PLANES_ORACLE, N_SCALARS
from .model import Net
from .rl_env import MahjongEnv, N_OBS_ROWS


class NetExtractor(BaseFeaturesExtractor):
    """Net 卷积主干作为特征提取器。

    obs [B, 99, 34]:前 91 行平面(其中 [75:91) 为 oracle 段——
    oracle guiding 打开时非零,退火/dropout 时为 0),后 8 行标量
    (第 0 列有效)。Net 按 91 平面 + 8 标量(99 通道)输入:
    oracle 通道是主干的一部分,推理/BC 阶段恒零,三者同构。

    特征 [B, 3*34] = policy 特征(relu(p_conv) 2×34,与 BC policy 头
    输入完全一致,可零填充移植)拼接 value 特征(relu(v_conv) 1×34,
    BC 训练过的 value 视角)。policy 头 = sb3 action_net(空 net_arch
    时 Linear(102,109),前 68 列搬 BC p_fc、后 34 列置零 → 初始
    策略与 BC 逐位一致);value 头 = vf MLP [128,128]。
    """

    def __init__(self, observation_space, blocks=2, width=64,
                 features_dim=3 * 34):
        super().__init__(observation_space, features_dim)
        from .features import N_PLANES_ORACLE

        self.n_planes_oracle = N_PLANES_ORACLE
        self.net = Net(n_planes=N_PLANES_ORACLE, n_scalars=N_SCALARS,
                       blocks=blocks, width=width)
        self._backbone = torch.nn.Sequential(self.net.stem, self.net.blocks)

    def forward(self, obs):
        planes = obs[:, :self.n_planes_oracle]
        scalars = obs[:, self.n_planes_oracle:, 0]
        b = scalars.unsqueeze(-1).expand(-1, -1, planes.shape[-1])
        x = self._backbone(torch.cat([planes, b], dim=1))
        p = torch.relu(self.net.p_conv(x)).flatten(1)
        v = torch.relu(self.net.v_conv(x)).flatten(1)
        return torch.cat([p, v], dim=1)


class BCPriorPPO(MaskablePPO):
    """PPO + BC 先验 KL 正则:policy loss 追加 λ·KL(π_new ‖ π_BC)。

    背景(ppo2/ppo3 教训):单步 target_kl 只约束每次更新,累积漂移
    依然毁掉 BC 先验(500k 步从 22.9% 崩到 6%)。本类把"距 BC 的
    KL"直接放进损失,梯度持续把策略拉回 BC 邻域——PPO 负责"在邻域
    内往高回报方向挪",λ 控制邻域半径。

    π_BC = BC 移植完成后的初始策略冻结副本(set_bc_reference 深拷贝,
    恒 eval、requires_grad False;主干 BN running stats 与 FreezeBN
    口径一致,两者永用同一套 BC 统计,比较稳定)。

    train() 复制自 sb3-contrib 2.4.0(仅插入正则项与日志),升级依赖
    时需同步。同一 minibatch、同一动作掩码下计算 KL:掩码位两分布
    概率同为 0,log-prob 差为常数,乘积恰 0,无数值病态。
    """

    def __init__(self, *args, bc_reg: float = 0.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.bc_reg = float(bc_reg)
        self._policy_ref = None

    def set_bc_reference(self):
        """BC 移植(load_bc_init)之后调用,冻结初始策略为参考。"""
        assert self.bc_reg > 0, "bc_reg=0 时无需参考策略"
        self._policy_ref = copy.deepcopy(self.policy)
        self._policy_ref.eval()
        for p in self._policy_ref.parameters():
            p.requires_grad_(False)

    def _bc_kl(self, obs, action_masks):
        """KL(π_new ‖ π_BC),对 batch 求均值;new 侧带梯度。"""
        dist_new = self.policy.get_distribution(obs, action_masks=action_masks)
        with torch.no_grad():
            dist_bc = self._policy_ref.get_distribution(
                obs, action_masks=action_masks)
        log_pn = F.log_softmax(dist_new.distribution.logits, dim=-1)
        log_pb = F.log_softmax(dist_bc.distribution.logits, dim=-1)
        return (log_pn.exp() * (log_pn - log_pb)).sum(-1).mean()

    def train(self) -> None:
        # 以下为 sb3-contrib 2.4.0 MaskablePPO.train() 全文,
        # 标注 [BC] 处为插入的先验正则
        self.policy.set_training_mode(True)
        # [BC] set_training_mode(True) 会把 BN 切回 train 模式,
        # 而 on_training_start/on_rollout_start 回调只在 rollout 前触发——
        # ppo1/2/3 的更新阶段实际用的是 minibatch 批统计(与文档意图
        # 不符)。此处显式冻结:更新与采样同用 running stats,
        # 且保证 _bc_kl 的 live/ref 口径一致(初始 KL=0)。
        for m in self.policy.modules():
            if isinstance(m, torch.nn.BatchNorm1d):
                m.eval()
        self._update_learning_rate(self.policy.optimizer)
        clip_range = self.clip_range(self._current_progress_remaining)  # type: ignore[operator]
        if self.clip_range_vf is not None:
            clip_range_vf = self.clip_range_vf(self._current_progress_remaining)  # type: ignore[operator]

        entropy_losses = []
        pg_losses, value_losses, bc_kls = [], [], []
        clip_fractions = []

        continue_training = True

        for epoch in range(self.n_epochs):
            approx_kl_divs = []
            for rollout_data in self.rollout_buffer.get(self.batch_size):
                actions = rollout_data.actions
                if isinstance(self.action_space, gym.spaces.Discrete):
                    actions = rollout_data.actions.long().flatten()

                values, log_prob, entropy = self.policy.evaluate_actions(
                    rollout_data.observations,
                    actions,
                    action_masks=rollout_data.action_masks,
                )

                values = values.flatten()
                advantages = rollout_data.advantages
                if self.normalize_advantage:
                    advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)

                ratio = torch.exp(log_prob - rollout_data.old_log_prob)

                policy_loss_1 = advantages * ratio
                policy_loss_2 = advantages * torch.clamp(ratio, 1 - clip_range, 1 + clip_range)
                policy_loss = -torch.min(policy_loss_1, policy_loss_2).mean()

                pg_losses.append(policy_loss.item())
                clip_fraction = torch.mean((torch.abs(ratio - 1) > clip_range).float()).item()
                clip_fractions.append(clip_fraction)

                if self.clip_range_vf is None:
                    values_pred = values
                else:
                    values_pred = rollout_data.old_values + torch.clamp(
                        values - rollout_data.old_values, -clip_range_vf, clip_range_vf
                    )
                value_loss = F.mse_loss(rollout_data.returns, values_pred)
                value_losses.append(value_loss.item())

                if entropy is None:
                    entropy_loss = -torch.mean(-log_prob)
                else:
                    entropy_loss = -torch.mean(entropy)

                entropy_losses.append(entropy_loss.item())

                # [BC] 先验正则:λ·KL(π_new ‖ π_BC)
                bc_kl = self._bc_kl(rollout_data.observations,
                                    rollout_data.action_masks) \
                    if self._policy_ref is not None else None
                if bc_kl is not None:
                    bc_kls.append(bc_kl.item())

                loss = (policy_loss + self.ent_coef * entropy_loss
                        + self.vf_coef * value_loss)
                if bc_kl is not None:
                    loss = loss + self.bc_reg * bc_kl

                with torch.no_grad():
                    log_ratio = log_prob - rollout_data.old_log_prob
                    approx_kl_div = torch.mean(
                        (torch.exp(log_ratio) - 1) - log_ratio).cpu().numpy()
                    approx_kl_divs.append(approx_kl_div)

                if self.target_kl is not None and approx_kl_div > 1.5 * self.target_kl:
                    continue_training = False
                    if self.verbose >= 1:
                        print(f"Early stopping at step {epoch} due to reaching max kl: {approx_kl_div:.2f}")
                    break

                self.policy.optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.policy.parameters(), self.max_grad_norm)
                self.policy.optimizer.step()

            if not continue_training:
                break

        self._n_updates += self.n_epochs
        explained_var = explained_variance(
            self.rollout_buffer.values.flatten(),
            self.rollout_buffer.returns.flatten())

        self.logger.record("train/entropy_loss", np.mean(entropy_losses))
        self.logger.record("train/policy_gradient_loss", np.mean(pg_losses))
        self.logger.record("train/value_loss", np.mean(value_losses))
        if bc_kls:
            self.logger.record("train/bc_kl", np.mean(bc_kls))
        self.logger.record("train/approx_kl", np.mean(approx_kl_divs))
        self.logger.record("train/clip_fraction", np.mean(clip_fractions))
        self.logger.record("train/loss", loss.item())
        self.logger.record("train/explained_variance", explained_var)
        self.logger.record("train/n_updates", self._n_updates, exclude="tensorboard")
        self.logger.record("train/clip_range", clip_range)
        if self.clip_range_vf is not None:
            self.logger.record("train/clip_range_vf", clip_range_vf)


class FreezeBNCallback(BaseCallback):
    """主干 BN 恒用 running stats(eval 模式)。

    SB3 每轮 train() 把 policy 置 train 模式(用 minibatch 统计);
    rollout 收集时又用旧 running stats → 采样/训练分布不一致,
    且 BN 统计随策略漂移失控。冻结后主干 BN 参数不更新
    (bc_train 移植的 BC 统计即最终统计),主干容量仍在 conv 权重。
    """

    def _on_training_start(self):
        self._freeze()

    def _on_rollout_start(self):
        self._freeze()

    def _on_step(self):
        return True

    def _freeze(self):
        for m in self.model.policy.modules():
            if isinstance(m, torch.nn.BatchNorm1d):
                m.eval()


class OracleAnnealCallback(BaseCallback):
    """oracle_p 从 1 线性退火到 0(前 anneal_frac 训练量),与 lr 同步。

    venv 为 SubprocVecEnv 时经 env_method 转发到各 worker 进程
    (每次 rollout 起点发一次,IPC 成本可忽略)。
    """

    def __init__(self, venv, raw_envs, anneal_frac):
        super().__init__()
        self.venv = venv
        self.raw_envs = raw_envs
        self.anneal_frac = anneal_frac

    def _on_rollout_start(self):
        self._apply()

    def _on_step(self):
        self.logger.record(
            "oracle/keep",
            max(0.0, 1.0 - self.num_timesteps / self.model._total_timesteps
                / max(self.anneal_frac, 1e-9)))
        return True

    def _apply(self):
        p = self.num_timesteps / self.model._total_timesteps
        keep = max(0.0, 1.0 - p / max(self.anneal_frac, 1e-9))
        if self.raw_envs:
            for env in self.raw_envs:
                env.oracle_p = keep
        else:
            self.venv.env_method("set_oracle_p", keep)


class SaveNetCallback(BaseCallback):
    """每 save_every 步存快照(net + action_net),供断点与强度轨迹评估。"""

    def __init__(self, out, blocks, width, save_every=50_000):
        super().__init__()
        self.out = out
        self.blocks = blocks
        self.width = width
        self.save_every = save_every

    def _on_step(self):
        if self.num_timesteps and self.num_timesteps % self.save_every == 0:
            self._save(os.path.join(self.out, f"ckpt_{self.num_timesteps}.pt"))
        return True

    def _save(self, path):
        pol = self.model.policy
        torch.save({
            "net": pol.features_extractor.net.state_dict(),
            "action_net": pol.action_net.state_dict(),
            "blocks": self.blocks, "width": self.width,
        }, path)

    def on_training_end(self):
        self._save(os.path.join(self.out, "final.pt"))


def load_ppo_init(model, path):
    """PPO 检查点续训:直接载入主干 + 策略头。

    PPO 的 ``net`` 已是 91 平面(99 通道)全量主干,``action_net`` 已是完整
    策略头——与 BC 移植(需 [75:91) 补零、policy 列搬运)不同,直接精确加载。
    用于从已训练的 PPO(final/ckpt_*.pt) warm-start 续跑。
    """
    ck = torch.load(path, map_location="cpu", weights_only=True)
    model.policy.features_extractor.net.load_state_dict(ck["net"], strict=True)
    model.policy.action_net.load_state_dict(ck["action_net"], strict=True)
    print(f"已从 {path} 加载 PPO 主干+策略头续训")


def load_bc_init(model, path):
    """BC 检查点移植:主干 + policy 头精确迁移,value 头随机。

    BC 是 75 平面(83 通道 stem)训练的;统一网络是 91 平面(99 通道)。
    在 [75:91) 插 16 个零权重通道——BC 从未见过 oracle 输入,零权重
    使 oracle 段置零时输出与 BC 逐位一致(函数保持移植)。
    action_net = Linear(3*34, 109):前 2*34 列搬 BC p_fc,后 1*34 列
    (value 特征)置零 → PPO 初始策略 == BC 策略(mask 语义同)。
    """
    ck = torch.load(path, map_location="cpu", weights_only=True)
    sd = dict(ck["state_dict"])
    w = sd["stem.0.weight"]
    if w.shape[1] == N_PLANES + N_SCALARS:
        pad = torch.zeros(w.shape[0], N_PLANES_ORACLE - N_PLANES, w.shape[2])
        sd["stem.0.weight"] = torch.cat([w[:, :N_PLANES], pad, w[:, N_PLANES:]], 1)
    assert sd["stem.0.weight"].shape[1] == N_PLANES_ORACLE + N_SCALARS, \
        f"stem 通道数 {sd['stem.0.weight'].shape[1]} 无法对齐 {N_PLANES_ORACLE + N_SCALARS}"
    model.policy.features_extractor.net.load_state_dict(sd, strict=False)
    n_feat = model.policy.action_net.in_features
    aw = torch.zeros(model.policy.action_net.out_features, n_feat)
    aw[:, :sd["p_fc.weight"].shape[1]] = sd["p_fc.weight"]
    model.policy.action_net.load_state_dict(
        {"weight": aw, "bias": sd["p_fc.bias"]})
    print(f"已从 {path} 移植 BC 主干+policy 头(oracle 零填充,value 头随机)")


def build_opponent_pool(spec, ckpts):
    """Parses a league spec 'name=weight,name=weight' into pool assets.

    ``legacy``/``shape-v2`` are built-in heuristic evaluators; every other
    name must be supplied via ``ckpts[name]=checkpoint_path`` (loaded once as a
    frozen policy via :func:`mj.evaluate.policy_player`).  Historical RL / BC
    checkpoints are the agent's own prior selves => self-play opponent pool.
    Returns ``(names, weights, pool)`` in spya presentation order.
    """
    names, weights, pool = [], [], {}
    for item in (chunk.strip() for chunk in spec.split(",") if chunk.strip()):
        name, _, weight = item.partition("=")
        weights.append(float(weight) if weight else 1.0)
        names.append(name)
        if name in ("legacy", "shape-v2"):
            from .bot import choose_action
            pool[name] = choose_action
        elif name in ckpts:
            from .evaluate import policy_player
            pool[name] = policy_player(ckpts[name])
        else:
            raise ValueError(
                f"unknown opponent {name!r}; pass --opponent-ckpt {name}=<path>")
    return names, weights, pool


def league_lineup(names, weights, rng):
    """Deterministically draw a 3-opponent lineup from the weighted pool."""
    total = sum(weights)
    lineup = []
    for _ in range(3):
        x = rng.random() * total
        for name, weight in zip(names, weights):
            x -= weight
            if x < 0:
                lineup.append(name)
                break
        else:
            lineup.append(names[-1])
    return lineup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=200_000)
    ap.add_argument("--blocks", type=int, default=2)
    ap.add_argument("--width", type=int, default=64)
    ap.add_argument("--n-envs", type=int, default=8)
    ap.add_argument("--n-steps", type=int, default=256)
    ap.add_argument("--bs", type=int, default=512)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--target-kl", type=float, default=0.03,
                    help="早停阈值(0=关);稀疏胜负下高 KL 会破坏 BC 策略")
    ap.add_argument("--oracle-anneal", type=float, default=0.5)
    ap.add_argument("--shape-k", type=float, default=0.02,
                    help="势函数 shaping 系数(0=关)")
    ap.add_argument("--you-cai-bi-kao", action="store_true",
                    help="有财必拷响(手有财神须爆头/杠开才可胡)")
    ap.add_argument("--ent-coef", type=float, default=0.001)
    ap.add_argument("--bc-reg", type=float, default=0.0,
                    help="BC 先验 KL 正则系数 λ(0=关);>0 时需 --init")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--init", default=None, help="BC/PPO checkpoint 初始化")
    ap.add_argument("--out", default="runs/ppo0")
    ap.add_argument("--opponents", default=None,
                    help="league 对手池 spec 'name=weight,...' "
                         "(legacy/shape-v2 内置,其余配 --opponent-ckpt)")
    ap.add_argument("--opponent-ckpt", action="append", default=None,
                    help="name=path 冻结对手 checkpoint(this self 的历史,自博弈)")
    ap.add_argument("--league-seed", type=int, default=0)
    ap.add_argument("--threads", type=int, default=0,
                    help="torch 线程数;8 物理核机器建议 8(16 会 oversubscribe)")
    ap.add_argument("--subproc", action="store_true",
                    help="SubprocVecEnv 并行环境步进(默认 Dummy 顺序)")
    args = ap.parse_args()

    if args.threads:
        torch.set_num_threads(args.threads)
    os.makedirs(args.out, exist_ok=True)

    from stable_baselines3.common.monitor import Monitor
    from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv

    pool = None
    lineup_rng = None
    if args.opponents:
        ckpts = {}
        for item in (args.opponent_ckpt or []):
            name, _, path = item.partition("=")
            if name and path:
                ckpts[name] = path
        names, weights, pool = build_opponent_pool(args.opponents, ckpts)
        lineup_rng = np.random.default_rng(args.league_seed)

    def make_env(i):
        opponents = None
        if pool is not None:
            lineup = league_lineup(names, weights, lineup_rng)
            opponents = [pool[name] for name in lineup]
        return MahjongEnv(seed=args.seed * 1000 + i, opponents=opponents,
                          shape_k=args.shape_k,
                          you_cai_bi_kao=args.you_cai_bi_kao)

    # 子进程 venv:环境步进(含 bot 决策的 shanten dfs)并行化,
    # 每进程独立记忆化缓存,不再受单线程 rollout 瓶颈
    if args.subproc:
        venv = SubprocVecEnv(
            [lambda i=i: Monitor(make_env(i)) for i in range(args.n_envs)],
            start_method="fork")
        raw_envs = []
    else:
        raw_envs = [make_env(i) for i in range(args.n_envs)]
        venv = DummyVecEnv([lambda e=e: Monitor(e) for e in raw_envs])

    def lr_sched(progress_remaining):
        # progress_remaining: 1→0;退火完成后 lr 降为 1/10
        p_done = 1.0 - progress_remaining
        return args.lr if p_done < args.oracle_anneal else args.lr / 10.0

    model = BCPriorPPO(
        "MlpPolicy",
        venv,
        bc_reg=args.bc_reg,
        policy_kwargs=dict(
            features_extractor_class=NetExtractor,
            features_extractor_kwargs=dict(blocks=args.blocks, width=args.width),
            net_arch=dict(pi=[], vf=[128, 128]),
        ),
        learning_rate=lr_sched,
        n_steps=args.n_steps,
        batch_size=args.bs,
        gamma=1.0,
        gae_lambda=0.95,
        ent_coef=args.ent_coef,
        vf_coef=0.5,
        max_grad_norm=0.5,
        target_kl=args.target_kl if args.target_kl > 0 else None,
        seed=args.seed,
        verbose=1,
        device="cpu",
    )
    if args.init:
        ck = torch.load(args.init, map_location="cpu", weights_only=True)
        assert (ck["blocks"], ck["width"]) == (args.blocks, args.width), \
            f"init blocks/width {ck['blocks']}/{ck['width']} 与训练参数不符"
        if "net" in ck and "action_net" in ck:
            load_ppo_init(model, args.init)
        else:
            load_bc_init(model, args.init)
        if args.bc_reg > 0:
            model.set_bc_reference()
            print(f"BC 先验正则开启:λ={args.bc_reg}")

    t0 = time.time()
    model.learn(total_timesteps=args.steps, progress_bar=False,
                callback=[FreezeBNCallback(),
                          OracleAnnealCallback(venv, raw_envs, args.oracle_anneal),
                          SaveNetCallback(args.out, args.blocks, args.width)])
    print(f"完成 {args.steps} 步,用时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
