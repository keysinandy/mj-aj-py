import { beforeEach, describe, expect, it } from "vitest";
import { ReplayEngine } from "../engine";
import { sessionFromFrames, sessionFromResponse } from "../session";
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

  it("旧帧按连续 round_no 派生场次,相同局号的非连续段不合并", () => {
    const source = frames().map((frame, index) => ({
      ...frame,
      round_no: [3, 4, 3][index],
    }));
    const session = sessionFromFrames(source, "online");

    expect(session.metadata.rounds.map((round) => round.roundNo)).toEqual([3, 4, 3]);
    expect(session.metadata.rounds.map((round) => round.roundId)).toEqual([
      "r1-n3-s0", "r2-n4-s10", "r3-n3-s20",
    ]);
    expect(session.metadata.rounds.map((round) => [round.startStepIndex, round.endStepIndex]))
      .toEqual([[0, 0], [1, 1], [2, 2]]);
    expect(session.metadata.rounds.every((round) =>
      round.maxMyWhiteboards === 0 && round.whiteboardMatch === false,
    )).toBe(true);
  });

  it("读取后端 snake_case round metadata,旧 session 缺失时兼容派生", () => {
    const source = frames();
    const response = {
      frames: source,
      session: {
        metadata: {
          source: "online" as const,
          rounds: [{
            round_id: "r1-n1-s0",
            ordinal: 1,
            round_no: 1,
            start_step_index: 0,
            end_step_index: 2,
            start_seq_no: 0,
            end_seq_no: 20,
            winner_seats: [2],
            ended: true,
          }],
        },
        steps: source.map((state, step_index) => ({ step_index, state })),
      },
    };
    const mapped = sessionFromResponse(response, "online");
    const legacyFrames = source.map((frame, index) => ({
      ...frame, round_no: index < 2 ? 1 : 2,
    }));
    const legacy = sessionFromResponse({
      frames: legacyFrames,
      session: { steps: legacyFrames.map((state, step_index) => ({ step_index, state })) },
    }, "online");

    expect(mapped.metadata.rounds[0]).toMatchObject({
      roundId: "r1-n1-s0", ordinal: 1, roundNo: 1,
      startStepIndex: 0, endStepIndex: 2,
      startSeqNo: 0, endSeqNo: 20, winnerSeats: [2], ended: true,
    });
    expect(legacy.metadata.rounds.map((round) => round.roundNo)).toEqual([1, 2]);
  });
});
