#!/usr/bin/env python3
"""Generate small, explicitly offline rollout teacher artifacts.

This command is an evidence-producing harness, not an online strategy switch.
It defaults to one source state so exploratory runs remain bounded; the
frozen release plan lives in the OpenSpec artifact and must be passed
explicitly for larger studies.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mj.decision.context import PublicDecisionContext
from mj.decision.profile import ProfileSpec
from mj.rollout.evaluator import PairedTeacher
from mj.rollout.teacher_data import teacher_artifact
from mj.game import Game


def _complete_public_context(game, seat):
    """Use only public history/state fields to make a teacher context.

    The generated source game is not used for its hidden hands or wall.  Its
    action-chain counters are public-history state; callers using a platform
    log must provide these counters only when the log proves them.
    """
    context = PublicDecisionContext.from_game(game, seat)
    return context.replace(
        chain_counts=tuple(int(x) for x in game.chain),
        chain_piao_counts=tuple(int(x) for x in game.chain_piao),
        rollout_valid=True, missing_fields=(), unsupported=())


def run(seed=242048, seat=0, dealer=0, ycbk=False, n0=32, batch=32,
        nmax=512, output=None):
    game = Game(seed=seed, dealer=dealer, you_cai_bi_kao=ycbk)
    # Advance the source only to the requested initial root.  No hidden source
    # material is passed to BeliefSampler; it independently samples worlds.
    context = _complete_public_context(game, seat)
    actions = context.legal_discards
    profile = ProfileSpec.shape_v2_discard()
    teacher = PairedTeacher(
        context, profile_fingerprint=profile.fingerprint, seed=seed,
        belief_version=profile.belief_version, n0=n0, batch=batch,
        nmax=nmax)
    result = teacher.evaluate(actions)
    value = teacher_artifact(
        result, context=context, profile=profile,
        metadata={"source_game_seed": seed, "seat": seat, "dealer": dealer,
                  "you_cai_bi_kao": bool(ycbk), "oracle": False,
                  "source_group": f"seed:{seed}"})
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if output:
        with open(output, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=242048)
    parser.add_argument("--seat", type=int, default=0)
    parser.add_argument("--dealer", type=int, default=0)
    parser.add_argument("--you-cai-bi-kao", action="store_true")
    parser.add_argument("--n0", type=int, default=32)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--nmax", type=int, default=512)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    value = run(args.seed, args.seat, args.dealer, args.you_cai_bi_kao,
                args.n0, args.batch, args.nmax, args.output)
    print(json.dumps({key: value[key] for key in (
        "status", "context_hash", "profile_fingerprint", "best_action",
        "runner_up", "sample_count", "attempted_samples", "failed_samples",
        "ambiguous", "stop_reason", "artifact_fingerprint")
        if key in value}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
