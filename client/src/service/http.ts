/**
 * clientd HTTP 客户端。Base URL 可通过 setApiBase 配置(桌面形态由
 * sidecar 实际端口设定);dev 默认 http://127.0.0.1:17320。
 */

import type { BackendSession } from "../replay/session";

let base = "http://127.0.0.1:17320";

export function setApiBase(url: string): void {
  base = url.replace(/\/$/, "");
}

/**
 * fetchJson 的默认超时(毫秒)。clientd 自身最慢的端点(平台连通性探测)服务端
 * 上限 6s,其余都是本地磁盘/内存操作;超过这个时间通常意味着后台没起来或已卡死,
 * 而不是请求真的很大。需要更长预算的调用可显式传 timeoutMs。
 */
export const DEFAULT_TIMEOUT_MS = 15_000;

/** 网络层失败(连接被拒 / 超时 / 读体中断)统一话术;本地服务未启动是最常见原因。 */
export const NETWORK_ERROR_MESSAGE = "无法连接本地服务，请确认客户端后台已启动";

export interface FetchJsonOptions {
  method?: string;
  body?: unknown;
  /** 覆盖默认超时;<= 0 表示不设超时(仅供确知的长任务使用)。 */
  timeoutMs?: number;
}

export async function fetchJson<T = unknown>(
  path: string,
  opts: FetchJsonOptions = {},
): Promise<T> {
  const url = `${base}${path}`;
  const init: RequestInit = {
    method: opts.method ?? "GET",
    headers: opts.body !== undefined
      ? { "Content-Type": "application/json" }
      : undefined,
  };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);

  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const controller = timeoutMs > 0 ? new AbortController() : null;
  if (controller) {
    init.signal = controller.signal;
  }
  const timer = controller
    ? setTimeout(() => controller.abort(), timeoutMs)
    : undefined;

  let resp: Response;
  let text: string;
  try {
    resp = await fetch(url, init);
    text = await resp.text();
  } catch {
    // 连接被拒 / DNS / 超时中断 / 读体失败:不把 "Failed to fetch" 之类原文
    // 抛给界面,统一给可操作的中文提示。
    throw new Error(NETWORK_ERROR_MESSAGE);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }

  let data: unknown = null;
  try {
    data = JSON.parse(text);
  } catch {
    data = text;
  }
  if (!resp.ok) {
    // 后端结构化错误(优先 message 字段)原样透出,不与网络错误混淆。
    const msg =
      data && typeof data === "object" && "message" in data
        ? String((data as { message: unknown }).message)
        : `${resp.status}`;
    throw new Error(msg);
  }
  return data as T;
}

export interface PlatformTokens {
  tournament: string;
  match: string;
  test_room: string[];
}

export interface PlatformSettings {
  server: string;
  tokens: PlatformTokens;
  selected_model: string | null;
}

export interface ConnectionTestResult {
  ok: boolean;
  status: number;
  mode: "tournament" | "match" | "test_room";
  code: string;
  message: string;
}

export interface ManagedModel {
  name: string;
  size: number;
  sha256?: string;
  imported_at?: number;
  contract: {
    planes?: number;
    scalars?: number;
    actions?: number;
  };
  selected: boolean;
}

export interface BatchSummary {
  batch_id: string;
  batch_dir: string;
  seed0: number | null;
  status: string;
  n_games: number | null;
  completed: number;
  skipped: number;
  stats: unknown;
  game_paths: string[];
  strategy?: string | null;
  evaluator?: string | null;
  model_name?: string | null;
}

export interface OnlineGameRef {
  date: string;
  gid: string;
  record_id?: string;
  path: string;
  name: string;
  started_at?: number | null;
  strategy?: string | null;
  evaluator?: string | null;
  model_name?: string | null;
}

export interface OnlineGamesQuery {
  startTs?: number;
  endTs?: number;
  offset?: number;
  limit?: number;
}

export interface OnlineRecordsResponse {
  games: OnlineGameRef[];
  offset?: number;
  limit?: number;
  has_more?: boolean;
  next_offset?: number | null;
}

export interface LocalRecordsResponse {
  batches: BatchSummary[];
}

export interface FramesResponse {
  gid: string | null;
  path: string;
  n_frames: number;
  frames: import("../replay/frame").ReplayFrame[];
  session?: BackendSession;
  verifications?: unknown[];
}

export type SessionStatus =
  | "created"
  | "running"
  | "finished"
  | "cancelled"
  | "error";

export interface AriaProgress {
  done: number;
  total: number;
  rate: number;
  last_index: number;
}

