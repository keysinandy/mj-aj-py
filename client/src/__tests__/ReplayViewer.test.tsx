import { beforeEach, describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { useReplayStore } from "../replay/replayStore";
import { entriesFromFrames } from "../replay/timeline";
import { Timeline } from "../components/Timeline";
import { ReplayViewer } from "../components/ReplayViewer";
import type { ReplayFrame } from "../replay/frame";

function buildFrames(n: number): ReplayFrame[] {
  return Array.from({ length: n }).map((_, i) => ({
    step: i,
    info_kind: "local",
    my_seat: 0,
    hands: [[1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], [], [], []],
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
}

describe("ReplayControls 步进与进度", () => {
  const frames = buildFrames(5);

  beforeEach(() => {
    useReplayStore.setState({ frames: [], index: 0, observeSeat: 0 });
    useReplayStore.getState().setFrames(frames);
  });

  it("按钮推进并更新计数", () => {
    render(<ReplayViewer />);
    expect(screen.getByTestId("index")).toHaveTextContent("1/5");
    fireEvent.click(screen.getByTestId("btn-next"));
    expect(screen.getByTestId("index")).toHaveTextContent("2/5");
    fireEvent.click(screen.getByTestId("btn-next"));
    fireEvent.click(screen.getByTestId("btn-back"));
    expect(screen.getByTestId("index")).toHaveTextContent("2/5");
  });

  it("拖动进度条定位到任意步,牌面状态与顺序推进一致", () => {
    render(<ReplayViewer />);
    fireEvent.click(screen.getByTestId("btn-next"));
    fireEvent.click(screen.getByTestId("btn-next"));
    fireEvent.click(screen.getByTestId("btn-next"));
    // 现在 index=3(第二个 步3 / b4墙)
    const seqWall = screen.getByTestId("wall").textContent;
    // 直接拖到 3
    fireEvent.change(screen.getByTestId("slider"), { target: { value: "3" } });
    expect(screen.getByTestId("index")).toHaveTextContent("4/5");
    expect(screen.getByTestId("wall").textContent).toBe(seqWall);
  });

  it("起始时上一步禁用,末步时下一步禁用", () => {
    render(<ReplayViewer />);
    expect(screen.getByTestId("btn-back")).toBeDisabled();
    fireEvent.click(screen.getByTestId("btn-next"));
    expect(screen.getByTestId("btn-back")).toBeEnabled();
  });
});

describe("Timeline 条目与高亮", () => {
  const frames = buildFrames(4);
  beforeEach(() => useReplayStore.getState().setFrames(frames));

  it("渲染条目标签,缺口条目带标注", () => {
    const entries = entriesFromFrames(frames);
    render(<Timeline entries={entries} currentIndex={0} onSelect={() => {}} />);
    expect(screen.getByText(/步3/)).toBeTruthy();
    const gapItem = document.querySelector("[data-testid='tl-item-3']");
    expect(gapItem!.classList.contains("tl-gap")).toBe(true);
  });

  it("点击时间线条目跳转到该步(帧墙数一致)", () => {
    render(<ReplayViewer />);
    fireEvent.click(screen.getByTestId("tl-item-3"));
    expect(screen.getByTestId("index")).toHaveTextContent("4/4");
    expect(screen.getByTestId("wall")).toHaveTextContent(`墙 ${77}`);
  });

  it("当前条目高亮", () => {
    const entries = entriesFromFrames(frames);
    render(<Timeline entries={entries} currentIndex={2} onSelect={() => {}} />);
    const it2 = document.querySelector("[data-testid='tl-item-2']")!;
    expect(it2.classList.contains("tl-current")).toBe(true);
  });
});