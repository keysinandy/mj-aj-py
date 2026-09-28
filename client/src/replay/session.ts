import type {
  InfoKind,
  ReplayDiagnostic,
  ReplayEvent,
  ReplayFrame,
  ReplayLocalRequest,
  ReplaySeqSource,
} from "./frame";
import type { StrategySnapshot } from "../strategy/types";

export interface ReplayStep {
  stepIndex: number;
  seqNo: number | null;
  seqSource: ReplaySeqSource | string;
  timestamp: number | null;
  event: ReplayEvent | null;
  localRequests: ReplayLocalRequest[];
  diagnostics: ReplayDiagnostic[];
  state: ReplayFrame;
}

/** 两种来源最终都交给同一个牌面状态渲染器。 */
export type ReplayState = ReplayFrame;

export interface ReplayRound {
  roundId: string;
  ordinal: number;
  roundNo: number;
  startStepIndex: number;
  endStepIndex: number;
  startSeqNo: number | null;
  endSeqNo: number | null;
  winnerSeats: number[];
  ended: boolean;
}

export interface ReplaySessionMetadata {
  source: InfoKind;
  id?: string | null;
  path?: string | null;
  /** 本局我方实际使用的策略(例如 bot/policy)及评价器。 */
  strategy?: string | null;
  evaluator?: string | null;
  modelName?: string | null;
  strategySnapshot?: StrategySnapshot | null;
  strategySnapshots?: Array<{ seat: number; snapshot: StrategySnapshot }>;
  stepCount: number;
  rounds: ReplayRound[];
  capabilities: {
    serverEvents: boolean;
    localRequests: boolean;
    diagnostics: boolean;
    fullInformation: boolean;
  };
  verifications?: unknown[];
}

export interface ReplaySession {
  metadata: ReplaySessionMetadata;
  initialState: ReplayFrame | null;
  steps: ReplayStep[];
}

export interface BackendStep {
  step_index?: number;
  seq_no?: number | null;
  seq_source?: ReplaySeqSource | string;
  timestamp?: number | null;
  event?: ReplayEvent | null;
  local_requests?: ReplayLocalRequest[];
  diagnostics?: ReplayDiagnostic[];
  state?: ReplayFrame;
}

export interface BackendReplayRound {
  round_id?: string;
  ordinal?: number;
  round_no?: number;
  start_step_index?: number;
  end_step_index?: number;
  start_seq_no?: number | null;
  end_seq_no?: number | null;
  winner_seats?: number[];
  ended?: boolean;
}

export interface BackendSession {
  metadata?: {
    source?: InfoKind;
    id?: string | null;
    path?: string | null;
    strategy?: string | null;
    evaluator?: string | null;
    model_name?: string | null;
    strategy_snapshot?: StrategySnapshot | null;
    strategy_snapshots?: Array<{ seat: number; snapshot: StrategySnapshot }>;
    step_count?: number;
    rounds?: BackendReplayRound[];
    capabilities?: {
      server_events?: boolean;
      local_requests?: boolean;
      diagnostics?: boolean;
      full_information?: boolean;
    };
    verifications?: unknown[];
  };
  initial_state?: ReplayFrame | null;
  steps?: BackendStep[];
}

export interface ReplayResponseLike {
  frames: ReplayFrame[];
  session?: BackendSession;
}

function frameStep(frame: ReplayFrame, index: number): ReplayStep {
  return {
    stepIndex: index,
    seqNo: frame.seq_no ?? frame.step ?? index,
    seqSource: frame.seq_source ?? "derived",
    timestamp: frame.timestamp ?? null,
    event: frame.event ?? null,
    localRequests: [...(frame.local_requests ?? [])],
    diagnostics: [...(frame.diagnostics ?? [])],
    state: frame,
  };
}

function validSeatList(value: unknown): number[] {
  return Array.isArray(value)
    ? [...new Set(value.filter((seat): seat is number =>
      Number.isInteger(seat) && seat >= 0 && seat < 4))].sort((a, b) => a - b)
    : [];
}

/** Derive stable round runs for frames and older sessions without metadata. */
export function roundsFromSteps(steps: ReplayStep[]): ReplayRound[] {
  if (steps.length === 0) return [];
  const segments: Array<{ start: number; end: number; roundNo: number }> = [];
  let start = 0;
  let currentRound: number | null = null;
  steps.forEach((step, index) => {
    const rawRound = step.state.round_no;
    const roundNo = Number.isFinite(rawRound)
      ? Math.round(rawRound)
      : currentRound ?? 1;
    if (currentRound === null) currentRound = roundNo;
    else if (roundNo !== currentRound) {
      segments.push({ start, end: index - 1, roundNo: currentRound });
      start = index;
      currentRound = roundNo;
    }
  });
  segments.push({ start, end: steps.length - 1, roundNo: currentRound ?? 1 });

  return segments.map(({ start: first, end, roundNo }, segmentIndex) => {
    const ordinal = segmentIndex + 1;
    const segment = steps.slice(first, end + 1);
    const seqs = segment.map((step) => step.seqNo).filter(
      (seq): seq is number => seq !== null && Number.isFinite(seq),
    );
    const finalFrame = segment[segment.length - 1].state;
    return {
      roundId: `r${ordinal}-n${roundNo}-s${seqs[0] ?? first}`,
      ordinal,
      roundNo,
      startStepIndex: first,
      endStepIndex: end,
      startSeqNo: seqs[0] ?? null,
      endSeqNo: seqs[seqs.length - 1] ?? null,
      winnerSeats: validSeatList(finalFrame.winner_seats),
      ended: Boolean(finalFrame.round_ended),
    };
  });
}

