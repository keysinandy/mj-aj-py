/**
 * 连接状态机:connecting / connected / reconnecting / disconnected。
 *
 * - start():创建 transport 并 open,首个成功连接前为 connecting;
 * - 服务崩溃:transport 抛 disconnected → BackoffReconnect 指数退避重连,
 *   重连成功回 connected;期间状态为 reconnecting,attempts 逐次递增;
 * - stop():用户主动停止会话 → 置 disconnected 并停止重连,应用/服务保持存活。
 *
 * `_transport/_reconnect/_cleanup` 为内部编排字段,underscore 前缀,不参与公共语义。
 */

import { create } from "zustand";
import {
  type Transport,
  type TransportFactory,
  BackoffReconnect,
  defaultScheduler,
  wsTransport,
} from "./transport";
import { WS_ENDPOINT } from "./config";

export type ConnStatus =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected";

let liveFactory: TransportFactory = () => wsTransport(WS_ENDPOINT);

/** 测试 / 换端口时注入传输工厂。 */
export function setConnectionFactory(f: TransportFactory): void {
  liveFactory = f;
}

/**
 * 设置连接端点。桌面形态由 Tauri 侧用 sidecar 实际端口(clientd 发现文件
 * local/clientd.ports.json)调用;Web 开发态默认直连固定端口。
 */
export function setConnectionEndpoint(url: string): void {
  liveFactory = () => wsTransport(url);
}

interface ConnectionStore {
  status: ConnStatus;
  attempts: number;
  error: string | null;
  start: () => void;
  stop: () => void;
  _transport: Transport | null;
  _reconnect: BackoffReconnect | null;
  _cleanup: (() => void) | null;
}

export const useConnectionStore = create<ConnectionStore>((set, get) => ({
  status: "disconnected",
  attempts: 0,
  error: null,
  _transport: null,
  _reconnect: null,
  _cleanup: null,

  start: () => {
    if (get()._transport) return; // 已在运行
    if (get().status === "connecting" || get().status === "reconnecting") return;

    const transport = liveFactory();
    const sandbox = {
      setTimeout: window.setTimeout.bind(window),
      clearTimeout: window.clearTimeout.bind(window),
    };
    const reconnect = new BackoffReconnect(
      // 重试:回到 connecting 并重新 open(attempts 保留当前重连序号)
      () => {
        set({ status: "connecting", error: null });
        transport.open();
      },
      defaultScheduler(sandbox),
    );
    reconnect.start();

    const offConnecting = transport.on("connecting", () =>
      set({ status: "connecting", error: null }),
    );
    const offConnected = transport.on("connected", () => {
      reconnect.notifyStatus("connected");
      set({ status: "connected", attempts: 0, error: null });
    });
    const offDisconnected = transport.on("disconnected", () => {
      reconnect.notifyStatus("disconnected");
      set({ status: "reconnecting", attempts: reconnect.attempts });
    });
    const cleanup = () => {
      offConnecting();
      offConnected();
      offDisconnected();
    };

    transport.open();
    set({
      status: "connecting",
      error: null,
      _transport: transport,
      _reconnect: reconnect,
      _cleanup: cleanup,
    });
  },

  stop: () => {
    const { _transport, _reconnect, _cleanup } = get();
    _reconnect?.stop();
    _cleanup?.();
    if (_transport) _transport.close();
    set({
      status: "disconnected",
      attempts: 0,
      error: null,
      _transport: null,
      _reconnect: null,
      _cleanup: null,
    });
  },
}));