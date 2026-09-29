"""Focused contracts for the opt-in legacyV2 speed-band frontier."""

import pytest

from mj.bot import choose_discard
from mj.legacy_eval import (
    FutureEvaluation,
    LegacyRootCandidate,
    LegacyTwoPlyProfile,
    SPEED_BAND_MIN_RATIO_BY_SHANTEN,
    SPEED_BAND_VERSION,
    _root_features,
    _speed_band_ratio,
    _weighted_root_key,
)
from mj.shanten import LEGACY_TWO_PLY_KERNEL_VERSION
from mj.shanten import shanten
from mj.strategy_runtime import _profile_from_config
from mj.tiles import counts


def _two_w_four_t_roots():
    # 14-tile standing hand from the replay fixture.  The extra public tiles
    # reproduce 57/17 for 2w and 48/15 for log-4s (4t).
    hand = list(counts("2337889m4556p479s"))
    visible = hand[:]
    for spec in ("9p", "9s", "1m", "ES", "8p"):
        for tile, amount in enumerate(counts(spec)):
            visible[tile] += amount
    roots = []
    for tile in (1, 21, 2, 7, 13):
        post = hand[:]
        post[tile] -= 1
        roots.append(LegacyRootCandidate(
            tile=tile, hand=tuple(post), shanten=shanten(post, 0),
            shape_loss=(5.0 if tile == 1 else 2.0 if tile == 21 else 0.0),
            feed_risk=0.0,
        ))
    return tuple(roots), tuple(visible)


def _speed_profile(**overrides):
    values = {
        "kernel": "python",
        "shape_quality_enabled": False,
        "shape_guard_enabled": False,
        "allow_partial": False,
        "speed_band_enabled": True,
        "pareto_frontier_enabled": True,
        "time_budget_ms": 1000.0,
        "soft_budget_ms": 1000.0,
        "hard_budget_ms": 1000.0,
    }
    values.update(overrides)
    return LegacyTwoPlyProfile.weighted_online(**values)


class _GoldenGame:
    def __init__(self, hand, visible):
        self.hands = [hand, [0] * 34, [0] * 34, [0] * 34]
        self.melds = [
            [("pong", 0)], [], [], [],
        ]
        self.discards = [[], [], [], []]
        self.freeze = 0
        self.freezer = None
        self.phase = "discard"
        self.rules_version = "hangzhou-platform-guide-v34"
        self._visible = visible

    def visible_counts(self, seat):
        return list(self._visible)


def test_899s_complete_search_does_not_keep_raw_speed_singleton():
    from tests.test_legacy_marginal_structure import _fixture_state
    hand, visible = _fixture_state()
    hand = list(hand)
    visible = list(visible)
    game = _GoldenGame(hand, visible)
    profile = _speed_profile(
        node_budget=5_000_000,
        time_budget_ms=5_000.0,
        soft_budget_ms=5_000.0,
        hard_budget_ms=5_000.0,
        max_frontier_candidates=3,
    )
    action, info = choose_discard(game, 0, return_info=True, profile=profile)
    rows = {row["tile"]: row for row in info["candidates"]}
    assert rows[26]["ukeire"] == 82
    assert rows[2]["ukeire"] == 77
    assert rows[2]["current_speed_ratio"] >= 0.78
    assert not rows[2]["speed_dominated"]
    assert info["weighted_two_ply_entered"]
    assert info["complete"]
    assert info["future_nodes"] > 0
    assert action != 26


@pytest.mark.skipif(
    not LEGACY_TWO_PLY_KERNEL_VERSION,
    reason="native weighted kernel is unavailable",
)
def test_899s_native_stage_a_uses_normalized_speed_certificate():
    from tests.test_legacy_marginal_structure import _fixture_state
    hand, visible = _fixture_state()
    game = _GoldenGame(list(hand), list(visible))
    profile = _speed_profile(
        kernel="rust",
        node_budget=100_000,
        time_budget_ms=50.0,
        soft_budget_ms=40.0,
        hard_budget_ms=50.0,
        max_frontier_candidates=3,
    )
    action, info = choose_discard(game, 0, return_info=True, profile=profile)
    assert info["complete"]
    assert not info["partial_accepted"]
    assert info["search_phase"] == "two_ply"
    assert info["future_nodes"] > 0
    assert action != 26


