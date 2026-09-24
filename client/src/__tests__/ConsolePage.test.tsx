import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

const running: SessionInfo = {
  ...finished,
  id: "run1",
  status: "running",
  result: null,
  finished_at: null,
};

vi.mock("../service/http", () => ({
  api: {
    createSession: vi.fn(),
    listSessions: vi.fn(),
    getSession: vi.fn(),
    stopSession: vi.fn(),
  },
}));

import { api } from "../service/http";

let sessions: SessionInfo[] = [];

function renderPage() {
  return render(
    <MemoryRouter>
      <ConsolePage />
    </MemoryRouter>,
  );
}

/** 等待一次"点击 → 创建会话 → 刷新列表"的异步流程彻底落地。 */
async function waitForStartSettled(name: string | RegExp) {
  await waitFor(() =>
    expect(screen.getByRole("button", { name })).toBeEnabled(),
  );
}

describe("ConsolePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessions = [finished];
    vi.mocked(api.listSessions).mockImplementation(() =>
      Promise.resolve({ sessions }));
    vi.mocked(api.createSession).mockImplementation((kind: string, config: any) => {
      const created: SessionInfo = {
        ...finished,
        id: "new",
        kind,
        status: "finished",
        result: null,
        finished_at: null,
        config,
      };
      sessions = [created, ...sessions];
      return Promise.resolve(created);
    });
    vi.mocked(api.stopSession).mockResolvedValue(finished);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("渲染表单与已有会话,已完成会话出现回放链接", async () => {
    renderPage();
    expect(await screen.findByText(/开始本地对战/)).toBeTruthy();
    expect(screen.getAllByRole("option", { name: "legacy-v2 启发式" })).toHaveLength(2);
    expect(await screen.findByText(/batch_test/)).toBeTruthy();
    expect(screen.getAllByRole("link", { name: /打开回放/ })).toHaveLength(1);
  });

  it("点击开始用主位/对手策略构造座位并创建会话", async () => {
    renderPage();
    await screen.findByText(/开始本地对战/);
    fireEvent.change(screen.getByLabelText("对手策略"), { target: { value: "legacy-v2" } });
    fireEvent.click(screen.getByText(/开始本地对战/));
    await waitForStartSettled(/开始本地对战/);

    expect(api.createSession).toHaveBeenCalledTimes(1);
    const [kind, config] = vi.mocked(api.createSession).mock.calls[0] as [string, any];
    expect(kind).toBe("arena");
    expect(config.n_games).toBe(16);
    expect(config.seats).toHaveLength(4);
    expect(config.seats[0].strategy).toBe("bot");
    expect(config.seats[0].evaluator).toBe("legacy-v2");
    expect(config.seats[1].evaluator).toBe("legacy-v2");
  });

  it("切换线上匹配后创建 match 会话且不把令牌放入配置", async () => {
    renderPage();
    await screen.findByText(/开始本地对战/);
    fireEvent.click(screen.getByRole("tab", { name: "线上匹配" }));
    expect(screen.getByRole("option", { name: "legacy-v2 启发式" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "开始线上匹配" }));
    await waitForStartSettled("开始线上匹配");

    expect(api.createSession).toHaveBeenCalledTimes(1);
    const [kind, config] = vi.mocked(api.createSession).mock.calls[0] as [string, any];
    expect(kind).toBe("match");
    expect(config.max_games).toBe(10);
    expect(config.strategy).toBe("bot");
    expect(config.evaluator).toBe("legacy-v2");
    expect(config).not.toHaveProperty("token");
    expect(config).not.toHaveProperty("match_token");
  });

  it("会话列表读取失败时显示可重试错误,重试成功后恢复", async () => {
    vi.mocked(api.listSessions).mockRejectedValueOnce(
      new Error("无法连接本地服务，请确认客户端后台已启动"),
    );
    renderPage();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("无法连接本地服务，请确认客户端后台已启动");
    expect(screen.queryByText("暂无会话。选择本地竞技场或线上匹配开始对战。")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    expect(await screen.findByText("#abc123")).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("运行中会话按单个 1 秒周期轮询,不重复请求", async () => {
    vi.useFakeTimers();
    sessions = [running];
    const view = renderPage();
    try {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(screen.getByText("#run1")).toBeTruthy();

      const afterMount = vi.mocked(api.listSessions).mock.calls.length;

      // 一个 tick 只应产生一次列表请求(旧实现是两条 1s interval 各发一次)。
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(vi.mocked(api.listSessions).mock.calls.length).toBe(afterMount + 1);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000);
      });
      expect(vi.mocked(api.listSessions).mock.calls.length).toBe(afterMount + 4);
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it("没有运行中会话时不轮询", async () => {
    vi.useFakeTimers();
    const view = renderPage();
    try {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      const afterMount = vi.mocked(api.listSessions).mock.calls.length;

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });
      expect(vi.mocked(api.listSessions).mock.calls.length).toBe(afterMount);
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });
});
