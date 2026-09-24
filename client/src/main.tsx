import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { setApiBase } from "./service/http";
import { setConnectionEndpoint } from "./service/connectionStore";
import { initClientdPorts } from "./service/clientdPorts";
import { HTTP_BASE, WS_ENDPOINT } from "./service/config";
import { applyTheme, resolveTheme } from "./theme";
import "./styles.css";

setApiBase(HTTP_BASE);
setConnectionEndpoint(WS_ENDPOINT);
// 桌面壳用动态端口启动 clientd,会把实际端口注入 webview;有注入时覆盖
// 上面的回退端点,并在端点晚到且连接已运行时重连。浏览器开发态无注入。
initClientdPorts();
// Apply the saved/system theme before React mounts so the first paint does not
// flash the opposite palette.
applyTheme(resolveTheme());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
