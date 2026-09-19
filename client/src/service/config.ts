/**
 * clientd 端点配置。Web 开发态从 Vite env 读取;缺省回退到一键启动脚本
 * 固定端口(http 17320 / ws 17321)。桌面(Tauri)形态由壳注入真实端口,
 * 此文件只在未注入时提供回退。
 */

export const HTTP_BASE =
  (import.meta.env.VITE_HTTP_BASE as string | undefined) ||
  "http://127.0.0.1:17320";

export const WS_ENDPOINT =
  (import.meta.env.VITE_WS_ENDPOINT as string | undefined) ||
  "ws://127.0.0.1:17321/ws";