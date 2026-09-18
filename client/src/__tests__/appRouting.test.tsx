import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { useConnectionStore, setConnectionFactory } from "../service/connectionStore";
import { FakeTransport } from "./FakeTransport";

describe("App 路由骨架", () => {
  beforeEach(() => {
    useConnectionStore.setState({
      status: "disconnected", attempts: 0, error: null,
      _transport: null, _reconnect: null, _cleanup: null,
    });
    setConnectionFactory(() => new FakeTransport());
  });
  afterEach(() => {
    useConnectionStore.getState().stop();
    vi.clearAllTimers();
  });

  it("导航含 控制台/房间/回放/设置,默认重定向到控制台", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByText("控制台")).toBeTruthy();
    expect(screen.getByText("房间")).toBeTruthy();
    expect(screen.getByText("回放")).toBeTruthy();
    expect(screen.getByText("设置")).toBeTruthy();
    // 默认重定向到 /console
    expect(screen.getByText("对战控制台")).toBeTruthy();
  });

  it("挂载即触发连接,并在页面显示已连接", () => {
    useConnectionStore.getState().start();
    const t = useConnectionStore.getState() as any;
    const fake = t._transport as FakeTransport;
    fake.emit("connected");

    render(
      <MemoryRouter initialEntries={["/rooms"]}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByText("已连接")).toBeTruthy();
    expect(screen.getAllByText("房间").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("heading", { name: "房间" })).toBeTruthy();
  });

  it("未知路由回退到控制台", () => {
    render(
      <MemoryRouter initialEntries={["/nope"]}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByText("对战控制台")).toBeTruthy();
  });
});