import { beforeEach, describe, expect, it } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
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

  it("把步进操作条放在牌桌中心", () => {
    render(<ReplayViewer />);
    const center = document.querySelector(".table-center") as HTMLElement;
    expect(within(center).getByTestId("btn-back")).toBeTruthy();
    expect(within(center).getByTestId("btn-play")).toBeTruthy();
    expect(within(center).getByTestId("btn-next")).toBeTruthy();
    expect(document.querySelector(".replay-control-row [data-testid='btn-play']")).toBeNull();
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

  it("本地默认玩家视角,可打开全知开关", () => {
    render(<ReplayViewer />);
    const button = screen.getByRole("button", { name: "全知视角" });
    expect(button).toBeEnabled();
    expect(button).not.toHaveClass("active");
    fireEvent.click(button);
    expect(button).toHaveClass("active");
  });

  it("线上回放锁定玩家视角", () => {
    useReplayStore.getState().setFrames(buildFrames(2).map((frame) => ({
      ...frame,
      info_kind: "online",
      hands: null,
      my_hand: [1, 0, 0],
    })));
    render(<ReplayViewer />);
    expect(screen.getByRole("button", { name: "全知视角" })).toBeDisabled();
    expect(screen.getByText("线上固定")).toBeTruthy();
  });

  it("支持首尾和 seqNo 跳转,缺口时不跳到未来步骤", () => {
    render(<ReplayViewer />);
    fireEvent.change(screen.getByTestId("seq-input"), { target: { value: "3" } });
    fireEvent.click(screen.getByTestId("btn-seq-jump"));
    expect(screen.getByTestId("index")).toHaveTextContent("4/5");
    fireEvent.click(screen.getByTestId("btn-first"));
    expect(screen.getByTestId("index")).toHaveTextContent("1/5");
    fireEvent.click(screen.getByTestId("btn-last"));
    expect(screen.getByTestId("index")).toHaveTextContent("5/5");
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

  it("按事件座位高亮我的操作", () => {
    const entries = entriesFromFrames([
      { ...frames[0], event: { type: "action", actor: 0, label: "打 1万" } },
      { ...frames[1], event: { type: "action", actor: 1, label: "打 2万" } },
    ]);
    render(<Timeline entries={entries} currentIndex={0} onSelect={() => {}} />);
    const mine = document.querySelector("[data-testid='tl-item-0']")!;
    const other = document.querySelector("[data-testid='tl-item-1']")!;
    expect(mine).toHaveAttribute("data-mine", "true");
    expect(mine).toHaveClass("tl-mine");
    expect(within(mine as HTMLElement).getByLabelText("我的操作")).toHaveTextContent("我");
    expect(other).toHaveAttribute("data-mine", "false");
    expect(other).not.toHaveClass("tl-mine");
  });

  it("事件类型结构化区分 gameplay 与挂在我方 seat 上的系统步骤", () => {
    const entries = entriesFromFrames([
      { ...frames[0], event: { type: "tile_drawn", seat: 0 } },
      { ...frames[1], event: { type: "tile_discarded", seat: 0 } },
      { ...frames[2], event: { type: "chi", seat: 0 } },
      { ...frames[3], event: { type: "peng", seat: 0 } },
      { ...frames[0], event: { type: "gang", seat: 0 } },
      { ...frames[1], event: { type: "hu", seat: 0 } },
      { ...frames[2], event: { type: "pass", seat: 0 } },
      { ...frames[3], event: { type: "tile_drawn", seat: 1 } },
      { ...frames[0], event: { type: "snapshot", seat: 0 } },
      { ...frames[1], event: { type: "timeout", seat: 0 } },
    ]);

    expect(entries.map((entry) => entry.actionKind)).toEqual([
      "draw", "discard", "chi", "peng", "gang", "hu", "pass", "draw", "other", "timeout",
    ]);
    expect(entries.map((entry) => entry.isGameplayAction)).toEqual([
      true, true, true, true, true, true, true, true, false, false,
    ]);
    expect(entries.map((entry) => entry.isMine)).toEqual([
      true, true, true, true, true, true, true, false, false, false,
    ]);
  });

  it("十场房间切场后牌桌、时间线和进度均使用当前场,seqNo 可跨场定位", () => {
    const tenRounds = buildFrames(20).map((frame, index) => ({
      ...frame,
      round_no: Math.floor(index / 2) + 1,
      seq_no: 100 + index,
      event: { type: "tile_discarded", seat: index % 4 },
    }));
    useReplayStore.getState().setFrames(tenRounds);
    render(<ReplayViewer />);

    expect(screen.getByTestId("round-navigation")).toHaveTextContent("第 1 / 10 场");
    expect(screen.getByTestId("index")).toHaveTextContent("1/2");
    expect(screen.getByTestId("tl-item-0")).toBeTruthy();
    expect(screen.queryByTestId("tl-item-2")).toBeNull();
    fireEvent.click(screen.getByTestId("btn-next-round"));
    expect(screen.getByTestId("round-navigation")).toHaveTextContent("第 2 / 10 场");
    expect(screen.getByTestId("round")).toHaveTextContent("第 2 局");
    expect(screen.getByTestId("index")).toHaveTextContent("1/2");
    expect(screen.getByTestId("tl-item-2")).toBeTruthy();
    expect(screen.queryByTestId("tl-item-1")).toBeNull();

    fireEvent.change(screen.getByTestId("seq-input"), { target: { value: "107" } });
    fireEvent.click(screen.getByTestId("btn-seq-jump"));
    expect(screen.getByTestId("round-navigation")).toHaveTextContent("第 4 / 10 场");
    expect(screen.getByTestId("index")).toHaveTextContent("2/2");
    expect(screen.getByTestId("seq-no")).toHaveTextContent("seq 107");
    expect(screen.getByTestId("tl-item-7")).toBeTruthy();
    expect(screen.queryByTestId("tl-item-5")).toBeNull();
  });

  it("我方动作筛选只改时间线,保留全局 replay index 并使用专用跳转", () => {
    const actions = buildFrames(5).map((frame, index) => ({
      ...frame,
      event: [
        { type: "snapshot", seat: 0 },
        { type: "tile_drawn", seat: 0 },
        { type: "tile_discarded", seat: 2 },
        { type: "pass", seat: 0 },
        { type: "diagnostic", seat: 0 },
      ][index],
    }));
    useReplayStore.getState().setFrames(actions);
    render(<ReplayViewer />);
    fireEvent.click(screen.getByTestId("btn-next"));
    const wallBefore = screen.getByTestId("wall").textContent;
    fireEvent.click(screen.getByTestId("actor-filter-mine"));

    expect(screen.getByTestId("tl-item-1")).toBeTruthy();
    expect(screen.queryByTestId("tl-item-0")).toBeNull();
    expect(screen.queryByTestId("tl-item-2")).toBeNull();
    expect(screen.getByTestId("wall").textContent).toBe(wallBefore);
    fireEvent.click(screen.getByTestId("btn-next-mine"));
    expect(screen.getByTestId("index")).toHaveTextContent("4/5");
    expect(screen.getByTestId("tl-item-3")).toHaveClass("tl-current");
    fireEvent.click(screen.getByTestId("btn-next"));
    expect(screen.getByTestId("index")).toHaveTextContent("5/5");
    expect(screen.queryByTestId("tl-item-4")).toBeNull();
    fireEvent.click(screen.getByTestId("actor-filter-all"));
    expect(screen.getByTestId("tl-item-4")).toBeTruthy();
  });

  it("胡牌标记只存在于 HU 及之后的帧,回退立即清除", () => {
    const winnerFrames = buildFrames(4).map((frame, index) => ({
      ...frame,
      event: index === 2 ? { type: "hu", seat: 2 } : { type: "tile_discarded", seat: 0 },
      winner_seats: index < 2 ? [] : index === 2 ? [2] : [2, 3],
    }));
    useReplayStore.getState().setFrames(winnerFrames);
    render(<ReplayViewer />);

    expect(screen.queryByTestId("winner-badge-2")).toBeNull();
    fireEvent.click(screen.getByTestId("btn-next"));
    expect(screen.queryByTestId("winner-badge-2")).toBeNull();
    fireEvent.click(screen.getByTestId("btn-next"));
    expect(screen.getByTestId("winner-badge-2")).toHaveTextContent("胡");
    expect(screen.queryByTestId("winner-badge-3")).toBeNull();
    fireEvent.click(screen.getByTestId("btn-next"));
    expect(screen.getByTestId("winner-badge-3")).toHaveTextContent("胡");
    fireEvent.click(screen.getByTestId("btn-back"));
    expect(screen.queryByTestId("winner-badge-3")).toBeNull();
    expect(screen.getByTestId("winner-badge-2")).toBeTruthy();
    fireEvent.click(screen.getByTestId("btn-back"));
    expect(screen.queryByTestId("winner-badge-2")).toBeNull();
  });
});
