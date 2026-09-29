"""回放帧管线(task 3.1 / 3.2):把对局记录变成可索引的帧数组。

统一 Frame 形状(JSON 可直接送前端):
    {step, info_kind: "local"|"online", my_seat,
     hands: [4][34]|None,   # None => 他家暗手不可见(线上)
     my_hand: [34]|None,    # 线上视角的本家手牌
     discards, melds, hand_counts, wall_remaining, scores, round_no,
     current: {seat, phase}, label, gap, discard_hints}

- 本地(3.1):预计算 = Game(seed) 按动作序列 step 一次,逐步快照;
  步进/拖动 = 帧数组索引,零重算;非法动作显式报错(不静默修正)。
- 线上(3.2):复用 Mirror 语义(snapshot 锚点 + events 增量),产出自家视角帧;
  他家暗手永不载入;跳段(协议固有)以最近锚点重建并输出 gap 标注。
"""

from __future__ import annotations

import json

from .. import logview
from ..game import Game
from ..platform.mirror import Mirror, MirrorInconsistent
from ..platform.proto import (
    EV_DRAWN, EV_DISCARDED, EV_PASS, EV_CHI, EV_PENG, EV_GANG, EV_HU,
    EV_TIMEOUT, EV_ROUND_ENDED, EV_GAME_ENDED,
)
from .wait_hints import PublicMaterialError, analyze_discard_hints

__all__ = ["local_frames", "local_session", "online_frames",
           "online_session", "scan_online_whiteboard_rounds",
           "DEAD_WALL_CONST"]

# 与 game.py DEAD_WALL 一致(4家 × 13 + ... → 活墙起算);墙数口径 84 - pops
DEAD_WALL_CONST = 14
_WALL_TOTAL = 84
WHITEBOARD_TILE = 33


def _whiteboard_count(hand):
    """Return the number of white dragons in a numeric visible hand."""
    if not isinstance(hand, (list, tuple)):
        return 0
    if (len(hand) == 34 and
            all(type(value) is int and 0 <= value <= 4 for value in hand)):
        return int(hand[WHITEBOARD_TILE])
    return sum(1 for tile in hand if type(tile) is int and tile == WHITEBOARD_TILE)


def _meld_to_json(entry, from_seat=None):
    kind = entry[0]
    if kind == "chow":
        a = entry[1]
        out = {"kind": "chow", "tiles": [int(a), int(a + 1), int(a + 2)]}
    else:
        out = {"kind": kind, "tiles": [int(t) for t in entry[1:]]}
    if isinstance(from_seat, int) and 0 <= from_seat < 4:
        out["from_seat"] = from_seat
    return out


def _melds_to_json(melds, meld_sources=None):
    """Serialize melds and preserve the source seat when it is evidenced."""
    out = []
    for seat, row in enumerate(melds):
        sources = meld_sources[seat] if meld_sources and seat < len(meld_sources) else []
        out.append([
            _meld_to_json(meld, sources[index] if index < len(sources) else None)
            for index, meld in enumerate(row)
        ])
    return out


def _response_window(game):
    pending = getattr(game, "pending", None)
    if pending is None:
        return None
    owner, tile = pending
    return {
        "owner": int(owner),
        "tile": int(tile),
        "phase": getattr(game, "phase", "react"),
    }


def _discard_hint_payload(game, seat, diagnostics=None, seq_no=None):
    """生成帧提示;公开物料失效时不修正计数,只留诊断。"""
    out_diagnostics = list(diagnostics or [])
    try:
        hints = analyze_discard_hints(game, seat)
    except PublicMaterialError as exc:
        out_diagnostics.append({
            "code": "discard_hints_unavailable",
            "severity": "warn",
            "message": str(exc),
            "seq_no": seq_no,
        })
        hints = []
    return hints, out_diagnostics


