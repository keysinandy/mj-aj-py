import { beforeEach, describe, expect, it } from "vitest";
import { useReplayStore } from "../replayStore";
import type { ReplayFrame } from "../frame";

const frames: ReplayFrame[] = Array.from({ length: 5 }).map((_, i) => ({
  step: i,
  info_kind: "local",
  my_seat: 0,
  hands: [[i, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], [], [], []],
  my_hand: [],
  discards: [[], [], [], []],
  melds: [[], [], [], []],
  wall_remaining: 80 - i,
  scores: [0, 0, 0, 0],
  round_no: 1,
  current: { seat: 0, phase: "playing" },
  label: `步${i}`,
  gap: i === 3,
}));

describe("replay store 步进语义", () => {
  beforeEach(() => {
    useReplayStore.setState({ frames: [], index: 0, observeSeat: 0 });
    useReplayStore.getState().setFrames(frames);
  });

  it("setFrames 后落在索引 0", () => {
    expect(useReplayStore.getState().index).toBe(0);
    expect(useReplayStore.getState().total()).toBe(5);
  });

  it("下一步/上一步推进与回退", () => {
    useReplayStore.getState().stepForward();
    expect(useReplayStore.getState().index).toBe(1);
    useReplayStore.getState().stepForward();
    useReplayStore.getState().stepForward();
    expect(useReplayStore.getState().index).toBe(3);
    useReplayStore.getState().stepBack();
    expect(useReplayStore.getState().index).toBe(2);
  });

  it("越界被夹紧:首步不可退,末步不可进", () => {
    useReplayStore.getState().jumpTo(0);
    useReplayStore.getState().stepBack();
    expect(useReplayStore.getState().index).toBe(0);
    useReplayStore.getState().jumpTo(4);
    useReplayStore.getState().stepForward();
    expect(useReplayStore.getState().index).toBe(4);
  });

  it("jumpTo 直接定位,与顺序推进到该步一致", () => {
    // 顺序推进到 3
    const store = useReplayStore.getState();
    store.jumpTo(0);
    store.stepForward(); store.stepForward(); store.stepForward();
    const seqFrame = store.frame()!;
    // 直接跳到 3
    store.jumpTo(3);
    const jumpFrame = store.frame()!;
    expect(jumpFrame.step).toBe(seqFrame.step);
    expect(jumpFrame.wall_remaining).toBe(seqFrame.wall_remaining);
    expect(useReplayStore.getState().index).toBe(3);
  });

  it("jumpTo 夹紧越界值", () => {
    useReplayStore.getState().jumpTo(99);
    expect(useReplayStore.getState().index).toBe(4);
    useReplayStore.getState().jumpTo(-3);
    expect(useReplayStore.getState().index).toBe(0);
  });

  it("空帧数组 frame() 返回 null,total 0", () => {
    useReplayStore.getState().setFrames([]);
    expect(useReplayStore.getState().frame()).toBeNull();
    expect(useReplayStore.getState().total()).toBe(0);
  });

  it("观察座位可切换", () => {
    useReplayStore.getState().setObserveSeat(2);
    expect(useReplayStore.getState().observeSeat).toBe(2);
  });
});