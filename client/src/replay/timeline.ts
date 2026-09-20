import type { ReplayFrame } from "../replay/frame";
import type { ReplayStep } from "./session";

export interface TimelineEntry {
  index: number;
  label: string;
  gap: boolean;
  seqNo: number | null;
  seqSource: string;
  actor: number | null;
  isMine: boolean;
  diagnosticCount: number;
  diagnosticSeverity: string | null;
}

function eventActor(event: ReplayFrame["event"]): number | null {
  if (!event) return null;
  for (const key of ["actor", "seat", "player"]) {
    const value = event[key];
    if (typeof value === "number" && Number.isInteger(value)) return value;
  }
  return null;
}

function entryFromFrame(frame: ReplayFrame, index: number, event = frame.event): TimelineEntry {
  const actor = eventActor(event);
  return {
    index,
    label: frame.label || `步骤 ${frame.step}`,
    gap: frame.gap,
    seqNo: frame.seq_no ?? frame.step ?? index,
    seqSource: frame.seq_source ?? "derived",
    actor,
    isMine: actor !== null && actor === frame.my_seat,
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
  return steps.map((step) => entryFromFrame(
    step.state,
    step.stepIndex,
    step.event ?? step.state.event,
  ));
}
