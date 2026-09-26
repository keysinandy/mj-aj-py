import copy
import random
from types import SimpleNamespace
from unittest import mock

from mj import shanten as shanten_module
from mj.bot import choose_discard
from mj.game import Game
from mj.legacy_eval import (
    LegacyRootCandidate,
    LegacyTwoPlyProfile,
    FutureEvaluation,
    _apply_shape_guard,
    _limit_weighted_frontier,
    _root_features,
    _weighted_future_for_root,
    _weighted_root_key,
)
from mj.shape_quality import SHAPE_QUALITY_VERSION, standing_shape_quality
from mj.tiles import counts
from mj.shape_quality import rust_standing_shape_quality
from tests.test_bot import _draw_game


def _shape(spec, *, locked=0):
    return standing_shape_quality(counts(spec), locked=locked)


def test_standing_shape_has_versioned_named_signature_and_non_aliasing_cost():
    quality = _shape("23455m 124s EE w")
    assert quality.version == SHAPE_QUALITY_VERSION
    assert quality.signature == (
        quality.complete_meld_count, quality.taatsu_count,
        quality.ryanmen_count, quality.central_kanchan_count,
        quality.edge_kanchan_count, quality.penchan_count,
        quality.pair_units, quality.isolated_count)
    assert quality.as_json()["quality"] == quality.encoded


def test_suited_taatsu_classes_obey_minimum_ordering():
    pairs = (("23s", "24s"), ("24s", "13s"), ("13s", "12s"),
             ("78s", "68s"), ("68s", "79s"), ("79s", "89s"))
    for better, worse in pairs:
        assert _shape(better).sort_key > _shape(worse).sort_key


def test_124_decomposition_prefers_24_over_12_without_reusing_tiles():
    kept_24 = _shape("23455m 24s EE w")
    kept_12 = _shape("23455m 12s EE w")
    assert kept_24.sort_key > kept_12.sort_key
    assert kept_24.central_kanchan_count == 1
    assert kept_24.penchan_count == 0
    assert kept_12.penchan_count == 1
    assert kept_12.central_kanchan_count == 0
    # 234m is one completed meld, not two simultaneously counted taatsu.
    assert kept_24.complete_meld_count == 1
    assert kept_24.taatsu_count == 1


def test_sequence_triplet_pair_and_honor_units_are_not_suited_taatsu():
    shape = _shape("123m 555p 77s EE")
    assert shape.complete_meld_count == 2
    assert shape.taatsu_count == 0
    assert shape.pair_units == 2
    assert shape.ryanmen_count == 0
    assert shape.central_kanchan_count == 0
    assert shape.edge_kanchan_count == 0
    assert shape.penchan_count == 0


def test_wildcards_are_excluded_and_locked_meld_count_does_not_fake_shape():
    baseline = _shape("123m 24s EE")
    for wildcards in range(5):
        actual = standing_shape_quality(counts("123m 24s EE" + "w" * wildcards),
                                        locked=wildcards % 5)
        assert actual.signature == baseline.signature


def test_shape_is_deterministic_and_input_order_independent():
    first = standing_shape_quality(counts("23455m 124s EE w"))
    second = standing_shape_quality(counts("55m 432m 421s EEw"))
    assert first == second


def test_shape_profile_is_versioned_and_disabled_profile_keeps_baseline_key():
    baseline = LegacyTwoPlyProfile.weighted_online(
        shape_quality_enabled=False, shape_quality_guard_enabled=False)
    phase_a = LegacyTwoPlyProfile.weighted_online(
        shape_quality_enabled=True,
        shape_quality_stage="root",
        shape_quality_guard_enabled=True,
    )
    assert baseline.fingerprint == "9fd50acbeb1fb6ed"
    assert baseline.fingerprint != phase_a.fingerprint
    assert phase_a.as_json()["shape_quality_version"] == SHAPE_QUALITY_VERSION
    assert phase_a.as_json()["shape_quality_enabled"] is True


