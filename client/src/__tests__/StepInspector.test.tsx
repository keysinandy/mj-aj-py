import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StepInspector } from "../components/StepInspector";
import { sessionFromFrames } from "../replay/session";
import type { ReplayFrame } from "../replay/frame";

function frame(step: number): ReplayFrame {
  return {
    step,
    info_kind: "local",
    my_seat: 0,
    hands: [[step], [], [], []],
    my_hand: [step],
    discards: [step ? [3] : [], [], [], []],
    melds: [[], [], [], []],
    wall_remaining: 84 - step,
    scores: [0, 0, 0, 0],
    round_no: 1,
    current: { seat: 0, phase: "playing" },
    label: step ? "弃牌" : "初始",
    gap: false,
    seq_no: step,
    seq_source: "local_action",
    event: step ? { type: "action", action: 3 } : { type: "session_start" },
    local_requests: step ? [{ type: "GET_STATE", status: 200 }] : [],
    diagnostics: step ? [{ code: "claim_miss", severity: "error", message: "未成功" }] : [],
  };
}

describe("StepInspector", () => {
  it("展示本地证据、诊断和前一步状态差异", () => {
    const session = sessionFromFrames([frame(0), frame(1)], "local");
    render(<StepInspector step={session.steps[1]} previous={session.steps[0]} />);
    expect(screen.getByTestId("step-event")).toHaveTextContent("action");
    expect(screen.getByTestId("local-evidence")).toHaveTextContent("GET_STATE");
    expect(screen.getByTestId("step-diagnostics")).toHaveTextContent("claim_miss");
    expect(screen.getByTestId("state-diff")).toHaveTextContent("P0 手牌");
  });
});
