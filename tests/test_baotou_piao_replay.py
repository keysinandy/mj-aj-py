"""Regression fixtures for the 2026-09-29 round-4 HU window."""

import json
from pathlib import Path

from mj.bot import choose_action
from mj.game import Game
from mj.strategy_runtime import decision_audit, snapshot_for_config
from mj.tiles import W, counts


REPLAY = Path(
    "local/games/20260929/"
    "u_9812ba08fe2f_a_d7f2f3368387_r1_b9_t0.jsonl"
)


def _draw_game(spec, drawn, live):
    game = Game.__new__(Game)
    hand = counts(spec)
    assert sum(hand) == 14
    game.hands = [hand, [0] * 34, [0] * 34, [0] * 34]
    game.melds = [[], [], [], []]
    game.discards = [[], [], [], []]
    game.dealer = 0
    game.base = 1
    game.you_cai_bi_kao = False
    game.wall = [0] * (20 + int(live))
    game.drawn = [drawn, None, None, None]
    game.chows = [0] * 4
    game.turn = 0
    game.phase = "discard"
    game.pending = None
    game.freeze = 0
    game.freezer = None
    game.chain = [0] * 4
    game.chain_piao = [0] * 4
    game.scores = [0] * 4
    game.done = False
    game.result = None
    game._kong_draw = False
    return game


def _legacy_v2(game):
    return choose_action(game, 0, evaluator="legacyV2",
                         return_evaluation=True)


def test_seq856_has_no_piao_candidate():
    # H0 + 中: 1w2 4w2 1b2 2t 3t2 6t 白3.
    game = _draw_game("11m44m11p2s33s6sCBBB", 31, 41)
    action, evaluation = _legacy_v2(game)
    assert action in game.legal_actions()
    assert evaluation["decision_scope"] == "hu_window_arbitration"
    assert evaluation["piao_candidates"] == []
    assert "piao_discard" not in {
        row["type"] for row in evaluation["hu_window_candidates"]
    }


def test_seq880_and_followups_keep_piao_in_the_root_set():
    # At seq880 the 6t is still in the 14-card window; after it is discarded,
    # every listed follow-up draw keeps the same piao-capable structure.
    cases = [
        (880, "11m44m11p22s33s6sBBB", 19, 37),
        (904, "11m44m11p22s33sBBB7m", 6, 33),
        (943, "11m44m11p22s33sBBBS", 28, 26),
        (967, "11m44m11p22s33sBBB8s", 25, 22),
        (991, "11m44m11p22s33sBBBE", 27, 18),
        (1015, "11m44m11p22s33sBBB7p", 15, 14),
        (1039, "11m44m11p22s33sBBB6p", 14, 10),
        (1063, "11m44m11p22s33sBBBF", 32, 6),
    ]
    snapshot = snapshot_for_config({"strategy": "bot", "evaluator": "legacyV2"})
    for seq, spec, drawn, live in cases:
        game = _draw_game(spec, drawn, live)
        action, evaluation = _legacy_v2(game)
        assert action in game.legal_actions(), seq
        assert evaluation["decision_scope"] == "hu_window_arbitration", seq
        assert evaluation["piao_candidates"] == [W], seq
        assert "piao_discard" in {
            row["type"] for row in evaluation["hu_window_candidates"]
        }, seq
        assert evaluation["reason"] != "hu_baotou_next_draw_override", seq
        audit = decision_audit(
            evaluation, action, 1.0, phase="draw", snapshot=snapshot,
            decision_id=seq,
        )
        assert audit["decision_scope"] == "hu_window_arbitration", seq
        assert audit["features"]["baotou_scope"]["entered"] is True, seq
        assert audit["features"]["hu_window_arbitration"]["entered"] is True
        assert audit["features"]["hu_window_arbitration"]["selected_type"] \
            == evaluation["selected_type"]


def test_seq1070_records_opponent_win_risk():
    assert REPLAY.exists()
    ended = []
    for raw in REPLAY.read_text(encoding="utf-8").splitlines():
        record = json.loads(raw)
        for event in record.get("events", ()):
            if event.get("seq") == 1070 and event.get("type") == "round_ended":
                ended.append(event)
    assert ended
    assert ended[-1]["seat"] == 1
    assert ended[-1]["data"]["draw"] is False
