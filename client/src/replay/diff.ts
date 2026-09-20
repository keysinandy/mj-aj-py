import type { ReplayFrame } from "./frame";

export interface ReplayStateDiff {
  path: string;
  label: string;
  before: unknown;
  after: unknown;
}
function same(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function addDiff(
  diffs: ReplayStateDiff[],
  path: string,
  label: string,
  before: unknown,
  after: unknown,
): void {
  if (!same(before, after)) diffs.push({ path, label, before, after });
}

/** 只比较当前查看器真正展示的状态字段,避免把日志元数据当成牌面变化。 */
export function diffFrames(
  previous: ReplayFrame | null,
  current: ReplayFrame,
): ReplayStateDiff[] {
  if (!previous) return [];
  const diffs: ReplayStateDiff[] = [];
  addDiff(diffs, "round_no", "局数", previous.round_no, current.round_no);
  addDiff(diffs, "wall_remaining", "墙", previous.wall_remaining, current.wall_remaining);
  addDiff(diffs, "scores", "积分", previous.scores, current.scores);
  addDiff(diffs, "current", "当前行动", previous.current, current.current);
  addDiff(diffs, "response_window", "响应窗口", previous.response_window, current.response_window);

  for (let seat = 0; seat < 4; seat += 1) {
    const beforeHand = previous.hands?.[seat] ?? (seat === previous.my_seat ? previous.my_hand : null);
    const afterHand = current.hands?.[seat] ?? (seat === current.my_seat ? current.my_hand : null);
    addDiff(diffs, `hands.${seat}`, `P${seat} 手牌`, beforeHand, afterHand);
    addDiff(diffs, `discards.${seat}`, `P${seat} 牌河`, previous.discards[seat], current.discards[seat]);
    addDiff(diffs, `melds.${seat}`, `P${seat} 副露`, previous.melds[seat], current.melds[seat]);
  }
  return diffs;
}

export function displayDiffValue(value: unknown): string {
  if (value === undefined) return "-";
  if (value === null) return "无/未知";
  const text = JSON.stringify(value);
  return text === undefined ? String(value) : text;
}
