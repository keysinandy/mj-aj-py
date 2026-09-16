"""Paired candidate/baseline games with source-game clustered bootstrap.

Both sides of a pair use the same seed, hero seat, dealer, YCBK flag and
opponent schedule, so ``Delta_i = score_candidate - score_baseline`` isolates
the policy change.  Individual games are clustered by source-game seed and
only complete clusters are resampled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..bot import choose_action
from ..decision.calibration import cluster_bootstrap
from ..decision.profile import fingerprint
from ..game import Game
from .distillation_profile import OpponentPopulationProfile

PAIRED_SCHEMA = "search-bc-paired-score-report-v1"
OPPONENT_SPLITS = ("self_play", "legacy_shape_v1", "frozen_population")


@dataclass(frozen=True)
class PairedSchedule:
    seed_start: int = 240000
    games: int = 16
    ycbk_variants: tuple[bool, ...] = (False, True)

    def __post_init__(self):
        if int(self.seed_start) < 0 or int(self.games) <= 0:
            raise ValueError("schedule requires a non-negative seed and games")
        if not self.ycbk_variants:
            raise ValueError("schedule requires at least one YCBK variant")
        object.__setattr__(self, "seed_start", int(self.seed_start))
        object.__setattr__(self, "games", int(self.games))
        object.__setattr__(self, "ycbk_variants",
                           tuple(bool(value) for value in self.ycbk_variants))

    @property
    def pairs(self):
        return self.games * len(self.ycbk_variants)

    def rows(self, n=None):
        rows = []
        count = self.pairs if n is None else min(int(n), self.pairs)
        for index in range(count):
            game_index = index // len(self.ycbk_variants)
            variant = index % len(self.ycbk_variants)
            seed = self.seed_start + game_index
            rows.append({
                "index": index, "seed": seed,
                "seat": game_index % 4, "dealer": (game_index // 4) % 4,
                "you_cai_bi_kao": bool(self.ycbk_variants[variant]),
                "cluster": f"game:{seed}",
            })
        return rows


def play_game(players: Sequence[Callable], *, seed, dealer=0,
              you_cai_bi_kao=False):
    """One complete game driven by per-seat callables."""
    game = Game(seed=seed, dealer=dealer, you_cai_bi_kao=you_cai_bi_kao)
    while not game.done:
        seat = int(game.current_seat())
        legal = tuple(int(action) for action in game.legal_actions())
        if not legal:
            raise RuntimeError("non-terminal state has no legal actions")
        action = int(players[seat](game, seat))
        if action not in legal:
            raise ValueError("policy selected an illegal action")
        game.step(action)
    return game


def checkpoint_callable(path, *, device="cpu"):
    """Manifest-bearing search-BC checkpoint as a per-seat callable."""
    from ..decision.policy_v3 import load_policy_value_model

    model = load_policy_value_model(path, device=device)

    def play(game, seat):
        legal = tuple(int(action) for action in game.legal_actions())
        distribution = model.predict_game(game, seat, legal_actions=legal)
        actions = tuple(int(action) for action in distribution.actions)
        best, best_key = None, None
        for order, (action, probability) in enumerate(zip(
                actions, distribution.probabilities)):
            key = (float(probability), -order)
            if best_key is None or key > best_key:
                best, best_key = action, key
        return int(best)

    return play


def heuristic_callable(evaluator):
    def play(game, seat):
        return int(choose_action(game, seat, evaluator=evaluator))
    return play


def policy_callable(source, *, device="cpu"):
    source = str(source)
    if source.startswith("checkpoint:"):
        return checkpoint_callable(source.split(":", 1)[1], device=device)
    if source.startswith("heuristic:"):
        return heuristic_callable(source.split(":", 1)[1])
    if source in ("legacy", "shape-v1", "shape-v2"):
        return heuristic_callable(source)
    raise ValueError(f"unsupported policy source: {source!r}")


def opponent_callables(source, *, candidate, population: OpponentPopulationProfile,
                       generation=0, source_group="", device="cpu"):
    """Per-seat opponent callables for one declared split."""
    if source == "self_play":
        return [candidate] * 4
    if source == "legacy_shape_v1":
        return [heuristic_callable("shape-v1")] * 4
    if source == "frozen_population":
        cache = {}

        def seat_policy(seat):
            name = population.member_for(
                generation=generation, source_group=source_group, seat=seat)
            if name not in cache:
                cache[name] = policy_callable(name, device=device)
            return cache[name]
        return [seat_policy(seat) for seat in range(4)]
    raise ValueError(f"unsupported opponent split: {source!r}")


def play_pair(schedule_row, *, candidate, baseline, opponents, hero_seat=None):
    """Play one candidate/baseline pair on identical schedules."""
    seat = int(schedule_row["seat"] if hero_seat is None else hero_seat)
    candidate_players = list(opponents)
    baseline_players = list(opponents)
    candidate_players[seat] = candidate
    baseline_players[seat] = baseline
    game_a = play_game(candidate_players, seed=schedule_row["seed"],
                       dealer=schedule_row["dealer"],
                       you_cai_bi_kao=schedule_row["you_cai_bi_kao"])
    game_b = play_game(baseline_players, seed=schedule_row["seed"],
                       dealer=schedule_row["dealer"],
                       you_cai_bi_kao=schedule_row["you_cai_bi_kao"])
    row = dict(schedule_row)
    row.update({
        "hero_seat": seat,
        "score_candidate": float(game_a.scores[seat]),
        "score_baseline": float(game_b.scores[seat]),
        "delta": float(game_a.scores[seat]) - float(game_b.scores[seat]),
        "winner_candidate": (game_a.result[0] if game_a.result else None),
        "winner_baseline": (game_b.result[0] if game_b.result else None),
        "multiplier_candidate": (game_a.result[1] if game_a.result else None),
        "multiplier_baseline": (game_b.result[1] if game_b.result else None),
        "draw_candidate": game_a.result is None,
        "draw_baseline": game_b.result is None,
    })
    return row


def paired_score_report(rows: Iterable[Mapping[str, Any]], *,
                        required_pairs=4096, rounds=2000, seed=0, alpha=0.05,
                        matrix="candidate_vs_baseline"):
    """Clustered CI plus secondary win/multiplier/draw diagnostics."""
    rows = list(rows)
    if not rows:
        raise ValueError("paired report requires at least one row")
    deltas = [float(row["delta"]) for row in rows]
    groups = [str(row["cluster"]) for row in rows]
    ci = cluster_bootstrap(deltas, groups, rounds=rounds, seed=seed,
                           alpha=alpha)
    wins = sum(1 for row in rows
               if row["winner_candidate"] == row["hero_seat"])
    losses = sum(1 for row in rows
                 if row["winner_baseline"] == row["hero_seat"])
    draws = sum(1 for row in rows if row["draw_candidate"])
    if ci["low"] > 0:
        verdict = "superior"
    elif ci["high"] <= 0 and ci["mean"] < 0:
        verdict = "regression"
    else:
        verdict = "non_regression_ambiguous"
    value = {
        "schema": PAIRED_SCHEMA,
        "matrix": str(matrix),
        "pairs": len(rows),
        "required_pairs": int(required_pairs),
        "meets_required_pairs": len(rows) >= int(required_pairs),
        "mean_delta": ci["mean"],
        "ci95": [ci["low"], ci["high"]],
        "ci95_lower": ci["low"],
        "alpha": float(alpha),
        "cluster": "source_game_seed",
        "bootstrap": {key: ci[key] for key in
                      ("n", "clusters", "rounds", "seed", "method")},
        "verdict": verdict,
        "score_metric": "hero_round_score_points",
        "secondary_diagnostics": {
            "candidate_win_rate": wins / len(rows),
            "baseline_win_rate": losses / len(rows),
            "draw_rate": draws / len(rows),
        },
        "oracle": False,
    }
    value["fingerprint"] = fingerprint(value, 24)
    return value


def opponent_factory(source, *, candidate, population: OpponentPopulationProfile,
                     generation=0, device="cpu"):
    """Build per-row opponents so population members can depend on the source
    game (the same group identity used by dataset generation)."""

    def build(source_group):
        return opponent_callables(
            source, candidate=candidate, population=population,
            generation=generation, source_group=source_group, device=device)

    return build
