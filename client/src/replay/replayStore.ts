/**
 * 回放查看器状态:帧数组(预计算) + 当前索引 + 观察座位。
 * 步进/拖动均为数组索引 → O(1),满足"人类可感知的即时响应"。
 */

import { create } from "zustand";
import type { ReplayFrame } from "./frame";

interface ReplayStore {
  frames: ReplayFrame[];
  index: number;
  observeSeat: number;
  setFrames: (frames: ReplayFrame[]) => void;
  stepForward: () => void;
  stepBack: () => void;
  jumpTo: (index: number) => void;
  setObserveSeat: (seat: number) => void;
  /** 当前帧;无记录时 null。 */
  frame: () => ReplayFrame | null;
  total: () => number;
}

export const useReplayStore = create<ReplayStore>((set, get) => ({
  frames: [],
  index: 0,
  observeSeat: 0,

  setFrames: (frames) => {
    if (frames.length === 0) {
      set({ frames, index: 0 });
      return;
    }
    set({ frames, index: 0 });
  },

  stepForward: () => {
    const { frames, index } = get();
    if (index < frames.length - 1) set({ index: index + 1 });
  },

  stepBack: () => {
    const { index } = get();
    if (index > 0) set({ index: index - 1 });
  },

  jumpTo: (i) => {
    const { frames } = get();
    if (frames.length === 0) return;
    const clamped = Math.max(0, Math.min(frames.length - 1, Math.round(i)));
    set({ index: clamped });
  },

  setObserveSeat: (s) => set({ observeSeat: s }),

  frame: () => {
    const { frames, index } = get();
    return frames.length ? frames[index] : null;
  },

  total: () => get().frames.length,
}));