/**
 * 统一回放查看器状态。
 *
 * 数据源只在 setSession/setFrames 时不同,后续导航、序号跳转和状态访问
 * 全部走 ReplayEngine,因此本地批次与线上记录不会各自维护播放器。
 */

import { create } from "zustand";
import type { ReplayFrame, ReplayVisibilityMode } from "./frame";
import { ReplayEngine } from "./engine";
import {
  roundsFromSteps,
  sessionFromFrames,
  type ReplayRound,
  type ReplaySession,
  type ReplayStep,
} from "./session";
import { entriesFromSteps } from "./timeline";

interface ReplayStore {
  session: ReplaySession | null;
  engine: ReplayEngine | null;
  /** 兼容既有组件和旧测试的只读帧投影。 */
  frames: ReplayFrame[];
  index: number;
  rounds: ReplayRound[];
  activeRoundId: string | null;
  actorFilter: "all" | "mine";
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
  selectRound: (roundId: string) => void;
  previousRound: () => void;
  nextRound: () => void;
  setActorFilter: (filter: "all" | "mine") => void;
  nextFilteredStep: () => void;
  previousFilteredStep: () => void;
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

function activeRound(rounds: ReplayRound[], id: string | null): ReplayRound | null {
  return rounds.find((round) => round.roundId === id) ?? rounds[0] ?? null;
}

function roundForIndex(rounds: ReplayRound[], index: number): ReplayRound | null {
  return rounds.find((round) => index >= round.startStepIndex && index <= round.endStepIndex) ?? null;
}

function filteredActionIndices(
  session: ReplaySession | null,
  round: ReplayRound | null,
): number[] {
  if (!session || !round) return [];
  return entriesFromSteps(session.steps)
    .filter((entry) => entry.index >= round.startStepIndex
      && entry.index <= round.endStepIndex && entry.isMine)
    .map((entry) => entry.index);
}

export const useReplayStore = create<ReplayStore>((set, get) => ({
  session: null,
  engine: null,
  frames: [],
  index: 0,
  rounds: [],
  activeRoundId: null,
  actorFilter: "all",
  observeSeat: 0,
  visibilityMode: "player",
  playing: false,
  speed: 1,

  setSession: (session) => {
    const engine = new ReplayEngine(session);
    const rounds = session.metadata.rounds?.length
      ? session.metadata.rounds
      : roundsFromSteps(session.steps);
    const firstRound = rounds[0] ?? null;
    set({
      session,
      engine,
      frames: framesFromSession(session),
      rounds,
      activeRoundId: firstRound?.roundId ?? null,
      actorFilter: "all",
      index: firstRound?.startStepIndex ?? 0,
      observeSeat: initialObserveSeat(session),
      visibilityMode: "player",
      playing: false,
    });
  },

  setFrames: (frames) => {
    get().setSession(sessionFromFrames(frames, frames[0]?.info_kind ?? "local"));
  },

  stepForward: () => {
    const { engine, index, rounds, activeRoundId } = get();
    const round = activeRound(rounds, activeRoundId);
    const end = round?.endStepIndex ?? (engine ? engine.total - 1 : 0);
    if (!engine || index >= end) {
      set({ playing: false });
      return;
    }
    set({ index: index + 1 });
  },

  stepBack: () => {
    const { index, rounds, activeRoundId } = get();
    const start = activeRound(rounds, activeRoundId)?.startStepIndex ?? 0;
    if (index > start) set({ index: index - 1, playing: false });
  },

  firstStep: () => {
    const { rounds, activeRoundId } = get();
    set({ index: activeRound(rounds, activeRoundId)?.startStepIndex ?? 0, playing: false });
  },

  lastStep: () => {
    const { engine, rounds, activeRoundId } = get();
    const round = activeRound(rounds, activeRoundId);
    set({ index: round?.endStepIndex ?? (engine ? Math.max(0, engine.total - 1) : 0), playing: false });
  },

  jumpTo: (i) => {
    const { engine, rounds, activeRoundId } = get();
    if (!engine || engine.total === 0) return;
    const round = activeRound(rounds, activeRoundId);
    const start = round?.startStepIndex ?? 0;
    const end = round?.endStepIndex ?? engine.total - 1;
    set({ index: Math.max(start, Math.min(end, engine.clampIndex(i))), playing: false });
  },

  jumpToSeqNo: (seqNo) => {
    const { engine, rounds, activeRoundId } = get();
    if (!engine || engine.total === 0 || !Number.isFinite(seqNo)) return;
    const index = engine.indexForSeqNo(seqNo);
    const round = roundForIndex(rounds, index);
    set({ index, activeRoundId: round?.roundId ?? activeRoundId, playing: false });
  },

  selectRound: (roundId) => {
    const round = get().rounds.find((item) => item.roundId === roundId);
    if (!round) return;
    set({ activeRoundId: round.roundId, index: round.startStepIndex, playing: false });
  },

  previousRound: () => {
    const { rounds, activeRoundId } = get();
    const currentIndex = rounds.findIndex((round) => round.roundId === activeRoundId);
    if (currentIndex <= 0) return;
    const target = rounds[currentIndex - 1];
    set({ activeRoundId: target.roundId, index: target.startStepIndex, playing: false });
  },

  nextRound: () => {
    const { rounds, activeRoundId } = get();
    const currentIndex = rounds.findIndex((round) => round.roundId === activeRoundId);
    if (currentIndex < 0 || currentIndex >= rounds.length - 1) return;
    const target = rounds[currentIndex + 1];
    set({ activeRoundId: target.roundId, index: target.startStepIndex, playing: false });
  },

  setActorFilter: (actorFilter) => set({ actorFilter }),

  nextFilteredStep: () => {
    const { session, rounds, activeRoundId, index } = get();
    const candidates = filteredActionIndices(session, activeRound(rounds, activeRoundId));
    const target = candidates.find((candidate) => candidate > index);
    if (target !== undefined) set({ index: target, playing: false });
  },

  previousFilteredStep: () => {
    const { session, rounds, activeRoundId, index } = get();
    const candidates = filteredActionIndices(session, activeRound(rounds, activeRoundId));
    const target = [...candidates].reverse().find((candidate) => candidate < index);
    if (target !== undefined) set({ index: target, playing: false });
  },

  setObserveSeat: (seat) => set({ observeSeat: Math.max(0, Math.min(3, Math.round(seat))) }),

  setVisibilityMode: (mode) => set({ visibilityMode: mode }),

  togglePlaying: () => {
    const { engine, index, playing, rounds, activeRoundId } = get();
    const round = activeRound(rounds, activeRoundId);
    const start = round?.startStepIndex ?? 0;
    const end = round?.endStepIndex ?? (engine ? engine.total - 1 : 0);
    if (!engine || end - start < 1) return;
    if (index >= end) {
      set({ index: start, playing: true });
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
