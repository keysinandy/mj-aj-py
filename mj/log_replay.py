"""自记日志离线重放:自家视角重建 + 合法集断言 + 训练样本产出。

数据源:mj/platform/recorder.py 的 JSONL(meta/req/snapshot/events/
decision/action/reset/end)。重放与在线 BotClient 同构:snapshot 全量
重建 Mirror(与在线 _mirror_from_snapshot 同参数),events 增量推进,
decision 记录点 build_game 断言线上合法集 == 重放合法集。

对账内容:
- 每个决策点合法集一致(legal_actions 是唯一真源的线上验证);
- 自家胡牌结算:round_ended 判自家胡时,引擎 step(HU) 的倍率/积分
  vs 服务端 fan/scores 逐项对账(他家暗手不可见,他家胡无法对账);
- 每局 round_ended scores 累计 vs end 记录终局积分。

CLI(对账校验):
  python3 -m mj.log_replay <gid或路径> [--root local/games]
"""

import argparse
import sys

from .features import action_to_flat, extract, legal_mask
from .game import HU
from .logview import find_logs, load_records
from .platform.mirror import Mirror, MirrorInconsistent
from .platform.proto import EV_HU, EV_ROUND_ENDED


def _ok_action_ids(recs):
    """配对且提交成功的决策 id 集(严格训练样本的过滤依据)。"""
    return {r["decision"] for r in recs
            if r["type"] == "action" and r.get("ok")
            and r.get("decision") is not None}


def replay_game(recs, want_samples=True):
    """逐记录重放一场对局,返回 report。

    report 键:meta/my_seat/n_rounds/round_scores/end_scores/illegal/
    warnings/samples/clean。samples 仅含配对成功动作的决策点
    (planes/scalars/mask/action_flat/seq/round_no)。
    """
    meta = None
    ok_ids = _ok_action_ids(recs)
    rep = {"meta": None, "my_seat": None, "n_rounds": 0, "round_scores": [],
           "end_scores": None, "illegal": [], "warnings": [],
           "samples": [], "clean": False}
    mirror = None
    for r in recs:
        t = r["type"]
        if t == "meta":
            meta = r
            rep["meta"] = r
        elif t == "snapshot":
            snap = r["snap"]
            rep["my_seat"] = snap.get("seat")
            mirror = Mirror(
                my_seat=snap["seat"], dealer=snap.get("dealer", 0),
                base=(meta or {}).get("base", 1),
                you_cai_bi_kao=bool((meta or {}).get("you_cai_bi_kao")),
                round_no=snap.get("round_no", 1))
            mirror.apply_snapshot(snap)
        elif t == "events":
            for ev in r["events"]:
                if mirror is None:
                    continue
                try:
                    _on_event(rep, mirror, ev)
                except MirrorInconsistent as e:
                    rep["warnings"].append(
                        f"重放失步({e}) seq={ev.get('seq')},"
                        f"等待快照(在线同款自愈)")
                    mirror = None
                    break
        elif t == "decision":
            if mirror is None:
                rep["warnings"].append(
                    f"决策 #{r.get('id')} 无前置状态(重放未达)")
                continue
            _on_decision(rep, mirror, r, want_samples, ok_ids)
        elif t == "reset":
            mirror = None
        elif t == "end":
            rep["end_scores"] = r.get("scores")
    # 每局积分累计 vs 终局
    if rep["end_scores"] is not None and rep["round_scores"]:
        acc = [0] * 4
        for s in rep["round_scores"]:
            for i in range(4):
                acc[i] += s[i]
        if acc != list(rep["end_scores"]):
            rep["illegal"].append(
                {"msg": f"终局积分不符: 各局累计 {acc} vs end {rep['end_scores']}"})
    rep["clean"] = (not rep["illegal"] and rep["end_scores"] is not None
                    and not any(r["type"] == "reset" for r in recs))
    return rep


