import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { GameTable } from "../components/GameTable";
import type { ReplayFrame } from "../replay/frame";

function localFrame(over: Partial<ReplayFrame> = {}): ReplayFrame {
  return {
    step: 3,
    info_kind: "local",
    my_seat: 0,
    hands: [
      [1, 0, 0, 2, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], // P0 一筒,四条×2,九万
      [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 0, 0], // P1 東南西北
      [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1], // P2 白板
      [],
    ],
    my_hand: [1, 0, 0, 2, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    discards: [[0], [27], [], [18]],
    melds: [
      [],
      [{ kind: "chow", tiles: [9, 10, 11] }],
      [{ kind: "peng", tiles: [18, 18, 18] }],
      [],
    ],
    wall_remaining: 61,
    scores: [1200, -400, -400, -400],
    round_no: 1,
    current: { seat: 0, phase: "playing" },
    label: "弃",
    gap: false,
    ...over,
  };
}

function onlineFrame(): ReplayFrame {
  return localFrame({
    info_kind: "online",
    hands: null,
    my_hand: [0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    discards: [[0], [27], [], []],
    scores: [0, 0, 0, 0],
  } as ReplayFrame);
}

describe("GameTable 本地全知视角", () => {
  it("渲染局号/墙数/分数/标签", () => {
    render(<GameTable frame={localFrame()} observeSeat={0} />);
    expect(screen.getByTestId("round")).toHaveTextContent("第 1 局");
    expect(screen.getByTestId("wall")).toHaveTextContent("墙 61");
    expect(screen.getByTestId("scores")).toHaveTextContent("P0 1200");
    expect(screen.getByTestId("label")).toHaveTextContent("弃");
  });

  it("观察座位切换被高亮", () => {
    render(<GameTable frame={localFrame()} observeSeat={1} />);
    const seatEls = Array.from(document.querySelectorAll(".seat"));
    expect(seatEls.length).toBe(4);
    const p1 = seatEls.find((el) => el.getAttribute("data-seat") === "1")!;
    expect(p1.classList.contains("seat-observe")).toBe(true);
    const p0 = seatEls.find((el) => el.getAttribute("data-seat") === "0")!;
    expect(p0.classList.contains("seat-observe")).toBe(false);
    // 座位标头齐全
    expect(screen.getByText(/座位 P0/)).toBeTruthy();
    expect(screen.getByText(/座位 P1/)).toBeTruthy();
  });

  it("牌河与副露字段与固定帧一致", () => {
    const { container } = render(<GameTable frame={localFrame()} observeSeat={0} />);
    const p1 = container.querySelector(`[data-seat="1"]`)!;
    // 吃 9,10,11 → 1筒 2筒 3筒(以单串渲染,用子串匹配)
    expect(within(p1 as HTMLElement).getByText(/1筒/)).toBeTruthy();
    expect(within(p1 as HTMLElement).getByText(/2筒/)).toBeTruthy();
    expect(within(p1 as HTMLElement).getByText(/3筒/)).toBeTruthy();
    // 牌河 東
    const p1River = within(p1 as HTMLElement).getAllByTestId("river")[0];
    expect(within(p1River).getByText("東")).toBeTruthy();
    // P2 白板手牌
    const p2 = container.querySelector(`[data-seat="2"]`)!;
    expect(within(p2 as HTMLElement).getByText("白·神")).toBeTruthy();
  });
});

describe("GameTable 线上自家视角", () => {
  it("他家暗手未知占位,本家手牌可见", () => {
    render(<GameTable frame={onlineFrame()} observeSeat={0} />);
    expect(screen.getAllByText("未知").length).toBe(3);
    const mySeat = Array.from(document.querySelectorAll(".seat")).find(
      (el) => el.getAttribute("data-seat") === "0",
    )!;
    // my_hand[1]=1 → 1 张 value=1 = "2万"
    expect(within(mySeat as HTMLElement).getByText("2万")).toBeTruthy();
  });

  it("线上视角即便切观察座位也看不到他家暗手", () => {
    render(<GameTable frame={onlineFrame()} observeSeat={2} />);
    const p2 = Array.from(document.querySelectorAll(".seat")).find(
      (el) => el.getAttribute("data-seat") === "2",
    )!;
    expect(within(p2 as HTMLElement).getByText("未知")).toBeTruthy();
  });
});