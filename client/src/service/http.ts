/**
 * clientd HTTP 客户端。Base URL 可通过 setApiBase 配置(桌面形态由
 * sidecar 实际端口设定);dev 默认 http://127.0.0.1:17320。
 */

import type { BackendSession } from "../replay/session";

let base = "http://127.0.0.1:17320";

export function setApiBase(url: string): void {
  base = url.replace(/\/$/, "");
}

export async function fetchJson<T = unknown>(
  path: string,
  opts: { method?: string; body?: unknown } = {},
): Promise<T> {
  const url = `${base}${path}`;
  const init: RequestInit = {
    method: opts.method ?? "GET",
    headers: opts.body !== undefined
      ? { "Content-Type": "application/json" }
      : undefined,
  };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
  const resp = await fetch(url, init);
  const text = await resp.text();
  let data: unknown = null;
  try {
    data = JSON.parse(text);
  } catch {
    data = text;
  }
  if (!resp.ok) {
    const msg =
      data && typeof data === "object" && "message" in data
        ? String((data as { message: unknown }).message)
        : `${resp.status}`;
    throw new Error(msg);
  }
  return data as T;
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
}

export interface OnlineGameRef {
  date: string;
  gid: string;
  path: string;
  name: string;
  started_at?: number | null;
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
  config: { n_games?: number; concurrency?: number; seats?: unknown[] };
  created_at: number;
  finished_at: number | null;
  result: { batch_id?: string; completed?: number; skipped?: number } | null;
  error: string | null;
  progress: AriaProgress | null;
}

export const api = {
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
  onlineFrames(gid: string): Promise<FramesResponse> {
    return fetchJson(`/api/records/online/${encodeURIComponent(gid)}/frames`);
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
  stopSession(id: string): Promise<SessionInfo> {
    return fetchJson(`/api/sessions/${id}/stop`, { method: "POST" });
  },
};
