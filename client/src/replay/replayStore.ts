/**
 * 统一回放查看器状态。
 *
 * 数据源只在 setSession/setFrames 时不同,后续导航、序号跳转和状态访问
 * 全部走 ReplayEngine,因此本地批次与线上记录不会各自维护播放器。
 */

import { create } from "zustand";
import type { ReplayFrame, ReplayVisibilityMode } from "./frame";
import { ReplayEngine } from "./engine";
import { sessionFromFrames, type ReplaySession, type ReplayStep } from "./session";

interface ReplayStore {
  session: ReplaySession | null;
  engine: ReplayEngine | null;
  /** 兼容既有组件和旧测试的只读帧投影。 */
  frames: ReplayFrame[];
  index: number;
  observeSeat: number;
  visibilityMode: ReplayVisibilityMode;
  playing: boolean;
  speed: number;
  setSession: (session: ReplaySession) => void;
  setFrames: (frames: ReplayFrame[]) => void;
  stepForward: () => void;
  stepBack: () => void;
  firstStep: () => void;
  lastStep: () => void;
  jumpTo: (index: number) => void;
  jumpToSeqNo: (seqNo: number) => void;
  setObserveSeat: (seat: number) => void;
  setVisibilityMode: (mode: ReplayVisibilityMode) => void;
  togglePlaying: () => void;
  setSpeed: (speed: number) => void;
  /** 当前状态;无记录时 null。 */
  frame: () => ReplayFrame | null;
  /** 当前步骤;无记录时 null。 */
  currentStep: () => ReplayStep | null;
  total: () => number;
}

function framesFromSession(session: ReplaySession): ReplayFrame[] {
  return session.steps.map((step) => step.state);
}

function initialObserveSeat(session: ReplaySession): number {
  const seat = session.steps[0]?.state.my_seat;
  return typeof seat === "number" && Number.isFinite(seat)
    ? Math.max(0, Math.min(3, Math.round(seat)))
    : 0;
}

export const useReplayStore = create<ReplayStore>((set, get) => ({
  session: null,
  engine: null,
  frames: [],
  index: 0,
  observeSeat: 0,
  visibilityMode: "player",
  playing: false,
  speed: 1,

  setSession: (session) => {
    const engine = new ReplayEngine(session);
    set({
      session,
      engine,
      frames: framesFromSession(session),
      index: 0,
      observeSeat: initialObserveSeat(session),
      visibilityMode: "player",
      playing: false,
    });
  },

  setFrames: (frames) => {
    get().setSession(sessionFromFrames(frames, frames[0]?.info_kind ?? "local"));
  },

  stepForward: () => {
    const { engine, index } = get();
    if (!engine || index >= engine.total - 1) {
      set({ playing: false });
      return;
    }
    set({ index: index + 1 });
  },

  stepBack: () => {
    const { index } = get();
    if (index > 0) set({ index: index - 1, playing: false });
  },

  firstStep: () => set({ index: 0, playing: false }),

  lastStep: () => {
    const { engine } = get();
    set({ index: engine ? Math.max(0, engine.total - 1) : 0, playing: false });
  },

  jumpTo: (i) => {
    const { engine } = get();
    if (!engine || engine.total === 0) return;
    set({ index: engine.clampIndex(i), playing: false });
  },

  jumpToSeqNo: (seqNo) => {
    const { engine } = get();
    if (!engine || engine.total === 0 || !Number.isFinite(seqNo)) return;
    set({ index: engine.indexForSeqNo(seqNo), playing: false });
  },

  setObserveSeat: (seat) => set({ observeSeat: Math.max(0, Math.min(3, Math.round(seat))) }),

  setVisibilityMode: (mode) => set({ visibilityMode: mode }),

  togglePlaying: () => {
    const { engine, index, playing } = get();
    if (!engine || engine.total < 2) return;
    if (index >= engine.total - 1) {
      set({ index: 0, playing: true });
      return;
    }
    set({ playing: !playing });
  },

  setSpeed: (speed) => {
    if (Number.isFinite(speed) && speed > 0) set({ speed });
  },

  frame: () => {
    const { engine, index, frames } = get();
    return engine?.stateAt(index) ?? frames[index] ?? null;
  },

  currentStep: () => {
    const { engine, index } = get();
    return engine?.stepAt(index) ?? null;
  },

  total: () => get().engine?.total ?? get().frames.length,
}));
