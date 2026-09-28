"""Regression coverage for the legacyV2 marginal structure admission guard."""

from unittest.mock import patch

from mj.bot import choose_discard
from mj.legacy_eval import LegacyTwoPlyProfile
from mj.strategy_runtime import _profile_from_config
from mj.structure_role import marginal_structure_role
from mj.tiles import counts


def _fixture_state():
    hand = [0] * 34
    for tile in (2, 6, 10, 11, 12, 12, 15, 21, 25, 26, 26):
        hand[tile] += 1
    visible = hand[:]
    for tile in (28, 17, 28, 0, 8, 18, 2, 32, 18, 28):
        visible[tile] += 1
    for tile, amount in ((27, 3), (30, 4), (9, 1), (10, 1),
                         (11, 1), (0, 3)):
        visible[tile] += amount
    return hand, visible


class _GoldenGame:
    def __init__(self, hand, visible):
        self.hands = [hand, [0] * 34, [0] * 34, [0] * 34]
        self.melds = [
            [("pong", 0)],
            [("pong", 27)],
            [("kong_closed", 30)],
            [("chow", 9)],
        ]
        self.discards = [[28, 17, 28, 0, 8, 18, 2, 32, 18, 28],
                         [], [], []]
        self.freeze = 0
        self.freezer = None
        self.phase = "discard"
        self.rules_version = "hangzhou-platform-guide-v34"
        self._visible = visible

    def visible_counts(self, seat):
        return list(self._visible)


def _golden_profile(**overrides):
    values = {
        "kernel": "python",
        "shape_guard_enabled": False,
        "shape_quality_enabled": False,
        "allow_partial": False,
        "node_budget": 5_000_000,
        "time_budget_ms": 5_000.0,
        "soft_budget_ms": 5_000.0,
        "hard_budget_ms": 5_000.0,
        "marginal_structure_guard_enabled": True,
    }
    values.update(overrides)
    return LegacyTwoPlyProfile.weighted_online(**values)


def test_899s_role_is_non_redundant_compound_loss():
    hand = counts("899s")
    role = marginal_structure_role(hand, 26, hand)
    assert role.lost_pair_option
    assert role.alternative_route_count_after < role.alternative_route_count_before
    assert not role.completed_meld_redundancy
    assert role.critical_compound_break
    assert role.loss_tier == "critical_compound"


def test_7899s_pair_loss_is_redundant_to_completed_789():
    hand = counts("7899s")
    role = marginal_structure_role(hand, 26, hand)
    assert role.lost_pair_option
    assert role.completed_meld_redundancy
    assert not role.critical_compound_break


def test_singleton_connectivity_uses_public_live_counts():
    middle = counts("5m")
    edge = counts("1m")
    middle_role = marginal_structure_role(middle, 4, middle)
    edge_role = marginal_structure_role(edge, 0, edge)
    assert middle_role.singleton_live_connectivity > edge_role.singleton_live_connectivity

    exhausted = middle[:]
    exhausted[3] = 4  # 4m
    exhausted[5] = 4  # 6m
    exhausted[2] = 4  # 3m
    exhausted[6] = 4  # 7m
    exhausted_role = marginal_structure_role(middle, 4, exhausted)
    assert exhausted_role.singleton_live_connectivity == 0


def test_hidden_opponent_tiles_do_not_change_role_or_admission():
    hand, visible = _fixture_state()
    first = _GoldenGame(hand, visible)
    second = _GoldenGame(hand, visible)
    second.hands[1][0] = 1
    profile = _golden_profile()
    first_action, first_info = choose_discard(
        first, 0, return_info=True, profile=profile)
    second_action, second_info = choose_discard(
        second, 0, return_info=True, profile=profile)
    assert first_action == second_action
    assert first_info["marginal_structure_guard"] == (
        second_info["marginal_structure_guard"])
    first_roles = {
        row["tile"]: row.get("marginal_loss_tier")
        for row in first_info["candidates"]
    }
    second_roles = {
        row["tile"]: row.get("marginal_loss_tier")
        for row in second_info["candidates"]
    }
    assert first_roles == second_roles