def _local_frame(game, my_seat, actor, label, gap=False, step=0,
                 *, seq_no=None, seq_source="local_action", timestamp=None,
                 event=None, local_requests=None, diagnostics=None,
                 meld_sources=None):
    frame_diagnostics = list(diagnostics or [])
    if gap:
        discard_hints = []
    else:
        discard_hints, frame_diagnostics = _discard_hint_payload(
            game, actor, frame_diagnostics, seq_no)
    drawn_tile = None
    drawn_seat = None
    drawn = getattr(game, "drawn", None)
    if isinstance(drawn, (list, tuple)):
        for seat, tile in enumerate(drawn):
            if (type(seat) is int and 0 <= seat < 4
                    and type(tile) is int and 0 <= tile < 34):
                drawn_tile, drawn_seat = tile, seat
                break
    result = getattr(game, "result", None)
    winner = result[0] if isinstance(result, (list, tuple)) and result else None
    winner_seats = (
        [int(winner)] if type(winner) is int and 0 <= winner < 4 else []
    )
    return {
        "step": step, "info_kind": "local", "my_seat": my_seat,
        "hands": [[int(v) for v in h] for h in game.hands],
        "my_hand": [int(v) for v in game.hands[my_seat]],
        "discards": [[int(v) for v in river] for river in game.discards],
        "melds": _melds_to_json(game.melds, meld_sources),
        "hand_counts": [sum(int(v) for v in hand) for hand in game.hands],
        "wall_remaining": game.live_wall_left(),
        "scores": [int(v) for v in game.scores],
        "round_no": 1,
        "drawn_tile": drawn_tile,
        "drawn_seat": drawn_seat,
        "draw_origin": (
            "kong_replacement" if drawn_tile is not None
            and bool(getattr(game, "_kong_draw", False))
            else "normal" if drawn_tile is not None else None
        ),
        "winner_seats": winner_seats,
        "round_ended": bool(getattr(game, "done", False)),
        "dealer": int(getattr(game, "dealer", 0)),
        "current": {"seat": actor, "phase": ("done" if game.done
                                             else getattr(game, "phase", "playing"))},
        "label": label, "gap": bool(gap),
        "seq_no": step if seq_no is None else seq_no,
        "seq_source": seq_source,
        "timestamp": timestamp,
        "event": event,
        "local_requests": list(local_requests or []),
        "diagnostics": frame_diagnostics,
        "response_window": _response_window(game),
        "discard_hints": discard_hints,
    }


def _action_label(action):
    if isinstance(action, int):
        return logview.action_name(action)
    if action is None:
        return ""
    return str(action)


def _annotation_bucket(raw, step, seq_no):
    """从可选记录注释中取出属于某一步的条目。

    竞技场旧记录没有旁路请求/诊断字段;新记录可以用 ``step`` 或
    ``seq_no`` 关联它们。缺少关联字段的条目不会被猜测挂载,避免把
    一条终局摘要伪装成某个动作步骤的证据。
    """
    if not raw:
        return []
    if isinstance(raw, dict):
        for key in (step, seq_no, str(step), str(seq_no)):
            if key in raw:
                value = raw[key]
                if isinstance(value, list):
                    return list(value)
                return [value]
        return []
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        marker = item.get("step", item.get("step_index"))
        if marker is None:
            marker = item.get("seq", item.get("seq_no"))
        if marker == step or marker == seq_no:
            out.append(item)
    return out


