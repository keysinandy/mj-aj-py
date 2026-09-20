import type { ReplayFrame } from "../replay/frame";
import type { ReplayStep } from "./session";

export interface TimelineEntry {
  index: number;
  label: string;
  gap: boolean;
  seqNo: number | null;
  seqSource: string;
  diagnosticCount: number;
  diagnosticSeverity: string | null;
}

/** 从帧数组派生时间线条目;gap 帧显式为缺口标注条目。 */
export function entriesFromFrames(frames: ReplayFrame[]): TimelineEntry[] {
  return frames.map((f, index) => ({
    index: index,
    label: f.label || `步骤 ${f.step}`,
    gap: f.gap,
    seqNo: f.seq_no ?? f.step ?? index,
    seqSource: f.seq_source ?? "derived",
    diagnosticCount: (f.diagnostics ?? []).length,
    diagnosticSeverity: f.diagnostics?.some((d) => d.severity === "error")
      ? "error"
      : f.diagnostics?.length
        ? "warn"
        : null,
  }));
}

export function entriesFromSteps(steps: ReplayStep[]): TimelineEntry[] {
  return steps.map((step) => ({
    index: step.stepIndex,
    label: step.state.label || String(step.event?.type ?? `步骤 ${step.stepIndex}`),
    gap: Boolean(step.state.gap),
    seqNo: step.seqNo,
    seqSource: step.seqSource,
    diagnosticCount: step.diagnostics.length,
    diagnosticSeverity: step.diagnostics.some((d) => d.severity === "error")
      ? "error"
      : step.diagnostics.length
        ? "warn"
        : null,
  }));
}