def test_golden_guard_blocks_singleton_and_completes_two_ply():
    hand, visible = _fixture_state()
    game = _GoldenGame(hand, visible)
    action, info = choose_discard(
        game, 0, return_info=True, profile=_golden_profile())

    assert info["marginal_structure_guard"]["frontier_singleton_blocked"]
    assert info["frontier_singleton_blocked"]
    assert info["role_guard_slack"] == 6
    assert info["weighted_two_ply_entered"]
    assert info["search_used"]
    assert info["future_nodes"] > 0
    assert info["complete"]
    assert action != 26
    assert any(
        row.get("admitted_by") == "marginal_structure_guard" and
        row.get("ukeire", 0) >= 76
        for row in info["candidates"]
    )
    assert len(info["marginal_structure_guard"]["admitted_tiles"]) <= 2
    assert len([row for row in info["candidates"]
                if row.get("admitted_by") in
                ("primary", "marginal_structure_guard") and
                row.get("complete")]) <= 3


def test_feature_off_keeps_legacy_singleton_action():
    hand, visible = _fixture_state()
    game = _GoldenGame(hand, visible)
    profile = _golden_profile(marginal_structure_guard_enabled=False)
    action, info = choose_discard(game, 0, return_info=True, profile=profile)
    assert action == 26
    assert info["level"] == "legacy-one-ply"
    assert info["short_circuit_reason"] == "frontier_singleton"
    assert not info["search_used"]
    assert info["marginal_structure_guard"]["skipped_reason"] == "feature_disabled"


def test_strategy_config_defaults_guard_on_and_allows_explicit_rollback():
    profile, _reaction = _profile_from_config(
        "bot", "legacyV2",
        {"marginal_structure_guard_enabled": True})
    assert profile.marginal_structure_guard_enabled
    default, _reaction = _profile_from_config("bot", "legacyV2", {})
    assert default.marginal_structure_guard_enabled
    rollback, _reaction = _profile_from_config(
        "bot", "legacyV2",
        {"marginal_structure_guard_enabled": False})
    assert not rollback.marginal_structure_guard_enabled


def test_anti_overfit_complete_hands_can_discard_9s_or_preserve_99():
    # Both are complete post-draw hands after the open PON.  The first keeps
    # a critical 55m route and reasonably discards 9s; the second has a useful
    # central 5m singleton and reasonably keeps redundant 99s.
    cases = (
        ("556m123p57899s", 26),
        ("58899m45p7899s", 4),
    )
    for spec, expected in cases:
        hand = counts(spec)
        game = _GoldenGame(hand, hand)
        action, info = choose_discard(
            game, 0, return_info=True,
            profile=_golden_profile(
                node_budget=100_000,
                time_budget_ms=1_000.0,
                soft_budget_ms=1_000.0,
                hard_budget_ms=1_000.0))
        rows = {row["tile"]: row for row in info["candidates"]}
        assert action == expected
        assert rows[26]["completed_meld_redundancy"]
        assert not rows[26]["critical_compound_break"]
        assert rows[4]["singleton_connectivity"] > 0


def test_role_guard_gap_above_slack_keeps_singleton():
    hand, visible = _fixture_state()
    game = _GoldenGame(hand, visible)
    profile = _golden_profile(marginal_structure_slack_by_shanten=(0, 0, 0, 0))
    action, info = choose_discard(game, 0, return_info=True, profile=profile)
    assert action == 26
    assert info["level"] == "legacy-one-ply"
    assert info["marginal_structure_guard"]["skipped_reason"] == (
        "no_role_preserving_challenger")


def test_guard_fallback_is_transactional_when_budget_is_unavailable():
    hand, visible = _fixture_state()
    game = _GoldenGame(hand, visible)
    profile = _golden_profile(
        node_budget=0, time_budget_ms=0.0,
        soft_budget_ms=0.0, hard_budget_ms=0.0)
    action, info = choose_discard(game, 0, return_info=True, profile=profile)
    assert action == 26
    assert not info["complete"]
    assert not info["search_used"]
    assert info["fallback_reason"]
    assert info["future_nodes"] == 0
    rows = [row for row in info["candidates"]
            if row.get("admitted_by") in
            ("primary", "marginal_structure_guard")]
    assert rows
    assert all(not row.get("complete") for row in rows)


def test_guard_fallback_records_missing_native_kernel():
    hand, visible = _fixture_state()
    game = _GoldenGame(hand, visible)
    profile = _golden_profile(kernel="rust")
    with patch("mj.legacy_eval.weighted_two_ply_frontier", None), \
            patch("mj.legacy_eval.WEIGHTED_TWO_PLY_KERNEL_VERSION", None):
        action, info = choose_discard(
            game, 0, return_info=True, profile=profile)
    assert action == 26
    assert not info["complete"]
    assert not info["search_used"]
    assert info["fallback_reason"] == "native_weighted_kernel_unavailable"
    assert all(row.get("future_ukeire") is None
               for row in info["candidates"]
               if row.get("admitted_by") in
               ("primary", "marginal_structure_guard"))