def _build_local_frames(rec):
    g = Game(seed=rec["seed"], dealer=rec.get("dealer", 0),
             base=rec.get("base", 1),
             you_cai_bi_kao=rec.get("you_cai_bi_kao", False))
    my_seat = _local_viewer_seat(rec)
    requests = rec.get("local_requests", rec.get("requests", []))
    diagnostics = rec.get("diagnostics", [])
    decision_audits = rec.get("decision_audits", [])
    # Game.melds intentionally keeps the compact engine tuple shape.  Keep a
    # parallel source-seat list here so the replay frame can place the claimed
    # tile at the correct visual side of each open meld.
    meld_sources = [[] for _ in range(4)]
    frames = [_local_frame(
        g, my_seat, g.current_seat(), "初始", step=0,
        seq_no=0, seq_source="local_initial",
        event={"type": "session_start"},
        local_requests=_annotation_bucket(requests, 0, 0),
        diagnostics=_annotation_bucket(diagnostics, 0, 0),
        meld_sources=meld_sources)]
    for k, action in enumerate(rec["actions"], start=1):
        legal = list(g.legal_actions())
        if action not in legal:
            raise ValueError(
                f"recorded action {action} illegal at step {k} "
                f"(legal={legal})")
        actor = g.current_seat()
        claim_source = None
        if getattr(g, "phase", None) == "react" and getattr(g, "pending", None):
            claim_source = g.pending[0]
        g.step(action)
        step_requests = _annotation_bucket(requests, k, k)
        for audit_entry in _annotation_bucket(decision_audits, k, k):
            if isinstance(audit_entry, dict):
                audit = audit_entry.get("decision_audit", audit_entry)
                step_requests.append({
                    "kind": "decision",
                    "type": "DECISION",
                    "id": (audit.get("decision_id")
                           if isinstance(audit, dict) else None),
                    "phase": (audit.get("phase")
                              if isinstance(audit, dict) else None),
                    "action": action,
                    "decision_audit": audit,
                })
        for seat, melds in enumerate(g.melds):
            while len(meld_sources[seat]) < len(melds):
                meld_sources[seat].append(
                    claim_source if seat == actor else None)
        frames.append(_local_frame(
            g, my_seat, g.current_seat(), _action_label(action), step=k,
            seq_no=k, seq_source="local_action",
            event={"type": "action", "action": action, "actor": actor,
                   "label": _action_label(action)},
            local_requests=step_requests,
            diagnostics=_annotation_bucket(diagnostics, k, k),
            meld_sources=meld_sources))
    return frames


def _local_viewer_seat(rec):
    """Resolve the physical seat of the local arena's main role.

    Arena records rotate the main role across physical seats and persist that
    mapping in ``roles``.  Older hand-written records may omit it, so retain
    the historical seat-0 fallback and accept an explicit viewer override.
    """
    for key in ("my_seat", "viewer_seat"):
        raw = rec.get(key)
        if isinstance(raw, int) and 0 <= raw < 4:
            return raw
    roles = rec.get("roles")
    if isinstance(roles, (list, tuple)):
        try:
            seat = roles.index(0)
        except ValueError:
            seat = None
        if isinstance(seat, int) and 0 <= seat < 4:
            return seat
    return 0


def local_frames(record):
    """本地记录 → 帧数组。record 可为路径或已加载 dict。"""
    from .records import load_game_record
    rec = (load_game_record(record) if isinstance(record, str)
           else record)
    return _build_local_frames(rec)


# ---------------------------------------------------------------------------
# 线上帧
# ---------------------------------------------------------------------------

_EV_LABEL = {
    EV_DRAWN: "摸", EV_DISCARDED: "弃", EV_PASS: "过", EV_CHI: "吃",
    EV_PENG: "碰", EV_GANG: "杠", EV_HU: "胡", EV_TIMEOUT: "超时",
    EV_ROUND_ENDED: "回合结束", EV_GAME_ENDED: "终局",
}


def _online_response_window(mirror):
    pending = getattr(mirror, "pending", None)
    if pending is None:
        return None
    owner, tile = pending
    return {
        "owner": int(owner),
        "tile": int(tile),
        "phase": "react",
        "response_order": list(getattr(mirror, "response_order", ()) or ()),
        "response_index": getattr(mirror, "response_index", None),
    }


