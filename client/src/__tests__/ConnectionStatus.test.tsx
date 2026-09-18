import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ConnectionStatus } from "../components/ConnectionStatus";
import { useConnectionStore } from "../service/connectionStore";

function renderWith(status: string) {
  useConnectionStore.setState({ status: status as any });
  render(<ConnectionStatus />);
}

describe("ConnectionStatus 组件", () => {
  it("connected → 显示 已连接", () => {
    renderWith("connected");
    expect(screen.getByText("已连接")).toBeTruthy();
  });

  it("connecting → 显示 连接中", () => {
    renderWith("connecting");
    expect(screen.getByText("连接中")).toBeTruthy();
  });

  it("reconnecting → 显示 重连中(带尝试次数)", () => {
    useConnectionStore.setState({ status: "reconnecting", attempts: 3 });
    render(<ConnectionStatus />);
    expect(screen.getByText("重连中(3)")).toBeTruthy();
  });

  it("disconnected → 显示 已断开", () => {
    renderWith("disconnected");
    expect(screen.getByText("已断开")).toBeTruthy();
  });
});