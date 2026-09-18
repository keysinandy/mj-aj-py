/**
 * 本地服务传输层(dingding 侧 sidecar)。
 *
 * 封装到 localhost clientd WS 的建立/断开,并提供"崩溃自愈"信号。
 * 真实桌面形态由 Tauri spawn Python sidecar;Web 开发态直连 WS。
 * 测试注入 FakeTransport 驱动状态机,不触真实网络。
 */

export type TransportEvent = "connecting" | "connected" | "disconnected";

export interface Transport {
  /** 建立连接;幂等,重复调用无副作用。 */
  open(): void;
  /** 主动关闭(用户停止会话等);会触发 disconnected,但连接层不得自动重连。 */
  close(): void;
  /** 订阅事件。返回解绑函数。 */
  on(event: TransportEvent, cb: () => void): () => void;
  /** 是否处于已连接。 */
  get isConnected(): boolean;
}

export type TransportFactory = () => Transport;

/** WebSocket 直连(Web 开发态 / 无 Tauri 时)。 */
export function wsTransport(url: string): Transport {
  let ws: WebSocket | null = null;
  let connecting = false;
  const cbs: Record<TransportEvent, Array<() => void>> = {
    connecting: [],
    connected: [],
    disconnected: [],
  };
  const emit = (e: TransportEvent) => cbs[e].forEach((f) => f());

  function teardownKeepalive() {
    connecting = false;
    ws = null;
  }

  return {
    open(): void {
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
        return;
      }
      connecting = true;
      emit("connecting");
      try {
        ws = new WebSocket(url);
      } catch {
        connecting = false;
        emit("disconnected");
        return;
      }
      ws.onopen = () => {
        connecting = false;
        emit("connected");
      };
      ws.onclose = () => {
        teardownKeepalive();
        emit("disconnected");
      };
      ws.onerror = () => {
        if (connecting) {
          connecting = false;
          emit("disconnected");
        }
      };
    },
    close(): void {
      teardownKeepalive();
      if (ws) {
        const w = ws;
        ws = null;
        w.onopen = null;
        w.onclose = null;
        w.onerror = null;
        try {
          w.close();
        } catch {
          /* ignore */
        }
      }
      emit("disconnected");
    },
    on(event, cb): () => void {
      cbs[event].push(cb);
      return () => {
        const i = cbs[event].indexOf(cb);
        if (i >= 0) cbs[event].splice(i, 1);
      };
    },
    get isConnected(): boolean {
      return !!ws && ws.readyState === WebSocket.OPEN;
    },
  };
}

/** Tauri sidecar:进程 spawn + 崩溃重启由 Tauri 侧负责,这里仅占位。 */
export function tauriTransport(): Transport {
  throw new Error(
    "tauriTransport 需在 Tauri 运行时初始化;开发/测试请用 wsTransport 或 FakeTransport",
  );
}

export * from "./reconnect";