def _online_frame(mirror, scores, step, label, gap=False, *, seq_no=None,
                  timestamp=None, event=None, local_requests=None,
                  diagnostics=None, meld_sources=None, winner_seats=None,
                  round_ended=False):
    hand_counts = [None] * 4
    me = int(mirror.me)
    hand_counts[me] = sum(int(v) for v in mirror.my_hand)
    public_counts = getattr(mirror, "public_hand_counts", None)
    if isinstance(public_counts, (list, tuple)) and len(public_counts) == 4:
        if all(isinstance(v, int) and v >= 0 for v in public_counts):
            hand_counts = [int(v) for v in public_counts]
            # The local hand is independently known even if a future mirror
            # implementation changes the public-count anchor semantics.
            hand_counts[me] = sum(int(v) for v in mirror.my_hand)
    frame_diagnostics = list(diagnostics or [])
    discard_hints = []
    snapshot_reanchor = (
        isinstance(event, dict) and event.get("type") == "snapshot"
    )
    drawn = getattr(mirror, "drawn", None)
    draw_origin = getattr(mirror, "draw_origin", None)
    if draw_origin == "NORMAL":
        draw_origin = "normal"
    elif draw_origin == "KONG_REPLACEMENT":
        draw_origin = "kong_replacement"
    elif isinstance(draw_origin, str):
        draw_origin = draw_origin.strip().lower().replace("-", "_") or None
    if ((not gap or snapshot_reanchor)
            and getattr(mirror, "drawn", None) is not None):
        try:
            projection = mirror.build_game("draw")
            discard_hints, frame_diagnostics = _discard_hint_payload(
                projection, me, frame_diagnostics, seq_no)
        except (MirrorInconsistent, ValueError):
            # 当前线上帧不能可靠重建摸后状态时,隐藏而不是沿用旧提示。
            discard_hints = []
    return {
        "step": step, "info_kind": "online",
        "my_seat": mirror.me, "hands": None,
        "my_hand": [int(v) for v in mirror.my_hand],
        "discards": [[int(v) for v in river] for river in mirror.discards],
        "melds": _melds_to_json(mirror.melds, meld_sources),
        "hand_counts": hand_counts,
        "wall_remaining": _WALL_TOTAL - mirror._pops,
        "scores": list(scores or [0, 0, 0, 0]),
        "round_no": mirror.round_no,
        "drawn_tile": int(drawn) if type(drawn) is int and 0 <= drawn < 34 else None,
        "drawn_seat": me if type(drawn) is int and 0 <= drawn < 34 else None,
        "draw_origin": draw_origin if type(drawn) is int and 0 <= drawn < 34 else None,
        "winner_seats": sorted({
            seat for seat in (winner_seats or [])
            if type(seat) is int and 0 <= seat < 4
        }),
        "round_ended": bool(round_ended),
        "dealer": int(getattr(mirror, "dealer", 0)),
        "current": {
            "seat": (me if getattr(mirror, "drawn", None) is not None
                      else _mirror_seat(mirror)),
            "phase": ("discard" if getattr(mirror, "drawn", None) is not None
                       else "react" if mirror.pending is not None else "online"),
        },
        "label": label, "gap": bool(gap),
        "seq_no": seq_no,
        "seq_source": (
            "snapshot" if isinstance(event, dict) and event.get("type") == "snapshot"
            else "server_event" if seq_no is not None else "derived"),
        "timestamp": timestamp,
        "event": event,
        "local_requests": list(local_requests or []),
        "diagnostics": frame_diagnostics,
        "response_window": _online_response_window(mirror),
        "discard_hints": discard_hints,
    }


def _mirror_seat(mirror):
    if mirror.pending is not None:
        return mirror.pending[0]
    return None


def _source_seat(value):
    """Read a validated source seat from a snapshot/event payload."""
    if type(value) is int and 0 <= value < 4:
        return value
    return None


def _payload_source_seat(payload):
    if not isinstance(payload, dict):
        return None
    for key in ("from_seat", "fromSeat", "source_seat", "sourceSeat"):
        source = _source_seat(payload.get(key))
        if source is not None:
            return source
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("from_seat", "fromSeat", "source_seat", "sourceSeat"):
            source = _source_seat(data.get(key))
            if source is not None:
                return source
    return None


def _snapshot_meld_sources(raw_melds, parsed_melds, previous=None):
    """Align optional snapshot source seats with Mirror's parsed meld rows."""
    previous = previous or []
    result = []
    for seat, row in enumerate(parsed_melds):
        raw_row = raw_melds[seat] if isinstance(raw_melds, list) and seat < len(raw_melds) else []
        old_row = previous[seat] if seat < len(previous) else []
        sources = []
        for index, _meld in enumerate(row):
            raw = raw_row[index] if isinstance(raw_row, list) and index < len(raw_row) else None
            source = _payload_source_seat(raw)
            if source is None and index < len(old_row):
                source = old_row[index]
            sources.append(source)
        result.append(sources)
    return result


