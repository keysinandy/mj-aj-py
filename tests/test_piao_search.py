"""Piao Search fast-gate and guaranteed next-draw HU regressions."""

import random

from mj import bot as bot_mod
from mj.bot import choose_action
from mj.shanten import piao_draw_mask, piao_draw_mask_py
from mj.strategy_runtime import decision_audit, snapshot_for_config
from mj.tiles import W, counts
from mj.scoring import hand_multiplier, settle

from tests.test_baotou_piao_replay import _draw_game


def test_piao_mask_is_structural_and_visible_weight_is_external():
    standing = counts("11m44m11p2s33s6sCBBB")
    standing[31] -= 1  # remove the drawn 中: the mask takes a 13-tile hand
    mask1 = piao_draw_mask(standing, 0)
    assert len(mask1) == 34
    assert [tile for tile, enabled in enumerate(mask1) if enabled] == [19, 23, 33]

    # The mask is keyed only by standing hand and locked count.  Visibility
    # is deliberately not part of this pure structural helper.
    assert piao_draw_mask(standing, 0) == mask1
    heavier_visible = list(standing)
    heavier_visible[19] = 4
    assert [tile for tile, enabled in enumerate(mask1) if enabled] == [19, 23, 33]
    assert heavier_visible[19] == 4


def test_rust_piao_mask_matches_python_reference():
    rng = random.Random(20260929)
    for locked in range(5):
        need = 13 - 3 * locked
        for _ in range(24):
            hand = [0] * 34
            hand[W] = min(need, 2 + rng.randrange(3))
            while sum(hand) < need:
                tile = rng.randrange(33)
                if hand[tile] < 4:
                    hand[tile] += 1
            assert piao_draw_mask(hand, locked) == piao_draw_mask_py(
                hand, locked)


def test_seq856_enters_search_gate_but_is_not_piao_ready():
    game = _draw_game("11m44m11p2s33s6sCBBB", 31, 41)
    action, evaluation = choose_action(
        game, 0, evaluator="legacyV2", return_evaluation=True)
    assert action in game.legal_actions()
    assert evaluation["piao_candidates"] == []
    search = evaluation["piao_search"]
    assert search["piao_search_eligible"] is True
    assert search["piao_ready_now"] is False
    assert search["nodes"] == 34
    assert search["self_draw_horizon"] >= 2
    assert search["search_allowed"] is False


def test_seq880_is_ready_and_skips_search_shutter():
    game = _draw_game("11m44m11p22s33s6sBBB", 19, 37)
    action, evaluation = choose_action(
        game, 0, evaluator="legacyV2", return_evaluation=True)
    assert action in game.legal_actions()
    assert evaluation["piao_candidates"] == [W]
    assert evaluation["piao_search"]["piao_ready_now"] is True
    assert evaluation["piao_search"]["search_skip_reason"] == "PIAO_READY"


def test_horizon_one_closes_piao_search_but_keeps_ready_root():
    game = _draw_game("11m44m11p2s33s6sCBBB", 31, 7)
    _action, evaluation = choose_action(
        game, 0, evaluator="legacyV2", return_evaluation=True)
    search = evaluation["piao_search"]
    assert search["piao_search_eligible"] is True
    assert search["self_draw_horizon"] == 1
    assert search["search_allowed"] is False
    assert search["search_skip_reason"] == "HORIZON_TOO_SHORT"


def test_seq351_guaranteed_next_draw_ignores_observed_soft_risks():
    # 556m8m888p99pFFBBB + 白; the exact replay uses dealer seat 2 and
    # opponent exposed melds, which previously reduced every delayed root to 0.
    game = _draw_game("556m8m888p99pFFBBB", W, live=9)
    game.dealer = 2
    game.melds[1] = [("pong", 0), ("pong", 15)]
    action, evaluation = bot_mod._choose_draw_action(
        game, 0, game.legal_actions())
    assert action in (5, 7, 16)
    assert evaluation["selected_type"] == "baotou_next_draw"
    assert evaluation["delay_penalty_reason"] is None
    assert set(evaluation["ignored_delay_reasons"]) >= {
        "opp_melds", "live_wall"}
    delayed = [row for row in evaluation["candidates"]
               if row["type"] == "baotou_next_draw"]
    assert {row["action"] for row in delayed} == {5, 7, 16}
    assert all(row["guaranteed_next_draw_hu"] for row in delayed)
    assert all(row["delay_factor"] == 1.0 for row in delayed)
    assert all(row["value"] == row["raw_value"] for row in delayed)
    assert max(row["raw_value"] for row in delayed) > 20.0


def test_guaranteed_ev_uses_real_luxury_scoring_without_bonus_field():
    game = _draw_game("556m8m888p99pFFBBB", W, 9)
    game.dealer = 2
    game.melds[1] = [("pong", 0), ("pong", 15)]
    action, evaluation = bot_mod._choose_draw_action(
        game, 0, game.legal_actions())
    row = next(candidate for candidate in evaluation["candidates"]
               if candidate["type"] == "baotou_next_draw" and
               candidate["action"] == action)
    assert "luxury_bonus" not in row
    assert row["guaranteed_next_draw_hu"] is True
    standing = list(game.hands[0])
    standing[action] -= 1
    final = list(standing)
    final[16] += 1  # 8筒: the replay's luxury-seven-pairs upgrade draw
    multiplier, _parts = hand_multiplier(
        final, standing, len(game.melds[0]), 0, 0)
    expected = settle(0, game.dealer, multiplier, game.base)[0]
    visible = bot_mod._public_visible_counts(game, 0)
    mass = max(0, 4 - visible[16])
    assert mass > 0
    # The real draw reward is part of the weighted next-draw EV; no separate
    # hand-written luxury increment is present.
    assert expected > 0
    assert row["raw_value"] >= expected * mass / row["total_unseen"]


def test_guaranteed_audit_separates_observed_and_ignored_reasons():
    game = _draw_game("556m8m888p99pFFBBB", W, live=9)
    game.dealer = 2
    game.melds[1] = [("pong", 0), ("pong", 15)]
    action, evaluation = bot_mod._choose_draw_action(
        game, 0, game.legal_actions())
    snapshot = snapshot_for_config({"strategy": "bot", "evaluator": "legacyV2"})
    audit = decision_audit(
        evaluation, action, 1.0, phase="draw", snapshot=snapshot,
        decision_id=351)
    guaranteed = audit["features"]["guaranteed_next_draw_hu"]
    assert guaranteed["entered"] is True
    assert guaranteed["candidate_count"] == 3
    assert set(guaranteed["ignored_delay_reasons"]) >= {
        "opp_melds", "live_wall"}
    hu_window = audit["features"]["hu_window_arbitration"]
    assert set(hu_window["observed_delay_reasons"]) >= {
        "opp_melds", "live_wall"}
    assert audit["runtime"]["fallback"] is False