export interface SessionInfo {
  id: string;
  kind: string;
  status: SessionStatus;
  config: {
    n_games?: number;
    concurrency?: number;
    seats?: unknown[];
    max_games?: number | null;
    strategy?: string;
    evaluator?: string;
    state_rate?: number;
    room_close_wait?: number;
    [key: string]: unknown;
  };
  created_at: number;
  finished_at: number | null;
  result: {
    batch_id?: string;
    completed?: number;
    skipped?: number;
    games?: number;
    rooms?: number;
    termination_reason?: string;
    final_status?: string | null;
    final_stage?: unknown;
    actions?: number;
    scores?: unknown[];
    stats?: Record<string, unknown>;
    [key: string]: unknown;
  } | null;
  error: string | null;
  progress: (Partial<AriaProgress> & {
    rooms?: number;
    phase?: string;
    message?: string;
    tournament_status?: string | null;
    stage?: unknown;
    poll_interval_sec?: number;
    games?: number;
    actions?: number;
    strategy_loaded?: boolean | null;
    strategy_status?: string;
    strategy_name?: string;
    evaluator?: string;
    model_name?: string | null;
    server_connected?: boolean | null;
    server_status?: string;
    server_message?: string;
    last_server_check_at?: number | null;
  }) | null;
}

export interface SessionLogEntry {
  id: number;
  timestamp: number;
  level: "info" | "warning" | "error";
  source: string;
  message: string;
}

export interface SessionLogsResponse {
  session_id: string;
  logs: SessionLogEntry[];
  next_cursor: number;
  has_more: boolean;
  truncated: boolean;
}

export const api = {
  getSettings(): Promise<PlatformSettings> {
    return fetchJson("/api/settings");
  },
  saveSettings(settings: Pick<PlatformSettings, "server" | "tokens">): Promise<PlatformSettings> {
    return fetchJson("/api/settings", { method: "PUT", body: settings });
  },
  testConnection(body: {
    mode: ConnectionTestResult["mode"];
    server: string;
    token: string;
  }): Promise<ConnectionTestResult> {
    return fetchJson("/api/settings/test-connection", { method: "POST", body });
  },
  listModels(): Promise<{ models: ManagedModel[] }> {
    return fetchJson("/api/models");
  },
  importModel(name: string, contentBase64: string): Promise<ManagedModel> {
    return fetchJson("/api/models/import", {
      method: "POST",
      body: { name, content_base64: contentBase64 },
    });
  },
  selectModel(name: string): Promise<ManagedModel> {
    return fetchJson(`/api/models/${encodeURIComponent(name)}/select`, {
      method: "POST",
    });
  },
  deleteModel(name: string): Promise<{ name: string; deleted: boolean }> {
    return fetchJson(`/api/models/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });
  },
  localBatches(): Promise<LocalRecordsResponse> {
    return fetchJson("/api/records/local");
  },
  onlineGames(params: OnlineGamesQuery = {}): Promise<OnlineRecordsResponse> {
    const query = new URLSearchParams();
    if (params.startTs !== undefined) query.set("start_ts", String(params.startTs));
    if (params.endTs !== undefined) query.set("end_ts", String(params.endTs));
    if (params.offset !== undefined) query.set("offset", String(params.offset));
    if (params.limit !== undefined) query.set("limit", String(params.limit));
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return fetchJson(`/api/records/online${suffix}`);
  },
  localFrames(batchId: string, game: number): Promise<FramesResponse> {
    return fetchJson("/api/records/local/frames", {
      method: "POST",
      body: { batch_id: batchId, game },
    });
  },
  onlineFrames(recordId: string): Promise<FramesResponse> {
    return fetchJson(`/api/records/online/${encodeURIComponent(recordId)}/frames`);
  },
  createSession(kind: string, config: unknown): Promise<SessionInfo> {
    return fetchJson("/api/sessions", { method: "POST", body: { kind, config } });
  },
  listSessions(): Promise<{ sessions: SessionInfo[] }> {
    return fetchJson("/api/sessions");
  },
  getSession(id: string): Promise<SessionInfo> {
    return fetchJson(`/api/sessions/${id}`);
  },
  getSessionLogs(id: string, after = 0, limit = 500): Promise<SessionLogsResponse> {
    const query = new URLSearchParams({ after: String(after), limit: String(limit) });
    return fetchJson(`/api/sessions/${encodeURIComponent(id)}/logs?${query}`);
  },
  stopSession(id: string): Promise<SessionInfo> {
    return fetchJson(`/api/sessions/${id}/stop`, { method: "POST" });
  },
};
