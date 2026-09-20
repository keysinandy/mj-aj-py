import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { setApiBase } from "./service/http";
import { setConnectionEndpoint } from "./service/connectionStore";
import { HTTP_BASE, WS_ENDPOINT } from "./service/config";
import { applyTheme, resolveTheme } from "./theme";
import "./styles.css";

setApiBase(HTTP_BASE);
setConnectionEndpoint(WS_ENDPOINT);
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
