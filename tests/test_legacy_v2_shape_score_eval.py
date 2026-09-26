from mj.bot import choose_action
from scripts import legacy_v2_shape_score_eval as score_eval
from tests.test_bot import _draw_game


def _fake_arm(score, evaluator):
    diagnostics = {
        "decision_scope": {"weighted_two_ply": 2},
        "changed_by_scope": ({"weighted_two_ply": 1}
                              if evaluator != score_eval.BASELINE else {}),
        "shanten": {"0": 2},
        "open_melds": {"1": 2},
        "wall_left": {"21-40": 2},
        "taatsu_upgrades": {"ryanmen+1": 1},
    }
    return {
        "score": score, "win": score > 0, "draw": False,
        "winning_multiplier": 2.0 if score > 0 else None,
        "shape_decisions": 2 if evaluator != score_eval.BASELINE else 0,
        "shape_changed_decisions": 1 if evaluator != score_eval.BASELINE else 0,
        "stage_b_decisions": 2 if evaluator.endswith("phase-b") else 0,
        "decision_scope": diagnostics["decision_scope"],
        "changed_by_scope": diagnostics["changed_by_scope"],
        "shanten": diagnostics["shanten"],
        "open_melds": diagnostics["open_melds"],
        "wall_left": diagnostics["wall_left"],
        "taatsu_upgrades": diagnostics["taatsu_upgrades"],
        "diagnostics": diagnostics,
    }


def test_score_runner_freezes_rotation_order_and_shape_change_buckets(monkeypatch):
    calls = []

    def fake_play(seed, hero_seat, dealer, evaluator):
        calls.append((seed, hero_seat, dealer, evaluator))
        return _fake_arm(5.0 if evaluator != score_eval.BASELINE else 1.0,
                         evaluator)

    monkeypatch.setattr(score_eval, "_play", fake_play)
    result = score_eval.run(stage=1, games=4, bootstrap_rounds=20,
                            progress_every=0)

    assert [(row[1], row[2]) for row in calls[::2]] == [
        (0, 0), (1, 0), (2, 0), (3, 0)]
    assert [row[3] for row in calls] == [
        score_eval.BASELINE, "legacy-v2-shape-phase-a",
        "legacy-v2-shape-phase-a", score_eval.BASELINE,
        score_eval.BASELINE, "legacy-v2-shape-phase-a",
        "legacy-v2-shape-phase-a", score_eval.BASELINE,
    ]
    assert result["frozen_schedule_match"] is False
    assert result["score_delta_candidate_minus_baseline"]["mean"] == 4.0
    assert result["shape_changes"]["changed_decisions"] == 4
    assert result["shape_changes"]["taatsu_upgrade_distribution"] == {
        "ryanmen+1": 4}
    assert result["changed_unchanged_game_score_buckets"][
        "games_with_shape_changed_decision"]["games"] == 4
    assert result["gate"]["passed"] is False
    assert result["gate"]["default_remains_disabled"] is True


def test_signature_delta_reads_selected_and_baseline_candidate_rows():
    evaluation = {
        "selected": 18, "shape_baseline_selected": 21,
        "candidates": [
            {"tile": 18,
             "standing_shape_signature": [2, 2, 1, 0, 0, 1, 1, 2]},
            {"tile": 21,
             "standing_shape_signature": [2, 2, 0, 0, 0, 2, 1, 2]},
        ],
    }
    assert score_eval._signature_delta(evaluation) == "ryanmen+1"


def test_frozen_profiles_disable_big_hand_and_isolate_shape_stage():
    baseline_a, candidate_a = score_eval._profiles(1)
    baseline_b, candidate_b = score_eval._profiles(2)
    assert baseline_a.fingerprint == baseline_b.fingerprint
    assert baseline_a.big_hand_enabled is False
    assert baseline_a.big_hand_same_shanten_enabled is False
    assert baseline_a.big_hand_plus_one_enabled is False
    assert candidate_a.big_hand_enabled is False
    assert candidate_a.big_hand_same_shanten_enabled is False
    assert candidate_a.shape_quality_stage == "root"
    assert candidate_b.shape_quality_stage == "full"


def test_score_profiles_match_the_registered_bot_evaluators():
    cases = (
        ("legacy-v2-baseline", score_eval._profiles(1)[0]),
        ("legacy-v2-shape-phase-a", score_eval._profiles(1)[1]),
        ("legacy-v2-shape-phase-b", score_eval._profiles(2)[1]),
    )
    for evaluator, expected_profile in cases:
        game = _draw_game("23455m 124s EEE", 18,
                          melds=[("chow", 15)])
        _action, evaluation = choose_action(
            game, 0, evaluator=evaluator, return_evaluation=True)
        assert evaluation["profile_fingerprint"] == expected_profile.fingerprint
