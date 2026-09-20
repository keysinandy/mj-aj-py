"""回放帧管线(task 3.1 / 3.2):把对局记录变成可索引的帧数组。

统一 Frame 形状(JSON 可直接送前端):
    {step, info_kind: "local"|"online", my_seat,
     hands: [4][34]|None,   # None => 他家暗手不可见(线上)
     my_hand: [34]|None,    # 线上视角的本家手牌
     discards, melds, wall_remaining, scores, round_no,
     current: {seat, phase}, label, gap}

- 本地(3.1):预计算 = Game(seed) 按动作序列 step 一次,逐步快照;
  步进/拖动 = 帧数组索引,零重算;非法动作显式报错(不静默修正)。
- 线上(3.2):复用 Mirror 语义(snapshot 锚点 + events 增量),产出自家视角帧;
  他家暗手永不载入;跳段(协议固有)以最近锚点重建并输出 gap 标注。
"""

from __future__ import annotations

from .. import logview
from ..game import Game
from ..platform.mirror import Mirror, MirrorInconsistent
from ..platform.proto import (
    EV_DRAWN, EV_DISCARDED, EV_PASS, EV_CHI, EV_PENG, EV_GANG, EV_HU,
    EV_TIMEOUT, EV_ROUND_ENDED, EV_GAME_ENDED,
)

__all__ = ["local_frames", "local_session", "online_frames",
           "online_session", "DEAD_WALL_CONST"]

# 与 game.py DEAD_WALL 一致(4家 × 13 + ... → 活墙起算);墙数口径 84 - pops
DEAD_WALL_CONST = 14
_WALL_TOTAL = 84


def _meld_to_json(entry):
    kind = entry[0]
    if kind == "chow":
        a = entry[1]
        return {"kind": "chow", "tiles": [int(a), int(a + 1), int(a + 2)]}
    return {"kind": kind, "tiles": [int(t) for t in entry[1:]]}


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


def _local_frame(game, my_seat, actor, label, gap=False, step=0,
                 *, seq_no=None, seq_source="local_action", timestamp=None,
                 event=None, local_requests=None, diagnostics=None):
    return {
        "step": step, "info_kind": "local", "my_seat": my_seat,
        "hands": [[int(v) for v in h] for h in game.hands],
        "my_hand": [int(v) for v in game.hands[my_seat]],
        "discards": [[int(v) for v in river] for river in game.discards],
        "melds": [[_meld_to_json(m) for m in ms] for ms in game.melds],
        "wall_remaining": game.live_wall_left(),
        "scores": [int(v) for v in game.scores],
        "round_no": 1,
        "current": {"seat": actor, "phase": ("done" if game.done
                                             else getattr(game, "phase", "playing"))},
        "label": label, "gap": bool(gap),
        "seq_no": step if seq_no is None else seq_no,
        "seq_source": seq_source,
        "timestamp": timestamp,
        "event": event,
        "local_requests": list(local_requests or []),
        "diagnostics": list(diagnostics or []),
        "response_window": _response_window(game),
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
    my_seat = 0
    requests = rec.get("local_requests", rec.get("requests", []))
    diagnostics = rec.get("diagnostics", [])
    frames = [_local_frame(
        g, my_seat, g.current_seat(), "初始", step=0,
        seq_no=0, seq_source="local_initial",
        event={"type": "session_start"},
        local_requests=_annotation_bucket(requests, 0, 0),
        diagnostics=_annotation_bucket(diagnostics, 0, 0))]
    for k, action in enumerate(rec["actions"], start=1):
        legal = list(g.legal_actions())
        if action not in legal:
            raise ValueError(
                f"recorded action {action} illegal at step {k} "
                f"(legal={legal})")
        actor = g.current_seat()
        g.step(action)
        frames.append(_local_frame(
            g, my_seat, g.current_seat(), _action_label(action), step=k,
            seq_no=k, seq_source="local_action",
            event={"type": "action", "action": action, "actor": actor,
                   "label": _action_label(action)},
            local_requests=_annotation_bucket(requests, k, k),
            diagnostics=_annotation_bucket(diagnostics, k, k)))
    return frames


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
                  diagnostics=None):
    return {
        "step": step, "info_kind": "online",
        "my_seat": mirror.me, "hands": None,
        "my_hand": [int(v) for v in mirror.my_hand],
        "discards": [[int(v) for v in river] for river in mirror.discards],
        "melds": [[_meld_to_json(m) for m in ms] for ms in mirror.melds],
        "wall_remaining": _WALL_TOTAL - mirror._pops,
        "scores": list(scores or [0, 0, 0, 0]),
        "round_no": mirror.round_no,
        "current": {"seat": _mirror_seat(mirror), "phase": "online"},
        "label": label, "gap": bool(gap),
        "seq_no": seq_no,
        "seq_source": (
            "snapshot" if isinstance(event, dict) and event.get("type") == "snapshot"
            else "server_event" if seq_no is not None else "derived"),
        "timestamp": timestamp,
        "event": event,
        "local_requests": list(local_requests or []),
        "diagnostics": list(diagnostics or []),
        "response_window": _online_response_window(mirror),
    }


def _mirror_seat(mirror):
    if mirror.pending is not None:
        return mirror.pending[0]
    return None


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
            self.mirror = Mirror(
                my_seat=snap["seat"], dealer=snap.get("dealer", 0),
                base=(self.meta or {}).get("base", 1),
                you_cai_bi_kao=self._base(), round_no=snap.get("round_no", 1))
            self.mirror.apply_snapshot(snap)
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
                    self.mirror.apply_event(ev)
                    label = _EV_LABEL.get(ev.get("type"), ev.get("type"))
                    self._emit(
                        label, self.mirror.round_no,
                        seq_no=ev.get("seq"), timestamp=ev.get("ts"),
                        event=dict(ev))
                except MirrorInconsistent:
                    # 失步 → 等待快照;缺口标注
                    self.mirror = None
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
            diagnostics=self.pending_diagnostics)
        self.pending_requests = []
        self.pending_diagnostics = []
        self.frames.append(frame)


def online_frames(records):
    """线上 jsonl 记录列表 → (frames, verifications)。"""
    builder = _OnlineBuilder()
    for rec in records:
        builder.ingest(rec)
    return builder.frames, builder.verifications


def _session_from_frames(frames, *, source, session_id=None, path=None,
                         verifications=None):
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
    }
    if verifications is not None:
        metadata["verifications"] = verifications
    return {
        "metadata": metadata,
        "initial_state": frames[0] if frames else None,
        "steps": steps,
    }


def local_session(record):
    """本地记录 → 统一 ReplaySession。"""
    from .records import load_game_record
    rec = (load_game_record(record) if isinstance(record, str) else record)
    frames = _build_local_frames(rec)
    path = record if isinstance(record, str) else None
    return _session_from_frames(frames, source="local", path=path)


def online_session(records, *, session_id=None, path=None):
    """线上 jsonl 记录 → 统一 ReplaySession。"""
    builder = _OnlineBuilder()
    for rec in records:
        builder.ingest(rec)
    return _session_from_frames(
        builder.frames, source="online", session_id=session_id, path=path,
        verifications=builder.verifications)
