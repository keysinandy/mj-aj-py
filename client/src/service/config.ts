/**
 * clientd 端点配置。web_client 启动器从默认端口对开始选择可用端口,
 * 并通过 Vite env 注入实际地址;直接启动前端时回退到 17320/17321。
 * 桌面(Tauri)形态由壳注入真实端口,此文件只在未注入时提供回退。
 */

export const HTTP_BASE =
  (import.meta.env.VITE_HTTP_BASE as string | undefined) ||
  "http://127.0.0.1:17320";

export const WS_ENDPOINT =
  (import.meta.env.VITE_WS_ENDPOINT as string | undefined) ||
  "ws://127.0.0.1:17321/ws";
