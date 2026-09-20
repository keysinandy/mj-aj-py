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

  it("默认玩家视角只显示观察座位手牌,但保留四家牌河与副露", () => {
    const { container } = render(<GameTable frame={localFrame()} observeSeat={0} />);
    const p1 = container.querySelector(`[data-seat="1"]`)!;
    expect(within(p1 as HTMLElement).getByText("未知")).toBeTruthy();
    // 副露与牌河仍属于公共信息。
    const p1Meld = within(p1 as HTMLElement).getByTestId("melds");
    expect(within(p1Meld).getByRole("img", { name: "1筒" })).toBeTruthy();
    expect(within(p1Meld).getByRole("img", { name: "2筒" })).toBeTruthy();
    expect(within(p1Meld).getByRole("img", { name: "3筒" })).toBeTruthy();
    // 牌河 東
    const p1River = within(p1 as HTMLElement).getAllByTestId("river")[0];
    expect(within(p1River).getByRole("img", { name: "東" })).toBeTruthy();
    expect(within(p1River).queryByText("東")).toBeNull();
    // P2 白板手牌
    const p2 = container.querySelector(`[data-seat="2"]`)!;
    expect(within(p2 as HTMLElement).getByText("未知")).toBeTruthy();
  });

  it("本地全知开关显示四家手牌", () => {
    const frame = localFrame();
    const sideHand = Array.from({ length: 34 }, () => 0);
    sideHand[0] = 14;
    frame.hands![1] = [...sideHand];
    frame.hands![3] = [...sideHand];
    const { container } = render(
      <GameTable frame={frame} observeSeat={0} visibilityMode="omniscient" />,
    );
    const p1 = container.querySelector(`[data-seat="1"]`)!;
    const p3 = container.querySelector(`[data-seat="3"]`)!;
    expect(within(p1 as HTMLElement).getByTestId("hand-tiles").querySelectorAll('[role="img"]')).toHaveLength(14);
    expect(within(p3 as HTMLElement).getByTestId("hand-tiles").querySelectorAll('[role="img"]')).toHaveLength(14);
    const p2 = container.querySelector(`[data-seat="2"]`)!;
    expect(within(p2 as HTMLElement).getByRole("img", { name: "白·神" })).toHaveClass("tile-svg-cai");
  });

  it("切换观察座位后默认显示该座位手牌", () => {
    const { container } = render(<GameTable frame={localFrame()} observeSeat={1} />);
    const p1 = container.querySelector(`[data-seat="1"]`)!;
    const p0 = container.querySelector(`[data-seat="0"]`)!;
    expect(within(within(p1 as HTMLElement).getByTestId("hand-tiles")).getByRole("img", { name: "東" })).toBeTruthy();
    expect(within(p0 as HTMLElement).getByText("未知")).toBeTruthy();
  });

  it("按观察座位旋转桌面,并同时显示庄家与当前轮次", () => {
    const { container } = render(<GameTable frame={localFrame({
      dealer: 2,
      current: { seat: 1, phase: "playing" },
    })} observeSeat={1} />);
    const table = container.querySelector(".game-table")!;
    expect(table.getAttribute("data-perspective-seat")).toBe("1");
    const dealer = container.querySelector('[data-testid="dealer-badge-2"]');
    expect(dealer).toBeTruthy();
    const turn = container.querySelector('[data-seat="1"]');
    expect(turn?.classList.contains("seat-turn")).toBe(true);
    expect(turn?.getAttribute("data-position")).toBe("bottom");
    expect(container.querySelector('[data-seat="2"]')?.getAttribute("data-position")).toBe("right");
  });

  it("在座位前标注庄家与主 BOT", () => {
    const { container } = render(<GameTable frame={localFrame({ dealer: 0 })} observeSeat={0} />);
    const p0 = container.querySelector('[data-seat="0"]')!;
    expect(within(p0 as HTMLElement).getByTestId("dealer-badge-0")).toHaveTextContent("（庄）");
    expect(within(p0 as HTMLElement).getByText("（主）座位 P0")).toBeTruthy();
    const p2 = container.querySelector('[data-seat="2"]')!;
    expect(within(p2 as HTMLElement).getByText("座位 P2")).toBeTruthy();
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
    expect(within(mySeat as HTMLElement).getByRole("img", { name: "2万" })).toBeTruthy();
  });

  it("线上视角即便切观察座位也看不到他家暗手", () => {
    render(<GameTable frame={onlineFrame()} observeSeat={2} visibilityMode="omniscient" />);
    const p2 = Array.from(document.querySelectorAll(".seat")).find(
      (el) => el.getAttribute("data-seat") === "2",
    )!;
    expect(within(p2 as HTMLElement).getByText("未知")).toBeTruthy();
  });
});