def test_shape_disabled_root_enrichment_skips_shape_work_and_fields():
    game = _draw_game("23455m 124s EE w", 18,
                      melds=[("chow", 15)])
    hand = list(game.hands[0])
    tile = next(index for index, amount in enumerate(hand) if amount)
    hand[tile] -= 1
    root = LegacyRootCandidate(
        tile=tile, hand=tuple(hand), shanten=0,
        standing_shape_quality=123,
        standing_shape_signature=(1, 2, 3, 4, 5, 6, 7, 8),
        shape_quality_version="stale")
    with mock.patch(
        "mj.legacy_eval.standing_shape_quality",
        side_effect=AssertionError("disabled shape must not execute")):
        enriched, _frontier, _diagnostics = _root_features(
            (root,), 1, game.visible_counts(0))
    assert enriched[0].standing_shape_quality is None
    assert enriched[0].standing_shape_signature is None
    assert enriched[0].shape_quality_version is None

    shaped, _frontier, _diagnostics = _root_features(
        (root,), 1, game.visible_counts(0), shape_quality_enabled=True)
    assert shaped[0].shape_quality_version == SHAPE_QUALITY_VERSION
    assert shaped[0].standing_shape_quality == standing_shape_quality(
        hand, locked=1).encoded


def test_frontier_flag_off_keeps_legacy_cap_and_shape_profile_prefers_24():
    kept_24 = standing_shape_quality(counts("23455m 24s EE w"))
    kept_12 = standing_shape_quality(counts("23455m 12s EE w"))
    roots = (
        LegacyRootCandidate(
            tile=18, hand=tuple(counts("23455m 24s EE w")), shanten=0,
            shape_loss=5, feed_risk=0,
            current_ukeire=11, current_ukeire_tiles=(22, 20, 27, 33),
            standing_shape_quality=kept_24.encoded,
            standing_shape_signature=kept_24.signature,
            shape_quality_version=kept_24.version,
        ),
        LegacyRootCandidate(
            tile=21, hand=tuple(counts("23455m 12s EE w")), shanten=0,
            shape_loss=3, feed_risk=0,
            current_ukeire=11, current_ukeire_tiles=(22, 20, 27, 33),
            standing_shape_quality=kept_12.encoded,
            standing_shape_signature=kept_12.signature,
            shape_quality_version=kept_12.version,
        ),
    )
    diagnostics = tuple((root, True, ()) for root in roots)
    old_frontier, _ = _limit_weighted_frontier(roots, diagnostics, 1)
    new_frontier, _ = _limit_weighted_frontier(
        roots, diagnostics, 1, shape_quality_enabled=True)
    assert old_frontier[0].tile == 21
    assert new_frontier[0].tile == 18


def test_new_shape_guard_uses_taatsu_class_gain_and_not_legacy_delta():
    primary_shape = standing_shape_quality(counts("12s"))
    admitted_shape = standing_shape_quality(counts("23s"))
    primary = LegacyRootCandidate(
        tile=18, hand=tuple(counts("12s")), shanten=0, shape_loss=5,
        current_ukeire=10, current_ukeire_tiles=(18, 19),
        standing_shape_quality=primary_shape.encoded,
        standing_shape_signature=primary_shape.signature,
        shape_quality_version=primary_shape.version,
    )
    challenger = LegacyRootCandidate(
        tile=19, hand=tuple(counts("23s")), shanten=0, shape_loss=100,
        current_ukeire=9, current_ukeire_tiles=(19,),
        standing_shape_quality=admitted_shape.encoded,
        standing_shape_signature=admitted_shape.signature,
        shape_quality_version=admitted_shape.version,
    )
    profile = LegacyTwoPlyProfile.weighted_online(
        shape_quality_enabled=True, shape_quality_stage="root",
        shape_quality_guard_enabled=True,
    )
    diagnostics = ((primary, True, ()),
                   (challenger, False, ("current_ukeire_frontier",)))
    with mock.patch(
            "mj.legacy_eval._weighted_native_ready", return_value=True):
        frontier, _, guard, admitted = _apply_shape_guard(
            (primary,), diagnostics, profile)
    assert [root.tile for root in frontier] == [18, 19]
    assert admitted[19] == "shape_guard"
    assert guard["policy"]["taatsu_class_gain"] == 1
    assert "shape_delta" not in guard["policy"]


