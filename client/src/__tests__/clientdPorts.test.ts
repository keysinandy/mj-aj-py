import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  PORTS_EVENT,
  PORTS_GLOBAL,
  applyClientdPorts,
  initClientdPorts,
  parseClientdPorts,
  resetClientdPortsState,
} from "../service/clientdPorts";
import { useConnectionStore, setConnectionFactory } from "../service/connectionStore";
import { setApiBase } from "../service/http";
import { api } from "../service/http";
import { FakeTransport } from "./FakeTransport";

const DEFAULT_HTTP_BASE = "http://127.0.0.1:17320";

describe("clientd dynamic port discovery bridge", () => {
  beforeEach(() => {
    resetClientdPortsState();
    setConnectionFactory(() => new FakeTransport());
    delete (window as unknown as Record<string, unknown>)[PORTS_GLOBAL];
  });

  afterEach(() => {
    useConnectionStore.getState().stop();
    useConnectionStore.setState({ status: "disconnected" });
    setApiBase(DEFAULT_HTTP_BASE);
    resetClientdPortsState();
    delete (window as unknown as Record<string, unknown>)[PORTS_GLOBAL];
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("解析注入端口并构出 HTTP/WS 端点", () => {
    expect(parseClientdPorts({ http: 18000, ws: 18001 })).toEqual({
      http: "http://127.0.0.1:18000",
      ws: "ws://127.0.0.1:18001/ws",
    });
    expect(parseClientdPorts({ http: 18000, ws: 18001, wsPath: "/data" })!.ws).toBe(
      "ws://127.0.0.1:18001/data",
    );
  });

  it("拒绝竞态下的 0 端口与非法字段", () => {
    // ws=0 正是 clientd 启动竞态会写出的值,壳与前端都必须拒绝。
    expect(parseClientdPorts({ http: 18000, ws: 0 })).toBeNull();
    expect(parseClientdPorts({ http: 0, ws: 18001 })).toBeNull();
    expect(parseClientdPorts({ http: 18000, ws: 18000 })).toBeNull();
    expect(parseClientdPorts({ http: 70000, ws: 18001 })).toBeNull();
    expect(parseClientdPorts({ http: "x", ws: 18001 })).toBeNull();
    expect(parseClientdPorts({ http: 18000 })).toBeNull();
    expect(parseClientdPorts({})).toBeNull();
    expect(parseClientdPorts(null)).toBeNull();
  });

  it("同一端点只应用一次(幂等)", () => {
    const applied: unknown[] = [];
    const spy = (endpoint: unknown) => applied.push(endpoint);

    expect(applyClientdPorts({ http: 18100, ws: 18101 }, spy)).not.toBeNull();
    expect(applyClientdPorts({ http: 18100, ws: 18101 }, spy)).toBeNull();
    expect(applied).toHaveLength(1);

    // 重置后可重新应用(模拟壳重启/新实例)。
    resetClientdPortsState();
    expect(applyClientdPorts({ http: 18100, ws: 18101 }, spy)).not.toBeNull();
    expect(applied).toHaveLength(2);

    // 非法端口不触发应用。
    expect(applyClientdPorts({ http: 18100, ws: 0 }, spy)).toBeNull();
    expect(applied).toHaveLength(2);
  });

  it("默认应用会更新 HTTP base(经 fetch 可观测)", async () => {
    const fetchMock = vi.fn(async (_url: string) => ({
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({
          server: "s",
          tokens: { tournament: "", match: "", test_room: [] },
          selected_model: null,
        }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    applyClientdPorts({ http: 18111, ws: 18112 });
    await api.getSettings();

    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://127.0.0.1:18111/api/settings",
    );
  });

  it("连接已运行时切换端点会重启状态机", () => {
    useConnectionStore.setState({ status: "connected" });
    const store = useConnectionStore.getState();
    const stop = vi.spyOn(store, "stop").mockImplementation(() => {});
    const start = vi.spyOn(store, "start").mockImplementation(() => {});

    applyClientdPorts({ http: 18211, ws: 18212 });

    expect(stop).toHaveBeenCalledTimes(1);
    expect(start).toHaveBeenCalledTimes(1);
  });

  it("未连接时不重启状态机", () => {
    useConnectionStore.setState({ status: "disconnected" });
    const store = useConnectionStore.getState();
    const stop = vi.spyOn(store, "stop").mockImplementation(() => {});
    const start = vi.spyOn(store, "start").mockImplementation(() => {});

    applyClientdPorts({ http: 18311, ws: 18312 });

    expect(stop).not.toHaveBeenCalled();
    expect(start).not.toHaveBeenCalled();
  });

  it("init 读取已注入全局并响应后续事件,解绑后不再响应", () => {
    const seen: unknown[] = [];
    (window as unknown as Record<string, unknown>)[PORTS_GLOBAL] = {
      http: 19001,
      ws: 19002,
    };

    const off = initClientdPorts(window, (raw) => seen.push(raw));
    expect(seen).toEqual([{ http: 19001, ws: 19002 }]);

    window.dispatchEvent(
      new CustomEvent(PORTS_EVENT, { detail: { http: 19003, ws: 19004 } }),
    );
    expect(seen).toHaveLength(2);

    off();
    window.dispatchEvent(
      new CustomEvent(PORTS_EVENT, { detail: { http: 19005, ws: 19006 } }),
    );
    expect(seen).toHaveLength(2);
  });
});
