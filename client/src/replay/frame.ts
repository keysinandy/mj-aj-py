/**
 * 回放帧类型 —— 与 mj/clientd/replay.py 的 Frame 形状一致(JSON 直通)。
 * 帧保留 local 全知数据供本地开关使用;默认渲染由 UI 选择玩家视角,
 * online 始终只含自家牌面,他家暗手不可见(None)。
 */

export type InfoKind = "local" | "online";
export type ReplayVisibilityMode = "player" | "omniscient";

export type ReplaySeqSource =
  | "server_event"
  | "snapshot"
  | "local_initial"
  | "local_action"
  | "derived";

export interface ReplayEvent {
  type: string;
  [key: string]: unknown;
}

export interface ReplayLocalRequest {
  kind?: string;
  type?: string;
  seq_no?: number | null;
  status?: number | null;
  latency_ms?: number | null;
  attempts?: number | null;
  [key: string]: unknown;
}

export interface ReplayDiagnostic {
  code: string;
  severity?: "info" | "warn" | "error" | string;
  message?: string;
  seq_no?: number | null;
  [key: string]: unknown;
}

export interface MeldEntry {
  kind: "chow" | "chi" | "peng" | "pong" | "kong_open" | "kong_closed"
    | "kong_claimed" | "kong_add" | "kan_open" | "kan_closed" | "kan_added";
  tiles: number[];
  called_tile?: number;
  called_index?: number;
  from_seat?: number | null;
}

export interface ReplayFrame {
  step: number;
  info_kind: InfoKind;
  my_seat: number;
  /** 本地帧的四家暗手原始数据; online 为 None。默认 UI 不会全部渲染。 */
  hands: number[][] | null;
  /** 线上视角的本家手牌。 */
  my_hand: number[] | null;
  /** 各家的已知手牌张数;未知时为 null,不代表空手。 */
  hand_counts?: Array<number | null> | null;
  discards: number[][];
  melds: MeldEntry[][];
  wall_remaining: number;
  scores: number[];
  round_no: number;
  /** 庄家座位；线上来自 snapshot，本地来自 Game.dealer。 */
  dealer?: number | null;
  dora_indicators?: number[];
  current: { seat: number | null; phase: string } | null;
  label: string;
  gap: boolean;
  /** 服务端事件序号;本地动作记录使用带来源标记的派生序号。 */
  seq_no?: number | null;
  seq_source?: ReplaySeqSource;
  timestamp?: number | null;
  event?: ReplayEvent | null;
  local_requests?: ReplayLocalRequest[];
  diagnostics?: ReplayDiagnostic[];
  response_window?: {
    owner: number;
    tile: number;
    phase: string;
    response_order?: number[];
    response_index?: number | null;
  } | null;
}

/** 把 count 向量 [34] 展开为具体牌列表。 */
export function expandHand(counts: number[]): number[] {
  const out: number[] = [];
  for (let t = 0; t < counts.length; t++) {
    for (let i = 0; i < counts[t]; i++) out.push(t);
  }
  return out;
}

const HONOR: Record<number, string> = {
  27: "東",
  28: "南",
  29: "西",
  30: "北",
  31: "中",
  32: "发",
};

const WHITEBOARD = 33; // 白板 = 财神(百搭)

/** 单张牌 → 中文标签。 */
export function tileLabel(t: number): string {
  if (t >= 0 && t <= 8) return `${t + 1}万`;
  if (t >= 9 && t <= 17) return `${t - 8}筒`;
  if (t >= 18 && t <= 26) return `${t - 17}条`;
  if (t === WHITEBOARD) return "白·神";
  return HONOR[t] ?? String(t);
}

export function isFullInfo(f: Pick<ReplayFrame, "info_kind" | "hands">): boolean {
  return f.info_kind === "local" && f.hands !== null;
}

export const SEATS = 4;