class _OnlineBuilder:
    def __init__(self):
        self.mirror = None
        self.meta = None
        self.scores = None
        self.frames = []
        self.verifications = []   # 决策点合法集对账
        self.gap = False
        self.pending_requests = []
        self.pending_diagnostics = []
        self.meld_sources = [[] for _ in range(4)]
        self.winner_seats = set()
        self.current_round_no = None
        self.round_ended = False

    def _base(self):
        return bool((self.meta or {}).get("you_cai_bi_kao", False))

    def ingest(self, rec):
        t = rec.get("type")
        if t == "meta":
            self.meta = rec
            return
        if t == "snapshot":
            snap = rec.get("snap") or {}
            was_gap = self.gap
            previous_sources = self.meld_sources
            previous_round = self.mirror.round_no if self.mirror is not None else None
            raw_snapshot_round = snap.get("round_no")
            snapshot_round = (
                int(raw_snapshot_round)
                if type(raw_snapshot_round) is int and raw_snapshot_round >= 0
                else self.current_round_no if self.current_round_no is not None
                else 1
            )
            if self.current_round_no is None:
                self.current_round_no = snapshot_round
            elif snapshot_round != self.current_round_no:
                self.winner_seats.clear()
                self.round_ended = False
                self.current_round_no = snapshot_round
            self.mirror = Mirror(
                my_seat=snap["seat"], dealer=snap.get("dealer", 0),
                base=(self.meta or {}).get("base", 1),
                you_cai_bi_kao=self._base(), round_no=snapshot_round)
            self.mirror.apply_snapshot(snap)
            self.meld_sources = _snapshot_meld_sources(
                snap.get("melds"), self.mirror.melds,
                previous_sources if previous_round == self.mirror.round_no else None)
            if snap.get("scores"):
                self.scores = list(snap["scores"])
            self.gap = False
            self._emit(
                "快照", self.mirror.round_no, gap=was_gap,
                seq_no=rec.get("seq"), timestamp=rec.get("ts"),
                event={"type": "snapshot", "seq": rec.get("seq")})
            return
        if t == "events":
            for ev in rec.get("events") or []:
                if self.mirror is None:
                    continue
                try:
                    pending_source = self.mirror.pending[0] if self.mirror.pending else None
                    before_meld_counts = [len(row) for row in self.mirror.melds]
                    self.mirror.apply_event(ev)
                    event_type = ev.get("type")
                    if event_type == EV_HU:
                        winner = _source_seat(ev.get("seat"))
                        if winner is not None:
                            self.winner_seats.add(winner)
                    elif event_type == EV_ROUND_ENDED:
                        self.round_ended = True
                    event_data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                    event_kind = ev.get("kind") or event_data.get("kind")
                    is_open_claim = (
                        event_type in {EV_CHI, EV_PENG}
                        or (event_type == EV_GANG and event_kind in (None, "ming", "open", "gang_ming"))
                    )
                    actor = _source_seat(ev.get("seat"))
                    source = _payload_source_seat(ev)
                    if source is None:
                        source = _source_seat(pending_source)
                    for seat, melds in enumerate(self.mirror.melds):
                        while len(self.meld_sources[seat]) < len(melds):
                            index = len(self.meld_sources[seat])
                            self.meld_sources[seat].append(
                                source if is_open_claim and seat == actor
                                and index >= before_meld_counts[seat] else None)
                    label = _EV_LABEL.get(ev.get("type"), ev.get("type"))
                    self._emit(
                        label, self.mirror.round_no,
                        seq_no=ev.get("seq"), timestamp=ev.get("ts"),
                        event=dict(ev))
                except MirrorInconsistent:
                    # 失步 → 等待快照;缺口标注
                    self.mirror = None
                    self.meld_sources = [[] for _ in range(4)]
                    self.gap = True
            return
        if t == "req":
            res = rec.get("res") or {}
            if res.get("snapshot") or res.get("finished"):
                hi, lo = res.get("seq"), rec.get("seq")
                if hi is not None and lo is not None and hi > lo:
                    self.gap = True
            self._attach(
                "local_requests",
                {
                    "kind": "state_request",
                    "type": "GET_STATE",
                    "seq_no": rec.get("seq"),
                    "status": rec.get("status"),
                    "latency_ms": rec.get("latency_ms"),
                    "attempts": rec.get("attempts"),
                    "request_kind": rec.get("request_kind"),
                    "requested_seq": rec.get("requested_seq"),
                    "response_seq": res.get("seq"),
                },
                rec.get("seq"))
            return
        if t == "decision":
            if self.mirror is None:
                return
            try:
                g = self.mirror.build_game(rec.get("phase"))
                legal = sorted(g.legal_actions())
            except (MirrorInconsistent, ValueError):
                legal = None
            self.verifications.append({
                "id": rec.get("id"), "seq": rec.get("seq"),
                "recorded": sorted(rec.get("legal") or []),
                "rebuilt": legal,
                "ok": legal is not None and legal == sorted(rec.get("legal") or []),
            })
            self._attach(
                "local_requests",
                {
                    "kind": "decision",
                    "type": "DECISION",
                    "id": rec.get("id"),
                    "phase": rec.get("phase"),
                    "action": rec.get("action"),
                    "latency_ms": rec.get("latency_ms"),
                    "fallback_reason": rec.get("fallback_reason"),
                    "decision_audit": rec.get("decision_audit"),
                },
                rec.get("seq"))
            if self.verifications[-1]["ok"] is False:
                self._attach(
                    "diagnostics",
                    {
                        "code": "replay_legal_mismatch",
                        "severity": "error",
                        "message": "决策点重建合法集与日志不一致",
                        "seq_no": rec.get("seq"),
                    },
                    rec.get("seq"))
            return
        if t == "claim_miss":
            self._attach(
                "diagnostics",
                {
                    "code": "claim_miss",
                    "severity": "error" if rec.get("chosen") is not None else "warn",
                    "message": rec.get("reason") or "吃碰杠未成功",
                    "phase": rec.get("phase"),
                    "seq_no": rec.get("seq"),
                },
                rec.get("seq"))
            return
        if t in {"action", "state_reconcile", "window_confirm",
                 "window_lifecycle", "window_authorization", "window_terminal",
                 "reset"}:
            self._attach(
                "local_requests",
                {"kind": t, "type": t.upper(),
                 "seq_no": rec.get("seq"),
                 "status": rec.get("status"),
                 "ok": rec.get("ok"),
                 "phase": rec.get("phase"),
                 "code": rec.get("code")},
                rec.get("seq"))
            return
        if t == "end":
            if rec.get("scores"):
                self.scores = list(rec["scores"])
            return

    def _attach(self, field, value, seq_no):
        """把旁路记录挂到最近的已知步骤,不生成新步骤。"""
        if not self.frames:
            pending_attr = {
                "local_requests": "pending_requests",
                "diagnostics": "pending_diagnostics",
            }[field]
            getattr(self, pending_attr).append(value)
            return
        target = None
        if seq_no is not None:
            for frame in reversed(self.frames):
                if frame.get("seq_no") == seq_no:
                    target = frame
                    break
            if target is None:
                for frame in reversed(self.frames):
                    frame_seq = frame.get("seq_no")
                    if frame_seq is not None and frame_seq <= seq_no:
                        target = frame
                        break
        if target is None:
            # 请求可能先于下一份 snapshot 到达,保留到下一个锚点/事件。
            pending_attr = {
                "local_requests": "pending_requests",
                "diagnostics": "pending_diagnostics",
            }[field]
            getattr(self, pending_attr).append(value)
            return
        target.setdefault(field, []).append(value)

    def _emit(self, label, round_no, gap=None, *, seq_no=None,
              timestamp=None, event=None):
        frame = _online_frame(
            self.mirror, self.scores, len(self.frames), label,
            gap=(self.gap if gap is None else gap), seq_no=seq_no,
            timestamp=timestamp, event=event,
            local_requests=self.pending_requests,
            diagnostics=self.pending_diagnostics,
            meld_sources=self.meld_sources,
            winner_seats=self.winner_seats,
            round_ended=self.round_ended)
        self.pending_requests = []
        self.pending_diagnostics = []
        self.frames.append(frame)