def test_current_ukeire_precedes_root_shape_and_feed_remains_a_late_tie_break():
    low_shape_high_ukeire = LegacyRootCandidate(
        tile=18, hand=tuple(counts("123m 123p 123s ESWN")),
        shanten=0, shape_loss=4, feed_risk=5, current_ukeire=10,
        standing_shape_quality=100)
    high_shape_low_ukeire = LegacyRootCandidate(
        tile=19, hand=tuple(counts("123m 123p 123s ESWN")),
        shanten=0, shape_loss=1, feed_risk=0, current_ukeire=9,
        standing_shape_quality=1000)
    common_future = FutureEvaluation(
        complete=True, future_improve_weight=1,
        future_ukeire_mean=4, future_ukeire_types_mean=2)
    futures = {18: common_future, 19: common_future}
    assert _weighted_root_key(
        low_shape_high_ukeire, futures, shape_quality_enabled=True) < (
        _weighted_root_key(high_shape_low_ukeire, futures,
                           shape_quality_enabled=True))

    same_shape_safer = LegacyRootCandidate(
        tile=20, hand=low_shape_high_ukeire.hand, shanten=0,
        shape_loss=4, feed_risk=1, current_ukeire=10,
        standing_shape_quality=100)
    equal_root_future = {18: common_future, 20: common_future}
    assert _weighted_root_key(
        same_shape_safer, equal_root_future,
        shape_quality_enabled=True) < _weighted_root_key(
            low_shape_high_ukeire, equal_root_future,
            shape_quality_enabled=True)


def test_child_shanten_filters_before_shape_quality_comparison():
    hand = counts("123m 123p 123s ESWN")
    root = LegacyRootCandidate(tile=18, hand=tuple(hand), shanten=1)
    visible = [4] * 34
    visible[32] = 3
    profile = LegacyTwoPlyProfile.weighted_online(
        kernel="python", shape_quality_enabled=True,
        shape_quality_stage="full", shape_quality_guard_enabled=False,
        node_budget=1000, soft_budget_ms=1000, hard_budget_ms=1000)
    seen_shape_hands = []

    def shape_quality(after, *, locked):
        seen_shape_hands.append(tuple(after))
        return SimpleNamespace(encoded=999 if after[0] else 1)

    def child_shanten(after, locked):
        return 0 if after[0] == 0 else 1

    with mock.patch("mj.legacy_eval._child_discards", return_value=(0, 1)), \
            mock.patch("mj.legacy_eval.shanten", side_effect=child_shanten), \
            mock.patch("mj.legacy_eval.ukeire",
                       return_value=(None, [32], 5)), \
        mock.patch("mj.legacy_eval.standing_shape_quality",
                       side_effect=shape_quality):
        result = _weighted_future_for_root(
            None, 0, root, 0,
            tuple(visible), profile, lambda *_args: 0,
            lambda *_args: 0)
    assert result.best_discard_by_draw == ((32, 0),)
    assert len(seen_shape_hands) == 1
    assert seen_shape_hands[0][0] == 0


def test_real_baotou_scope_golden_changes_4s_to_1s_only_when_enabled():
    baseline = LegacyTwoPlyProfile.weighted_online(
        shape_quality_enabled=False, shape_quality_guard_enabled=False)
    candidate = LegacyTwoPlyProfile.weighted_online(
        shape_quality_enabled=True,
        shape_quality_stage="root",
        shape_quality_guard_enabled=True,
    )
    observed = {}
    for name, profile in (("baseline", baseline), ("candidate", candidate)):
        game = _draw_game("23455m 124s EE w", 18,
                          melds=[("chow", 15)])
        with mock.patch.object(
                shanten_module, "BAOTOU_UKEIRE_RUST", True):
            action, info = choose_discard(
                game, 0, return_info=True, profile=profile)
        observed[name] = (action, info)

    old_action, old_info = observed["baseline"]
    new_action, new_info = observed["candidate"]
    assert old_action == 21
    assert new_action == 18
    assert old_info["decision_scope"] == "baotou_scope"
    assert new_info["decision_scope"] == "baotou_scope"
    assert new_info["stage_b_entered"] is False
    assert new_info["future_shape_quality_mean"] is None
    assert new_info["shape_changed_winner"] is True
    rows = {row["tile"]: row for row in new_info["candidates"]}
    assert rows[18]["shanten"] == rows[21]["shanten"] == 0
    assert rows[18]["current_ukeire"] == rows[21]["current_ukeire"] == 11
    assert rows[18]["baotou_ukeire"] == rows[21]["baotou_ukeire"] == 0
    assert rows[18]["discard_shape_cost"] == 5
    assert rows[21]["discard_shape_cost"] == 3
    assert rows[18]["standing_shape_quality"] > rows[21][
        "standing_shape_quality"]


