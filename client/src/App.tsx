import { useEffect } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useConnectionStore } from "./service/connectionStore";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { ConsolePage } from "./pages/ConsolePage";
import { RoomsPage } from "./pages/RoomsPage";
import { ReplayPage } from "./pages/ReplayPage";
import { SettingsPage } from "./pages/SettingsPage";

export default function App() {
  const start = useConnectionStore((s) => s.start);

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
          <NavLink to="/console">控制台</NavLink>
          <NavLink to="/rooms">房间</NavLink>
          <NavLink to="/replay">回放</NavLink>
          <NavLink to="/settings">设置</NavLink>
        </nav>
        <ConnectionStatus />
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<Navigate to="/console" replace />} />
          <Route path="/console" element={<ConsolePage />} />
          <Route path="/rooms" element={<RoomsPage />} />
          <Route path="/replay" element={<ReplayPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/console" replace />} />
        </Routes>
      </main>
    </div>
  );
}