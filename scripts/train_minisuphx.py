#!/usr/bin/env python3
"""Mini-Suphx campaign 编排 CLI(任务 12.1):create/status/bc/dagger/ppo/gate/promote。

单机与双机共用同一状态机(mj/training/campaign.py);这里只是薄 CLI。

用法示例:
  # 创建 campaign
  python scripts/train_minisuphx.py create --campaign demo --out runs/minisuphx/demo
  # 30k BC → BC0 anchor
  python scripts/train_minisuphx.py bc --out runs/minisuphx/demo --bc-data data/bc \
      --epochs 10 --device cuda
  # DAgger D1(learned 执行 30%)后训练 BC-v1
  python scripts/train_minisuphx.py dagger --out runs/minisuphx/demo \
      --dagger-data data/dagger --round D1 --learned-ckpt runs/minisuphx/demo/bc0/best.pt \
      --games 3000
  # 一期 on-policy PPO(单机)
  python scripts/train_minisuphx.py ppo --out runs/minisuphx/demo \
      --policy runs/minisuphx/demo/bc-v1/best.pt --rollout 16384 --device cuda
  # paired gate / promote
  python scripts/train_minisuphx.py gate  --out runs/minisuphx/demo \
      --ckpt runs/minisuphx/demo/policies/policy_000001.pt --level smoke
  python scripts/train_minisuphx.py promote --out runs/minisuphx/demo \
      --ckpt runs/minisuphx/demo/policies/policy_000001.pt --level full
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mj.training import campaign as cam  # noqa: E402
from mj.training.minisuphx_manifest import git_head  # noqa: E402


def _load(out: str) -> cam.CampaignState:
    return cam.CampaignState.load(os.path.join(out, "campaign.json"))


def _status(out: str):
    state = _load(out)
    print(json.dumps(state.payload(), ensure_ascii=False, indent=2))


def _render(state: cam.CampaignState):
    print(f"campaign={state.campaign_id} phase={state.phase} "
          f"feature={state.feature_contract} policy_v={state.policy_version}")
    if state.anchor:
        print(f"  anchor [{state.anchor['kind']}] {state.anchor['sha256'][:12]} "
              f"{state.anchor['path']}")
    for pol in state.policies:
        print(f"  policy_v{pol['version']} {pol['sha256'][:12]} {pol['path']}")
    for r in state.dagger_rounds:
        print(f"  dagger {r['round']}: n={r['n_samples']} "
              f"exec_prob={r['learned_exec_prob']} disc_rate={r['discrepancy_rate']:.3f}")
    for p in state.paired:
        print(f"  paired {p['level']}: verdict={p['verdict']} "
              f"delta={p['mean_delta']:+.3f} ci={p['ci95']}")
    if state.champion:
        print(f"  champion {state.champion['sha256'][:12]} {state.champion['path']}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="train_minisuphx")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create", help="创建 campaign 目录 + run manifest")
    p.add_argument("--campaign", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--blocks", type=int, default=6)
    p.add_argument("--width", type=int, default=128)
    p.add_argument("--seed", type=str, default="0-29999")
    p.add_argument("--feature", default=cam.FEATURE_PUBLIC)
    p.set_defaults(fn=_cmd_create)

    p = sub.add_parser("status", help="打印 campaign 状态")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=_cmd_status)

    p = sub.add_parser("resume", help="验证 campaign 状态并打印可恢复信息")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=_cmd_resume)

    p = sub.add_parser("bc", help="训练 BC checkpoint 并登记为 anchor")
    p.add_argument("--out", required=True)
    p.add_argument("--bc-data", required=True)
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--bs", type=int, default=512)
    p.add_argument("--device", default="cpu")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--kind", default="bc0")
    p.set_defaults(fn=_cmd_bc)

    p = sub.add_parser("dagger", help="生成一轮 DAgger shard 并登记")
    p.add_argument("--out", required=True)
    p.add_argument("--dagger-data", required=True)
    p.add_argument("--round", required=True, choices=("D1", "D2", "D3"))
    p.add_argument("--learned-ckpt", required=True)
    p.add_argument("--games", type=int, default=3000)
    p.add_argument("--exec-prob", type=float, default=None,
                   help="learned 执行概率;默认 D1=0.3/D2=0.6/D3=0.9")
    p.add_argument("--seed-start", type=int, default=0)
    p.add_argument("--hero", type=int, default=0)
    p.add_argument("--device", default="cpu")
    p.set_defaults(fn=_cmd_dagger)

    p = sub.add_parser("ppo", help="一期 on-policy PPO round")
    p.add_argument("--out", required=True)
    p.add_argument("--policy", required=True)
    p.add_argument("--rollout", type=int, default=16384)
    p.add_argument("--seed", type=int, default=100)
    p.add_argument("--hero", type=int, default=0)
    p.add_argument("--device", default="cpu")
    p.add_argument("--batch", type=int, default=1024)
    p.add_argument("--epochs", type=int, default=4)
    p.set_defaults(fn=_cmd_ppo)

    p = sub.add_parser("gate", help="paired gate(smoke/fast/full)")
    p.add_argument("--out", required=True)
    p.add_argument("--ckpt", required=True)
    p.add_argument("--level", choices=tuple(cam.GATE_LEVELS), default="smoke")
    p.add_argument("--device", default="cpu")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--games", type=int, default=None)
    p.add_argument("--required", type=int, default=None)
    p.set_defaults(fn=_cmd_gate)

    p = sub.add_parser("promote", help="full gate 通过才晋级 Champion")
    p.add_argument("--out", required=True)
    p.add_argument("--ckpt", required=True)
    p.add_argument("--level", choices=tuple(cam.GATE_LEVELS), default="full")
    p.add_argument("--device", default="cpu")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--required", type=int, default=None)
    p.set_defaults(fn=_cmd_promote)

    args = ap.parse_args(argv)
    return int(args.fn(args)) if callable(getattr(args, "fn", None)) else -1


def _cmd_create(args):
    lo, _, hi = (args.seed.partition("-"))
    cam.create(args.out, args.campaign, blocks=args.blocks, width=args.width,
               feature_contract_name=args.feature, git_commit=git_head(),
               seed_lo=int(lo), seed_hi=int(hi or lo))
    _render(_load(args.out))
    return 0


def _cmd_status(args):
    _render(_load(args.out))
    return 0


def _cmd_resume(args):
    state = _load(args.out)
    print(f"resume=ready campaign={state.campaign_id} phase={state.phase} "
          f"next_policy_v={state.policy_version} rollouts={len(state.rollouts)}")
    _render(state)
    return 0


def _cmd_bc(args):
    state = cam.CampaignState.load(os.path.join(args.out, "campaign.json"))
    best = cam.run_bc(state, args.bc_data, epochs=args.epochs, batch_size=args.bs,
                      device=args.device, seed=args.seed, anchor_kind=args.kind)
    print(f"BC anchor written: {best}")
    _render(state)
    return 0


def _cmd_dagger(args):
    state = _load(args.out)
    prob = args.exec_prob if args.exec_prob is not None else cam._TENOR[args.round]
    meta = cam.gen_dagger(state, args.dagger_data, args.round,
                          learned_ckpt=args.learned_ckpt,
                          learned_exec_prob=prob, games=args.games,
                          seed_start=args.seed_start, hero=args.hero,
                          device=args.device)
    print(f"dagger {args.round}: {meta['transition_count']} samples, "
          f"discrepancy_rate={meta['disagreement_rate']:.3f}")
    _render(state)
    return 0


def _cmd_ppo(args):
    state = _load(args.out)
    ckpt, stats = cam.run_ppo_round(
        state, policy_ckpt=args.policy, rollout_n=args.rollout, seed=args.seed,
        hero=args.hero, device=args.device, git_commit=git_head(),
        batch_size=args.batch, ppo_epochs=args.epochs)
    print(f"PPO update: policy_v{stats['policy_version']} loss={stats.get('loss')}")
    _render(state)
    return 0


def _cmd_gate(args):
    state = _load(args.out)
    report = cam.run_gate(state, args.ckpt, level=args.level, device=args.device,
                          seed=args.seed, games=args.games,
                          required_pairs=args.required)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _cmd_promote(args):
    state = _load(args.out)
    report = cam.promote(state, args.ckpt, level=args.level, device=args.device,
                         seed=args.seed, required_pairs=args.required)
    print(f"promotion({args.level}): {report['verdict']} "
          f"delta={report['mean_delta']:+.3f}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
