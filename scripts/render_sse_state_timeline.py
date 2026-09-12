#!/usr/bin/env python3
"""Render a self-contained HTML timeline for one online room.

The client calls the notification stream ``/notify`` (SSE) and the state
endpoint ``/state``.  The UI calls the latter the status/state lane so that
the two transport paths can be compared without implying that a ``/status``
endpoint exists.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import statistics
from collections import Counter
from pathlib import Path


def _percentile(values, p):
    values = sorted(float(v) for v in values if isinstance(v, (int, float)))
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    index = (len(values) - 1) * p
    lo = int(index)
    hi = min(lo + 1, len(values) - 1)
    fraction = index - lo
    return values[lo] + (values[hi] - values[lo]) * fraction


def _round(value, digits=1):
    return round(value, digits) if isinstance(value, (int, float)) else None


def _local_time(epoch):
    if not isinstance(epoch, (int, float)):
        return None
    return dt.datetime.fromtimestamp(epoch).astimezone().isoformat(
        timespec="milliseconds")


def _read_game(path):
    rows = []
    raw_count = 0
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw_count += 1
            if row.get("type") not in {"sse_frame", "req"}:
                continue
            row["_line"] = line_no
            rows.append(row)

    rows.sort(key=lambda row: (row.get("ts", 0), row.get("_line", 0)))
    start = next((row.get("ts") for row in rows
                  if isinstance(row.get("ts"), (int, float))), None)
    events = []
    for index, row in enumerate(rows):
        ts = row.get("ts")
        rel_ms = ((ts - start) * 1000.0
                  if isinstance(ts, (int, float)) and start is not None else None)
        if row.get("type") == "sse_frame":
            events.append({
                "id": f"{path.name}:{row['_line']}",
                "index": index,
                "kind": "sse",
                "endpoint": "/api/games/{gid}/notify",
                "ts": ts,
                "local": _local_time(ts),
                "rel_ms": _round(rel_ms),
                "seq": row.get("seq"),
                "closed": bool(row.get("closed")),
                "accepted": bool(row.get("accepted")),
                "wake_enqueued": bool(row.get("wake_enqueued")),
                "deduplicated": bool(row.get("deduplicated")),
                "connection_id": row.get("connection_id"),
                "status": None,
                "latency_ms": None,
                "request_kind": None,
                "logical_request_id": None,
                "attempt_index": None,
                "reason": [],
                "requested_seq": None,
                "response_seq": None,
                "queue_wait_ms": None,
                "physical_attempts": None,
                "retry_429": 0,
                "retry_gateway": 0,
                "backoff_ms": 0,
                "snapshot": None,
                "gap": None,
                "n_events": None,
                "detail": {
                    "payload": row.get("payload"),
                    "previous_wake_seq": row.get("previous_wake_seq"),
                    "last_wake_seq": row.get("last_wake_seq"),
                    "raw_line": row.get("raw_line"),
                    "record_line": row.get("_line"),
                },
            })
            continue

        transport = row.get("transport") or {}
        throttle = row.get("throttle") or {}
        res = row.get("res") or {}
        attempts = transport.get("state_attempts") or []
        first_attempt = attempts[0] if attempts else {}
        attempt_throttle = first_attempt.get("throttle") or {}
        queue_wait = throttle.get("queue_wait_ms")
        if queue_wait is None:
            queue_wait = attempt_throttle.get("queue_wait_ms")
        events.append({
            "id": f"{path.name}:{row['_line']}",
            "index": index,
            "kind": "state",
            "endpoint": "/api/games/{gid}/state",
            "ts": row.get("ts"),
            "local": _local_time(row.get("ts")),
            "rel_ms": _round(rel_ms),
            "seq": row.get("seq"),
            "closed": None,
            "accepted": None,
            "wake_enqueued": None,
            "deduplicated": None,
            "connection_id": None,
            "status": row.get("status"),
            "latency_ms": row.get("latency_ms"),
            "request_kind": row.get("request_kind"),
            "logical_request_id": row.get("logical_request_id"),
            "attempt_index": row.get("attempt_index"),
            "reason": row.get("reason") or [],
            "requested_seq": row.get("requested_seq", row.get("seq")),
            "response_seq": res.get("seq"),
            "queue_wait_ms": queue_wait,
            "physical_attempts": transport.get("state_physical_attempts",
                                               transport.get("attempts")),
            "retry_429": transport.get("retry_429", 0),
            "retry_gateway": transport.get("retry_gateway", 0),
            "backoff_ms": transport.get("backoff_ms", 0),
            "snapshot": res.get("snapshot"),
            "gap": res.get("gap"),
            "n_events": res.get("n_events"),
            "detail": {
                "record_line": row.get("_line"),
                "response": {
                    "snapshot": res.get("snapshot"),
                    "gap": res.get("gap"),
                    "n_events": res.get("n_events"),
                    "pending": res.get("pending"),
                    "finished": res.get("finished"),
                    "seq": res.get("seq"),
                },
                "attempts": [
                    {
                        "attempt_index": item.get("attempt_index"),
                        "status": item.get("status"),
                        "latency_ms": item.get("latency_ms"),
                        "started_epoch": item.get("started_epoch"),
                        "headers_received_mono": item.get(
                            "headers_received_mono"),
                        "body_finished_mono": item.get("body_finished_mono"),
                        "deadline_left_at_headers_ms": item.get(
                            "deadline_left_at_headers_ms"),
                        "deadline_left_at_response_ms": item.get(
                            "deadline_left_at_response_ms"),
                        "throttle": item.get("throttle"),
                    }
                    for item in attempts
                ],
            },
        })

    return {
        "gid": path.stem,
        "file": str(path),
        "name": path.name,
        "raw_count": raw_count,
        "events": events,
    }


def _summary(game):
    events = game["events"]
    sse = [item for item in events if item["kind"] == "sse"]
    state = [item for item in events if item["kind"] == "state"]
    latencies = [item["latency_ms"] for item in state
                 if isinstance(item["latency_ms"], (int, float))]
    statuses = Counter(str(item["status"]) for item in state)
    kinds = Counter(item["request_kind"] or "unknown" for item in state)
    first = next((item["ts"] for item in events if item["ts"] is not None), None)
    last = next((item["ts"] for item in reversed(events)
                 if item["ts"] is not None), None)
    duration = (last - first if first is not None and last is not None else None)
    return {
        "gid": game["gid"],
        "batch": game["gid"].rsplit("_", 2)[-2]
        if len(game["gid"].rsplit("_", 2)) >= 3 else game["gid"],
        "file": game["file"],
        "events": len(events),
        "raw_events": game["raw_count"],
        "first_ts": first,
        "last_ts": last,
        "first_local": _local_time(first),
        "last_local": _local_time(last),
        "duration_s": _round(duration),
        "sse_frames": len(sse),
        "sse_accepted": sum(bool(item["accepted"]) for item in sse),
        "sse_wake_enqueued": sum(bool(item["wake_enqueued"]) for item in sse),
        "sse_deduplicated": sum(bool(item["deduplicated"]) for item in sse),
        "sse_closed": sum(bool(item["closed"]) for item in sse),
        "state_requests": len(state),
        "state_statuses": dict(statuses),
        "request_kinds": dict(kinds),
        "state_429": sum(item["retry_429"] or 0 for item in state),
        "state_502": sum(item["retry_gateway"] or 0 for item in state),
        "backoff_ms": _round(sum(item["backoff_ms"] or 0 for item in state)),
        "latency_p50_ms": _round(_percentile(latencies, 0.5)),
        "latency_p95_ms": _round(_percentile(latencies, 0.95)),
        "latency_max_ms": _round(max(latencies) if latencies else None),
        "queue_p50_ms": _round(_percentile(
            [item["queue_wait_ms"] for item in state
             if isinstance(item["queue_wait_ms"], (int, float))], 0.5)),
        "queue_max_ms": _round(max(
            [item["queue_wait_ms"] for item in state
             if isinstance(item["queue_wait_ms"], (int, float))],
            default=None)),
    }


def _json_script(value):
    # Keep JSON inside a script tag safe even if a raw SSE line contains HTML.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace(
        "<", "\\u003c")


def _html(room, date, games):
    summaries = [_summary(game) for game in games]
    payload = {
        "room": room,
        "date": date,
        "generated_at": dt.datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "games": games,
        "summaries": summaries,
    }
    default_gid = max(
        summaries, key=lambda item: item.get("last_ts") or 0)["gid"] if summaries else ""
    return """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SSE / State 时间线 - ROOM_PLACEHOLDER</title>
