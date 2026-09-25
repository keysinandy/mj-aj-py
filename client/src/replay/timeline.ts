import type { ReplayFrame } from "../replay/frame";
import type { ReplayStep } from "./session";

export interface TimelineEntry {
  index: number;
  label: string;
  gap: boolean;
  seqNo: number | null;
  seqSource: string;
  actor: number | null;
  actionKind: "draw" | "discard" | "chi" | "peng" | "gang" | "hu" | "pass" | "timeout" | "other";
  isGameplayAction: boolean;
  isMine: boolean;
  diagnosticCount: number;
  diagnosticSeverity: string | null;
}

function eventActor(event: ReplayFrame["event"]): number | null {
  if (!event) return null;
  for (const key of ["actor", "seat", "player"]) {
    const value = event[key];
    if (typeof value === "number" && Number.isInteger(value) && value >= 0 && value < 4) return value;
  }
  return null;
}

function actionKindFromCode(action: unknown): TimelineEntry["actionKind"] {
  if (typeof action !== "number" || !Number.isInteger(action)) return "other";
  if (action >= 0 && action <= 33) return "discard";
  if (action === -1) return "pass";
  if (action >= -4 && action <= -2) return "chi";
  if (action === -5) return "peng";
  if ((action >= -40 && action <= -6) || (action >= -74 && action <= -41)) return "gang";
  if (action === -75) return "hu";
  return "other";
}

function actionKindFromLabel(label: unknown): TimelineEntry["actionKind"] {
  if (typeof label !== "string") return "other";
  const value = label.trim().toLowerCase();
  if (value.startsWith("摸") || value.startsWith("draw")) return "draw";
  if (value.startsWith("打") || value.startsWith("弃") || value.startsWith("discard")) return "discard";
  if (value.startsWith("吃") || value.startsWith("chi")) return "chi";
  if (value.startsWith("碰") || value.startsWith("peng")) return "peng";
  if (value.startsWith("杠") || value.startsWith("gang") || value.startsWith("kong")) return "gang";
  if (value.startsWith("胡") || value.startsWith("hu")) return "hu";
  if (value === "过" || value.startsWith("过 ") || value.startsWith("pass")) return "pass";
  return "other";
}

function actionKind(frame: ReplayFrame, event: ReplayFrame["event"]): TimelineEntry["actionKind"] {
  switch (event?.type) {
    case "tile_drawn": case "draw": return "draw";
    case "tile_discarded": case "discard": return "discard";
    case "chi": return "chi";
    case "peng": return "peng";
    case "gang": case "kong": return "gang";
    case "hu": return "hu";
    case "pass": return "pass";
    case "timeout": return "timeout";
    case "action": {
      const codeKind = actionKindFromCode(event.action);
      return codeKind === "other"
        ? actionKindFromLabel(event.label ?? frame.label)
        : codeKind;
    }
    default: return "other";
  }
}

function entryFromFrame(frame: ReplayFrame, index: number, event = frame.event): TimelineEntry {
  const actor = eventActor(event);
  const kind = actionKind(frame, event);
  const isGameplayAction = kind !== "other" && kind !== "timeout";
  return {
    index,
    label: frame.label || `步骤 ${frame.step}`,
    gap: frame.gap,
    seqNo: frame.seq_no ?? frame.step ?? index,
    seqSource: frame.seq_source ?? "derived",
    actor,
    actionKind: kind,
    isGameplayAction,
    isMine: isGameplayAction && actor !== null && actor === frame.my_seat,
    diagnosticCount: (frame.diagnostics ?? []).length,
    diagnosticSeverity: frame.diagnostics?.some((d) => d.severity === "error")
      ? "error"
      : frame.diagnostics?.length
        ? "warn"
        : null,
  };
}

/** 从帧数组派生时间线条目;gap 帧显式为缺口标注条目。 */
export function entriesFromFrames(frames: ReplayFrame[]): TimelineEntry[] {
  return frames.map((frame, index) => entryFromFrame(frame, index));
}

export function entriesFromSteps(steps: ReplayStep[]): TimelineEntry[] {
  return steps.map((step, index) => entryFromFrame(
    step.state,
    index,
    step.event ?? step.state.event,
  ));
}