@pytest.mark.skipif(
    not LEGACY_TWO_PLY_KERNEL_VERSION,
    reason="native weighted kernel is unavailable",
)
def test_speed_band_python_native_certificate_parity():
    from tests.test_legacy_marginal_structure import _fixture_state
    hand, visible = _fixture_state()
    python_game = _GoldenGame(list(hand), list(visible))
    native_game = _GoldenGame(list(hand), list(visible))
    common = dict(
        node_budget=5_000_000,
        time_budget_ms=5_000.0,
        soft_budget_ms=5_000.0,
        hard_budget_ms=5_000.0,
        max_frontier_candidates=3,
    )
    python_profile = _speed_profile(kernel="python", **common)
    native_profile = _speed_profile(kernel="rust", **common)
    python_action, python_info = choose_discard(
        python_game, 0, return_info=True, profile=python_profile)
    native_action, native_info = choose_discard(
        native_game, 0, return_info=True, profile=native_profile)
    assert native_action == python_action
    assert native_info["complete"] and python_info["complete"]
    python_rows = {row["tile"]: row for row in python_info["candidates"]}
    native_rows = {row["tile"]: row for row in native_info["candidates"]}
    assert native_rows.keys() == python_rows.keys()
    for tile in native_rows:
        for field in (
            "future_improve_weight", "future_ukeire", "future_ukeire_types",
            "future_best_discards",
        ):
            assert native_rows[tile].get(field) == python_rows[tile].get(field), (
                tile, field)


def test_profile_fingerprints_speed_band_and_thresholds():
    profile = _speed_profile()
    assert profile.speed_band_version == SPEED_BAND_VERSION
    assert profile.speed_band_min_ratio_by_shanten == (
        *SPEED_BAND_MIN_RATIO_BY_SHANTEN,
    )
    assert profile.as_json()["speed_band_enabled"] is True
    assert profile.as_json()["pareto_frontier_enabled"] is True
    assert profile.fingerprint != LegacyTwoPlyProfile.weighted_online().fingerprint
    assert 35 / 57 < _speed_band_ratio(
        2, profile.speed_band_min_ratio_by_shanten)


def test_strategy_config_can_roll_out_speed_band_as_a_pair():
    profile, _reaction = _profile_from_config(
        "bot", "legacyV2",
        {"speed_band_enabled": True, "pareto_frontier_enabled": True},
    )
    assert profile.speed_band_enabled
    assert profile.pareto_frontier_enabled


def test_57_vs_48_stays_in_shanten_two_band_and_reaches_frontier():
    roots, visible = _two_w_four_t_roots()
    profile = _speed_profile()
    enriched, frontier, diagnostics = _root_features(
        roots, 0, visible, marginal_role_enabled=True,
        speed_band_enabled=True,
        speed_band_min_ratio_by_shanten=(
            profile.speed_band_min_ratio_by_shanten),
        pareto_frontier_enabled=True,
        max_frontier_candidates=3,
    )
    rows = {root.tile: root for root in enriched}
    assert rows[1].current_ukeire == 57
    assert rows[21].current_ukeire == 48
    assert rows[21].current_speed_ratio == 48 / 57
    assert rows[21].current_speed_ratio >= 0.82
    assert rows[21].in_competitive_speed_band
    assert not rows[21].speed_dominated
    assert not rows[21].pareto_dominated
    assert rows[1].shape_loss == 5.0
    assert rows[21].shape_loss == 2.0
    assert rows[1].feed_risk == rows[21].feed_risk == 0.0
    assert 21 in {root.tile for root in frontier}
    assert dict((root.tile, missing) for root, _ok, missing in diagnostics)[21] == ()
    assert rows[1].marginal_loss_tier != rows[21].marginal_loss_tier
    assert len(frontier) <= 3


