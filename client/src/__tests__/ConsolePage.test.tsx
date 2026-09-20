import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ConsolePage } from "../pages/ConsolePage";
import type { SessionInfo } from "../service/http";

const finished: SessionInfo = {
  id: "abc123",
  kind: "arena",
  status: "finished",
  config: { n_games: 2, concurrency: 2 },
  created_at: 1,
  finished_at: 2,
  result: { batch_id: "batch_test", completed: 2, skipped: 0 },
  error: null,
  progress: { done: 2, total: 2, rate: 1, last_index: 1 },
};

vi.mock("../service/http", () => ({
  api: {
    createSession: vi.fn(() =>
      Promise.resolve({ ...finished, id: "new", status: "running" })),
    listSessions: vi.fn(() => Promise.resolve({ sessions: [finished] })),
    getSession: vi.fn(),
    stopSession: vi.fn(() => Promise.resolve(finished)),
  },
}));

import { api } from "../service/http";

function renderPage() {
  return render(
    <MemoryRouter>
      <ConsolePage />
    </MemoryRouter>,
  );
}

describe("ConsolePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listSessions).mockResolvedValue({ sessions: [finished] });
  });

  it("渲染表单与已有会话、已完成会话出现回放链接", async () => {
    renderPage();
    expect(await screen.findByText(/开始本地对战/)).toBeTruthy();
    expect(screen.getByText(/batch_test/)).toBeTruthy();
    expect(screen.getAllByRole("link", { name: /打开回放/ })).toHaveLength(1);
  });

  it("点击开始用主位/对手策略构造座位并创建会话", async () => {
    renderPage();
    await screen.findByText(/开始本地对战/);
    fireEvent.click(screen.getByText(/开始本地对战/));
    await screen.findByText(/启动中/); // 异步
    await vi.waitFor(() =>
      expect(api.createSession).toHaveBeenCalledTimes(1),
    );
    const [kind, config] = vi.mocked(api.createSession).mock.calls[0] as [string, any];
    expect(kind).toBe("arena");
    expect(config.n_games).toBe(16);
    expect(config.seats).toHaveLength(4);
    expect(config.seats[0].strategy).toBe("bot");
    expect(config.seats[0].evaluator).toBe("legacy");
  });

  it("切换线上匹配后创建 match 会话且不把令牌放入配置", async () => {
    renderPage();
    await screen.findByText(/开始本地对战/);
    fireEvent.click(screen.getByRole("tab", { name: "线上匹配" }));
    fireEvent.click(screen.getByRole("button", { name: "开始线上匹配" }));
    await vi.waitFor(() =>
      expect(api.createSession).toHaveBeenCalledTimes(1),
    );
    const [kind, config] = vi.mocked(api.createSession).mock.calls[0] as [string, any];
    expect(kind).toBe("match");
    expect(config.max_games).toBe(10);
    expect(config.strategy).toBe("bot");
    expect(config.evaluator).toBe("legacy");
    expect(config).not.toHaveProperty("token");
    expect(config).not.toHaveProperty("match_token");
  });
});
