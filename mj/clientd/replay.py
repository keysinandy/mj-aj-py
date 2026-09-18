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

__all__ = ["local_frames", "online_frames", "DEAD_WALL_CONST"]

# 与 game.py DEAD_WALL 一致(4家 × 13 + ... → 活墙起算);墙数口径 84 - pops
DEAD_WALL_CONST = 14
_WALL_TOTAL = 84


def _meld_to_json(entry):
    kind = entry[0]
    if kind == "chow":
        a = entry[1]
        return {"kind": "chow", "tiles": [int(a), int(a + 1), int(a + 2)]}
    return {"kind": kind, "tiles": [int(t) for t in entry[1:]]}


def _local_frame(game, my_seat, actor, label, gap=False, step=0):
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
                                             else "playing")},
        "label": label, "gap": bool(gap),
    }


def _action_label(action):
    if isinstance(action, int):
        return logview.action_name(action)
    if action is None:
        return ""
    return str(action)


def local_frames(record):
    """本地记录 → 帧数组。record 可为路径或已加载 dict。"""
    from .records import load_game_record
    rec = (load_game_record(record) if isinstance(record, str)
           else record)
    g = Game(seed=rec["seed"], dealer=rec.get("dealer", 0),
             base=rec.get("base", 1),
             you_cai_bi_kao=rec.get("you_cai_bi_kao", False))
    my_seat = 0
    frames = [_local_frame(g, my_seat, g.current_seat(), "初始", step=0)]
    for k, action in enumerate(rec["actions"], start=1):
        legal = list(g.legal_actions())
        if action not in legal:
            raise ValueError(
                f"recorded action {action} illegal at step {k} "
                f"(legal={legal})")
        actor = g.current_seat()
        g.step(action)
        frames.append(_local_frame(g, my_seat, g.current_seat(),
                                   _action_label(action), step=k))
    return frames


# ---------------------------------------------------------------------------
# 线上帧
# ---------------------------------------------------------------------------

_EV_LABEL = {
    EV_DRAWN: "摸", EV_DISCARDED: "弃", EV_PASS: "过", EV_CHI: "吃",
    EV_PENG: "碰", EV_GANG: "杠", EV_HU: "胡", EV_TIMEOUT: "超时",
    EV_ROUND_ENDED: "回合结束", EV_GAME_ENDED: "终局",
}


def _online_frame(mirror, scores, step, label, gap=False):
    return {
        "step": step, "info_kind": "online",
        "my_seat": mirror.me, "hands": None,
        "my_hand": [int(v) for v in mirror.my_hand],
        "discards": [[int(v) for v in river] for river in mirror.discards],
        "melds": [[_meld_to_json(m) for m in ms] for ms in mirror.melds],
        "wall_remaining": _WALL_TOTAL - mirror._pops,
        "scores": scores,
        "round_no": mirror.round_no,
        "current": {"seat": _mirror_seat(mirror), "phase": "online"},
        "label": label, "gap": bool(gap),
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
            self._emit("快照", self.mirror.round_no, gap=was_gap)
            return
        if t == "events":
            for ev in rec.get("events") or []:
                if self.mirror is None:
                    continue
                try:
                    self.mirror.apply_event(ev)
                    label = _EV_LABEL.get(ev.get("type"), ev.get("type"))
                    self._emit(label, self.mirror.round_no)
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
            return
        if t == "end":
            if rec.get("scores"):
                self.scores = list(rec["scores"])
            return

    def _emit(self, label, round_no, gap=None):
        self.frames.append(_online_frame(
            self.mirror, self.scores, len(self.frames), label,
            gap=(self.gap if gap is None else gap)))


def online_frames(records):
    """线上 jsonl 记录列表 → (frames, verifications)。"""
    builder = _OnlineBuilder()
    for rec in records:
        builder.ingest(rec)
    return builder.frames, builder.verifications