def _on_event(rep, mirror, ev):
    et = ev.get("type")
    if et == EV_HU and ev.get("seat") == mirror.me:
        _settle_own_hu(rep, mirror, ev)
    if et == EV_ROUND_ENDED:
        _settle_own_hu_round_ended(rep, mirror, ev)
        rep["n_rounds"] += 1
        data = ev.get("data") or {}
        if data.get("scores"):
            rep["round_scores"].append(list(data["scores"]))
    mirror.apply_event(ev)


def _settle_own_hu(rep, mirror, ev):
    """自家胡:引擎结算 vs round_ended 前的 hu 事件(尚无 fan 数据,
    实际对账在 round_ended 数据可用时进行——这里先只验证引擎可胡)。"""
    try:
        g = mirror.build_game("draw")
        if HU in g.legal_actions():
            g.step(HU)
            rep["_own_hu"] = {"scores": list(g.scores),
                              "result": list(g.result) if g.result else None,
                              "seq": ev.get("seq")}
    except (MirrorInconsistent, ValueError) as e:
        rep["warnings"].append(f"自家胡结算构建失败: {e} seq={ev.get('seq')}")


def _settle_own_hu_round_ended(rep, mirror, ev):
    """round_ended 判自家胡:引擎 step(HU) 结果 vs 服务端 fan/scores。"""
    data = ev.get("data") or {}
    own = rep.pop("_own_hu", None)
    if own is None or data.get("draw") or data.get("winner") != mirror.me:
        return
    if own["result"] is None:
        rep["illegal"].append({"seq": own["seq"], "msg": "自家胡: 引擎判流局"})
        return
    _, mult, _ = own["result"]
    fan = data.get("fan", data.get("multiplier"))
    if fan is not None and fan != mult:
        rep["illegal"].append({
            "seq": own["seq"],
            "msg": f"自家胡倍率不符: 引擎 {mult} vs 平台 {fan}"})
    if data.get("scores") and own["scores"] != list(data["scores"]):
        rep["illegal"].append({
            "seq": own["seq"],
            "msg": f"自家胡积分不符: 引擎 {own['scores']} "
                   f"vs 平台 {data['scores']}"})


def _on_decision(rep, mirror, r, want_samples, ok_ids):
    try:
        g = mirror.build_game(r["phase"])
    except MirrorInconsistent as e:
        rep["warnings"].append(f"决策 #{r.get('id')} 构建失败: {e}")
        return
    legal = sorted(g.legal_actions())
    if legal != list(r.get("legal") or []):
        rep["illegal"].append({
            "seq": r.get("seq"), "id": r.get("id"),
            "msg": f"合法集不符: 线上 {r.get('legal')} vs 重放 {legal}"})
        return
    if not want_samples or r.get("id") not in ok_ids:
        return
    planes, scalars = extract(g, mirror.me)
    rep["samples"].append({
        "planes": planes, "scalars": scalars,
        "mask": legal_mask(g),
        "action_flat": action_to_flat(r["action"]),
        "seq": r.get("seq"), "round_no": mirror.round_no,
        "decision_id": r.get("id"),
    })


# ---------- CLI ----------

def validate(path):
    recs = load_records(path)
    rep = replay_game(recs, want_samples=False)
    m = rep["meta"] or {}
    print(f"===== {path} =====")
    print(f"令牌 {m.get('name')} 座次 {rep['my_seat']} "
          f"YCBK={int(bool(m.get('you_cai_bi_kao')))} base={m.get('base')}")
    print(f"局数 {rep['n_rounds']}, 终局 {rep['end_scores']}, "
          f"干净: {rep['clean']}")
    for w in rep["warnings"]:
        print(f"  [warn] {w}")
    for ill in rep["illegal"]:
        print(f"  [ILLEGAL seq={ill.get('seq')}] {ill['msg']}")
    print(f"干净: {rep['clean']}")
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description="自记日志对账校验")
    ap.add_argument("target", help="gid 或日志文件路径")
    ap.add_argument("--root", default="local/games")
    args = ap.parse_args(argv)
    paths = find_logs(args.target, args.root)
    if not paths:
        print(f"未找到日志: {args.target} (root={args.root})", file=sys.stderr)
        return 1
    n_bad = 0
    for p in paths:
        rep = validate(p)
        n_bad += len(rep["illegal"])
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