<style>
:root { color-scheme: light; --ink:#172033; --muted:#667085; --line:#e5e7eb;
  --blue:#2563eb; --blue-bg:#eff6ff; --green:#15803d; --green-bg:#f0fdf4;
  --amber:#b45309; --amber-bg:#fffbeb; --red:#b91c1c; --red-bg:#fef2f2;
  --purple:#7e22ce; --purple-bg:#faf5ff; }
* { box-sizing:border-box; }
body { margin:0; background:#f6f7fb; color:var(--ink); font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
main { max-width:1680px; margin:0 auto; padding:24px; }
h1 { margin:0 0 6px; font-size:24px; }
h2 { margin:26px 0 12px; font-size:17px; }
.subtle { color:var(--muted); }
.note { margin:14px 0; padding:12px 14px; border:1px solid #bfdbfe; border-radius:10px; background:var(--blue-bg); }
.cards { display:grid; grid-template-columns:repeat(6,minmax(125px,1fr)); gap:10px; margin:18px 0; }
.card { background:white; border:1px solid var(--line); border-radius:10px; padding:12px; box-shadow:0 1px 2px #00000008; }
.card .label { color:var(--muted); font-size:12px; }
.card .value { margin-top:4px; font-size:21px; font-weight:700; }
.controls { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin:14px 0; }
select,input { border:1px solid #cbd5e1; border-radius:7px; padding:8px 10px; background:white; color:var(--ink); }
input { min-width:280px; }
.panel { background:white; border:1px solid var(--line); border-radius:10px; overflow:hidden; }
.table-wrap { overflow:auto; max-height:720px; }
table { width:100%; border-collapse:collapse; min-width:940px; }
th { position:sticky; top:0; z-index:2; background:#f8fafc; color:#475467; text-align:left; font-size:12px; padding:9px 10px; border-bottom:1px solid var(--line); }
td { padding:8px 10px; border-bottom:1px solid #eef0f4; vertical-align:top; white-space:nowrap; }
tr:hover { background:#fafcff; }
tr.sse-row { background:#f8fbff; }
tr.state-row { background:#fff; }
.badge { display:inline-block; padding:2px 7px; border-radius:999px; font-size:12px; font-weight:600; }
.badge.sse { color:#1d4ed8; background:#dbeafe; }
.badge.state { color:#166534; background:#dcfce7; }
.badge.full { color:#7e22ce; background:#f3e8ff; }
.badge.delta { color:#0369a1; background:#e0f2fe; }
.badge.confirm { color:#b45309; background:#fef3c7; }
.badge.retry { color:#b91c1c; background:#fee2e2; }
.mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }
.muted { color:#98a2b3; }
.ok { color:var(--green); }
.warn { color:var(--amber); }
.bad { color:var(--red); }
details { cursor:pointer; }
details pre { white-space:pre-wrap; min-width:480px; max-width:900px; margin:8px 0 0; padding:9px; background:#f8fafc; border-radius:6px; color:#344054; font-size:11px; }
.legend { display:flex; flex-wrap:wrap; gap:8px 16px; color:var(--muted); margin:9px 0; }
.small { font-size:12px; }
@media (max-width:1000px) { .cards { grid-template-columns:repeat(3,minmax(120px,1fr)); } main { padding:14px; } }
@media (max-width:620px) { .cards { grid-template-columns:repeat(2,minmax(120px,1fr)); } input { min-width:180px; width:100%; } }
</style>
</head>
<body>
<main>
  <h1>SSE / 状态接口发送时间线</h1>
  <div class="subtle">房间 <span class="mono">ROOM_PLACEHOLDER</span> · 日志日期 DATE_PLACEHOLDER · 生成时间 GENERATED_PLACEHOLDER</div>
  <div class="note">
    当前客户端实际使用两个接口：<b>GET /api/games/{gid}/notify</b>（SSE，只推送状态变化水位）和
    <b>GET /api/games/{gid}/state</b>（状态快照/增量拉取）。仓库中没有 <b>/status</b> 接口；页面将后者作为“状态接口”展示。
  </div>
  <div class="controls">
    <label>选择局：<select id="gameSelect"></select></label>
    <label>显示：<select id="kindSelect"><option value="all">SSE + 状态</option><option value="sse">仅 SSE</option><option value="state">仅状态</option></select></label>
    <input id="search" placeholder="搜索 seq、request id、reason、状态码…">
    <label class="small"><input id="onlyInteresting" type="checkbox"> 只看唤醒/去重/重试/非 200</label>
  </div>
  <div id="cards" class="cards"></div>
  <div class="legend">
    <span><span class="badge sse">SSE</span> 服务端 notify 帧</span>
    <span><span class="badge state">STATE</span> /state 请求</span>
    <span><span class="badge full">FULL</span> seq=0 全量快照</span>
    <span><span class="badge delta">DELTA</span> 增量状态</span>
    <span><span class="badge retry">RETRY</span> 发生重试或退避</span>
  </div>
  <h2>房间概览</h2>
  <div class="panel"><div class="table-wrap"><table><thead><tr>
    <th>局</th><th>起止时间</th><th>持续</th><th>SSE 帧</th><th>唤醒/去重</th><th>状态请求</th><th>状态码</th><th>延迟 p50/p95/max</th><th>队列 p50/max</th><th>429/502/退避</th>
  </tr></thead><tbody id="overview"></tbody></table></div></div>
  <h2>选中局的交错时间线</h2>
  <div class="subtle small">相对时间从该局第一条 SSE/状态记录开始；点击“详情”可查看该条日志的关键字段。</div>
  <div class="panel"><div class="table-wrap"><table><thead><tr>
    <th>相对时间</th><th>日志时间</th><th>接口</th><th>seq</th><th>HTTP</th><th>请求类型</th><th>逻辑请求</th><th>延迟</th><th>队列</th><th>结果/原因</th><th>详情</th>
  </tr></thead><tbody id="timeline"></tbody></table></div></div>
</main>
<script>
const DATA = DATA_PLACEHOLDER;
const DEFAULT_GID = DEFAULT_GID_PLACEHOLDER;
const byId = id => document.getElementById(id);
const fmt = (v, suffix='') => v === null || v === undefined || v === '' ? '—' : `${v}${suffix}`;
const num = (v, digits=1) => typeof v === 'number' ? Number(v.toFixed(digits)) : null;
function escapeHtml(value) { const d=document.createElement('div'); d.textContent=String(value ?? ''); return d.innerHTML; }
function jsonPretty(value) { return escapeHtml(JSON.stringify(value, null, 2)); }
function gameByGid(gid) { return DATA.games.find(g => g.gid === gid); }
function summaryByGid(gid) { return DATA.summaries.find(g => g.gid === gid); }
function badge(kind, item) {
  if (kind === 'sse') return '<span class="badge sse">SSE</span>';
  const k=item.request_kind || 'STATE';
  const cls=k === 'RESYNC' ? 'full' : k === 'WINDOW_CONFIRM' ? 'confirm' : 'delta';
  return `<span class="badge state">STATE</span> <span class="badge ${cls}">${escapeHtml(k)}</span>`;
}
function renderCards(s) {
  const values = [
    ['SSE 帧', s.sse_frames], ['状态请求', s.state_requests],
    ['SSE 唤醒', s.sse_wake_enqueued], ['SSE 去重', s.sse_deduplicated],
    ['状态延迟 p50', fmt(s.latency_p50_ms,' ms')], ['状态队列 max', fmt(s.queue_max_ms,' ms')],
  ];
  byId('cards').innerHTML = values.map(([label,value]) => `<div class="card"><div class="label">${label}</div><div class="value">${value}</div></div>`).join('');
}
function renderOverview() {
  byId('overview').innerHTML = DATA.summaries.map(s => {
    const status=Object.entries(s.state_statuses).map(([k,v])=>`${k}:${v}`).join(' ');
    return `<tr data-gid="${escapeHtml(s.gid)}"><td class="mono">${escapeHtml(s.batch || s.gid)}</td>
      <td class="small">${escapeHtml(s.first_local || '—')}<br>${escapeHtml(s.last_local || '—')}</td>
      <td>${fmt(s.duration_s,' s')}</td><td>${s.sse_frames}</td>
      <td>${s.sse_wake_enqueued} / ${s.sse_deduplicated}</td><td>${s.state_requests}</td>
      <td class="small">${escapeHtml(status)}</td>
      <td>${fmt(s.latency_p50_ms,' / ')}${s.latency_p50_ms!==null?' / '+fmt(s.latency_p95_ms)+' / '+fmt(s.latency_max_ms)+' ms':''}</td>
      <td>${fmt(s.queue_p50_ms,' / ')}${s.queue_p50_ms!==null?' / '+fmt(s.queue_max_ms)+' ms':''}</td>
      <td>${s.state_429} / ${s.state_502} / ${fmt(s.backoff_ms,' ms')}</td></tr>`;
  }).join('');
  document.querySelectorAll('#overview tr').forEach(row => row.addEventListener('click', () => {
    byId('gameSelect').value=row.dataset.gid; render(); window.scrollTo({top:0,behavior:'smooth'});
  }));
}
function interesting(item) {
  return item.kind === 'sse' ? Boolean(item.wake_enqueued || item.deduplicated || item.closed)
    : Boolean(item.status !== 200 || item.retry_429 || item.retry_gateway || item.backoff_ms || item.request_kind === 'WINDOW_CONFIRM');
}
function searchable(item) {
  return JSON.stringify(item).toLowerCase();
}
function rowHtml(item) {
  const isRetry=item.retry_429 || item.retry_gateway || item.backoff_ms;
  const result=item.kind==='sse'
    ? `seq=${fmt(item.seq)}${item.closed?' · closed':''}${item.wake_enqueued?' · wake':''}${item.deduplicated?' · dedup':''}`
    : `req=${fmt(item.requested_seq)} → resp=${fmt(item.response_seq)}${item.snapshot?' · snapshot':''}${item.gap?' · gap':''}${item.reason.length?' · '+item.reason.join(','):''}`;
  const details={...item.detail, endpoint:item.endpoint, timestamp:item.local, relative_ms:item.rel_ms};
  return `<tr class="${item.kind}-row">
    <td class="mono">${fmt(item.rel_ms,' ms')}</td><td class="small">${escapeHtml(item.local || '—')}</td>
    <td>${badge(item.kind,item)}<div class="muted small">${escapeHtml(item.endpoint)}</div></td>
    <td class="mono">${fmt(item.seq)}</td><td class="${item.status && item.status!==200?'bad':'ok'}">${fmt(item.status)}</td>
    <td>${item.kind==='sse'?'—':escapeHtml(item.request_kind || 'STATE')}</td>
    <td class="mono">${escapeHtml(item.logical_request_id || (item.kind==='sse'?`conn-${item.connection_id??'?'}`:'—'))}</td>
    <td>${fmt(item.latency_ms,' ms')}</td><td>${fmt(item.queue_wait_ms,' ms')}</td>
    <td class="${isRetry?'warn':''}">${escapeHtml(result)}${isRetry?`<br><span class="badge retry">retry/backoff ${fmt(item.backoff_ms,' ms')}</span>`:''}</td>
    <td><details><summary>展开</summary><pre>${jsonPretty(details)}</pre></details></td>
  </tr>`;
}
function render() {
  const gid=byId('gameSelect').value || DEFAULT_GID;
  const game=gameByGid(gid); const summary=summaryByGid(gid); if(!game || !summary)return;
  renderCards(summary);
  const kind=byId('kindSelect').value; const search=byId('search').value.trim().toLowerCase(); const only=byId('onlyInteresting').checked;
  const rows=game.events.filter(item => (kind==='all'||item.kind===kind) && (!search||searchable(item).includes(search)) && (!only||interesting(item)));
  byId('timeline').innerHTML=rows.map(rowHtml).join('') || '<tr><td colspan="11" class="muted">没有匹配记录</td></tr>';
}
const select=byId('gameSelect');
DATA.summaries.forEach(s => { const o=document.createElement('option'); o.value=s.gid; o.textContent=s.gid; select.appendChild(o); });
select.value=DEFAULT_GID; renderOverview();
['gameSelect','kindSelect','search','onlyInteresting'].forEach(id => byId(id).addEventListener(id==='search'?'input':'change',render));
render();
</script>
</body>
</html>
""".replace("ROOM_PLACEHOLDER", room).replace("DATE_PLACEHOLDER", date).replace(
        "GENERATED_PLACEHOLDER", payload["generated_at"]).replace(
        "DATA_PLACEHOLDER", _json_script(payload)).replace(
        "DEFAULT_GID_PLACEHOLDER", json.dumps(default_gid))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=dt.datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--room", required=True)
    parser.add_argument("--root", default="local/games")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    pattern = os.path.join(args.root, args.date, f"*_{args.room}_*.jsonl")
    paths = [Path(item) for item in sorted(glob.glob(pattern))]
    if not paths:
        raise SystemExit(f"no game logs found for room {args.room!r}: {pattern}")
    games = [_read_game(path) for path in paths]
    html = _html(args.room, args.date, games)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    total = sum(len(game["events"]) for game in games)
    print(f"wrote {output} ({len(games)} games, {total} SSE/state rows)")


if __name__ == "__main__":
    main()
