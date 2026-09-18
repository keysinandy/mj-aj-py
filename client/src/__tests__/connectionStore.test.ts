import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { useConnectionStore, setConnectionFactory } from "../service/connectionStore";
import { FakeTransport } from "./FakeTransport";

function currentFake(): FakeTransport | null {
  const st = useConnectionStore.getState() as any;
  return st._transport as FakeTransport;
}

describe("connection state machine", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setConnectionFactory(() => new FakeTransport());
  });

  afterEach(() => {
    useConnectionStore.getState().stop();
    vi.useRealTimers();
  });

  it("start() → connecting, then transport connected → connected", () => {
    useConnectionStore.getState().start();
    expect(useConnectionStore.getState().status).toBe("connecting");

    currentFake()!.emit("connected");
    expect(useConnectionStore.getState().status).toBe("connected");
    expect(useConnectionStore.getState().attempts).toBe(0);
  });

  it("服务崩溃(disconnected) → reconnecting,指数退避后自动恢复 connected", () => {
    const store = useConnectionStore.getState();
    store.start();
    currentFake()!.emit("connected");
    expect(useConnectionStore.getState().status).toBe("connected");

    // 模拟服务进程被强杀
    currentFake()!.emit("disconnected");
    expect(useConnectionStore.getState().status).toBe("reconnecting");
    expect(useConnectionStore.getState().attempts).toBe(1);

    // 到达退避时刻 → 自动 open
    vi.advanceTimersByTime(250);
    expect(useConnectionStore.getState().status).toBe("connecting");
    expect(currentFake()!.opened).toBeGreaterThanOrEqual(2);

    // 重连成功
    currentFake()!.emit("connected");
    expect(useConnectionStore.getState().status).toBe("connected");
    expect(useConnectionStore.getState().attempts).toBe(0);
  });

  it("连续失败退避递增;成功后归零", () => {
    const store = useConnectionStore.getState();
    store.start();
    currentFake()!.emit("connected");

    currentFake()!.emit("disconnected");
    expect(useConnectionStore.getState().attempts).toBe(1);
    vi.advanceTimersByTime(250);
    currentFake()!.emit("connected");
    expect(useConnectionStore.getState().attempts).toBe(0);

    // 连续两次失败(之间未成功):退避应递增
    currentFake()!.emit("disconnected");
    expect(useConnectionStore.getState().attempts).toBe(1);
    vi.advanceTimersByTime(250);
    currentFake()!.emit("disconnected"); // 重试中又失败
    expect(useConnectionStore.getState().attempts).toBe(2);

    // 成功连接后归零
    currentFake()!.emit("connected");
    expect(useConnectionStore.getState().attempts).toBe(0);
  });

  it("stop() → disconnected,不再自动重连", () => {
    const store = useConnectionStore.getState();
    store.start();
    currentFake()!.emit("connected");

    useConnectionStore.getState().stop();
    expect(useConnectionStore.getState().status).toBe("disconnected");
    expect(currentFake()).toBeNull();

    // 即便再触发 disconnect,也没有 transport 可调度
    vi.advanceTimersByTime(5000);
    expect(useConnectionStore.getState().status).toBe("disconnected");
  });

  it("start() 幂等:已在运行时不重复创建 transport", () => {
    const store = useConnectionStore.getState();
    store.start();
    const first = currentFake();
    store.start();
    expect(currentFake()).toBe(first);
  });
});