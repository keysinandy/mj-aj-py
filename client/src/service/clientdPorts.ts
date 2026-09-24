/**
 * Tauri 壳 → 前端端口发现桥。
 *
 * 桌面壳用动态端口(0)启动 clientd,拿到实际端口后注入 webview:
 * `window.__CLIENTD_PORTS__ = {http, ws, wsPath}` 同时派发 `clientd-ports`
 * CustomEvent。本模块启动时读取已注入的端口并监听后续注入,拿到后覆盖
 * HTTP base 与 WS 端点;若连接已在运行则重启状态机以切到新端口。
 *
 * 浏览器开发态没有注入,端点保持 config.ts 的回退值。
 */

import { setApiBase } from "./http";
import { setConnectionEndpoint, useConnectionStore } from "./connectionStore";

export interface ClientdEndpoint {
  http: string;
  ws: string;
}

export const PORTS_GLOBAL = "__CLIENTD_PORTS__";
export const PORTS_EVENT = "clientd-ports";

function toPort(value: unknown): number | null {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isInteger(n) && n > 0 && n <= 65535 ? n : null;
}

/** 校验并归一化壳注入的端口;非法(含竞态下的 0 / 缺字段)返回 null。 */
export function parseClientdPorts(raw: unknown): ClientdEndpoint | null {
  if (typeof raw !== "object" || raw === null) return null;
  const value = raw as Record<string, unknown>;
  const http = toPort(value.http);
  const ws = toPort(value.ws);
  if (http === null || ws === null || http === ws) return null;
  const path =
    typeof value.wsPath === "string" && value.wsPath.startsWith("/")
      ? value.wsPath
      : "/ws";
  return { http: `http://127.0.0.1:${http}`, ws: `ws://127.0.0.1:${ws}${path}` };
}

function applyEndpoint(endpoint: ClientdEndpoint): void {
  setApiBase(endpoint.http);
  setConnectionEndpoint(endpoint.ws);
  const store = useConnectionStore.getState();
  if (store.status !== "disconnected") {
    // 端点晚于首次连接到达:用新端点重建链路。
    store.stop();
    store.start();
  }
}

let appliedWs: string | null = null;

/** 测试用:清除「已应用」缓存,使同一端点可再次生效。 */
export function resetClientdPortsState(): void {
  appliedWs = null;
}

/**
 * 应用壳注入的端口。返回生效的端点;端口非法或与上次相同则返回 null。
 * `apply` 可注入以便测试,缺省更新真实端点并在需要时重连。
 */
export function applyClientdPorts(
  raw: unknown,
  apply: (endpoint: ClientdEndpoint) => void = applyEndpoint,
): ClientdEndpoint | null {
  const endpoint = parseClientdPorts(raw);
  if (!endpoint || endpoint.ws === appliedWs) return null;
  appliedWs = endpoint.ws;
  apply(endpoint);
  return endpoint;
}

/**
 * 读取已注入端口并监听后续注入。返回解绑函数。
 */
export function initClientdPorts(
  target: Window = window,
  onPorts: (raw: unknown) => void = applyClientdPorts,
): () => void {
  const injected = (target as unknown as Record<string, unknown>)[PORTS_GLOBAL];
  if (injected) onPorts(injected);
  const handler = (event: Event) => {
    onPorts((event as CustomEvent).detail);
  };
  target.addEventListener(PORTS_EVENT, handler);
  return () => target.removeEventListener(PORTS_EVENT, handler);
}