def online_frames(records):
    """线上 jsonl 记录列表 → (frames, verifications)。"""
    builder = _OnlineBuilder()
    for rec in records:
        builder.ingest(rec)
    return builder.frames, builder.verifications


def scan_online_whiteboard_rounds(path, minimum=2):
    """Scan an online JSONL for rounds where our hand has many whiteboards.

    The index endpoint needs this information before a user opens a game.  It
    therefore scans the lightweight snapshot and draw/discard records instead
    of constructing the full replay session.  A full session remains the
    authoritative source for the round metadata after the user opens a game.
    """
    try:
        threshold = int(minimum)
    except (TypeError, ValueError):
        return []
    if threshold < 1:
        return []

    segments = []
    current = None
    my_seat = None
    whiteboards = 0

    def raw_whiteboard_count(hand):
        if not isinstance(hand, (list, tuple)):
            return 0
        if (len(hand) == 34 and
                all(type(value) is int and 0 <= value <= 4
                    for value in hand)):
            return int(hand[WHITEBOARD_TILE])
        return sum(1 for tile in hand if tile in ("白", "w", WHITEBOARD_TILE))

    def update_segment(round_no, count):
        nonlocal current
        if type(round_no) is not int or round_no < 0:
            round_no = current["round_no"] if current else 1
        if current is None or current["round_no"] != round_no:
            current = {
                "ordinal": len(segments) + 1,
                "round_no": round_no,
                "max_my_whiteboards": 0,
            }
            segments.append(current)
        current["max_my_whiteboards"] = max(
            current["max_my_whiteboards"], count)

    try:
        stream = open(path, "r", encoding="utf-8")
    except OSError:
        return []
    with stream:
        for line in stream:
            try:
                record = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            if record.get("type") == "snapshot":
                snap = record.get("snap") or {}
                if type(snap.get("seat")) is int:
                    my_seat = snap.get("seat")
                whiteboards = raw_whiteboard_count(snap.get("my_hand"))
                update_segment(snap.get("round_no"), whiteboards)
                continue
            if record.get("type") != "events" or my_seat is None:
                continue
            for event in record.get("events") or ():
                if not isinstance(event, dict) or event.get("seat") != my_seat:
                    continue
                tile = event.get("tile")
                if event.get("type") == EV_DRAWN and tile in (
                        "白", "w", WHITEBOARD_TILE):
                    whiteboards += 1
                elif event.get("type") == EV_DISCARDED and tile in (
                        "白", "w", WHITEBOARD_TILE):
                    whiteboards = max(0, whiteboards - 1)
                update_segment(current["round_no"] if current else 1,
                               whiteboards)
    return [segment for segment in segments
            if segment["max_my_whiteboards"] >= threshold]


