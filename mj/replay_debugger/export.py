"""Deterministic JSON and self-contained offline HTML export."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Iterable

from .model import ReplaySession


def safe_json(value) -> str:
    """Embed JSON as data, including protection against ``</script>``."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
            .replace("&", "\\u0026")
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("/", "\\u002f"))


_CSS = r"""
:root { color-scheme: light; font: 14px/1.45 system-ui, sans-serif; color: #20252b; background: #f4f6f8; }
body { margin: 0; } header { background: #17212b; color: white; padding: 14px 20px; }
main { max-width: 1500px; margin: auto; padding: 14px; } button, select, input { padding: 5px 8px; border: 1px solid #bbc5ce; border-radius: 4px; background: white; }
button { cursor: pointer; } .bar, .grid, .table { display: grid; gap: 10px; } .bar { grid-template-columns: repeat(auto-fit, minmax(110px, max-content)); align-items: center; }
.grid { grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); margin-top: 12px; } .panel { background: white; border: 1px solid #d9e0e6; border-radius: 6px; padding: 10px; overflow: auto; }
.table { grid-template-columns: repeat(2, minmax(0, 1fr)); } .seat { border: 1px solid #e0e5ea; padding: 8px; min-height: 90px; }
.me { border-color: #e08d31; background: #fffaf0; } .muted { color: #66727d; } .unknown { color: #8b4c00; font-style: italic; }
pre { white-space: pre-wrap; word-break: break-word; margin: 6px 0 0; } .chips { display: flex; flex-wrap: wrap; gap: 4px; } .chip { background: #edf2f5; padding: 2px 5px; border-radius: 3px; }
.error { color: #a92828; } .ok { color: #247442; } .wide { grid-column: 1 / -1; }
"""


_JS = r"""
(function () {
  const data = JSON.parse(document.getElementById('replay-data').textContent);
  const ui = { frame: 0, local: data.cursor.localStepIndex, view: (data.view || {}).handVisibility || 'LOCAL_KNOWLEDGE', selected: (data.view || {}).selectedPlayer || 0, playing: false };
  const byId = id => document.getElementById(id);
  const text = value => value == null ? '' : String(value);
  const pretty = value => JSON.stringify(value == null ? null : value, null, 2);
  function frames() { return data.seqFrames || []; }
  function frame() { return frames()[ui.frame] || null; }
  function step() { return (data.localSteps || []).find(s => s.index === ui.local) || null; }
  function stateFor(frame) {
    if (!frame) return null;
    const key = String(frame.seqNo);
    const server = (data.states || {}).server || {};
    return server[key] || server["(" + String(frame.roundNo) + ", " + key + ")"] || frame.serverAfter;
  }
  function project(state) {
    if (!state) return null;
    const result = JSON.parse(JSON.stringify(state));
    (result.players || []).forEach(p => {
      if (ui.view === 'OMNISCIENT' || (ui.view === 'PLAYER_VIEW' && p.seat === ui.selected)) return;
      if (p.hand && p.hand.status === 'KNOWN') p.hand = { status: 'HIDDEN', evidence: p.hand.evidence || 'RECORDED' };
    });
    return result;
  }
  function render() {
    const fs = frames(); const f = frame(); const s = step(); const projected = project(stateFor(f));
    byId('position').textContent = f ? ('round ' + text(f.roundNo) + ' / seq ' + text(f.seqNo) + ' / ' + (ui.local == null ? 'server' : 'local ' + ui.local)) : 'unavailable';
    // Keep the fallback renderer safe even when a React bundle is unavailable.
    byId('table').textContent = pretty(projected);
    byId('server-event').textContent = pretty(f && f.serverEvent);
    byId('local-step').textContent = pretty(s);
    const req = (data.requests || []).find(r => r.requestId === (s && s.requestId) || r.logicalRequestId === (s && s.requestId));
    byId('request').textContent = pretty(req);
    const diags = (data.diagnostics || []).filter(d => !f || d.roundNo === f.roundNo && (d.seqNo == null || d.seqNo === f.seqNo));
    byId('diagnostics').textContent = pretty(diags.length ? diags : (data.diagnostics || []).slice(0, 5));
    byId('coverage').textContent = pretty(data.sourceCoverage || {});
    byId('raw-search').textContent = pretty((data.rawRecords || []).filter(r => !byId('search').value || JSON.stringify(r).toLowerCase().includes(byId('search').value.toLowerCase())).slice(0, 20));
    byId('first').disabled = !fs.length || ui.frame === 0; byId('last').disabled = !fs.length || ui.frame === fs.length - 1;
  }
  function move(n) { ui.frame = Math.max(0, Math.min(frames().length - 1, ui.frame + n)); const f = frame(); ui.local = f && f.localSteps && f.localSteps.length ? (data.localSteps.find(s => s.stepId === f.localSteps[0]) || {}).index : null; render(); }
  function moveLocal(n) { const all = data.localSteps || []; if (!all.length) return; const at = ui.local == null ? 0 : all.findIndex(s => s.index === ui.local); const next = Math.max(0, Math.min(all.length - 1, at + n)); ui.local = all[next].index; const fidx = frames().findIndex(f => (f.localSteps || []).includes(all[next].stepId)); if (fidx >= 0) ui.frame = fidx; render(); }
  function jumpDiagnostic(direction) { const ds = data.diagnostics || []; if (!ds.length) return; const current = ds.findIndex(d => d.seqNo === (frame() || {}).seqNo && d.localStepIndex === ui.local); const d = ds[Math.max(0, Math.min(ds.length - 1, (current < 0 ? 0 : current) + direction))]; const fidx = frames().findIndex(f => f.seqNo === d.seqNo && f.roundNo === d.roundNo); if (fidx >= 0) ui.frame = fidx; if (d.localStepIndex != null) ui.local = d.localStepIndex; render(); }
  byId('first').onclick = () => { ui.frame = 0; render(); }; byId('last').onclick = () => { ui.frame = frames().length - 1; render(); };
  byId('prev').onclick = () => move(-1); byId('next').onclick = () => move(1); byId('local-prev').onclick = () => moveLocal(-1); byId('local-next').onclick = () => moveLocal(1);
  byId('diag-prev').onclick = () => jumpDiagnostic(-1); byId('diag-next').onclick = () => jumpDiagnostic(1);
  byId('play').onclick = () => { ui.playing = !ui.playing; byId('play').textContent = ui.playing ? 'Pause' : 'Play'; if (ui.playing) tick(); };
  function tick() { if (!ui.playing) return; moveLocal(1); if (ui.local < (data.localSteps || []).length - 1) setTimeout(tick, 350); else { ui.playing = false; byId('play').textContent = 'Play'; } }
  byId('view').onchange = e => { ui.view = e.target.value; render(); }; byId('selected').onchange = e => { ui.selected = Number(e.target.value); render(); }; byId('search').oninput = render;
  byId('seq').onchange = e => { const n = Number(e.target.value); const i = frames().findIndex(f => f.seqNo === n); if (i < 0) { byId('message').textContent = 'That seq is unavailable; the valid cursor was retained.'; return; } ui.frame = i; render(); };
  render();
})();
"""