function roundsFromBackend(
  rawRounds: BackendReplayRound[] | undefined,
  steps: ReplayStep[],
): ReplayRound[] {
  if (!Array.isArray(rawRounds) || rawRounds.length === 0) {
    return roundsFromSteps(steps);
  }
  const rounds = rawRounds.map((raw, index): ReplayRound | null => {
    const ordinal = Number.isInteger(raw.ordinal) && (raw.ordinal ?? 0) > 0
      ? raw.ordinal!
      : index + 1;
    const roundNo = Number.isFinite(raw.round_no) ? Math.round(raw.round_no!) : 1;
    const startStepIndex = raw.start_step_index;
    const endStepIndex = raw.end_step_index;
    if (!Number.isInteger(startStepIndex) || !Number.isInteger(endStepIndex)
      || startStepIndex! < 0 || endStepIndex! < startStepIndex!) return null;
    const startSeqNo = Number.isFinite(raw.start_seq_no) ? raw.start_seq_no! : null;
    const endSeqNo = Number.isFinite(raw.end_seq_no) ? raw.end_seq_no! : null;
    return {
      roundId: raw.round_id || `r${ordinal}-n${roundNo}-s${startSeqNo ?? startStepIndex}`,
      ordinal,
      roundNo,
      startStepIndex: startStepIndex!,
      endStepIndex: endStepIndex!,
      startSeqNo,
      endSeqNo,
      winnerSeats: validSeatList(raw.winner_seats),
      ended: Boolean(raw.ended),
    };
  });
  const valid = rounds.every((round): round is ReplayRound => round !== null)
    && rounds.length > 0
    && rounds[0]!.startStepIndex === 0
    && rounds[rounds.length - 1]!.endStepIndex === steps.length - 1
    && rounds.every((round, index) => index === 0
      || rounds[index - 1]!.endStepIndex + 1 === round!.startStepIndex);
  return valid ? rounds as ReplayRound[] : roundsFromSteps(steps);
}

function metadataFromFrames(
  frames: ReplayFrame[],
  source: InfoKind,
  id?: string | null,
  path?: string | null,
): ReplaySessionMetadata {
  const steps = frames.map(frameStep);
  return {
    source,
    id,
    path,
    stepCount: frames.length,
    rounds: roundsFromSteps(steps),
    capabilities: {
      serverEvents: source === "online",
      localRequests: frames.some((frame) => (frame.local_requests ?? []).length > 0),
      diagnostics: frames.some((frame) => (frame.diagnostics ?? []).length > 0),
      fullInformation: source === "local",
    },
  };
}

/**
 * 兼容旧版只返回 frames 的 API,同时保证线上/本地都进入同一个模型。
 * 旧帧中的可选步骤元数据会被保留,不会因为适配而丢失。
 */
export function sessionFromFrames(
  frames: ReplayFrame[],
  source: InfoKind,
  id?: string | null,
  path?: string | null,
): ReplaySession {
  const steps = frames.map((frame, index) => frameStep(frame, index));
  return {
    metadata: metadataFromFrames(frames, source, id, path),
    initialState: frames[0] ?? null,
    steps,
  };
}

/** 把后端的 snake_case Session 转成前端统一模型。 */
export function sessionFromResponse(
  response: ReplayResponseLike,
  source: InfoKind,
  id?: string | null,
  path?: string | null,
): ReplaySession {
  const backend = response.session;
  if (!backend?.steps?.length) {
    return sessionFromFrames(response.frames, source, id, path);
  }

  const frames = response.frames;
  const steps: ReplayStep[] = [];
  backend.steps.forEach((raw, index) => {
    const state = raw.state ?? frames[index];
    if (!state) return;
    steps.push({
      stepIndex: raw.step_index ?? index,
      seqNo: raw.seq_no ?? state.seq_no ?? state.step ?? index,
      seqSource: raw.seq_source ?? state.seq_source ?? "derived",
      timestamp: raw.timestamp ?? state.timestamp ?? null,
      event: raw.event ?? state.event ?? null,
      localRequests: [
        ...(raw.local_requests ?? state.local_requests ?? []),
      ],
      diagnostics: [
        ...(raw.diagnostics ?? state.diagnostics ?? []),
      ],
      state,
    });
  });

  const rawMeta = backend.metadata;
  const rawCapabilities = rawMeta?.capabilities;
  return {
    metadata: {
      source: rawMeta?.source ?? source,
      id: rawMeta?.id ?? id,
      path: rawMeta?.path ?? path,
      strategy: rawMeta?.strategy,
      evaluator: rawMeta?.evaluator,
      modelName: rawMeta?.model_name,
      strategySnapshot: rawMeta?.strategy_snapshot,
      strategySnapshots: rawMeta?.strategy_snapshots,
      stepCount: rawMeta?.step_count ?? steps.length,
      rounds: roundsFromBackend(rawMeta?.rounds, steps),
      capabilities: {
        serverEvents: rawCapabilities?.server_events ?? source === "online",
        localRequests: rawCapabilities?.local_requests ?? steps.some((step) => step.localRequests.length > 0),
        diagnostics: rawCapabilities?.diagnostics ?? steps.some((step) => step.diagnostics.length > 0),
        fullInformation: rawCapabilities?.full_information ?? source === "local",
      },
      verifications: rawMeta?.verifications,
    },
    initialState: backend.initial_state ?? steps[0]?.state ?? null,
    steps,
  };
}
