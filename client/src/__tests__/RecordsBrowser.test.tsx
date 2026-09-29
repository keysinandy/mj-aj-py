import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { RecordsBrowser } from "../components/RecordsBrowser";
import { api, type BatchSummary } from "../service/http";

vi.mock("../service/http", () => ({
  api: {
    localBatches: () =>
      Promise.resolve({
        batches: [
          {
            batch_id: "b1",
            batch_dir: "local/arena/b1",
            seed0: 7,
            status: "finished",
            n_games: 2,
            completed: 2,
            skipped: 0,
            stats: { win_rate: 0.25 },
            game_paths: [
              "local/arena/b1/game_000000.json",
              "local/arena/b1/game_000001.json",
            ],
          } as BatchSummary,
        ],
      }),
    onlineGames: vi.fn(() =>
      Promise.resolve({
        games: [
          {
            date: "2026-09-18", gid: "g42", path: "x/g42.jsonl", name: "tok_g42",
            strategy: "bot", evaluator: "shape-v2",
          },
        ],
      })),
    localFrames: vi.fn(() =>
      Promise.resolve({ gid: null, path: "p", n_frames: 2, frames: [] }),
    ),
  },
}));

describe("RecordsBrowser", () => {
  beforeEach(() => vi.clearAllMocks());

  it("两级浏览:展开批次看到单局并触发打开回调", async () => {
    const onOpenLocal = vi.fn();
    render(<RecordsBrowser onOpenLocal={onOpenLocal} onOpenOnline={vi.fn()} />);
    await screen.findByText(/b1/);
    fireEvent.click(screen.getByText(/b1/));
    const openBtn = await screen.findByRole("button", { name: /打开 game_000000/ });
    fireEvent.click(openBtn);
    expect(onOpenLocal).toHaveBeenCalledWith("b1", 0, expect.any(String));
  });

  it("线上日志按日期分组,点击触发打开线上回调", async () => {
    const onOpenOnline = vi.fn();
    render(<RecordsBrowser onOpenLocal={vi.fn()} onOpenOnline={onOpenOnline} />);
    await screen.findByText("2026-09-18");
    expect(screen.queryByRole("button", { name: /打开 tok_g42/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /2026-09-18/ }));
    expect(screen.getByText(/我方策略 · BOT（shape-v2）/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /打开 tok_g42/ }));
    expect(onOpenOnline).toHaveBeenCalledWith("g42", expect.any(String));
  });

  it("按日期时间搜索时只请求一页并带上时间范围", async () => {
    render(<RecordsBrowser onOpenLocal={vi.fn()} onOpenOnline={vi.fn()} />);
    await screen.findByText("2026-09-18");
    fireEvent.change(screen.getByLabelText("开始时间"), {
      target: { value: "2026-09-18T10:00" },
    });
    fireEvent.change(screen.getByLabelText("结束时间"), {
      target: { value: "2026-09-18T11:00" },
    });
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    await waitFor(() => expect(api.onlineGames).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 0, limit: 20, startTs: expect.any(Number), endTs: expect.any(Number) }),
    ));
  });

  it("白板筛选把命中的 round 展示在对局列表", async () => {
    vi.mocked(api.onlineGames).mockResolvedValue({
      games: [{
        date: "2026-09-19", gid: "white-game", path: "x/white-game.jsonl",
        name: "tok_white-game", whiteboard_rounds: [{
          ordinal: 4, round_no: 4, max_my_whiteboards: 3,
        }],
      }],
      has_more: false,
    });
    render(<RecordsBrowser onOpenLocal={vi.fn()} onOpenOnline={vi.fn()} />);
    await screen.findByText("2026-09-19");
    fireEvent.click(screen.getByLabelText("我方白板至少 2 张"));
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    await waitFor(() => expect(api.onlineGames).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 0, limit: 20, minWhiteboards: 2 }),
    ));
    fireEvent.click(screen.getByRole("button", { name: /2026-09-19/ }));
    expect(await screen.findByTestId("whiteboard-match")).toHaveTextContent(
      "白板≥2：第4场(3张)",
    );
  });
});
