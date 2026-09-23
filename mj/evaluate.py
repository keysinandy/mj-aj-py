"""自博弈评估:启发式 bot / 随机 / 任意可调用玩家互对。

player 取值:True=启发式 bot,False/None=随机(模拟平台超时自动胡
兜底),callable=(g, seat) -> action。
"""

import random
import sys
import time

import numpy as np

from mj.game import Game, HU
from mj.bot import choose_action
from mj.legacy_eval import DEFAULT_BOT_EVALUATOR


def _pick(player, g, seat, evaluator=DEFAULT_BOT_EVALUATOR):
    if player is True:
        return choose_action(g, seat, evaluator=evaluator)
    if callable(player):
        return player(g, seat)
    acts = g.legal_actions()
    return HU if HU in acts else random.choice(acts)


def _play_game(players, seed, dealer=0, you_cai_bi_kao=False,
               evaluator=DEFAULT_BOT_EVALUATOR):
    g = Game(seed=seed, dealer=dealer, you_cai_bi_kao=you_cai_bi_kao)
    while not g.done:
        seat = g.current_seat()
        act = _pick(players[seat], g, seat, evaluator=evaluator)
        acts = g.legal_actions()
        if act not in acts:
            act = random.choice(acts)
        g.step(act)
    return g


def run_games(players, n=200, seed0=0, dealer="rotate", you_cai_bi_kao=False,
              evaluator=DEFAULT_BOT_EVALUATOR):
    """players: 长度 4 的玩家列表(见模块 docstring)。
    dealer: "rotate" 逐局轮转庄家(默认,消除庄家 ×8 收付偏置),
    或指定固定座位;you_cai_bi_kao: 有财必拷响开关。"""
    stats = {
        "wins": [0] * 4,
        "score": [0] * 4,
        "mults": {},
        "draws": 0,
    }
    t0 = time.time()
    for i in range(n):
        d = i % 4 if dealer == "rotate" else int(dealer)
        g = _play_game(players, seed0 + i, dealer=d,
                       you_cai_bi_kao=you_cai_bi_kao,
                       evaluator=evaluator)
        if g.result:
            seat, mult, _parts = g.result
            stats["wins"][seat] += 1
            stats["mults"][mult] = stats["mults"].get(mult, 0) + 1
        else:
            stats["draws"] += 1
        for s in range(4):
            stats["score"][s] += g.scores[s]
    stats["elapsed"] = time.time() - t0
    return stats