def render_html(session: ReplaySession) -> str:
    payload = safe_json(session.as_dict())
    react_bundle = _load_react_bundle()
    if react_bundle is not None:
        javascript, stylesheet = react_bundle
        return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="ui-framework" content="React + shadcn/ui"><title>Mahjong Replay Debugger</title><style>""" + stylesheet + """</style></head>
<body><div id="root"></div><script>globalThis.__REPLAY_DATA__=""" + payload + """;</script><script>""" + javascript + """</script></body></html>
"""
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mahjong Replay Debugger</title><style>""" + _CSS + """</style></head>
<body><header><strong>Mahjong Replay Debugger</strong><div id="position" class="muted"></div></header><main>
<div class="panel bar"><button id="first">|&lt;</button><button id="prev">&lt;</button><label>Seq <input id="seq" type="number" min="0"></label><button id="next">&gt;</button><button id="last">&gt;|</button><button id="local-prev">Local −</button><button id="local-next">Local +</button><button id="play">Play</button><button id="diag-prev">Previous diagnostic</button><button id="diag-next">Next diagnostic</button><label>View <select id="view"><option>PLAYER_VIEW</option><option selected>LOCAL_KNOWLEDGE</option><option>OMNISCIENT</option></select></label><label>Player <select id="selected"><option value="0">0</option><option value="1">1</option><option value="2">2</option><option value="3">3</option></select></label><label>Search <input id="search" type="search"></label></div>
<p id="message" class="muted">Visibility modes are presentation semantics, not access control over this complete exported evidence file.</p>
<div id="table" class="panel"></div><div class="grid"><section class="panel"><h3>Server event</h3><pre id="server-event"></pre></section><section class="panel"><h3>Local step</h3><pre id="local-step"></pre></section><section class="panel"><h3>Request / response / merge</h3><pre id="request"></pre></section><section class="panel"><h3>Diagnostics</h3><pre id="diagnostics"></pre></section><section class="panel"><h3>Source coverage</h3><pre id="coverage"></pre></section><section class="panel wide"><h3>Raw evidence search</h3><pre id="raw-search"></pre></section></div></main>
<script type="application/json" id="replay-data">""" + payload + """</script><script>""" + _JS + """</script></body></html>
"""


def _load_react_bundle() -> tuple[str, str] | None:
    """Load the locally built React viewer, without introducing a network URL.

    The Python CLI remains usable before the optional npm build; once the
    checked-in/generated Vite artifact exists, every export is driven by the
    React application and its local shadcn/ui components.
    """
    root = Path(__file__).resolve().parents[2] / "web" / "replay_debugger" / "dist"
    javascript_path = root / "replay-app.js"
    if not javascript_path.is_file():
        return None
    styles = []
    for path in sorted(root.glob("*.css")):
        styles.append(path.read_text(encoding="utf-8"))
    return javascript_path.read_text(encoding="utf-8"), "\n".join(styles)


def _resolved(path: str | os.PathLike[str]) -> Path:
    return Path(path).expanduser().resolve()


def write_export(session: ReplaySession, out_dir: str | os.PathLike[str], *,
                 overwrite: bool = False, input_paths: Iterable[str | os.PathLike[str]] = ()) -> tuple[Path, Path]:
    out = _resolved(out_dir)
    inputs = {_resolved(p) for p in input_paths if p}
    if out in inputs:
        raise ValueError("output directory resolves to an input evidence path")
    json_path, html_path = out / "replay.json", out / "index.html"
    if (json_path.exists() or html_path.exists()) and not overwrite:
        raise FileExistsError(f"outputs already exist under {out}; use --overwrite")
    if json_path in inputs or html_path in inputs:
        raise ValueError("output would overwrite input evidence")
    out.mkdir(parents=True, exist_ok=True)
    json_path.write_text(session.to_json(indent=2) + "\n", encoding="utf-8")
    html_path.write_text(render_html(session), encoding="utf-8")
    return json_path, html_path


export_replay = write_export