def _session_from_frames(frames, *, source, session_id=None, path=None,
                         verifications=None, strategy=None, evaluator=None,
                         model_name=None, strategy_snapshot=None,
                         strategy_snapshots=None):
    """把帧状态包装为统一步骤模型。

    ``state`` 保留在每个步骤中是有意的:前端可直接从任一步建立
    checkpoint,而不需要重新解释来源特有的 SSE 或动作格式。旧 API 的
    ``frames`` 字段仍由调用方返回,用于兼容已有客户端。
    """
    steps = []
    for index, frame in enumerate(frames):
        steps.append({
            "step_index": index,
            "seq_no": frame.get("seq_no"),
            "seq_source": frame.get("seq_source", "derived"),
            "timestamp": frame.get("timestamp"),
            "event": frame.get("event"),
            "local_requests": list(frame.get("local_requests") or []),
            "diagnostics": list(frame.get("diagnostics") or []),
            "state": frame,
        })
    capabilities = {
        "server_events": source == "online",
        "local_requests": any(s["local_requests"] for s in steps),
        "diagnostics": any(s["diagnostics"] for s in steps),
        "full_information": source == "local",
    }
    metadata = {
        "source": source,
        "id": session_id,
        "path": path,
        "step_count": len(steps),
        "capabilities": capabilities,
        "rounds": _rounds_from_steps(steps),
    }
    for key, value in (("strategy", strategy), ("evaluator", evaluator),
                       ("model_name", model_name)):
        if value is not None:
            metadata[key] = value
    if strategy_snapshot is not None:
        metadata["strategy_snapshot"] = strategy_snapshot
    if strategy_snapshots is not None:
        metadata["strategy_snapshots"] = strategy_snapshots
    if verifications is not None:
        metadata["verifications"] = verifications
    return {
        "metadata": metadata,
        "initial_state": frames[0] if frames else None,
        "steps": steps,
    }


