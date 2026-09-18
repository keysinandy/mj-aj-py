/**
 * clientd HTTP 客户端。Base URL 可通过 setApiBase 配置(桌面形态由
 * sidecar 实际端口设定);dev 默认 http://127.0.0.1:17320。
 */

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
}

export interface LocalRecordsResponse {
  batches: BatchSummary[];
}

export interface FramesResponse {
  gid: string | null;
  path: string;
  n_frames: number;
  frames: import("../replay/frame").ReplayFrame[];
}

export const api = {
  localBatches(): Promise<LocalRecordsResponse> {
    return fetchJson("/api/records/local");
  },
  onlineGames(): Promise<{ games: OnlineGameRef[] }> {
    return fetchJson("/api/records/online");
  },
  localFrames(batchId: string, game: number): Promise<FramesResponse> {
    return fetchJson("/api/records/local/frames", {
      method: "POST",
      body: { batch_id: batchId, game },
    });
  },
};