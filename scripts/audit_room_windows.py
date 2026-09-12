"""Per-window audit for one room's claim_miss / 409 / auto-play records.

Reads JSONL logs and reconstructs the timeline around each failure:
source discard event -> window_confirm attempts -> decisions -> actions ->
terminal event (timeout / 409). Read-only; used for acceptance attribution.
"""
import json
import sys
from pathlib import Path

_HONOR = {"东": 27, "南": 28, "西": 29, "北": 30, "中": 31, "发": 32,
          "白": 33}


def tile_int(tile):
    """Protocol tile string ("2w"/"9t"/"发") -> internal int id."""
    if not isinstance(tile, str) or len(tile) < 2:
        return _HONOR.get(tile)
    try:
        return int(tile[0]) - 1 + {"w": 0, "b": 9, "t": 18}[tile[1]]
    except (KeyError, ValueError):
        return _HONOR.get(tile)


def load(path):
    records = []
    with open(path, encoding="utf8") as fh:
        for i, line in enumerate(fh, 1):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec["_line"] = i
            records.append(rec)
    return records


def window_key(rec):
    wid = rec.get("window_id") or {}
    return (wid.get("round_id"), wid.get("discard_owner"),
            wid.get("source_discard_seq"), wid.get("tile"))


def audit(paths):
    for path in paths:
        records = load(path)
        gid = path.stem
        miss_idx = [i for i, r in enumerate(records)
                    if r.get("type") == "claim_miss"]
        for i in miss_idx:
            rec = records[i]
            print("=" * 90)
            print(f"[{gid}] line {rec['_line']} claim_miss "
                  f"phase={rec.get('phase')} reason={rec.get('reason')} "
                  f"legal={rec.get('legal')} chosen={rec.get('chosen')} "
                  f"seq={rec.get('seq')} ts={rec.get('ts')}")
            # Walk back to the source tile_discarded matching pending tiles.
            pending = rec.get("pending") or []
            want_seat = pending[0] if len(pending) > 0 else None
            want_tile = tile_int(pending[1]) if len(pending) > 1 else None
            start = None
            for j in range(i - 1, -1, -1):
                r = records[j]
                if r.get("type") == "events":
                    evs = r.get("events") or []
                    for e in reversed(evs):
                        if (e.get("type") == "tile_discarded"
                                and (want_seat is None
                                     or e.get("seat") == want_seat)
                                and (want_tile is None
                                     or tile_int(e.get("tile")) == want_tile)):
                            start = (j, e)
                            break
                    if start:
                        break
            if start is None:
                print("  !! source discard not found")
                continue
            j0, src = start
            print(f"  source tile_discarded line {records[j0]['_line']} "
                  f"ts={src.get('ts')} seat={src.get('seat')} "
                  f"data={src.get('data')}")
            # Timeline between source and miss (+ a few records after).
            for j in range(j0, min(len(records), i + 6)):
                r = records[j]
                t = r.get("type")
                if t == "req":
                    reasons = r.get("reason") or []
                    if "WINDOW_CONFIRM" in reasons or r.get("request_kind", "").startswith("WINDOW"):
                        st = (r.get("transport", {})
                                .get("state_attempts") or [{}])
                        last = st[-1] if st else {}
                        thr = last.get("throttle") or {}
                        print(f"    L{r['_line']} req {r.get('logical_request_id')} "
                              f"kind={r.get('request_kind')} seq={r.get('seq')} "
                              f"status={r.get('status')} lat={r.get('latency_ms')} "
                              f"attempts={r.get('attempts')} "
                              f"queue={thr.get('queue_wait_ms')} "
                              f"urgent={thr.get('urgent')} "
                              f"dl_left={last.get('deadline_left_at_response_ms')}")
                elif t == "window_confirm":
                    print(f"    L{r['_line']} window_confirm "
                          f"phase={r.get('phase')} outcome={r.get('outcome')} "
                          f"reason={r.get('reason')} "
                          f"snapshot_phase={r.get('snapshot_phase')} "
                          f"legal={r.get('legal')} chosen={r.get('chosen')} "
                          f"exact_dl={r.get('exact_deadline_at')} "
                          f"window={window_key(r)}")
                elif t == "decision":
                    print(f"    L{r['_line']} decision id={r.get('id')} "
                          f"phase={r.get('phase')} action={r.get('action')} "
                          f"window={window_key(r)}")
                elif t == "action":
                    print(f"    L{r['_line']} action phase={r.get('phase')} "
                          f"ok={r.get('ok')} status={r.get('status')} "
                          f"code={r.get('code')} "
                          f"payload={r.get('payload')} "
                          f"deadline={r.get('deadline_at')} "
                          f"window={window_key(r)}")
                elif t == "events":
                    for e in (r.get("events") or []):
                        et = e.get("type")
                        if et in ("timeout", "pass", "chi", "peng", "gang",
                                  "hu"):
                            print(f"    L{r['_line']} event {et} "
                                  f"seat={e.get('seat')} "
                                  f"ts={e.get('ts')} data={e.get('data')}")
                elif t == "claim_miss":
                    print(f"    L{r['_line']} CLAIM_MISS reason={r.get('reason')}")
            print()


def main():
    room = sys.argv[1]
    root = Path("local/games/20260911")
    paths = sorted(root.glob(f"*_{room}_r*_b*.jsonl"))
    if not paths:
        sys.exit(f"no logs for {room}")
    audit(paths)


if __name__ == "__main__":
    main()