def test_baotou_budget_abort_keeps_whole_legacy_key_without_shape_pollution():
    profile = LegacyTwoPlyProfile.weighted_online(
        shape_quality_enabled=True, shape_quality_stage="root",
        shape_quality_guard_enabled=True)
    game = _draw_game("23455m 124s EE w", 18,
                      melds=[("chow", 15)])
    with mock.patch.object(shanten_module, "BAOTOU_UKEIRE_RUST", True), \
            mock.patch("mj.bot.BAOTOU_UKE_BUDGET_NODES", 0):
        action, info = choose_discard(
            game, 0, return_info=True, profile=profile)
    assert action == 21
    assert info["fallback_reason"] == "baotou_budget_exceeded"
    assert info["decision_scope"] == "baotou_scope"
    assert not info.get("baotou_shape_used", False)
    assert not info.get("shape_changed_winner", False)


def test_baotou_kernel_unavailable_and_push_abort_keep_legacy_action():
    profile = LegacyTwoPlyProfile.weighted_online(
        shape_quality_enabled=True, shape_quality_stage="root",
        shape_quality_guard_enabled=True)
    for patcher in (
        mock.patch.object(shanten_module, "BAOTOU_UKEIRE_RUST", False),
        mock.patch("mj.bot._push_abort_reason", return_value="xy_guard"),
    ):
        game = _draw_game("23455m 124s EE w", 18,
                          melds=[("chow", 15)])
        with patcher:
            action, info = choose_discard(
                game, 0, return_info=True, profile=profile)
        assert action == 21
        assert info["decision_scope"] == "baotou_scope"
        assert not info.get("baotou_shape_used", False)
        assert not info.get("shape_changed_winner", False)


def test_shape_off_weighted_profile_keeps_action_and_root_diagnostics_equal():
    profile_a = LegacyTwoPlyProfile.weighted_online(
        workers=1, shape_quality_enabled=False,
        shape_quality_guard_enabled=False)
    profile_b = LegacyTwoPlyProfile.weighted_online(
        shape_quality_enabled=False, shape_quality_guard_enabled=False,
        workers=1)

    def run(profile):
        game = _draw_game("23455m 124s EEE", 18,
                          melds=[("chow", 15)])
        action, info = choose_discard(
            game, 0, return_info=True, profile=profile)
        return action, info

    action_a, info_a = run(profile_a)
    action_b, info_b = run(profile_b)
    assert action_a == action_b
    assert info_a["profile_fingerprint"] == info_b["profile_fingerprint"]
    assert info_a["selected"] == info_b["selected"]
    stable_a = [{key: value for key, value in row.items()
                 if key != "future_elapsed_ms"}
                for row in info_a["candidates"]]
    stable_b = [{key: value for key, value in row.items()
                 if key != "future_elapsed_ms"}
                for row in info_b["candidates"]]
    assert stable_a == stable_b
    assert info_a["shape_quality_used"] is False
    assert info_b["shape_quality_used"] is False


def test_python_rust_shape_signature_parity_for_10000_random_hands():
    if shanten_module._rust_standing_shape_quality is None:
        return
    rng = random.Random(20260926)
    deck = [tile for tile in range(34) for _ in range(4)]
    for _ in range(10_000):
        locked = rng.randrange(5)
        concealed = 13 - 3 * locked + rng.randrange(2)
        hand = [0] * 34
        for tile in rng.sample(deck, concealed):
            hand[tile] += 1
        python = standing_shape_quality(hand, locked=locked)
        assert standing_shape_quality(hand, locked=locked) == python
        native = rust_standing_shape_quality(hand)
        assert native == (python.version, python.signature, python.encoded)


def test_weighted_stage_b_draw_5s_discards_2s_and_keeps_45s_in_both_kernels():
    def run(kernel):
        game = _draw_game("23455m 124s EEE", 18,
                          melds=[("chow", 15)])
        assert game.hands[0][33] == 0  # independent of baotou_scope
        profile = LegacyTwoPlyProfile.weighted_online(
            kernel=kernel,
            shape_quality_enabled=True,
            shape_quality_stage="full",
            shape_quality_guard_enabled=True,
            soft_budget_ms=1000.0,
            hard_budget_ms=1000.0,
            time_budget_ms=1000.0,
            workers=1,
        )
        return choose_discard(game, 0, return_info=True, profile=profile)

    rust_action, rust_info = run("rust")
    python_action, python_info = run("python")
    assert rust_info["decision_scope"] == "weighted_two_ply"
    assert rust_info["stage_b_entered"] is True
    assert rust_info["complete"] is True
    assert rust_action == python_action == 18
    rust_roots = {row["tile"]: row for row in rust_info["candidates"]}
    python_roots = {row["tile"]: row for row in python_info["candidates"]}
    for tile in (18, 21):
        assert rust_roots[tile]["shanten"] == python_roots[tile]["shanten"] == 0
        assert rust_roots[tile]["ukeire"] == python_roots[tile]["ukeire"] == 8
        assert rust_roots[tile]["ukeire_types"] == python_roots[tile][
            "ukeire_types"] == 2
        assert rust_roots[tile]["future_shape_quality_sum"] == (
            python_roots[tile]["future_shape_quality_sum"])
        assert rust_roots[tile]["child_best_discards_by_draw"] == (
            python_roots[tile]["child_best_discards_by_draw"])
        assert rust_roots[tile]["child_shape_quality_by_draw"] == (
            python_roots[tile]["child_shape_quality_by_draw"])

    # Root 1s keeps 24s. After the 5s draw, Stage B removes 2s and leaves 45s.
    assert rust_roots[18]["child_best_discards_by_draw"]["22"] == 19
    assert rust_roots[18]["child_shape_quality_by_draw"]["22"] > (
        rust_roots[21]["child_shape_quality_by_draw"]["22"])


