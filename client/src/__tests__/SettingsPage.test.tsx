import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SettingsPage } from "../pages/SettingsPage";

const mocks = vi.hoisted(() => ({
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
  testConnection: vi.fn(),
  listModels: vi.fn(),
  importModel: vi.fn(),
  selectModel: vi.fn(),
  deleteModel: vi.fn(),
}));

vi.mock("../service/http", () => ({
  api: {
    ...mocks,
  },
}));

const initialSettings = {
  server: "https://mahjong.example.com",
  tokens: {
    tournament: "tournament-token",
    match: "match-token",
    test_room: ["room-token-1", "room-token-2"],
  },
  selected_model: "current.onnx",
};

const currentModel = {
  name: "current.onnx",
  size: 1024,
  contract: { planes: 75, scalars: 8, actions: 109 },
  selected: true,
};

describe("SettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getSettings.mockResolvedValue(initialSettings);
    mocks.saveSettings.mockImplementation((value) => Promise.resolve({
      ...initialSettings,
      ...value,
    }));
    mocks.listModels.mockResolvedValue({ models: [currentModel] });
    mocks.testConnection.mockResolvedValue({
      ok: true, status: 200, mode: "match", code: "OK", message: "连接成功",
    });
    mocks.selectModel.mockResolvedValue({ ...currentModel });
    mocks.deleteModel.mockResolvedValue({ name: currentModel.name, deleted: true });
  });

  it("读取并保存服务器与三类令牌设置", async () => {
    render(<SettingsPage />);
    expect(await screen.findByDisplayValue("https://mahjong.example.com")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("服务器地址"), {
      target: { value: "https://new.example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存平台设置" }));

    await waitFor(() => expect(mocks.saveSettings).toHaveBeenCalledWith({
      server: "https://new.example.com",
      tokens: initialSettings.tokens,
    }));
    expect(await screen.findByText("平台连接设置已保存，下一次会话立即生效")).toBeTruthy();
  });

  it("坏模型导入提示原因且不改变当前选择", async () => {
    mocks.importModel.mockRejectedValue(new Error("ONNX 动作空间契约不匹配"));
    render(<SettingsPage />);
    expect(await screen.findByText("current.onnx")).toBeTruthy();

    const file = new File(["not-an-onnx"], "broken.onnx", { type: "application/onnx" });
    if (!("arrayBuffer" in file)) {
      Object.defineProperty(file, "arrayBuffer", {
        value: async () => new TextEncoder().encode("not-an-onnx").buffer,
      });
    }
    fireEvent.change(screen.getByLabelText("导入 ONNX 模型"), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: "导入模型" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("ONNX 动作空间契约不匹配");
    expect(screen.getByText("current.onnx")).toBeTruthy();
    expect(screen.queryByText("broken.onnx")).toBeNull();
  });
});
