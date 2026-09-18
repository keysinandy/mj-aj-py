import type { ReplayFrame } from "../replay/frame";

export interface TimelineEntry {
  index: number;
  label: string;
  gap: boolean;
}

/** 从帧数组派生时间线条目;gap 帧显式为缺口标注条目。 */
export function entriesFromFrames(frames: ReplayFrame[]): TimelineEntry[] {
  return frames.map((f) => ({
    index: f.step,
    label: f.label || `步骤 ${f.step}`,
    gap: f.gap,
  }));
}