def test_57_vs_48_full_two_ply_future_metrics_are_frozen():
    _roots, visible = _two_w_four_t_roots()
    game = _GoldenGame(list(counts("2337889m4556p479s")), list(visible))
    game.melds = [[], [], [], []]
    profile = _speed_profile(
        node_budget=5_000_000,
        time_budget_ms=5_000.0,
        soft_budget_ms=5_000.0,
        hard_budget_ms=5_000.0,
        max_frontier_candidates=3,
    )
    _action, info = choose_discard(
        game, 0, return_info=True, profile=profile)
    rows = {row["tile"]: row for row in info["candidates"]}
    assert info["weighted_two_ply_entered"]
    assert info["complete"]
    for tile in (1, 21):
        assert rows[tile]["complete"]
        assert rows[tile]["future_improve_weight"] is not None
        assert rows[tile]["future_ukeire_mean"] is not None
        assert rows[tile]["future_ukeire_types_mean"] is not None


def test_pareto_dominance_is_strict_and_structure_sensitive():
    roots, visible = _two_w_four_t_roots()
    profile = _speed_profile()
    enriched, _frontier, _diagnostics = _root_features(
        roots, 0, visible, marginal_role_enabled=True,
        speed_band_enabled=True,
        speed_band_min_ratio_by_shanten=(
            profile.speed_band_min_ratio_by_shanten),
        pareto_frontier_enabled=True,
        max_frontier_candidates=3,
    )
    rows = {root.tile: root for root in enriched}
    # 2w is faster but breaks a taatsu; it cannot dominate the connected 4t.
    assert not rows[1].pareto_dominated_by or 21 not in rows[1].pareto_dominated_by
    assert not rows[21].pareto_dominated
    # 5b is strictly worse than 8w in this fixture and is pruned.
    assert rows[13].pareto_dominated
    assert rows[13].pareto_dominated_by == (7,)


def test_cap_and_diagnostics_are_input_order_independent():
    roots, visible = _two_w_four_t_roots()
    profile = _speed_profile()

    def run(items):
        enriched, frontier, diagnostics = _root_features(
            items, 0, visible, marginal_role_enabled=True,
            speed_band_enabled=True,
            speed_band_min_ratio_by_shanten=(
                profile.speed_band_min_ratio_by_shanten),
            pareto_frontier_enabled=True,
            max_frontier_candidates=2,
        )
        return (
            tuple(root.tile for root in frontier),
            {root.tile: (
                root.speed_dominated, root.pareto_dominated,
                root.speed_dominated_by, root.pareto_dominated_by,
            ) for root in enriched},
            {root.tile: missing for root, _ok, missing in diagnostics},
        )

    assert run(roots) == run(tuple(reversed(roots)))


def test_band_comparator_allows_lower_marginal_loss_after_equal_future():
    roots, visible = _two_w_four_t_roots()
    profile = _speed_profile()
    enriched, frontier, _diagnostics = _root_features(
        roots, 0, visible, marginal_role_enabled=True,
        speed_band_enabled=True,
        speed_band_min_ratio_by_shanten=(
            profile.speed_band_min_ratio_by_shanten),
        pareto_frontier_enabled=True,
        max_frontier_candidates=3,
    )
    rows = {root.tile: root for root in enriched}
    future = {
        tile: FutureEvaluation(
            complete=True, future_improve_weight=20,
            future_ukeire_mean=100.0, future_ukeire_types_mean=10.0,
        )
        for tile in (1, 21)
    }
    assert min(
        (root for root in frontier if root.tile in future),
        key=lambda root: _weighted_root_key(
            root, future, speed_band_enabled=True),
    ).tile == 21


def test_feature_off_keeps_raw_current_ukeire_frontier():
    roots, visible = _two_w_four_t_roots()
    enriched, frontier, _diagnostics = _root_features(
        roots, 0, visible, marginal_role_enabled=False,
        speed_band_enabled=False, pareto_frontier_enabled=False,
    )
    assert tuple(root.tile for root in frontier) == (1,)
    assert all(root.current_speed_ratio is None for root in enriched)
