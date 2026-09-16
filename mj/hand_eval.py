"""Visible-state, shape-aware evaluation for the heuristic Mahjong bot.

The module is deliberately independent from :mod:`mj.bot`: it is the
specification-side evaluator used by both discard and reaction decisions.  A
``Game`` is converted to an immutable :class:`EvalContext`; the evaluator
never reads ``Game.wall`` or an opponent's concealed hand.

``legacy`` remains the production default.  ``shape-v1`` is opt-in through
``evaluate_discard_candidates``/``evaluate_reaction`` and the runner CLI.
The probabilities in this file are bounded model features (uniform unseen,
without opponent actions), not claims about real-game win probability.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
import json
import threading
import time
from typing import Iterable, Optional, Sequence

from .game import (
    PASS, PONG, CHOW_LOW, CHOW_MID, CHOW_HIGH, KONG_OPEN,
)
from .shanten import (
    shanten, ukeire,
    best_future_discard as _best_future_discard_kernel,
    FUTURE_DISCARD_KERNEL_VERSION,
)
from .tiles import W
from .win import is_baotou


MODEL_ASSUMPTION = "uniform_unseen_no_opponent_actions"
PROFILE_VERSION = "shape-v1"
_MISSING = object()


@lru_cache(maxsize=64)
def _cached_profile_fingerprint(name, version, w_i, w_h, w_b, w_c,
                                tau_pong, tau_chow, discard_nodes,
                                react_nodes, discard_ms, react_ms,
                                explanation, future_kernel):
    payload = {
        "name": name, "version": version,
        "wI": w_i, "wH": w_h, "wB": w_b, "wC": w_c,
        "tau_pong_multiplier": tau_pong,
        "tau_chow_multiplier": tau_chow,
        "discard_node_budget": discard_nodes,
        "react_node_budget": react_nodes,
        "discard_time_budget_ms": discard_ms,
        "react_time_budget_ms": react_ms,
        "explanation": explanation,
        "future_discard_kernel": future_kernel,
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()[:16]


class InvalidEvaluationInput(ValueError):
    """Raised when a public count cannot represent a Mahjong table."""


@dataclass(frozen=True)
class EvalProfile:
    """Versioned evaluator parameters.

    The initial weights are diagnostic defaults from the OpenSpec design.  A
    caller must explicitly request ``shape-v1``; legacy never silently uses
    these values.
    """

    name: str = "legacy"
    version: str = "legacy"
    wI: float = 0.25
    wH: float = 0.5
    wB: float = 0.05
    wC: float = 0.025
    tau_pong_multiplier: float = 1.0
    tau_chow_multiplier: float = 1.0
    discard_node_budget: int = 4096
    react_node_budget: int = 2048
    # Leave a small margin below the external 20/10ms acceptance limits.  The
    # Rust batched future-discard kernel and decision-local caches make 17.5ms
    # a useful discard budget while retaining roughly 2.5ms for action plumbing;
    # the profile still reports any uninterruptible kernel overrun.
    discard_time_budget_ms: float = 17.5
    react_time_budget_ms: float = 7.0
    explanation: bool = True

    @classmethod
    def legacy(cls) -> "EvalProfile":
        return cls(name="legacy", version="legacy", wI=0.0, wH=0.0,
                   wB=0.0, wC=0.0, explanation=False)

    @classmethod
    def shape_v1(cls, **kwargs) -> "EvalProfile":
        values = {"name": PROFILE_VERSION, "version": PROFILE_VERSION}
        values.update(kwargs)
        return cls(**values)

    @property
    def fingerprint(self) -> str:
        return _cached_profile_fingerprint(
            self.name, self.version, self.wI, self.wH, self.wB, self.wC,
            self.tau_pong_multiplier, self.tau_chow_multiplier,
            self.discard_node_budget, self.react_node_budget,
            self.discard_time_budget_ms, self.react_time_budget_ms,
            self.explanation, FUTURE_DISCARD_KERNEL_VERSION)


def profile_for(value=None) -> EvalProfile:
    if isinstance(value, EvalProfile):
        return value
    if value in (None, "legacy"):
        return EvalProfile.legacy()
    if value in ("shape-v1", "shape_v1", "shape"):
        return EvalProfile.shape_v1()
    raise ValueError(f"unknown evaluator profile: {value}")


@dataclass(frozen=True)
class EvalContext:
    """All information allowed to influence an evaluation.

    ``hand`` is the current concealed hand.  Standing states contain
    ``13 - 3*locked`` tiles; a post-draw state may contain one additional tile
    only while the caller is about to enumerate a discard.  ``visible`` must
    include this hand, all public rivers and all meld tiles.
    """

    hand: tuple = field(default_factory=lambda: (0,) * 34)
    locked: int = 0
    visible: tuple = field(default_factory=lambda: (0,) * 34)
    melds: tuple = ()
    chows: int = 0
    phase: str = "discard"
    you_cai_bi_kao: bool = False
    freeze: bool = False
    freezer: int = -1
    live_wall: int = 0
    drawn: Optional[int] = None
    kong_draw: bool = False
    seat: int = 0
    pending_owner: Optional[int] = None
    pending_tile: Optional[int] = None

    def __post_init__(self):
        hand = tuple(int(x) for x in self.hand)
        visible = tuple(int(x) for x in self.visible)
        if len(hand) != 34 or len(visible) != 34:
            raise InvalidEvaluationInput("hand and visible must have 34 tiles")
        # Direct offline callers often provide only a hand.  In that narrow
        # case the least surprising visible view is the hand itself; an
        # explicitly non-zero public vector is always respected.
        if not any(visible) and any(hand):
            visible = hand
        if any(x < 0 for x in hand + visible):
            raise InvalidEvaluationInput("negative tile count")
        if not 0 <= self.locked <= 4:
            raise InvalidEvaluationInput(f"invalid locked={self.locked}")
        if any(x > 4 for x in visible):
            raise InvalidEvaluationInput("visible count exceeds four copies")
        if any(hand[i] > visible[i] for i in range(34)):
            raise InvalidEvaluationInput("hand is not included in visible")
        object.__setattr__(self, "hand", hand)
        object.__setattr__(self, "visible", visible)
        object.__setattr__(self, "melds", tuple(self.melds or ()))

    @classmethod
    def from_game(cls, g, seat: int, phase: Optional[str] = None):
        hand = tuple(g.hands[seat])
        visible = tuple(g.visible_counts(seat))
        pending = getattr(g, "pending", None)
        owner, tile = pending if pending is not None else (None, None)
        return cls(
            hand=hand,
            locked=len(g.melds[seat]),
            visible=visible,
            melds=tuple(tuple(m) for m in g.melds[seat]),
            chows=int(getattr(g, "chows", [0] * 4)[seat]),
            phase=phase or getattr(g, "phase", "discard"),
            you_cai_bi_kao=bool(getattr(g, "you_cai_bi_kao", False)),
            freeze=bool(getattr(g, "in_freeze", lambda _s: False)(seat)),
            freezer=getattr(g, "freezer", -1),
            live_wall=max(0, int(getattr(g, "live_wall_left", lambda: 0)())),
            drawn=getattr(g, "drawn", [None] * 4)[seat],
            kong_draw=bool(getattr(g, "_kong_draw", False)),
            seat=seat,
            pending_owner=owner,
            pending_tile=tile,
        )

    def replace(self, **changes) -> "EvalContext":
        values = {name: getattr(self, name) for name in self.__dataclass_fields__}
        values.update(changes)
        return EvalContext(**values)

    def fast_replace(self, *, hand=_MISSING, locked=_MISSING,
                     visible=_MISSING, melds=_MISSING, chows=_MISSING,
                     phase=_MISSING, you_cai_bi_kao=_MISSING,
                     freeze=_MISSING, freezer=_MISSING, live_wall=_MISSING,
                     drawn=_MISSING, kong_draw=_MISSING, seat=_MISSING,
                     pending_owner=_MISSING, pending_tile=_MISSING):
        """Create an internally trusted child context without validation.

        All callers are derived from a validated context and only change
        tile counts or the small set of rule fields shown above.  Public
        constructors and :meth:`replace` retain full validation; the fast
        path avoids rebuilding the dataclass field dictionary and scanning
        two 34-element vectors for every hypothetical draw/discard node.
        """
        child = object.__new__(EvalContext)
        for name, value in (
            ("hand", hand), ("locked", locked), ("visible", visible),
            ("melds", melds), ("chows", chows), ("phase", phase),
            ("you_cai_bi_kao", you_cai_bi_kao), ("freeze", freeze),
            ("freezer", freezer), ("live_wall", live_wall),
            ("drawn", drawn), ("kong_draw", kong_draw), ("seat", seat),
            ("pending_owner", pending_owner), ("pending_tile", pending_tile),
        ):
            object.__setattr__(child, name,
                               getattr(self, name) if value is _MISSING else value)
        return child

    def cache_key(self, profile: EvalProfile, level: str = "Q"):
        return (self.hand, self.locked, self.visible, self.melds,
                self.chows, self.phase, self.you_cai_bi_kao, self.freeze,
                self.freezer, self.live_wall, self.drawn, self.kong_draw,
                self.pending_owner, self.pending_tile, profile.fingerprint,
                level)

    @property
    def remaining(self):
        return tuple(max(0, 4 - x) for x in self.visible)

    @property
    def unknown_pool(self):
        return sum(self.remaining)

    def with_draw(self, tile: int) -> "EvalContext":
        if not 0 <= tile < 34 or self.remaining[tile] <= 0:
            raise InvalidEvaluationInput(f"tile {tile} is not unseen")
        hand = list(self.hand)
        visible = list(self.visible)
        hand[tile] += 1
        visible[tile] += 1
        return self.replace(hand=tuple(hand), visible=tuple(visible),
                           drawn=tile)


@dataclass(frozen=True)
class Decomposition:
    """One material-safe standard/七对 allocation."""

    kind: str = "standard"
    melds: tuple = ()             # (kind, natural tiles, wilds)
    pair: Optional[tuple] = None  # (natural tiles, wilds)
    extra_pairs: tuple = ()
    taatsu: tuple = ()            # (kind, natural tiles, wilds)
    singles: tuple = ()
    wild_left: int = 0
    score: int = 99
    pair_count: int = 0
    head_index: int = -1
    # Wildcards left after explicit units may still be consumed by the
    # canonical shanten allocation (complete a taatsu, become the head, or
    # pair up into a meld).  Keep that implicit allocation visible instead of
    # reporting a score that secretly reuses ``wild_left``.
    implicit_wild: int = 0

    @property
    def wild_used(self):
        total = sum(int(u[2]) for u in self.melds)
        total += int(self.pair[1]) if self.pair else 0
        total += sum(int(u[1]) for u in self.extra_pairs)
        total += sum(int(u[2]) for u in self.taatsu)
        total += int(self.implicit_wild)
        return total

    @property
    def material_used(self):
        used = [0] * 34
        for _kind, tiles, _wild in self.melds:
            for t in tiles:
                used[t] += 1
        for unit in ((self.pair,) if self.pair else ()):
            for t in unit[0]:
                used[t] += 1
        for tiles, _wild in self.extra_pairs:
            for t in tiles:
                used[t] += 1
        for _kind, tiles, _wild in self.taatsu:
            for t in tiles:
                used[t] += 1
        used[W] += self.wild_used
        return tuple(used)

    def as_json(self):
        def unit(u):
            if len(u) == 3 and isinstance(u[0], str):
                return {"kind": u[0], "tiles": list(u[1]), "wild": u[2]}
            return {"tiles": list(u[0]), "wild": u[1]}
        return {
            "kind": self.kind,
            "melds": [unit(u) for u in self.melds],
            "pair": unit(("pair", self.pair[0], self.pair[1]))
            if self.pair else None,
            "extra_pairs": [unit(("pair", u[0], u[1]))
                            for u in self.extra_pairs],
            "taatsu": [unit(u) for u in self.taatsu],
            "singles": list(self.singles), "wild_left": self.wild_left,
            "wild_used": self.wild_used, "score": self.score,
            "pair_count": self.pair_count, "head_index": self.head_index,
            "implicit_wild": self.implicit_wild,
        }


def _decomposition_marker(d: Decomposition):
    """Cheap stable identity for bounded traversal and de-duplication.

    The previous hot path repeatedly materialized nested JSON dictionaries
    solely to sort or identify a decomposition.  The immutable tuple fields
    already contain the same identity; their repr preserves deterministic
    ordering without allocating the explanation payload.
    """
    return repr((d.kind, d.melds, d.pair, d.extra_pairs, d.taatsu,
                 d.singles, d.wild_left, d.score, d.pair_count,
                 d.head_index, d.implicit_wild))


@dataclass(frozen=True)
class CandidateEvaluation:
    tile: int
    shanten: int
    ukeire_tiles: tuple
    u1: int
    p1: float
    improvement: Optional[float]
    h2: Optional[float]
    b: float
    c: float
    q0: float
    q: Optional[float]
    best_discard: Optional[int] = None
    level: str = "Q0"
    complete: bool = True
    model_assumption: str = MODEL_ASSUMPTION
    decomposition: Optional[dict] = None
    improvement_paths: tuple = ()
    weighted_contributions: Optional[dict] = None
    nodes: int = 0
    kernel_calls: int = 0
    elapsed_ms: float = 0.0
    fallback_reason: Optional[str] = None

    @property
    def I(self):
        return self.improvement

    @property
    def H2(self):
        return self.h2

    @property
    def B(self):
        return self.b

    @property
    def C(self):
        return self.c

    @property
    def Q0(self):
        return self.q0

    @property
    def Q(self):
        return self.q

    def as_json(self):
        return {
            "tile": self.tile, "shanten": self.shanten,
            "ukeire_tiles": list(self.ukeire_tiles), "U1": self.u1,
            "p1": self.p1, "I": self.improvement, "H2": self.h2,
            "B": self.b, "C": self.c, "Q0": self.q0, "Q": self.q,
            "best_discard": self.best_discard, "level": self.level,
            "complete": self.complete,
            "model_assumption": self.model_assumption,
            "decomposition": self.decomposition,
            "improvement_paths": list(self.improvement_paths),
            "weighted_contributions": self.weighted_contributions,
            "nodes": self.nodes, "kernel_calls": self.kernel_calls,
            "elapsed_ms": self.elapsed_ms,
            "fallback_reason": self.fallback_reason,
        }


@dataclass(frozen=True)
class HandEvaluation:
    """A complete evaluation result suitable for JSON logging."""

    profile: str
    profile_fingerprint: str
    level: str
    shanten: int
    ukeire_tiles: tuple
    u1: int
    p1: float
    improvement: Optional[float]
    h2: Optional[float]
    b: float
    c: float
    q0: float
    q: Optional[float]
    best_discard: Optional[int] = None
    decomposition: Optional[dict] = None
    reason: str = ""
    candidates: tuple = ()
    selected: Optional[dict] = None
    improvement_paths: tuple = ()
    weighted_contributions: Optional[dict] = None
    legacy_best: Optional[int] = None
    model_assumption: str = MODEL_ASSUMPTION
    nodes: int = 0
    kernel_calls: int = 0
    elapsed_ms: float = 0.0
    fallback_reason: Optional[str] = None
    complete: bool = True

    @property
    def I(self):
        return self.improvement

    @property
    def H2(self):
        return self.h2

    @property
    def B(self):
        return self.b

    @property
    def C(self):
        return self.c

    @property
    def Q0(self):
        return self.q0

    @property
    def Q(self):
        return self.q

    def as_json(self):
        return {
            "version": self.profile, "profile": self.profile,
            "profile_fingerprint": self.profile_fingerprint,
            "level": self.level, "shanten": self.shanten,
            "ukeire_tiles": list(self.ukeire_tiles), "U1": self.u1,
            "p1": self.p1, "I": self.improvement, "H2": self.h2,
            "B": self.b, "C": self.c, "Q0": self.q0, "Q": self.q,
            "best_discard": self.best_discard, "reason": self.reason,
            "decomposition": self.decomposition,
            "improvement_paths": list(self.improvement_paths),
            "weighted_contributions": self.weighted_contributions,
            "candidates": list(self.candidates), "selected": self.selected,
            "legacy_best": self.legacy_best,
            "model_assumption": self.model_assumption,
            "nodes": self.nodes, "kernel_calls": self.kernel_calls,
            "elapsed_ms": self.elapsed_ms,
            "fallback_reason": self.fallback_reason,
            "complete": self.complete,
        }

    to_json = as_json


class _Budget:
    def __init__(self, node_limit, time_limit_ms):
        self.node_limit = max(0, int(node_limit))
        self.time_limit_ms = max(0.0, float(time_limit_ms))
        self.started = time.monotonic()
        self.nodes = 0
        self.kernel_calls = 0
        self.exceeded = None
        # These caches live for one complete decision.  A Q0 pass and its Q
        # continuation therefore share exact base features, while separate
        # decisions cannot leak mutable evaluation state into one another.
        self.base_cache = {}
        self.future_cache = {}

    def tick(self, n=1):
        if self.exceeded:
            return False
        # Check before expanding, so the reported node count never hides an
        # over-budget branch.  A cache hit is still a logical node.
        if self.nodes + n > self.node_limit:
            self.exceeded = "node_budget"
            return False
        if (time.monotonic() - self.started) * 1000.0 >= self.time_limit_ms:
            self.exceeded = "time_budget"
            return False
        self.nodes += n
        return True

    def kernel(self):
        self.kernel_calls += 1

    @property
    def elapsed_ms(self):
        return (time.monotonic() - self.started) * 1000.0


# Decomposition cache is bounded and keyed by every hand-affecting argument.
_DECOMP_CACHE = OrderedDict()
_DECOMP_LOCK = threading.RLock()
_DECOMP_CAP = 8192


def clear_cache():
    with _DECOMP_LOCK:
        _DECOMP_CACHE.clear()


def warmup(profile="shape-v1"):
    """Prime Rust dispatch and the bounded decomposition cache off-window."""
    p = profile_for(profile)
    sample = (1, 1, 2, 0, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0,
              1, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1)
    try:
        evaluate_standing(EvalContext(hand=sample, visible=sample), p,
                          level="Q0")
    except (InvalidEvaluationInput, ValueError):
        # A warmup must never make a runner fail; this branch is only for a
        # future ruleset whose sample hand size changes.
        pass


def _score_like(m, t, p, w, locked, n):
    need_melds = 4 - locked
    sup = min(w, t)
    melds = m + sup
    w -= sup
    t -= sup
    pair = 1 if p >= 1 or w >= 1 else 0
    if p >= 1:
        p -= 1
    elif w:
        w -= 1
    melds += w // 2
    w %= 2
    t_all = t + w + p
    gaps = max(0, need_melds - melds)
    return 2 * gaps - min(max(0, t_all), gaps) - pair


def _wild_accounting(taatsu_n, pair_n, wild):
    """Return ``(implicit_used, left)`` for the shared shanten allocation.

    Explicit units already deducted their wildcard substitutions before this
    helper is called.  The remaining wildcards are consumed in the same order
    as :func:`mj.shanten._score`: complete existing taatsu, make a head, then
    form all-wild melds.  Recording this split makes decomposition material
    conservation auditable even for hands containing several white tiles.
    """
    used = 0
    w = max(0, int(wild))
    t = max(0, int(taatsu_n))
    take = min(w, t)
    used += take
    w -= take
    if pair_n >= 1:
        # A natural pair is the head; no wildcard is consumed here.
        pair_n -= 1
    elif w:
        used += 1
        w -= 1
    meld_w = (w // 2) * 2
    used += meld_w
    w -= meld_w
    return used, w


def _unit_key(melds, pairs, taatsu, wild):
    return (tuple(melds), tuple(pairs), tuple(taatsu), wild)


def _enumerate_standard(counts, locked, limit=512, target=None,
                        stop_after_target=False, prune=True):
    """Bounded, material-safe DFS over natural units.

    The first remaining natural tile is always consumed by the selected
    branch.  This avoids overlapping 1234/889 allocations while still
    retaining their alternative decompositions.  Wildcards are accounted for
    explicitly in every unit and are never silently reused.
    """
    natural = list(counts[:33])
    wild0 = counts[W]
    need_melds = 4 - locked
    out = []
    seen = set()
    # The traversal order is chosen for useful explanations, not for proving
    # optimality.  A plain ``return once len(out) == limit`` therefore used
    # to let a late 1-shanten allocation be hidden behind the first 128
    # 2-shanten variants.  Keep a conservative shanten lower bound while
    # traversing, and (when the caller supplies the canonical target) retain
    # only target-level decompositions.  This keeps the search bounded while
    # making ``min(result.score)`` independent of the output cap.
    save = (0, 0, 1, 2, 2, 3, 4, 4, 5, 6, 6, 7, 8, 8, 9)
    total_natural = sum(natural)
    total_count = sum(counts)
    standing_count = total_count == 13 - 3 * locked
    need_base = 2 * need_melds

    def lower_bound(meld_n, taatsu_n, pair_n, wild, rem_natural):
        idx = min(len(save) - 1, max(0, rem_natural))
        value = (need_base - 2 * meld_n - taatsu_n - pair_n
                 - 2 * wild - save[idx])
        # A standing hand cannot have negative shanten; the 14-tile state can
        # reach -1 and is intentionally left unclamped.
        if standing_count:
            value = max(0, value)
        return value

    def current_cap():
        if len(out) < max(1, limit):
            return None
        return max(d.score for d in out)

    def add_result(melds, pairs, taatsu, singles, wild):
        # One pair can be the head; the remaining pair units are retained as
        # taatsu by the shanten formula.  Emit a head variant and a no-head
        # variant so pair purpose is observable.
        variants = [-1] + list(range(len(pairs)))
        for head in variants:
            key = _unit_key(melds, pairs, taatsu, wild) + (head, tuple(singles))
            if key in seen:
                continue
            seen.add(key)
            if head >= 0:
                pair = pairs[head]
                extras = tuple(p for i, p in enumerate(pairs) if i != head)
            else:
                pair = None
                extras = tuple(pairs)
            score = _score_like(len(melds), len(taatsu), len(pairs), wild,
                                locked, total_count)
            if standing_count:
                score = max(0, score)
            implicit, wild_left = _wild_accounting(
                len(taatsu), len(pairs), wild)
            if target is not None and score > target:
                continue
            value = Decomposition(
                kind="standard", melds=tuple(melds), pair=pair,
                extra_pairs=extras, taatsu=tuple(taatsu),
                singles=tuple(singles), wild_left=wild_left, score=score,
                pair_count=len(pairs), head_index=head,
                implicit_wild=implicit)
            if not prune:
                out.append(value)
                continue
            out.append(value)
            cap = max(1, limit)
            if len(out) > cap:
                # Remove only the currently worst result.  Ties are kept in
                # deterministic traversal order until the final sort; all
                # target-level results have equal score and are therefore
                # retained as far as the requested cap allows.
                worst = max(range(len(out)),
                            key=lambda j: (out[j].score,
                                           _decomposition_marker(out[j])))
                out.pop(worst)

    def walk(start, melds, pairs, taatsu, singles, wild, rem_natural):
        if not prune and len(out) >= limit:
            return
        if stop_after_target and target is not None and out:
            return
        if prune:
            lb = lower_bound(len(melds), len(taatsu), len(pairs), wild,
                             rem_natural)
            if target is not None and lb > target:
                return
            cap = current_cap()
            if cap is not None and lb > cap:
                return
        i = next((x for x in range(start, 33) if natural[x]), None)
        if i is None:
            add_result(melds, pairs, taatsu, singles, wild)
            return
        if len(melds) < need_melds:
            # Triplet with 0..2 wild substitutions.  At least one natural
            # copy is required, so 财神 can never create a natural PONG.
            for k in range(min(3, natural[i]), 0, -1):
                need_w = 3 - k
                if wild < need_w:
                    continue
                natural[i] -= k
                walk(i, melds + (("triplet", (i,) * k, need_w),), pairs,
                     taatsu, singles, wild - need_w, rem_natural - k)
                natural[i] += k

            # A sequence may contain the first natural tile in any position;
            # missing members are paid with distinct wildcards.
            if i < 27:
                lo = i - (i % 9)
                for start_tile in (i - 2, i - 1, i):
                    if not (lo <= start_tile <= i and start_tile % 9 <= 6):
                        continue
                    seq = tuple(range(start_tile, start_tile + 3))
                    req = [1 if natural[x] else 0 for x in seq]
                    if not any(x == i for x in seq):
                        continue
                    # Use available natural copies, including duplicates, and
                    # fill the rest with wilds.
                    if any(natural[x] < req[j] for j, x in enumerate(seq)):
                        continue
                    missing = 3 - sum(req)
                    if wild < missing:
                        continue
                    for x in seq:
                        natural[x] -= req[seq.index(x)]
                    natural_tiles = tuple(x for j, x in enumerate(seq) if req[j])
                    walk(start_tile,
                         melds + (("sequence", natural_tiles, missing),),
                         pairs, taatsu, singles, wild - missing,
                         rem_natural - sum(req))
                    for x in seq:
                        natural[x] += req[seq.index(x)]

        # Pair units are retained even after all meld slots are occupied: they
        # can still be the head or a seven-pair branch.
        for k in (2, 1):
            need_w = 2 - k
            if natural[i] < k or wild < need_w:
                continue
            natural[i] -= k
            walk(i, melds, pairs + (((i,) * k, need_w),), taatsu,
                 singles, wild - need_w, rem_natural - k)
            natural[i] += k

        # Incomplete two-tile sequence units.  Wild substitutions are useful
        # for B but deliberately excluded from C (public chow potential).
        if i < 27:
            lo = i - i % 9
            for j in (i + 1, i + 2):
                if j >= lo + 9 or natural[j] <= 0:
                    continue
                natural[i] -= 1
                natural[j] -= 1
                walk(i, melds, pairs, taatsu + (("taatsu", (i, j), 0),),
                     singles, wild, rem_natural - 2)
                natural[i] += 1
                natural[j] += 1
        if wild and i < 27:
            # A natural tile plus a wild can be completed into one of the two
            # surrounding sequences; record only once per tile.
            natural[i] -= 1
            walk(i, melds, pairs,
                 taatsu + (("taatsu", (i,), 1),), singles, wild - 1,
                 rem_natural - 1)
            natural[i] += 1

        # Giving up one copy creates an explicit single.  Keep this branch
        # last so the bounded traversal first discovers complete/taatsu
        # allocations (otherwise a hand such as 1234 can fill the cap with
        # singleton variants before seeing 123+4).
        natural[i] -= 1
        walk(i, melds, pairs, taatsu, singles + (i,), wild,
             rem_natural - 1)
        natural[i] += 1

    walk(0, (), (), (), (), wild0, total_natural)
    if not out:
        implicit, wild_left = _wild_accounting(0, 0, wild0)
        out.append(Decomposition(score=_score_like(0, 0, 0, wild0,
                                                   locked, total_count),
                                  wild_left=wild_left,
                                  implicit_wild=implicit))
    out.sort(key=lambda d: (d.score, -len(d.melds), -d.pair_count,
                            -len(d.taatsu), _decomposition_marker(d)))
    return tuple(out[:limit])


def _chiitoi_descriptor(counts, locked):
    if locked:
        return None
    pairs = sum(c // 2 for c in counts[:33])
    singles = sum(c % 2 for c in counts[:33])
    wild = counts[W]
    pair_wild = min(singles, wild)
    rest = wild - pair_wild
    pair_wild += (rest // 2) * 2
    pairs_after = pairs + min(singles, wild) + (rest // 2)
    wild_left = rest % 2
    s = 7 - pairs_after - (1 if singles + wild_left else 0)
    if sum(counts) == 13:
        s = max(0, s)
    return Decomposition(kind="chiitoi", score=s, pair_count=pairs_after,
                         wild_left=wild_left, implicit_wild=pair_wild)


def enumerate_decompositions(counts: Sequence[int], locked=0, limit=512,
                             optimal=True):
    counts = tuple(int(x) for x in counts)
    if len(counts) != 34:
        raise InvalidEvaluationInput("counts must have 34 entries")
    key = (counts, int(locked), int(limit), bool(optimal))
    with _DECOMP_LOCK:
        hit = _DECOMP_CACHE.get(key)
        if hit is not None:
            _DECOMP_CACHE.move_to_end(key)
            return hit
    # Use the canonical shanten as a target for the bounded decomposition
    # search by default.  The live evaluator can explicitly request the
    # latency-bounded best-effort traversal; its outer shanten comparison is
    # still hard, so this flag affects only optional B/C explanations.
    try:
        target = shanten(counts, locked) if optimal else None
    except (ValueError, TypeError):
        target = None
    std = _enumerate_standard(counts, locked, limit=limit, target=target,
                              prune=bool(optimal))
    chi = _chiitoi_descriptor(counts, locked)
    values = list(std)
    if chi is not None:
        values.append(chi)
    values.sort(key=lambda d: (d.score, 0 if d.kind == "standard" else 1,
                               _decomposition_marker(d)))
    result = tuple(values)
    with _DECOMP_LOCK:
        if len(_DECOMP_CACHE) >= _DECOMP_CAP:
            _DECOMP_CACHE.popitem(last=False)
        _DECOMP_CACHE[key] = result
    return result


def _remaining(ctx: EvalContext):
    rem = []
    for n in ctx.visible:
        if n > 4:
            raise InvalidEvaluationInput("visible count exceeds four copies")
        rem.append(max(0, 4 - n))
    return rem, sum(rem)


def _legal_waits(ctx: EvalContext, counts, vis):
    waits = []
    for t in range(34):
        if max(0, 4 - vis[t]) <= 0:
            continue
        c = list(counts)
        c[t] += 1
        # The canonical shanten implementation returns -1 exactly for a
        # fourteen-tile win.  Use it as the fast legal-HU test; the win DFS is
        # only needed for callers that explicitly inspect wildcard gate
        # details.
        if shanten(c, ctx.locked) >= 0:
            continue
        # A normal future draw is never treated as a杠开 draw.  In YCBK mode,
        # the standing hand must already be爆头 before a white-containing
        # hand can submit HU, matching Game._can_hu.
        if not _hu_gate_allows(ctx, counts, c):
            continue
        waits.append(t)
    return tuple(waits)


def _hu_gate_allows(ctx: EvalContext, standing, completed):
    """Return whether a normal (non-kong) future HU is rule-legal."""
    return not (ctx.you_cai_bi_kao and completed[W] > 0
                and not ctx.kong_draw
                and not is_baotou(standing, ctx.locked))


def _base_features(ctx: EvalContext, budget: Optional[_Budget] = None,
                   s_hint: Optional[int] = None,
                   rem_hint=None):
    cache = getattr(budget, "base_cache", None) if budget is not None else None
    cache_key = None
    if cache is not None:
        # These are the only context values read by U1, including the
        # white-tile HU gate for a tenpai hand.  Phase/structure fields are
        # handled by the caller and do not belong in this key.
        cache_key = (ctx.hand, ctx.locked, ctx.visible,
                     ctx.you_cai_bi_kao, ctx.kong_draw)
        cached = cache.get(cache_key)
        # A hit remains one logical node so warm and cold runs keep the same
        # node-budget semantics.
        if budget.tick():
            if cached is not None:
                return cached
        elif cached is not None:
            # Keep diagnostic callers useful even when they inspect a result
            # after the shared clock has expired.
            return cached
    if rem_hint is None:
        rem, n_unknown = _remaining(ctx)
    else:
        rem, n_unknown = rem_hint
    s = shanten(ctx.hand, ctx.locked) if s_hint is None else int(s_hint)
    if budget and s_hint is None:
        budget.kernel()
    if s == 0:
        waits = _legal_waits(ctx, ctx.hand, ctx.visible)
        u1 = sum(rem[t] for t in waits)
        tiles = waits
    elif s > 0:
        result = ukeire(ctx.hand, ctx.locked, ctx.visible)
        if budget:
            budget.kernel()
        tiles = tuple(result[1])
        # Rust and Python both return the count using the supplied visible;
        # recompute to keep invalid/zero pools explicit.
        u1 = sum(rem[t] for t in tiles)
    else:
        tiles, u1 = (), 0
    p1 = (u1 / n_unknown) if n_unknown else 0.0
    result = (s, tuple(tiles), u1, p1, tuple(rem), n_unknown)
    if cache is not None and cache_key is not None:
        cache[cache_key] = result
    return result


def _taatsu_effective_tiles(unit):
    kind, tiles, wild = unit
    if wild:
        # A natural+wild pair can be completed into any of the finite sequence
        # neighbours; enumerate actual legal sequence additions only.
        t = tiles[0]
        if t >= 27:
            return ()
        lo = t - t % 9
        return tuple(x for x in (t - 2, t - 1, t + 1, t + 2)
                     if lo <= x < lo + 9)
    if kind == "pair":
        return (tiles[0],) if tiles else ()
    if len(tiles) != 2:
        return ()
    a, b = tiles
    if a >= 27 or b >= 27:
        return ()
    lo = a - a % 9
    if b == a + 1:
        return tuple(x for x in (a - 1, b + 1) if lo <= x < lo + 9)
    if b == a + 2:
        return (a + 1,)
    return ()


def _structure_scores(ctx: EvalContext, s: int, rem,
                     profile: Optional[EvalProfile] = None):
    profile = profile or EvalProfile.shape_v1()
    # Four variants retain the first material-safe alternatives
    # (1234/3445/689/889) while keeping the complete-Q path inside the
    # discard-window budget.  The public enumerator still accepts a larger
    # limit for offline diagnostics and property tests.
    # The public enumerator retains several target-level alternatives for
    # offline explanations.  A live discard decision starts with the fast
    # traversal and, when the seven-pair branch is not the target, adds one
    # target-level standard allocation.  The latter stops at the first
    # optimal result (rather than enumerating hundreds of explanations), so
    # overlap-heavy hands retain a material-safe B/C witness without turning
    # the optional explanation into an unbounded window cost.
    fast = enumerate_decompositions(ctx.hand, ctx.locked, limit=4,
                                    optimal=False)
    chi = _chiitoi_descriptor(ctx.hand, ctx.locked)
    optimal = ()
    # The bounded unpruned walk is ordered to discover complete/taatsu
    # allocations first.  If it already exposes a minimum-score standard
    # witness, a second target-only DFS can only duplicate that witness and
    # needlessly consume the decision clock.  Keep the target DFS as a
    # correctness fallback for the rare case where the bounded walk misses
    # the minimum (and for chiitoi-leading hands no standard target is needed).
    has_fast_target = any(d.score == s for d in fast)
    if not (chi is not None and chi.score == s) and not has_fast_target:
        optimal = _enumerate_standard(
            ctx.hand, ctx.locked, limit=1, target=s,
            stop_after_target=True, prune=True)
    decomps = []
    seen = set()
    for d in tuple(optimal) + tuple(fast):
        if d.score != s:
            continue
        marker = _decomposition_marker(d)
        if marker not in seen:
            seen.add(marker)
            decomps.append(d)
    if not decomps:
        return 0.0, 0.0, None
    K = max(1, 4 - ctx.locked)
    best = None
    for d in decomps:
        if d.kind == "chiitoi":
            b = min(1.0, max(0.0, d.pair_count / 7.0))
            c = 0.0
        else:
            m = len(d.melds)
            h = 1 if d.pair is not None else 0
            # Extra natural pairs are incomplete meld-capable units; a head
            # pair is intentionally excluded from this sum.
            units = list(d.taatsu)
            units.extend(("pair", p[0], p[1]) for p in d.extra_pairs)
            vals = []
            for unit in units:
                effective = _taatsu_effective_tiles(unit)
                unseen = sum(rem[t] for t in effective)
                vals.append(min(1.0, unseen / 8.0))
            b = (2 * m + h + sum(vals)) / (2 * K + 1)

            c_parts = []
            # C counts only natural pairs not assigned as the head.
            if d.extra_pairs:
                for ptiles, pwild in d.extra_pairs:
                    if pwild == 0 and len(ptiles) == 2 \
                            and ptiles[0] == ptiles[1]:
                        c_parts.append(rem[ptiles[0]] / 2.0)
            if not ctx.freeze and ctx.chows < 2:
                chow_parts = []
                for unit in d.taatsu:
                    if unit[2] != 0 or len(unit[1]) != 2:
                        continue
                    vals_u = sum(rem[t] for t in _taatsu_effective_tiles(unit))
                    chow_parts.append(min(1.0, vals_u / 8.0))
                slots = min(2 - ctx.chows, max(0, K - m))
                c_parts.extend(sorted(chow_parts, reverse=True)[:slots])
            denom = max(1, K - m)
            c = min(1.0, max(0.0, sum(c_parts) / denom))
        score = profile.wB * b + profile.wC * c
        if best is None or (score, b, c) > best[:3]:
            best = (score, b, c, d)
    return best[1], best[2], best[3]


def _best_future_discard(ctx, drawn, budget: Optional[_Budget] = None,
                         include_tiles=True):
    future_cache = getattr(budget, "future_cache", None) if budget is not None else None
    future_key = None
    if future_cache is not None:
        future_key = (tuple(drawn), ctx.locked, ctx.visible,
                      ctx.you_cai_bi_kao, ctx.kong_draw, bool(include_tiles))
        cached = future_cache.get(future_key)
        if cached is not None:
            # A cached child is still one logical hypothetical node, but no
            # kernel call is charged because its result is already material.
            if not budget.tick():
                return None
            return cached
    # Batch the common, non-YCBK path in Rust.  The kernel computes all
    # minimum-shanten discard ties and their U1 totals in one FFI call; YCBK
    # still uses Python because its HU gate depends on the standing
    # 爆头/财神 context, which the generic kernel intentionally does not know.
    if not ctx.you_cai_bi_kao:
        kernel = _best_future_discard_kernel(
            list(drawn), ctx.locked, list(ctx.visible), include_tiles)
        if kernel is not None:
            discard, child_s, tiles, u1 = kernel
            if discard >= 0:
                child = list(drawn)
                child[discard] -= 1
                rem = tuple(max(0, 4 - n) for n in ctx.visible)
                n_unknown = sum(rem)
                p = (u1 / n_unknown) if n_unknown else 0.0
                if budget:
                    # One tick represents this batched hypothetical layer;
                    # the Rust internals are accounted as kernel calls below.
                    if not budget.tick():
                        return None
                    budget.kernel()
                result = ((p, u1, -discard), discard, tuple(child),
                          child_s, p, tuple(tiles))
                if future_cache is not None:
                    future_cache[future_key] = result
                return result
    best_s = None
    choices = []
    for d in range(34):
        if drawn[d] <= 0:
            continue
        if budget and not budget.tick():
            return None
        c = list(drawn)
        c[d] -= 1
        s = shanten(c, ctx.locked)
        if budget:
            budget.kernel()
        if best_s is None or s < best_s:
            best_s, choices = s, [(d, c)]
        elif s == best_s:
            choices.append((d, c))
    if not choices:
        return None
    # Future Q0 uses direct p1 only.  The same post-draw visible denominator
    # is used for every alternative; discarding does not reduce visible.
    # Compute it once for all tied best discards and pass the known shanten to
    # the base layer, avoiding a second 34-tile scan and Rust shanten call.
    vis = ctx.visible
    rem = tuple(max(0, 4 - n) for n in vis)
    n_unknown = sum(rem)
    best = None
    for d, c in choices:
        s2, tiles, u, p, _r, _n = _base_features(
            ctx.fast_replace(hand=tuple(c), visible=vis), budget,
            s_hint=best_s, rem_hint=(rem, n_unknown))
        key = (p, u, -d)
        if best is None or key > best[0]:
            best = (key, d, c, s2, p, tiles)
    if best is not None and future_cache is not None and future_key is not None:
        # Keep the cached child immutable; callers only inspect it while
        # constructing the next H2 state.
        best = (best[0], best[1], tuple(best[2]), best[3], best[4], best[5])
        future_cache[future_key] = best
    return best


def _future_lookahead(ctx, s, rem, n_unknown, budget, direct_tiles=None,
                      base_u1=None):
    """Compute I and H2 in one next-draw traversal.

    The previous implementation walked the same direct draw states once for
    I and again for H2.  A child draw and its best immediate discard are
    independent of which feature consumes them, so sharing that work keeps
    the feature definitions unchanged while cutting duplicate kernel calls.
    """
    need_i = n_unknown > 1
    need_h2 = ctx.live_wall > 1 and n_unknown > 0
    if not need_i and not need_h2:
        return 0.0, 0.0, (), True
    direct = (set(range(34)) if direct_tiles is None
              else {int(t) for t in direct_tiles})
    # For a fixed standing hand, the hold-after-draw baseline has the same
    # legal ukeire/wait tile set as the original base evaluation.  Only the
    # denominator and (when t itself is effective) one remaining copy change;
    # recomputing ``_base_features`` for every draw needlessly repeats a Rust
    # ukeire call and its 34-tile scan.  ``None`` keeps this helper useful to
    # offline callers that do not have the original U1 handy.
    base_tiles = direct
    base_u1_value = base_u1
    # I deliberately retains the full unseen-tile enumeration.  Replacing an
    # isolated tile can still alter the standard/七对 allocation and its U1,
    # so a structural-neighbour shortcut would change the exact feature.  The
    # safe reductions below skip only branches whose shanten bound proves
    # they cannot contribute.
    draw_tiles = range(34)
    total_i = 0.0
    total_h2 = 0.0
    paths = []
    for t in draw_tiles:
        weight = rem[t]
        if weight <= 0:
            continue
        if not budget.tick():
            return total_i, total_h2, tuple(paths), False
        drawn = list(ctx.hand)
        drawn[t] += 1
        vis2 = list(ctx.visible)
        vis2[t] += 1
        first_prob = weight / n_unknown
        drawn_s = shanten(drawn, ctx.locked)
        drawn_is_win = (drawn_s < 0
                        and _hu_gate_allows(ctx, ctx.hand, drawn))
        budget.kernel()
        if drawn_is_win:
            # A direct winning draw is a terminal H2 branch.  It is never an
            # I improvement, matching the two original feature functions.
            if need_h2 and t in direct:
                total_h2 += first_prob
            continue

        # Adding one tile can lower shanten by at most one.  A direct
        # improvement therefore cannot contribute to I (the best discard is
        # strictly below the standing s).  H2 can only reach a tenpai child
        # from s==1; for s>=2 these direct branches are provably irrelevant
        # and are skipped before the expensive best-discard search.
        if drawn_s < s and s >= 2:
            continue

        # I considers every remaining draw that can preserve s; H2 considers
        # only its proven direct effective set.  Both features use the same
        # post-draw child when a branch is relevant to either feature.
        child_ctx = ctx.fast_replace(visible=tuple(vis2))
        post = _best_future_discard(child_ctx, drawn, budget,
                                    include_tiles=False)
        if post is None:
            return total_i, total_h2, tuple(paths), False
        _key, d, child, child_s, child_p1, child_tiles = post

        if need_i and child_s == s:
            # Baseline is the original hand held after this same draw.  A
            # shrinking unknown pool alone therefore contributes zero.
            # Keep the original standing hand as the hold baseline while
            # updating only the visible pool for the hypothetical draw.  This
            # matches the established I definition and keeps the child at a
            # valid standing tile count.
            if base_u1_value is None:
                held_s, _held_tiles, _held_u, held_p1, _r2, _n2 = _base_features(
                    child_ctx, budget)
            else:
                held_s = s
                n2 = max(0, n_unknown - 1)
                held_u = base_u1_value - (1 if t in base_tiles else 0)
                held_p1 = (held_u / n2) if n2 else 0.0
            if held_s == s:
                gain = max(0.0, child_p1 - held_p1)
                if gain:
                    if not child_tiles:
                        _child_result = ukeire(child, ctx.locked, vis2)
                        child_tiles = tuple(_child_result[1])
                        budget.kernel()
                    contribution = first_prob * gain
                    total_i += contribution
                    paths.append({"draw": t, "remaining": weight,
                                  "best_discard": d, "p1": child_p1,
                                  "hold_p1": held_p1, "gain": gain,
                                  "contribution": contribution,
                                  "ukeire_tiles": list(child_tiles)})

        if need_h2 and t in direct and child_s == 0:
            waits = _legal_waits(
                child_ctx.fast_replace(hand=tuple(child), kong_draw=False),
                child, vis2)
            n2 = max(0, n_unknown - 1)
            if n2:
                total_h2 += first_prob * sum(
                    max(0, 4 - vis2[w]) for w in waits) / n2
    return (min(1.0, max(0.0, total_i)),
            min(1.0, max(0.0, total_h2)), tuple(paths), True)


def evaluate_standing(ctx: EvalContext, profile=None, level="Q0",
                      budget: Optional[_Budget] = None,
                      base_evaluation: Optional[HandEvaluation] = None
                      ) -> HandEvaluation:
    """Evaluate one standing hand.  ``level=Q`` adds I/H2.

    ``base_evaluation`` is an internal continuation supplied when the exact
    Q0 result for this same context is already complete.  Reusing its U1 and
    structure fields avoids a second decomposition/base pass before Q starts;
    public callers can omit it and retain the original behavior.
    """
    profile = profile_for(profile)
    expected = 13 - 3 * ctx.locked
    if sum(ctx.hand) != expected:
        raise InvalidEvaluationInput(
            f"standing hand has {sum(ctx.hand)} tiles, expected {expected}")
    started = time.monotonic()
    budget = budget or _Budget(
        profile.discard_node_budget,
        profile.discard_time_budget_ms)
    reuse_base = (level.upper() == "Q"
                  and base_evaluation is not None
                  and base_evaluation.profile_fingerprint == profile.fingerprint
                  and base_evaluation.complete)
    if reuse_base:
        s = base_evaluation.shanten
        tiles = tuple(base_evaluation.ukeire_tiles)
        u1 = base_evaluation.u1
        rem, n_unknown = _remaining(ctx)
        p1 = (u1 / n_unknown) if n_unknown else 0.0
        b, c = base_evaluation.b, base_evaluation.c
        decomposition = base_evaluation.decomposition
    else:
        s, tiles, u1, p1, rem, n_unknown = _base_features(ctx, budget)
        # Reaction Q0 uses the same public evaluator and score fields, but its
        # phase-specific structure feature is bounded by the short response
        # window.  Full discard Q0 retains the material-safe decomposition DFS.
        if level.upper() == "Q0" and ctx.phase == "react":
            b, c = _reaction_q0_shape(ctx, s, rem)
            d = None
        else:
            b, c, d = _structure_scores(ctx, s, rem, profile)
        decomposition = d.as_json() if d else None
    q0 = p1 + profile.wB * b + profile.wC * c
    improvement = h2 = None
    paths = ()
    complete = True
    fallback = None
    if level.upper() == "Q":
        improvement, h2, paths, ok = _future_lookahead(
            ctx, s, rem, n_unknown, budget, direct_tiles=tiles,
            base_u1=u1)
        if not ok:
            complete = False
            fallback = budget.exceeded or "future_budget"
        q = (p1 + profile.wI * improvement + profile.wH * h2
             + profile.wB * b + profile.wC * c) if complete else None
    else:
        q = q0
    return HandEvaluation(
        profile=profile.name, profile_fingerprint=profile.fingerprint,
        level=("Q" if level.upper() == "Q" and complete else "Q0"),
        shanten=s, ukeire_tiles=tuple(tiles), u1=u1, p1=p1,
        improvement=improvement, h2=h2, b=b, c=c, q0=q0, q=q,
        decomposition=decomposition,
        improvement_paths=paths,
        weighted_contributions={
            "p1": p1, "I": profile.wI * improvement if improvement is not None else None,
            "H2": profile.wH * h2 if h2 is not None else None,
            "B": profile.wB * b, "C": profile.wC * c,
        },
        model_assumption=MODEL_ASSUMPTION, nodes=budget.nodes,
        kernel_calls=budget.kernel_calls,
        elapsed_ms=round((time.monotonic() - started) * 1000.0, 3),
        fallback_reason=fallback, complete=complete)


def _legacy_key(tile, uke, shape=0, feed=0):
    return (tile == W, -uke, shape, feed, tile)


def _shape_cost(hand, tile):
    n = hand[tile]
    if tile >= 27:
        return 10 if n >= 3 else 6 if n == 2 else 0
    lo = tile - tile % 9
    cost = 2 + (8 if n >= 3 else 4 if n == 2 else 0)
    for delta in (-1, 1):
        x = tile + delta
        if lo <= x < lo + 9 and hand[x]:
            cost += 3
    for delta in (-2, 2):
        x = tile + delta
        if lo <= x < lo + 9 and hand[x]:
            cost += 1
    return cost


def _feed_risk(g, seat, tile):
    if g is None:
        return 0.0
    nxt = (seat + 1) % 4
    seen = [0] * 34
    for d in g.discards[nxt]:
        seen[d] += 1
    for kind, mt in g.melds[nxt]:
        if kind == "chow":
            for x in (mt, mt + 1, mt + 2):
                seen[x] += 1
        else:
            seen[mt] += 3
    risk = seen[tile] * 0.3
    if tile < 27:
        lo = tile - tile % 9
        risk += sum(seen[x] * 0.5 for x in
                    (tile - 1, tile + 1, tile - 2, tile + 2)
                    if lo <= x < lo + 9)
    return risk


def evaluate_discard_candidates(g, seat, profile=None):
    """Return ``(action, HandEvaluation)`` for an opt-in shape evaluator."""
    profile = profile_for(profile or "shape-v1")
    ctx = EvalContext.from_game(g, seat, phase="discard")
    legal = [a for a in g.legal_actions() if 0 <= a <= 33]
    if not legal:
        raise InvalidEvaluationInput("no legal discard candidate")
    # Keep freeze and rule legality authoritative.  HU/财飘/KONG branches are
    # handled by mj.bot before reaching this function.
    best_s = None
    cands = []
    for t in sorted(set(legal)):
        c = list(ctx.hand)
        c[t] -= 1
        s = shanten(c, ctx.locked)
        if best_s is None or s < best_s:
            best_s, cands = s, [(t, c)]
        elif s == best_s:
            cands.append((t, c))
    # A single shared budget makes fallback semantics visible.  Q0 is complete
    # before any Q result is allowed to influence selection.
    budget = _Budget(profile.discard_node_budget,
                     profile.discard_time_budget_ms)
    q0_results = []
    legacy_best = None
    for t, c in cands:
        ev_ctx = ctx.fast_replace(hand=tuple(c))
        ev = evaluate_standing(ev_ctx, profile, level="Q0", budget=budget)
        uke = ev.u1
        key = _legacy_key(t, uke, _shape_cost(ctx.hand, t),
                          _feed_risk(g, seat, t))
        if legacy_best is None or key < legacy_best[0]:
            legacy_best = (key, t)
        q0_results.append((t, c, ev))
        if budget.exceeded:
            break
    if len(q0_results) != len(cands):
        # The legacy implementation remains the safe, complete fallback.
        from .bot import choose_discard
        action = choose_discard(g, seat)
        return action, HandEvaluation(
            profile=profile.name, profile_fingerprint=profile.fingerprint,
            level="legacy", shanten=best_s, ukeire_tiles=(), u1=0, p1=0.0,
            improvement=None, h2=None, b=0.0, c=0.0, q0=0.0, q=None,
            best_discard=action, reason="budget_fallback_legacy",
            legacy_best=action, fallback_reason=budget.exceeded,
            complete=False, nodes=budget.nodes,
            kernel_calls=budget.kernel_calls,
            elapsed_ms=round(budget.elapsed_ms, 3))
    def q_upper_bound(item):
        """A conservative Q upper bound for proven candidate pruning.

        ``I`` is an average of gains whose child p1 is at most one.  ``H2``
        only visits direct effective draws, so it is bounded by the current
        direct p1 when the standing hand is at most one shanten; for
        shanten >=2 the combined lookahead proves H2 is zero.  The bound is
        used only for the non-negative shape weights and never discards an
        equal-valued candidate, preserving all declared tie-breaks.
        """
        _tile, _hand, ev = item
        if profile.wI < 0 or profile.wH < 0:
            return float("inf")
        n = max(0, sum(max(0, 4 - x) for x in ctx.visible))
        if n <= 1:
            i_max = 0.0
        else:
            n2 = n - 1
            direct = set(ev.ukeire_tiles)
            i_max = 0.0
            for draw, weight in enumerate(max(0, 4 - x)
                                           for x in ctx.visible):
                if weight <= 0:
                    continue
                hold_u = ev.u1 - (1 if draw in direct else 0)
                hold_p = max(0.0, hold_u) / n2
                i_max += (weight / n) * max(0.0, 1.0 - hold_p)
            i_max = min(1.0, max(0.0, i_max))
        h_max = ev.p1 if ev.shanten <= 1 else 0.0
        return ev.q0 + profile.wI * i_max + profile.wH * h_max

    # Evaluate the candidates with the largest possible Q first.  Once one
    # complete Q is known, a strict upper-bound loser needs no expensive
    # future traversal; it is retained in the result table as an explicit
    # ``q_pruned`` Q0 entry rather than being silently dropped.
    ordered_q0 = sorted(q0_results,
                        key=lambda item: (-q_upper_bound(item), item[0]))
    q_by_tile = {}
    q_pruned = {}
    for item in ordered_q0:
        t, c, q0 = item
        upper = q_upper_bound(item)
        best_q_value = max((ev.q for ev in q_by_tile.values()
                            if ev.q is not None), default=None)
        if best_q_value is not None and upper < best_q_value:
            q_pruned[t] = upper
            continue
        # Q begins with another base/decomposition pass that is not
        # interruptible in the Rust/Python kernel.  Refuse to enter that pass
        # once the shared clock has already expired; otherwise a last
        # candidate can overrun the discard budget by an entire traversal.
        if not budget.tick(0):
            break
        q = evaluate_standing(ctx.fast_replace(hand=tuple(c)), profile,
                              level="Q", budget=budget,
                              base_evaluation=q0)
        if not q.complete:
            break
        q_by_tile[t] = q
        if budget.exceeded:
            break
    complete_q = (not budget.exceeded
                  and len(q_by_tile) + len(q_pruned) == len(cands))
    # A single Rust/Python kernel call is not pre-emptible.  It can finish
    # just after the last internal clock check, so perform one final check
    # before advertising a complete Q result instead of hiding that overrun
    # behind an upper-bound-pruned tail.
    if complete_q and not budget.tick(0):
        complete_q = False
    if complete_q:
        q_results = []
        for t, c, q0 in q0_results:
            q_results.append((t, c, q_by_tile.get(t, q0)))
    else:
        q_results = [(t, c, q_by_tile[t]) for t, c, _q0 in q0_results
                     if t in q_by_tile]
    results = q_results if complete_q else q0_results
    level = "Q" if complete_q else "Q0"
    def score(item):
        t, _c, ev = item
        value = ev.q if level == "Q" else ev.q0
        # Preserve财神 protection whenever a non-white same-shanten candidate
        # exists.  It is a hard tie-break, never an after-the-fact exception.
        return (value, ev.p1, -_shape_cost(ctx.hand, t),
                -_feed_risk(g, seat, t), -t)
    non_w = [x for x in results if x[0] != W]
    pool = non_w or list(results)
    selected_t, selected_c, selected_ev = max(pool, key=score)
    serialized = []
    for t, c, ev in results:
        item = ev.as_json()
        item.update({"tile": t, "shape_cost": _shape_cost(ctx.hand, t),
                     "feed_risk": _feed_risk(g, seat, t),
                     "selected": t == selected_t})
        if complete_q and t in q_pruned:
            item.update({"q_pruned": True,
                         "q_upper_bound": q_pruned[t]})
        serialized.append(item)
    reason = ("complete_Q" if complete_q else "complete_Q0_fallback")
    total_nodes = sum(ev.nodes for _, _, ev in results)
    total_kernels = sum(ev.kernel_calls for _, _, ev in results)
    return selected_t, HandEvaluation(
        profile=profile.name, profile_fingerprint=profile.fingerprint,
        level=level, shanten=best_s,
        ukeire_tiles=selected_ev.ukeire_tiles, u1=selected_ev.u1,
        p1=selected_ev.p1, improvement=selected_ev.improvement,
        h2=selected_ev.h2, b=selected_ev.b, c=selected_ev.c,
        q0=selected_ev.q0, q=selected_ev.q,
        best_discard=selected_t, reason=reason,
        candidates=tuple(serialized), selected=serialized[[x[0] for x in results].index(selected_t)],
        legacy_best=legacy_best[1], model_assumption=MODEL_ASSUMPTION,
        nodes=total_nodes, kernel_calls=total_kernels,
        elapsed_ms=round(budget.elapsed_ms, 3),
        fallback_reason=None if complete_q else (budget.exceeded or "q_incomplete"),
        complete=complete_q)


def _claim_options(g, seat, acts):
    owner, tile = g.pending
    hand = list(g.hands[seat])
    locked = len(g.melds[seat])
    out = []
    if PONG in acts:
        c = hand[:]
        c[tile] -= 2
        out.append((PONG, c, locked + 1, 0))
    for a in (CHOW_LOW, CHOW_MID, CHOW_HIGH):
        if a not in acts:
            continue
        pos = CHOW_LOW - a
        start = tile - pos
        c = hand[:]
        for x in (start, start + 1, start + 2):
            if x != tile:
                c[x] -= 1
        out.append((a, c, locked + 1, 1))
    return out


def _reaction_q0_shape(ctx, s, rem):
    """Cheap, material-safe B/C proxy for a reaction-window Q0 pass.

    Full decomposition explanations are useful on a discard turn but can
    consume the entire 7 ms reaction budget for one candidate.  Reaction Q0
    still needs the same shape weights, so use bounded natural-unit counts
    (pairs, adjacent/nearby sequences and triplets) with the same [0, 1]
    range as ``_structure_scores``.  The complete Q path below retains the
    exact decomposition when the deadline leaves room for it.
    """
    hand = ctx.hand
    pairs = sum(1 for n in hand[:33] if n >= 2)
    triplets = sum(1 for n in hand[:33] if n >= 3)
    runs = 0
    near_runs = 0
    for base in (0, 9, 18):
        for i in range(base, base + 8):
            if hand[i] and hand[i + 1]:
                runs += 1
        for i in range(base, base + 7):
            if hand[i] and hand[i + 2]:
                near_runs += 1
    slots = max(1, 4 - ctx.locked)
    b = (2 * ctx.locked + min(1.0, pairs / 2.0)
         + min(2.0, runs) * 0.25 + min(2.0, near_runs) * 0.125
         + min(2.0, triplets) * 0.25) / (2 * slots + 1)
    b = min(1.0, max(0.0, b))
    pair_outs = sum(rem[t] for t in range(33) if hand[t] >= 2)
    c = min(1.0, max(0.0, pair_outs / 16.0
                      + min(2.0, runs + near_runs) / 8.0))
    return b, c


def _reaction_q0_evaluation(ctx, profile, budget):
    """Shared evaluator entry for the bounded reaction Q0 layer."""
    return evaluate_standing(ctx, profile, level="Q0", budget=budget)


def evaluate_reaction(g, seat, profile=None):
    """Shape-v1 PASS/claim evaluation; returns ``(action, evaluation)``.

    Reaction windows are much shorter than a draw/discard turn.  The old
    implementation entered full ``Q`` for PASS and the first claim, then
    discarded every partial result and fell back to the legacy heuristic as
    soon as the shared budget expired.  The ladder is transactional:

    ``legacy -> complete Q0 for every option -> complete Q for every option``.

    An incomplete Q0 pass uses legacy, while an incomplete Q keeps the
    already-complete Q0 ordering.  No partially evaluated Q candidate can
    influence the selected action.
    """
    profile = profile_for(profile or "shape-v1")
    acts = g.legal_actions()
    if KONG_OPEN in acts:
        # KONG_OPEN is explicitly frozen to the legacy decision path.
        from .bot import _legacy_claim_react
        action = _legacy_claim_react(g, seat, acts)
        return action, {"version": profile.name, "level": "legacy",
                        "profile": profile.name,
                        "profile_fingerprint": profile.fingerprint,
                        "reason": "kong_open_legacy", "selected": action,
                        "candidates": []}
    ctx = EvalContext.from_game(g, seat, phase=getattr(g, "phase", "react"))
    budget = _Budget(profile.react_node_budget, profile.react_time_budget_ms)

    def post_discard_items(post, locked):
        """Enumerate all minimum-shanten immediate discards for one claim."""
        best_s = None
        items = []
        for d in range(34):
            if post[d] <= 0:
                continue
            if not budget.tick():
                return None
            child = post[:]
            child[d] -= 1
            child_s = shanten(child, locked)
            budget.kernel()
            if best_s is None or child_s < best_s:
                best_s, items = child_s, [(d, child)]
            elif child_s == best_s:
                items.append((d, child))
        return items or None

    # Build the option table before evaluating it.  PASS and every legal
    # claim therefore share exactly the same Q0/Q candidate set.
    specs = [{"action": PASS,
              "post_items": [(None, list(g.hands[seat]))],
              "locked": ctx.locked, "chow_inc": 0}]
    q0_complete = True
    for act, post, locked, chow_inc in _claim_options(g, seat, acts):
        items = post_discard_items(post, locked)
        if items is None:
            q0_complete = False
            break
        specs.append({"action": act, "post_items": items,
                      "locked": locked, "chow_inc": chow_inc})

    def evaluate_spec(spec, level, base_evaluations=None):
        """Evaluate one option and choose its best immediate discard."""
        results = []
        for discard, hand in spec["post_items"]:
            if not budget.tick(0):
                return None
            option_ctx = ctx.fast_replace(
                hand=tuple(hand), locked=spec["locked"],
                chows=ctx.chows + spec["chow_inc"],
                pending_tile=None, pending_owner=None)
            base_ev = (base_evaluations.get((discard, tuple(hand)))
                       if base_evaluations is not None else None)
            ev = (_reaction_q0_evaluation(option_ctx, profile, budget)
                  if level == "Q0" else
                  evaluate_standing(option_ctx, profile, level=level,
                                    budget=budget,
                                    base_evaluation=base_ev))
            # ``evaluate_standing(Q0)`` has no inner traversal to mark a
            # timeout, so check the shared clock after the call as well.
            if not budget.tick(0):
                return None
            if level == "Q" and not ev.complete:
                return None
            results.append((discard, hand, ev))
        if not results:
            return None
        return max(results, key=lambda x: (
            x[2].q if level == "Q" and x[2].q is not None else x[2].q0,
            x[2].p1,
            -(x[0] if x[0] is not None else -1))), results

    q0_options = []
    q0_bases = {}
    if q0_complete:
        for spec in specs:
            evaluated = evaluate_spec(spec, "Q0")
            if evaluated is None:
                q0_complete = False
                break
            result, all_results = evaluated
            discard, hand, ev = result
            q0_bases[spec["action"]] = {
                (item_discard, tuple(item_hand)): item_ev
                for item_discard, item_hand, item_ev in all_results
            }
            q0_options.append((spec["action"], ev, discard,
                               spec["chow_inc"], spec, hand))

    if not q0_complete:
        from .bot import _choose_react
        action = _choose_react(g, seat, acts)
        return action, {
            "version": profile.name, "level": "legacy",
            "profile": profile.name,
            "profile_fingerprint": profile.fingerprint,
            "reason": "budget_fallback_legacy",
            "fallback_stage": "Q0",
            "selected": action, "candidates": [],
            "fallback_reason": budget.exceeded or "q0_incomplete",
            "q0_complete": False,
            "nodes": budget.nodes, "kernel_calls": budget.kernel_calls,
            "elapsed_ms": round(budget.elapsed_ms, 3),
        }

    # Q0 is complete for PASS and every legal claim.  Full Q can only replace
    # the complete table atomically.
    q_options = []
    # A full Q branch contains an uninterruptible future-draw traversal.  Do
    # not start it with only the normal reaction-window margin left: the
    # traversal would overrun the deadline and throw away a perfectly valid
    # Q0 table.  Larger offline profiles can still opt into the complete Q
    # ordering.
    q_min_remaining_ms = 12.0
    q_complete = (budget.tick(0)
                  and profile.react_time_budget_ms - budget.elapsed_ms
                  >= q_min_remaining_ms)
    if q_complete:
        for action0, _ev0, _discard0, _ci, spec, _hand0 in q0_options:
            evaluated = evaluate_spec(
                spec, "Q", base_evaluations=q0_bases.get(action0))
            if evaluated is None:
                q_complete = False
                break
            result, _all_results = evaluated
            discard, hand, ev = result
            q_options.append((action0, ev, discard, spec["chow_inc"],
                              spec, hand))

    options = q_options if q_complete else q0_options
    level = "Q" if q_complete else "Q0"
    pass_ev = next(item[1] for item in options if item[0] == PASS)
    pass_s = pass_ev.shanten
    # Keep the shape-v1 boundary explicit in the explanation.  The threshold
    # is a versioned normalized-Q comparison, not a hidden hard filter; the
    # same value helper is used by selection and serialization so a logged
    # decision can be recomputed without guessing which layer ran.
    unknown_pool = max(0, sum(max(0, 4 - x) for x in ctx.visible))

    def option_value(ev):
        value = (ev.q if level == "Q" and ev.q is not None else ev.q0)
        return float(value)

    pass_value = option_value(pass_ev)

    def threshold_for(action):
        if action == PASS or unknown_pool <= 0:
            return None
        base = (2 if action == PONG else 4) / unknown_pool
        multiplier = (profile.tau_pong_multiplier if action == PONG
                      else profile.tau_chow_multiplier)
        return float(base * multiplier)

    def allowed(item):
        action, ev, _discard, _ci, _spec, _hand = item
        if action == PASS:
            return True
        if ev.shanten < pass_s:
            return True
        if ev.shanten > pass_s:
            return False
        threshold = threshold_for(action)
        if threshold is None:
            return False
        return option_value(ev) - pass_value >= threshold

    accepted = [item for item in options if allowed(item)]
    if not accepted:
        action = PASS
    else:
        # PASS is not preferred over a strict shanten improvement.  Among
        # equal-shanten accepted claims, the selected (Q or Q0) score orders
        # all options using one consistent layer.
        action = max(
            accepted,
            key=lambda item: (
                item[1].shanten < pass_s,
                item[1].q if level == "Q" and item[1].q is not None
                else item[1].q0,
                -(item[1].q0),
                -(item[0])))[0]

    serialized = []
    for item_action, ev, discard, _ci, _spec, _hand in options:
        data = ev.as_json() if hasattr(ev, "as_json") else dict(ev)
        data.update({"action": item_action, "best_discard": discard,
                     "accepted": any(item_action == x[0]
                                     for x in accepted)})
        if item_action != PASS:
            value = option_value(ev)
            threshold = threshold_for(item_action)
            data.update({
                "pass_shanten": pass_s,
                "pass_value": pass_value,
                "delta_vs_pass": value - pass_value,
                "tau": threshold,
                "threshold_unit": "normalized_Q",
                "threshold_formula": "Q_claim-Q_pass >= tau[action]",
                "unknown_pool": unknown_pool,
            })
        serialized.append(data)
    return action, {
        "version": profile.name, "profile": profile.name,
        "profile_fingerprint": profile.fingerprint,
        "level": level, "reason": "shape_reaction" if q_complete
        else "shape_reaction_q0_fallback", "selected": action,
        "candidates": serialized, "pass": pass_ev.as_json(),
        "threshold": {
            "unit": "normalized_Q", "formula":
            "Q_claim-Q_pass >= tau[action]", "unknown_pool": unknown_pool,
            "layer": level,
        },
        "nodes": budget.nodes, "kernel_calls": budget.kernel_calls,
        "elapsed_ms": round(budget.elapsed_ms, 3),
        "fallback_reason": None if q_complete
        else (budget.exceeded or "q_incomplete"),
        "fallback_stage": None if q_complete else "Q",
        "q0_complete": True, "q_complete": q_complete,
    }


# Short aliases used by offline diagnostics and hidden integrations.
evaluate_discard = evaluate_discard_candidates
evaluate_claim = evaluate_reaction
evaluate_hand = evaluate_standing
shape_v1_profile = EvalProfile.shape_v1
