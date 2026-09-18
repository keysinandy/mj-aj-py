/**
 * 回放帧类型 —— 与 mj/clientd/replay.py 的 Frame 形状一致(JSON 直通)。
 * 视角规则由 info_kind 决定:local 全书,online 他家暗手不可见(None)。
 */

export type InfoKind = "local" | "online";

export interface MeldEntry {
  kind: "chow" | "peng" | "kong_open" | "kong_closed" | "kong_claimed";
  tiles: number[];
}

export interface ReplayFrame {
  step: number;
  info_kind: InfoKind;
  my_seat: number;
  /** 四家暗手;None => 他家暗手不可见(online)。 */
  hands: number[][] | null;
  /** 线上视角的本家手牌。 */
  my_hand: number[] | null;
  discards: number[][];
  melds: MeldEntry[][];
  wall_remaining: number;
  scores: number[];
  round_no: number;
  current: { seat: number | null; phase: string } | null;
  label: string;
  gap: boolean;
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