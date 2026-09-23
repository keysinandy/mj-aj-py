import { useEffect } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useConnectionStore } from "./service/connectionStore";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { TournamentPage } from "./pages/TournamentPage";
import { ConsolePage } from "./pages/ConsolePage";
import { RoomsPage } from "./pages/RoomsPage";
import { ReplayPage } from "./pages/ReplayPage";
import { SettingsPage } from "./pages/SettingsPage";
import { useTheme } from "./theme";

export default function App() {
  const start = useConnectionStore((s) => s.start);
  const { theme, toggleTheme } = useTheme();

  useEffect(() => {
    start();
    return () => useConnectionStore.getState().stop();
  }, [start]);

  return (
    <div className="app">
      <header className="app-header">
        <NavLink to="/" end className="brand">
          杭州麻将客户端
        </NavLink>
        <nav className="app-nav">
          <NavLink to="/tournament">锦标赛</NavLink>
          <NavLink to="/console">控制台</NavLink>
          <NavLink to="/rooms">房间</NavLink>
          <NavLink to="/replay">回放</NavLink>
          <NavLink to="/settings">设置</NavLink>
        </nav>
        <ConnectionStatus />
        <button
          className="theme-toggle"
          type="button"
          aria-pressed={theme === "dark"}
          aria-label={theme === "dark" ? "切换到浅色模式" : "切换到暗色模式"}
          title={theme === "dark" ? "切换到浅色模式" : "切换到暗色模式"}
          onClick={toggleTheme}
        >
          <span className="theme-toggle-icon" aria-hidden="true">
            {theme === "dark" ? "☀" : "☾"}
          </span>
          <span>{theme === "dark" ? "浅色" : "暗色"}</span>
        </button>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<Navigate to="/tournament" replace />} />
          <Route path="/tournament" element={<TournamentPage />} />
          <Route path="/console" element={<ConsolePage />} />
          <Route path="/rooms" element={<RoomsPage />} />
          <Route path="/replay" element={<ReplayPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/tournament" replace />} />
        </Routes>
      </main>
    </div>
  );
}