def _rounds_from_steps(steps):
    """Index contiguous round_no runs without changing step or seq identity."""
    if not steps:
        return []
    segments = []
    start = 0
    current_round = None
    for index, step in enumerate(steps):
        state = step.get("state") or {}
        raw_round = state.get("round_no")
        round_no = (int(raw_round) if type(raw_round) in (int, float)
                    and raw_round >= 0 else current_round)
        if round_no is None:
            round_no = 1
        if current_round is None:
            current_round = round_no
        elif round_no != current_round:
            segments.append((start, index - 1, current_round))
            start = index
            current_round = round_no
    segments.append((start, len(steps) - 1, current_round))

    rounds = []
    for ordinal, (start, end, round_no) in enumerate(segments, start=1):
        segment = steps[start:end + 1]
        seqs = [step.get("seq_no") for step in segment
                if step.get("seq_no") is not None]
        final_state = segment[-1].get("state") or {}
        max_my_whiteboards = max(
            (_whiteboard_count((step.get("state") or {}).get("my_hand"))
             for step in segment),
            default=0,
        )
        winners = final_state.get("winner_seats") or []
        winners = sorted({seat for seat in winners
                          if type(seat) is int and 0 <= seat < 4})
        start_seq = seqs[0] if seqs else None
        round_id = f"r{ordinal}-n{round_no}-s{start_seq if start_seq is not None else start}"
        rounds.append({
            "round_id": round_id,
            "ordinal": ordinal,
            "round_no": round_no,
            "start_step_index": start,
            "end_step_index": end,
            "start_seq_no": start_seq,
            "end_seq_no": seqs[-1] if seqs else None,
            "winner_seats": winners,
            "ended": bool(final_state.get("round_ended", False)),
            "max_my_whiteboards": max_my_whiteboards,
            "whiteboard_match": max_my_whiteboards >= 2,
        })
    return rounds


def local_session(record):
    """本地记录 → 统一 ReplaySession。"""
    from .records import load_game_record
    rec = (load_game_record(record) if isinstance(record, str) else record)
    frames = _build_local_frames(rec)
    path = record if isinstance(record, str) else None
    strategy = evaluator = model_name = strategy_snapshot = None
    viewer = _local_viewer_seat(rec)
    seats = rec.get("seats")
    roles = rec.get("roles")
    if isinstance(roles, (list, tuple)) and viewer in range(len(roles)):
        role_index = roles[viewer]
    else:
        role_index = 0
    if isinstance(seats, list) and isinstance(role_index, int) \
            and 0 <= role_index < len(seats):
        role = seats[role_index]
        if isinstance(role, dict):
            strategy = role.get("strategy")
            evaluator = role.get("evaluator")
            model_name = role.get("model_name")
    snapshots = rec.get("strategy_snapshots") or []
    if isinstance(snapshots, list):
        strategy_snapshot = next((
            entry.get("snapshot") for entry in snapshots
            if isinstance(entry, dict) and entry.get("seat") == viewer), None)
    return _session_from_frames(
        frames, source="local", path=path, strategy=strategy,
        evaluator=evaluator, model_name=model_name,
        strategy_snapshot=strategy_snapshot,
        strategy_snapshots=snapshots if isinstance(snapshots, list) else None)


def online_session(records, *, session_id=None, path=None):
    """线上 jsonl 记录 → 统一 ReplaySession。"""
    builder = _OnlineBuilder()
    for rec in records:
        builder.ingest(rec)
    meta = next((rec for rec in records
                 if isinstance(rec, dict) and rec.get("type") == "meta"), {})
    return _session_from_frames(
        builder.frames, source="online", session_id=session_id, path=path,
        verifications=builder.verifications,
        strategy=meta.get("strategy"), evaluator=meta.get("evaluator"),
        model_name=meta.get("model_name"),
        strategy_snapshot=meta.get("strategy_snapshot"))