def test_random_weighted_roots_match_python_and_rust_shape_metrics():
    compared = 0
    for seed in range(20261000, 20261150):
        game = Game(seed=seed)
        if game.hands[0][33] and game.live_wall_left() > 0:
            # This test targets the ordinary weighted route, not baotou's
            # deliberate early return.
            continue
        outputs = {}
        for kernel in ("rust", "python"):
            profile = LegacyTwoPlyProfile.weighted_online(
                kernel=kernel, shape_quality_enabled=True,
                shape_quality_stage="full",
                shape_quality_guard_enabled=False,
                max_frontier_candidates=3,
                node_budget=1_000_000, soft_budget_ms=2000.0,
                hard_budget_ms=2000.0)
            outputs[kernel] = choose_discard(
                Game(seed=seed), 0, return_info=True, profile=profile)
        rust_action, rust_info = outputs["rust"]
        python_action, python_info = outputs["python"]
        if (rust_info.get("decision_scope") != "weighted_two_ply" or
                not rust_info.get("complete") or
                not python_info.get("complete")):
            continue
        assert rust_action == python_action
        fields = (
            "shanten", "ukeire", "ukeire_types", "future_improve_weight",
            "future_ukeire", "future_ukeire_types",
            "future_shape_quality_sum", "future_shape_quality_mean",
            "child_best_discards_by_draw", "child_shape_quality_by_draw",
        )
        rust_roots = {row["tile"]: row for row in rust_info["candidates"]
                      if row.get("complete")}
        python_roots = {
            row["tile"]: row for row in python_info["candidates"]
            if row.get("complete")}
        assert set(rust_roots) == set(python_roots)
        for tile in rust_roots:
            for field in fields:
                assert rust_roots[tile].get(field) == python_roots[tile].get(
                    field), (seed, tile, field)
        compared += 1
        if compared >= 10:
            break
    assert compared == 10


def test_shape_decision_ignores_opponent_hands_and_true_wall_order():
    visible_game = _draw_game("23455m 124s EEE", 18,
                              melds=[("chow", 15)])
    hidden_game = copy.deepcopy(visible_game)
    hidden_game.hands[1] = counts("111m 222p 333s EEEE")
    hidden_game.hands[2] = counts("444m 555p 666s SSSS")
    hidden_game.hands[3] = counts("777m 888p 999s NNNN")
    hidden_game.wall = list(reversed(range(60)))
    profile = LegacyTwoPlyProfile.weighted_online(
        shape_quality_enabled=True, shape_quality_stage="full",
        shape_quality_guard_enabled=False,
        soft_budget_ms=1000.0, hard_budget_ms=1000.0,
        time_budget_ms=1000.0)
    visible_action, visible_info = choose_discard(
        visible_game, 0, return_info=True, profile=profile)
    hidden_action, hidden_info = choose_discard(
        hidden_game, 0, return_info=True, profile=profile)
    assert visible_action == hidden_action
    assert visible_info["decision_scope"] == hidden_info["decision_scope"]
    assert visible_info["selected"] == hidden_info["selected"]
    visible_rows = {row["tile"]: row for row in visible_info["candidates"]}
    hidden_rows = {row["tile"]: row for row in hidden_info["candidates"]}
    for tile in visible_rows:
        for field in (
                "shanten", "ukeire", "ukeire_types",
                "future_improve_weight", "future_ukeire",
                "future_ukeire_types", "future_shape_quality_sum",
                "child_best_discards_by_draw",
                "child_shape_quality_by_draw"):
            assert visible_rows[tile].get(field) == hidden_rows[tile].get(field)
