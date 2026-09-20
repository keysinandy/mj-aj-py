import { beforeEach, describe, expect, it } from "vitest";
import { ReplayEngine } from "../engine";
import { sessionFromFrames } from "../session";
import { useReplayStore } from "../replayStore";
import type { ReplayFrame } from "../frame";

function frames(): ReplayFrame[] {
  return [0, 1, 2].map((step) => ({
    step,
    info_kind: step === 0 ? "local" : "online",
    my_seat: 0,
    hands: step === 0 ? [[], [], [], []] : null,
    my_hand: [],
    discards: [[], [], [], []],
    melds: [[], [], [], []],
    wall_remaining: 84 - step,
    scores: [0, 0, 0, 0],
    round_no: 1,
    current: { seat: 0, phase: "playing" },
    label: `step-${step}`,
    gap: false,
    seq_no: step * 10,
    seq_source: step === 0 ? "local_initial" : "server_event",
  }));
}

describe("ReplaySession 与 ReplayEngine", () => {
  beforeEach(() => {
    useReplayStore.setState({
      session: null,
      engine: null,
      frames: [],
      index: 0,
      observeSeat: 0,
      playing: false,
      speed: 1,
    });
  });

  it("本地和线上帧都能进入同一个序号导航器", () => {
    const session = sessionFromFrames(frames(), "online");
    const engine = new ReplayEngine(session, 2);
    expect(engine.total).toBe(3);
    expect(engine.stateAt(2)?.wall_remaining).toBe(82);
    expect(engine.indexForSeqNo(19)).toBe(1);
    expect(engine.indexForSeqNo(1)).toBe(0);
  });

  it("旁路记录由步骤承载而不是增加步骤数", () => {
    const source = frames();
    source[1].local_requests = [{ type: "GET_STATE", status: 200 }];
    source[1].diagnostics = [{ code: "claim_miss", severity: "error" }];
    const session = sessionFromFrames(source, "online");
    useReplayStore.getState().setSession(session);
    useReplayStore.getState().jumpToSeqNo(10);
    expect(useReplayStore.getState().total()).toBe(3);
    expect(useReplayStore.getState().index).toBe(1);
    expect(useReplayStore.getState().currentStep()?.localRequests).toHaveLength(1);
    expect(useReplayStore.getState().currentStep()?.diagnostics[0].code).toBe("claim_miss");
  });
});