def fair_match(player, n=192, seed0=0, you_cai_bi_kao=False,
               evaluator=DEFAULT_BOT_EVALUATOR):
    """player 轮转四座位、庄家独立轮转((座位,庄家) 16 组合均衡)
    对抗启发式 bot,返回 player 视角统计。

    n 最好是 16 的倍数(默认 192 = 每组合 12 局)。
    """
    wins = [0] * 4
    pw = draws = 0
    score = 0.0
    opp_score = 0.0
    mults = {}
    t0 = time.time()
    for i in range(n):
        seat = i % 4
        dealer = (i // 4) % 4
        players = [True] * 4
        players[seat] = player
        g = _play_game(players, seed0 + i, dealer=dealer,
                       you_cai_bi_kao=you_cai_bi_kao,
                       evaluator=evaluator)
        if g.result:
            w, mult, _parts = g.result
            wins[w] += 1
            if w == seat:
                pw += 1
                mults[mult] = mults.get(mult, 0) + 1
        else:
            draws += 1
        score += g.scores[seat]
        opp_score += sum(g.scores[s] for s in range(4) if s != seat) / 3
    return {
        "games": n,
        "wins": pw,
        "win_rate": pw / n,
        "draws": draws,
        "avg_score": score / n,
        "opp_avg_score": opp_score / n,
        "wins_by_seat": wins,
        "mults": dict(sorted(mults.items())),
        "elapsed": time.time() - t0,
    }


def report_fair(name, st):
    print(f"== {name} ({st['games']} 局, {st['elapsed']:.0f}s) ==")
    print(f"胜 {st['wins']} ({st['win_rate']:.1%})  流局 {st['draws']}")
    print(f"平均得分 {st['avg_score']:+.2f} vs 对手 {st['opp_avg_score']:+.2f}")
    print(f"各座位和牌(全桌口径) {st['wins_by_seat']}  player 倍率 {st['mults']}")
    return st


def report(name, stats, n):
    print(f"== {name} ({n} 局, {stats['elapsed']:.0f}s) ==")
    print(f"和牌分布: {stats['wins']}  流局: {stats['draws']}")
    print(f"总得分: {stats['score']}  平均: {[round(x / n, 2) for x in stats['score']]}")
    print(f"倍率分布: {dict(sorted(stats['mults'].items()))}")
    print()
    return stats


def policy_player(ckpt, device="cpu", temperature=0.0):
    """加载 checkpoint(BC 的 best.pt 或 PPO 的 final.pt),返回
    (g, seat) -> 合法 mask 内策略动作。temperature=0 取 argmax,
    >0 按温度采样(自博弈数据多样性用)。

    两种 checkpoint 格式:
    - BC:{"state_dict": Net 全量, ...} → logits = Net 前向
    - PPO:{"net": 主干, "action_net": 独立 policy 头, ...} →
      logits = action_net(cat(p 特征, v 特征))(train_ppo 布局)
    obs 布局差异自动处理:BC 输入 75 平面(无 oracle);PPO 输入
    91 平面(oracle 段推理时置零)。
    """
    import torch

    from mj.features import (
        N_PLANES, N_PLANES_ORACLE, N_SCALARS, extract, legal_mask, flat_to_action,
    )
    from mj.model import Net

    ck = torch.load(ckpt, map_location=device, weights_only=True)
    # 输入宽度从 checkpoint 推断:BC(75 平面)与 PPO(91 平面,
    # 含 oracle 通道,推理时置零)的 Net 同构不同宽,按 stem 形状取
    n_planes = ck["state_dict"]["stem.0.weight"].shape[1] - N_SCALARS \
        if "state_dict" in ck else ck["net"]["stem.0.weight"].shape[1] - N_SCALARS
    model = Net(blocks=ck["blocks"], width=ck["width"],
                n_planes=n_planes).to(device)
    if "state_dict" in ck:  # BC 格式:Net 自带 policy 头
        model.load_state_dict(ck["state_dict"])
        action_net = None
    else:  # PPO 格式:policy 头是独立 action_net(cat(p,v) 特征)
        model.load_state_dict(ck["net"])
        action_net = torch.nn.Linear(
            ck["action_net"]["weight"].shape[1],
            ck["action_net"]["weight"].shape[0]).to(device)
        action_net.load_state_dict(ck["action_net"])
        action_net.eval()
    model.eval()

    def play(g, seat):
        planes, scalars = extract(g, seat)
        mask = legal_mask(g)
        if n_planes > planes.shape[0]:  # BC 老数据宽 75,补零到网络宽度
            pad = np.zeros((n_planes - planes.shape[0], 34), dtype=np.float32)
            planes = np.concatenate([planes, pad])
        with torch.no_grad():
            if action_net is None:
                logits, _v = model(
                    torch.as_tensor(planes[None], dtype=torch.float32, device=device),
                    torch.as_tensor(scalars[None], dtype=torch.float32, device=device),
                )
            else:
                b = torch.as_tensor(scalars[None], dtype=torch.float32, device=device)
                bb = b.unsqueeze(-1).expand(-1, -1, planes.shape[-1])
                x = model.blocks(model.stem(torch.cat(
                    [torch.as_tensor(planes[None], dtype=torch.float32, device=device), bb], 1)))
                feats = torch.cat([torch.relu(model.p_conv(x)).flatten(1),
                                   torch.relu(model.v_conv(x)).flatten(1)], 1)
                logits = action_net(feats)
        m = torch.as_tensor(mask, device=device)
        logits = logits[0].masked_fill(~m, float("-inf"))
        if temperature > 0:
            probs = torch.softmax(logits / temperature, dim=-1)
            a = int(torch.multinomial(probs, 1))
        else:
            a = int(logits.argmax())
        return flat_to_action(a)

    return play


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="离线麻将策略评估")
    ap.add_argument("n", nargs="?", type=int, default=200,
                    help="对局数(兼容旧的第一个位置参数)")
    ap.add_argument("--bot-evaluator",
                     choices=("legacy", "legacy-two-ply-v1", "legacy-v1",
                              "legacyV2", "legacy-v2",
                              "weighted-two-ply-frontier-v1", "shape-v1"),
                    default=DEFAULT_BOT_EVALUATOR)
    args = ap.parse_args()
    n = args.n
    # 座位 0 = 启发式 bot,其余随机
    report("1 bot vs 3 random",
           run_games([True, False, False, False], n=n,
                     evaluator=args.bot_evaluator), n)
    # 四家全 bot(自博弈基线,和牌率应显著高于随机)
    report("4 bots", run_games([True, True, True, True], n=n,
                                evaluator=args.bot_evaluator), n)
