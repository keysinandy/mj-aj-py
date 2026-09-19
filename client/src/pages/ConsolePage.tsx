import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type SessionInfo } from "../service/http";

const MAIN_STRATEGIES: Array<{ key: string; label: string; config: object }> = [
  { key: "shape-v2", label: "shape-v2 启发式", config: { strategy: "bot", evaluator: "shape-v2", fallback_ms: 10 } },
  { key: "legacy", label: "legacy 启发式", config: { strategy: "bot", evaluator: "legacy" } },
  { key: "random", label: "随机", config: { strategy: "random" } },
];

const OPPONENT_STRATEGIES: Array<{ key: string; label: string; config: object }> = [
  { key: "random", label: "随机", config: { strategy: "random" } },
  { key: "legacy", label: "legacy 启发式", config: { strategy: "bot", evaluator: "legacy" } },
  { key: "shape-v2", label: "shape-v2 启发式", config: { strategy: "bot", evaluator: "shape-v2", fallback_ms: 10 } },
];

const STATUS_LABEL: Record<string, string> = {
  created: "已创建",
  running: "运行中",
  finished: "已完成",
  cancelled: "已取消",
  error: "失败",
};

function seatConfig(strategy: string) {
  const hit =
    MAIN_STRATEGIES.find((s) => s.key === strategy) ??
    MAIN_STRATEGIES[0];
  return hit.config;
}

export function ConsolePage() {
  const [nGames, setNGames] = useState(16);
  const [concurrency, setConcurrency] = useState(4);
  const [mainStrategy, setMainStrategy] = useState("shape-v2");
  const [oppStrategy, setOppStrategy] = useState("random");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);

  const refresh = useCallback(() => {
    api.listSessions().then((r) => setSessions(r.sessions)).catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(() => {
      refresh();
    }, 1000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    const hasRunning = sessions.some((s) => s.status === "running");
    if (!hasRunning) return;
    const timer = window.setInterval(refresh, 1000);
    return () => window.clearInterval(timer);
  }, [sessions, refresh]);

  async function startBattle() {
    setStarting(true);
    setError(null);
    try {
      const main = seatConfig(mainStrategy);
      const opp = seatConfig(oppStrategy);
      const seats = [main, opp, { ...opp }, { ...opp }];
      await api.createSession("arena", { n_games: nGames, concurrency, seats });
      await refresh();
    } catch (e) {
      setError(String((e as Error).message ?? "启动失败"));
    } finally {
      setStarting(false);
    }
  }

  return (
    <section className="page" data-page="console">
      <h1>对战控制台</h1>

      <div className="arena-form">
        <div className="form-grid">
          <label>
            局数
            <input
              type="number" min={1} max={200} value={nGames}
              onChange={(e) => setNGames(Number(e.target.value))} />
          </label>
          <label>
            并行
            <input
              type="number" min={1} max={16} value={concurrency}
              onChange={(e) => setConcurrency(Number(e.target.value))} />
          </label>
          <label>
            主位(我的视角)
            <select value={mainStrategy} onChange={(e) => setMainStrategy(e.target.value)}>
              {MAIN_STRATEGIES.map((s) => (
                <option key={s.key} value={s.key}>{s.label}</option>
              ))}
            </select>
          </label>
          <label>
            对手策略
            <select value={oppStrategy} onChange={(e) => setOppStrategy(e.target.value)}>
              {OPPONENT_STRATEGIES.map((s) => (
                <option key={s.key} value={s.key}>{s.label}</option>
              ))}
            </select>
          </label>
        </div>
        <button className="primary" onClick={startBattle} disabled={starting}>
          {starting ? "启动中…" : "开始本地对战"}
        </button>
        {error && <p className="error">{error}</p>}
      </div>

      <h2 className="section-title">进行中 / 历史会话</h2>
      {sessions.length === 0 && <p className="muted">暂无会话。点击"开始本地对战"跑一个批次。</p>}
      <ul className="session-list">
        {sessions.map((s) => {
          const running = s.status === "running";
          const progress = s.progress;
          return (
            <li key={s.id} className={`session ${s.status}`}>
              <span className="session-id">#{s.id}</span>
              <span className="session-date">{new Date(s.created_at * 1000).toLocaleString()}</span>
              <span className={`chip chip-${s.status}`}>{STATUS_LABEL[s.status] ?? s.status}</span>
              <span className="muted">
                {s.config?.n_games ?? "?"}局 × {s.config?.concurrency ?? "?"}并发
                {running && progress ? ` · ${progress.done}/${progress.total}` : ""}
              </span>
              {s.error && <span className="error">{s.error}</span>}
              {s.status === "finished" && s.result?.batch_id && (
                <Link className="open-replay" to="/replay">
                  打开回放(batch {s.result.batch_id})
                </Link>
              )}
              {running && (
                <button className="stop" onClick={() => api.stopSession(s.id).then(refresh)}>
                  停止
                </button>
              )}
            </li>
          );
        })}
      </ul>
      <p className="muted">
        对局记录落在 <code>local/arena/batch_*/</code>,回放页按批次浏览单局。
      </p>
    </section>
  );
}