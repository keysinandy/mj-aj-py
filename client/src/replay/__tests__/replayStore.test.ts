import { beforeEach, describe, expect, it } from "vitest";
import { useReplayStore } from "../replayStore";
import { sessionFromFrames } from "../session";
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
    useReplayStore.setState({ frames: [], index: 0, observeSeat: 0, visibilityMode: "player" });
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

  it("视角切换默认从玩家视角开始,重新载入记录会复位", () => {
    useReplayStore.getState().setVisibilityMode("omniscient");
    expect(useReplayStore.getState().visibilityMode).toBe("omniscient");
    useReplayStore.getState().setFrames(frames);
    expect(useReplayStore.getState().visibilityMode).toBe("player");
  });

  it("本地轮转主位作为默认观察座位", () => {
    const rotated = frames.map((frame) => ({ ...frame, my_seat: 2 }));
    useReplayStore.getState().setFrames(rotated);
    expect(useReplayStore.getState().observeSeat).toBe(2);
  });

  it("round 导航默认选第一场,步进与首尾均限制在当前场", () => {
    const multiround = frames.map((frame, index) => ({
      ...frame,
      round_no: index < 2 ? 1 : index < 4 ? 2 : 3,
    }));
    useReplayStore.getState().setSession(sessionFromFrames(multiround, "online"));
    const store = useReplayStore.getState();

    expect(store.activeRoundId).toBe(store.rounds[0].roundId);
    expect(store.index).toBe(0);
    store.nextRound();
    expect(useReplayStore.getState().index).toBe(2);
    store.lastStep();
    expect(useReplayStore.getState().index).toBe(3);
    store.stepBack();
    expect(useReplayStore.getState().index).toBe(2);
    store.stepBack();
    expect(useReplayStore.getState().index).toBe(2);
    store.stepForward();
    store.setSpeed(1);
    useReplayStore.setState({ playing: true });
    store.stepForward();
    expect(useReplayStore.getState().index).toBe(3);
    expect(useReplayStore.getState().playing).toBe(false);
    store.nextRound();
    expect(useReplayStore.getState().index).toBe(4);
    store.nextRound();
    expect(useReplayStore.getState().index).toBe(4);
    store.previousRound();
    expect(useReplayStore.getState().index).toBe(2);
  });

  it("seqNo 跨场定位同步切换场次,旧 round 缺失时从 steps 派生", () => {
    const multiround = frames.map((frame, index) => ({
      ...frame,
      round_no: index < 2 ? 1 : index < 4 ? 2 : 3,
      seq_no: 100 + index,
    }));
    const session = sessionFromFrames(multiround, "online");
    const oldSession = {
      ...session,
      metadata: { ...session.metadata, rounds: [] },
    };
    useReplayStore.getState().setSession(oldSession);
    useReplayStore.getState().jumpToSeqNo(104);

    expect(useReplayStore.getState().index).toBe(4);
    expect(useReplayStore.getState().activeRoundId).toBe(useReplayStore.getState().rounds[2].roundId);
    expect(useReplayStore.getState().rounds).toHaveLength(3);
  });

  it("筛选内导航使用全局 index,并且不会跨 round", () => {
    const actionFrames = frames.map((frame, index) => ({
      ...frame,
      round_no: index < 4 ? 1 : 2,
      event: [
        { type: "snapshot" },
        { type: "tile_drawn", seat: 0 },
        { type: "tile_discarded", seat: 2 },
        { type: "pass", seat: 0 },
        { type: "tile_discarded", seat: 0 },
      ][index],
    }));
    useReplayStore.getState().setSession(sessionFromFrames(actionFrames, "online"));
    const before = useReplayStore.getState().frame();
    useReplayStore.getState().setActorFilter("mine");
    expect(useReplayStore.getState().index).toBe(0);
    expect(useReplayStore.getState().frame()).toBe(before);

    useReplayStore.getState().nextFilteredStep();
    expect(useReplayStore.getState().index).toBe(1);
    useReplayStore.getState().nextFilteredStep();
    expect(useReplayStore.getState().index).toBe(3);
    useReplayStore.getState().nextFilteredStep();
    expect(useReplayStore.getState().index).toBe(3);
    useReplayStore.getState().previousFilteredStep();
    expect(useReplayStore.getState().index).toBe(1);
    useReplayStore.getState().previousFilteredStep();
    expect(useReplayStore.getState().index).toBe(1);
  });
});
