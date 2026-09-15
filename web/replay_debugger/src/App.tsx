import { useEffect, useMemo, useState } from "react";
import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./components/ui/card";
import { Input } from "./components/ui/input";
import { Select } from "./components/ui/select";
import { cn } from "./lib/utils";
import "./styles.css";

type ReplayData = any;
type Cursor = { frame: number; local: number | null; mode: "BEFORE" | "AFTER" };

const data: ReplayData = (globalThis as any).__REPLAY_DATA__ || {};
const pretty = (value: unknown) => JSON.stringify(value ?? null, null, 2);

function stateFor(frame: any, step: any, view: string, boundary: string) {
  if (!frame) return null;
  if (view === "LOCAL_KNOWLEDGE") {
    if (!step?.stepId) return null;
    const states = data.states?.steps?.[step.stepId];
    return states?.[boundary === "BEFORE" ? "expectedBefore" : "expectedAfter"] || null;
  }
  return (boundary === "BEFORE" ? frame.serverBefore : frame.serverAfter) || null;
}

function project(state: any, view: string, selected: number) {
  if (!state) return null;
  const copy = JSON.parse(JSON.stringify(state));
  (copy.players || []).forEach((player: any) => {
    if (view === "OMNISCIENT" || (view === "PLAYER_VIEW" && player.seat === selected)) return;
    if (player.hand?.status === "KNOWN") player.hand = { status: "HIDDEN", evidence: player.hand.evidence || "RECORDED" };
  });
  return copy;
}

const privateKeys = new Set(["hand", "hands", "my_hand", "myHand", "start_hands", "startHands"]);

function redactPrivate(value: any, view: string, selected: number, inheritedSeat: number | null = null): any {
  if (view === "OMNISCIENT" || value == null) return value;
  if (Array.isArray(value)) return value.map(item => redactPrivate(item, view, selected, inheritedSeat));
  if (typeof value !== "object") return value;
  const seat = typeof value.seat === "number" ? value.seat : inheritedSeat;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => {
    if (privateKeys.has(key) && !(view === "PLAYER_VIEW" && seat === selected)) {
      return [key, { status: "HIDDEN", evidence: "RECORDED" }];
    }
    return [key, redactPrivate(item, view, selected, seat)];
  }));
}

function projectInspector(value: any, view: string, selected: number): any {
  if (value == null) return value;
  if (Array.isArray(value)) return value.map(item => projectInspector(item, view, selected));
  if (typeof value !== "object") return value;
  if (Array.isArray(value.players) && value.round) return project(value, view, selected);
  return redactPrivate(Object.fromEntries(Object.entries(value).map(([key, item]) => [
    key, projectInspector(item, view, selected),
  ])), view, selected);
}

function JsonPanel({ title, value, className }: { title: string; value: unknown; className?: string }) {
  return <Card className={className}><CardHeader><CardTitle>{title}</CardTitle></CardHeader><CardContent><pre className="json">{pretty(value)}</pre></CardContent></Card>;
}

function Table({ state, selected }: { state: any; selected: number }) {
  if (!state) return <Card><CardContent><span className="unknown">No server state at this cursor.</span></CardContent></Card>;
  return <Card><CardHeader><CardTitle>Table · four seats</CardTitle></CardHeader><CardContent className="seat-grid">{(state.players || []).map((player: any) => {
    const hand = player.hand || {};
    const handValue = hand.status === "KNOWN" ? ((hand.value || hand.tiles || []).join(" ") || "known empty") : `[${hand.status || "UNKNOWN"}]`;
    return <div key={player.seat} className={cn("seat-card", player.seat === selected && "selected-seat")}>
      <div className="seat-heading"><span>Seat {player.seat}</span>{player.seat === selected && <Badge>selected</Badge>}</div>
      <div><span className="label">Hand</span><span className={hand.status === "KNOWN" ? "" : "unknown"}>{handValue}</span></div>
      <div><span className="label">River</span>{player.river?.length ? player.river.map((river: any, i: number) => <span className="tile" key={`${player.seat}-${i}`}>{river.tile || "?"}{river.called ? ` · ${river.callType}` : ""}</span>) : <span className="unknown">known empty</span>}</div>
      <div><span className="label">Melds</span>{player.melds?.length ? player.melds.map((meld: any) => <span className="meld" key={meld.meldId}>{meld.type} {meld.tiles?.join(" ")}</span>) : <span className="unknown">known empty</span>}</div>
    </div>;
  })}</CardContent></Card>;
}

