import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_TIMEOUT_MS,
  NETWORK_ERROR_MESSAGE,
  fetchJson,
  setApiBase,
} from "../service/http";

/** 直接测真实 helper(不 mock 模块),只替换全局 fetch。 */
const fetchMock = vi.fn();

function jsonResponse(
  body: unknown,
  init: { ok?: boolean; status?: number } = {},
): Response {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    text: () => Promise.resolve(text),
  } as unknown as Response;
}

function lastInit(): RequestInit {
  const calls = fetchMock.mock.calls;
  return (calls[calls.length - 1]?.[1] ?? {}) as RequestInit;
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  setApiBase("http://127.0.0.1:17320");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("fetchJson", () => {
  it("GET 成功时解析 JSON,默认方法为 GET 且不带 body/Content-Type", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ server: "https://example.com" }));

    await expect(fetchJson("/api/settings")).resolves.toEqual({
      server: "https://example.com",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:17320/api/settings");
    const init = lastInit();
    expect(init.method).toBe("GET");
    expect(init.body).toBeUndefined();
    expect(init.headers).toBeUndefined();
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("带 body 时序列化 JSON 并设置 Content-Type", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));

    await fetchJson("/api/settings", { method: "PUT", body: { server: "s" } });

    const init = lastInit();
    expect(init.method).toBe("PUT");
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    expect(init.body).toBe(JSON.stringify({ server: "s" }));
  });

  it("base 末尾斜杠会被归一化", async () => {
    setApiBase("http://localhost:1234/");
    fetchMock.mockResolvedValue(jsonResponse({}));

    await fetchJson("/api/models");

    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:1234/api/models");
  });

  it("保留后端结构化错误 message", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ message: "ONNX 动作空间契约不匹配" }, { ok: false, status: 400 }),
    );

    await expect(fetchJson("/api/models/import", { method: "POST", body: {} }))
      .rejects.toThrow("ONNX 动作空间契约不匹配");
  });

  it("非 JSON 错误体回退到状态码", async () => {
    fetchMock.mockResolvedValue(jsonResponse("boom", { ok: false, status: 500 }));

    await expect(fetchJson("/api/sessions")).rejects.toThrow("500");
  });

  it("连接被拒等网络失败统一为中文提示", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(fetchJson("/api/sessions"))
      .rejects.toThrow(NETWORK_ERROR_MESSAGE);
  });

  it("读体阶段失败也归为网络错误", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      text: () => Promise.reject(new TypeError("terminated")),
    } as unknown as Response);

    await expect(fetchJson("/api/records/local"))
      .rejects.toThrow(NETWORK_ERROR_MESSAGE);
  });

  it("默认超时会中断请求并抛中文网络错误", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => {
        const abortError = new Error("The operation was aborted.");
        abortError.name = "AbortError";
        reject(abortError);
      });
    }));

    const pending = fetchJson("/api/sessions");
    const rejection = expect(pending).rejects.toThrow(NETWORK_ERROR_MESSAGE);

    expect(lastInit().signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS);
    await rejection;
    expect(lastInit().signal?.aborted).toBe(true);
  });

  it("可用 timeoutMs 覆盖默认超时", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
    }));

    const pending = fetchJson("/api/sessions", { timeoutMs: 50 });
    const rejection = expect(pending).rejects.toThrow(NETWORK_ERROR_MESSAGE);

    await vi.advanceTimersByTimeAsync(50);
    await rejection;
  });

  it("timeoutMs <= 0 时不设超时也不挂定时器", async () => {
    vi.useFakeTimers();
    fetchMock.mockReturnValue(new Promise<Response>(() => {}));

    void fetchJson("/api/never", { timeoutMs: 0 }).catch(() => undefined);

    expect(lastInit().signal).toBeUndefined();
    expect(vi.getTimerCount()).toBe(0);
  });
});
