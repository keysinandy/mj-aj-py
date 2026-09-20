import type {
  InfoKind,
  ReplayDiagnostic,
  ReplayEvent,
  ReplayFrame,
  ReplayLocalRequest,
  ReplaySeqSource,
} from "./frame";

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

export interface ReplaySessionMetadata {
  source: InfoKind;
  id?: string | null;
  path?: string | null;
  /** 本局我方实际使用的策略(例如 bot/policy)及评价器。 */
  strategy?: string | null;
  evaluator?: string | null;
  modelName?: string | null;
  stepCount: number;
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

export interface BackendSession {
  metadata?: {
    source?: InfoKind;
    id?: string | null;
    path?: string | null;
    strategy?: string | null;
    evaluator?: string | null;
    model_name?: string | null;
    step_count?: number;
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

function metadataFromFrames(
  frames: ReplayFrame[],
  source: InfoKind,
  id?: string | null,
  path?: string | null,
): ReplaySessionMetadata {
  return {
    source,
    id,
    path,
    stepCount: frames.length,
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
      stepCount: rawMeta?.step_count ?? steps.length,
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