export default function App() {
  const frames = data.seqFrames || [];
  const localSteps = data.localSteps || [];
  const initialFrame = Math.max(0, frames.findIndex((item: any) => item.roundNo === data.cursor?.roundNo && item.seqNo === data.cursor?.seqNo));
  const [cursor, setCursor] = useState<Cursor>({ frame: initialFrame, local: data.cursor?.localStepIndex ?? null, mode: data.cursor?.phase || "AFTER" });
  const [view, setView] = useState(data.view?.handVisibility || "LOCAL_KNOWLEDGE");
  const [selected, setSelected] = useState(data.view?.selectedPlayer ?? 0);
  const [search, setSearch] = useState("");
  const [playing, setPlaying] = useState(false);
  const [message, setMessage] = useState("Visibility modes affect presentation; the exported evidence file retains its complete source records.");
  const frame = frames[cursor.frame] || null;
  const step = localSteps.find((item: any) => item.index === cursor.local) || null;
  const projected = useMemo(() => project(stateFor(frame, step, view, cursor.mode), view, selected), [frame, step, view, selected, cursor.mode]);
  const displayedStep = useMemo(() => {
    if (!step || view !== "LOCAL_KNOWLEDGE") return step;
    const copy = JSON.parse(JSON.stringify(step));
    const responseStep = localSteps.find((item: any) => item.requestId === step.requestId && item.type === "STATE_RESPONSE");
    if (step.type === "STATE_REQUEST" && responseStep && responseStep.index > (cursor.local ?? -1) && copy.payload?.res) {
      copy.payload.res = { status: "UNKNOWN", evidence: "UNKNOWN" };
    }
    return copy;
  }, [step, localSteps, cursor.local, view]);
  const request = useMemo(() => {
    const original = (data.requests || []).find((item: any) => item.requestId === step?.requestId || item.logicalRequestId === step?.requestId);
    if (!original) return null;
    const copy = JSON.parse(JSON.stringify(original));
    const related = localSteps.filter((item: any) => item.requestId === original.requestId || item.requestId === original.logicalRequestId);
    const responseStep = related.find((item: any) => item.type === "STATE_RESPONSE");
    const mergeStep = related.find((item: any) => item.type === "STATE_MERGE");
    const current = cursor.local;
    if (view === "LOCAL_KNOWLEDGE" && (current == null || (responseStep && responseStep.index > current))) {
      copy.response = { status: "UNKNOWN", evidence: "UNKNOWN" };
      copy.responseSeq = null;
      copy.requestToResponseDiff = { complete: false, equal: false, unknownFields: ["response"] };
      copy.effectiveMergeDiff = { complete: false, equal: false, unknownFields: ["merge"] };
      copy.expectedObservedDiff = { complete: false, equal: false, unknownFields: ["observed"] };
    } else if (view === "LOCAL_KNOWLEDGE" && (mergeStep == null || mergeStep.index > (current ?? -1))) {
      copy.stateAfterMerge = null;
      copy.effectiveMergeDiff = { complete: false, equal: false, unknownFields: ["merge"] };
    }
    return projectInspector(copy, view, selected);
  }, [step, localSteps, cursor.local, view, selected]);
  const visibleRawRecords = useMemo(() => {
    const visibleRefs = new Set(localSteps.filter((item: any) => cursor.local != null && item.index <= cursor.local).flatMap((item: any) => item.rawRefs || []));
    return (data.rawRecords || []).filter((item: any) => view !== "LOCAL_KNOWLEDGE" || visibleRefs.has(item.recordId)).map((item: any) => {
      const copy = JSON.parse(JSON.stringify(item));
      if (view === "LOCAL_KNOWLEDGE" && copy.raw?.type === "req") {
        const related = localSteps.filter((local: any) => (local.rawRefs || []).includes(copy.recordId));
        const response = related.find((local: any) => local.type === "STATE_RESPONSE");
        if (response && cursor.local != null && response.index > cursor.local) copy.raw.res = { status: "UNKNOWN", evidence: "UNKNOWN" };
      }
      const projected = redactPrivate(copy, view, selected);
      if (view !== "OMNISCIENT") projected.rawText = JSON.stringify(projected.raw ?? null);
      return projected;
    }).filter((item: any) => !search || JSON.stringify(item).toLowerCase().includes(search.toLowerCase())).slice(0, 30);
  }, [cursor.local, localSteps, search, selected, view]);
  const diagnostics = useMemo(() => (data.diagnostics || [])
    .filter((item: any) => !frame || (item.roundNo === frame.roundNo && (item.seqNo == null || item.seqNo === frame.seqNo)))
    .filter((item: any) => view !== "LOCAL_KNOWLEDGE" || (cursor.local != null && (item.localStepIndex == null || item.localStepIndex <= cursor.local)))
    .map((item: any) => projectInspector(item, view, selected)), [frame, cursor.local, view, selected]);
  const moveFrame = (delta: number) => setCursor(current => { const frameIndex = Math.max(0, Math.min(Math.max(frames.length - 1, 0), current.frame + delta)); const target = frames[frameIndex]; const local = target?.localSteps?.length ? (localSteps.find((item: any) => item.stepId === target.localSteps[0])?.index ?? null) : null; return { ...current, frame: frameIndex, local }; });
  const moveLocal = (delta: number) => setCursor(current => { if (!localSteps.length) return current; const at = current.local == null ? 0 : localSteps.findIndex((item: any) => item.index === current.local); const index = Math.max(0, Math.min(localSteps.length - 1, (at < 0 ? 0 : at) + delta)); const local = localSteps[index]; const frameIndex = frames.findIndex((item: any) => item.localSteps?.includes(local.stepId)); return { ...current, frame: frameIndex >= 0 ? frameIndex : current.frame, local: local.index }; });
  const jump = (diagnostic: any) => { const frameIndex = frames.findIndex((item: any) => item.seqNo === diagnostic.seqNo && item.roundNo === diagnostic.roundNo); setCursor(current => ({ ...current, frame: frameIndex >= 0 ? frameIndex : current.frame, local: diagnostic.localStepIndex ?? current.local })); };
  const jumpDiagnostic = (direction: number) => { const list = data.diagnostics || []; if (!list.length) return; const at = list.findIndex((item: any) => item.diagnosticId === diagnostics[0]?.diagnosticId); jump(list[Math.max(0, Math.min(list.length - 1, (at < 0 ? 0 : at) + direction))]); };
  const setSeq = (value: number) => { const frameIndex = frames.findIndex((item: any) => item.seqNo === value); if (frameIndex < 0) { setMessage(`seq ${value} is unavailable in this round; the valid cursor was retained.`); return; } const target = frames[frameIndex]; const local = target?.localSteps?.length ? (localSteps.find((item: any) => item.stepId === target.localSteps[0])?.index ?? null) : null; setCursor(current => ({ ...current, frame: frameIndex, local })); setMessage("Cursor updated atomically across table, event, request, diff, and diagnostics."); };
  const togglePlayback = () => {
    setPlaying(current => !current);
    if (!playing) {
      const next = Math.min(localSteps.length - 1, (cursor.local ?? 0) + 1);
      if (localSteps[next]) moveLocal(1);
    }
  };
  useEffect(() => {
    if (!playing || !localSteps.length) return undefined;
    const timer = window.setInterval(() => setCursor(current => {
      const at = current.local == null ? 0 : localSteps.findIndex((item: any) => item.index === current.local);
      if (at >= localSteps.length - 1) { setPlaying(false); return current; }
      const local = localSteps[at + 1];
      const frameIndex = frames.findIndex((item: any) => item.localSteps?.includes(local.stepId));
      return { ...current, local: local.index, frame: frameIndex >= 0 ? frameIndex : current.frame };
    }), 350);
    return () => window.clearInterval(timer);
  }, [playing, localSteps, frames]);

  return <div className="app-shell">
    <header className="topbar"><div><div className="eyebrow">OFFLINE EVIDENCE WORKSPACE</div><h1>Mahjong Replay Debugger</h1><p>{data.gameId || "unknown game"} · {frame ? `Round ${frame.roundNo} / Seq ${frame.seqNo}` : "no frame"}</p></div><div className="header-stats"><Badge>{data.sourceCoverage?.trace ? "TRACE" : "LEGACY EVIDENCE"}</Badge><span>{data.diagnostics?.length || 0} diagnostics</span></div></header>
    <main className="content">
      <Card><CardContent className="toolbar"><div className="button-group"><Button onClick={() => setCursor(current => ({ ...current, frame: 0 }))}>First</Button><Button onClick={() => moveFrame(-1)}>Previous</Button><Button onClick={() => moveFrame(1)}>Next</Button><Button onClick={() => setCursor(current => ({ ...current, frame: Math.max(frames.length - 1, 0) }))}>Last</Button><Button onClick={() => moveLocal(-1)}>Local −</Button><Button onClick={() => moveLocal(1)}>Local +</Button><Button onClick={togglePlayback}>{playing ? "Pause" : "Play"}</Button><Button onClick={() => jumpDiagnostic(-1)}>Previous diagnostic</Button><Button onClick={() => jumpDiagnostic(1)}>Next diagnostic</Button></div><label>Seq <Input type="number" min="0" value={frame?.seqNo ?? ""} onChange={event => setSeq(Number(event.target.value))} /></label><label>Boundary <Select value={cursor.mode} onChange={event => setCursor(current => ({ ...current, mode: event.target.value as Cursor["mode"] }))}><option>BEFORE</option><option>AFTER</option></Select></label><label>View <Select value={view} onChange={event => setView(event.target.value)}><option>PLAYER_VIEW</option><option>LOCAL_KNOWLEDGE</option><option>OMNISCIENT</option></Select></label><label>Player <Select value={selected} onChange={event => setSelected(Number(event.target.value))}><option value={0}>0</option><option value={1}>1</option><option value={2}>2</option><option value={3}>3</option></Select></label><label>Search <Input value={search} onChange={event => setSearch(event.target.value)} /></label></CardContent></Card>
      <p className="message">{message}</p>
      <Table state={projected} selected={selected} />
      <div className="panel-grid"><JsonPanel title="Server event" value={projectInspector(frame?.serverEvent, view, selected)} /><JsonPanel title="Local step" value={projectInspector(displayedStep, view, selected)} /><JsonPanel title="Request / response / merge" value={request} /><JsonPanel title="Structured business diff" value={request?.expectedObservedDiff || request?.effectiveMergeDiff} /><JsonPanel title="Diagnostics" value={diagnostics.length ? diagnostics : projectInspector(data.diagnostics?.slice(0, 5), view, selected)} /><JsonPanel title="Source coverage" value={data.sourceCoverage} /><JsonPanel title={`Raw evidence${search ? ` · ${visibleRawRecords.length} matches` : ""}`} value={visibleRawRecords} className="wide" /></div>
      <Card><CardHeader><CardTitle>Current round summary</CardTitle></CardHeader><CardContent className="summary-row"><span>Frames: {frames.length}</span><span>Local steps: {localSteps.length}</span><span>Requests: {data.requests?.length || 0}</span><span>View: <Badge>{view}</Badge></span>{(data.diagnostics || []).filter((item: any) => item.type === "MISSING_LOCAL_TRANSITION").map((item: any) => <Button key={item.diagnosticId} onClick={() => jump(item)}>Jump to {item.message}</Button>)}</CardContent></Card>
    </main>
  </div>;
}
