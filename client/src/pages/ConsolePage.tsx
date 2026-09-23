import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type SessionInfo } from "../service/http";

const MAIN_STRATEGIES: Array<{ key: string; label: string; config: object }> = [
  { key: "legacy-v2", label: "legacy-v2 启发式", config: { strategy: "bot", evaluator: "legacy-v2" } },
  { key: "legacy", label: "legacy 启发式", config: { strategy: "bot", evaluator: "legacy" } },
  { key: "shape-v2", label: "shape-v2 启发式", config: { strategy: "bot", evaluator: "shape-v2", fallback_ms: 10 } },
  { key: "random", label: "随机", config: { strategy: "random" } },
];

const OPPONENT_STRATEGIES: Array<{ key: string; label: string; config: object }> = [
  { key: "random", label: "随机", config: { strategy: "random" } },
  { key: "legacy-v2", label: "legacy-v2 启发式", config: { strategy: "bot", evaluator: "legacy-v2" } },
  { key: "legacy", label: "legacy 启发式", config: { strategy: "bot", evaluator: "legacy" } },
  { key: "shape-v2", label: "shape-v2 启发式", config: { strategy: "bot", evaluator: "shape-v2", fallback_ms: 10 } },
];

const MATCH_STRATEGIES: Array<{ key: string; label: string; config: object }> = [
  { key: "legacy-v2", label: "legacy-v2 启发式", config: { strategy: "bot", evaluator: "legacy-v2" } },
  { key: "legacy", label: "legacy 启发式", config: { strategy: "bot", evaluator: "legacy" } },
  { key: "shape-v2", label: "shape-v2 启发式", config: { strategy: "bot", evaluator: "shape-v2" } },
  { key: "policy", label: "policy（设置中的当前模型）", config: { strategy: "policy" } },
  { key: "random", label: "随机", config: { strategy: "random" } },
];

type BattleMode = "arena" | "match";

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
  const [battleMode, setBattleMode] = useState<BattleMode>("arena");
  const [nGames, setNGames] = useState(16);
  const [concurrency, setConcurrency] = useState(4);
  const [mainStrategy, setMainStrategy] = useState("legacy-v2");
  const [oppStrategy, setOppStrategy] = useState("random");
  const [matchGames, setMatchGames] = useState(10);
  const [matchStrategy, setMatchStrategy] = useState("legacy-v2");
  const [matchStateRate, setMatchStateRate] = useState(16);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);

  const refresh = useCallback(() => {
    api.listSessions().then((r) => setSessions(
      r.sessions.filter((session) => session.kind !== "tournament"),
    )).catch(() => {});
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

  async function startMatch() {
    setStarting(true);
    setError(null);
    try {
      const strategy = MATCH_STRATEGIES.find((s) => s.key === matchStrategy)
        ?? MATCH_STRATEGIES[0];
      await api.createSession("match", {
        max_games: matchGames,
        state_rate: matchStateRate,
        room_close_wait: 65,
        ...strategy.config,
      });
      await refresh();
    } catch (e) {
      setError(String((e as Error).message ?? "启动线上匹配失败"));
    } finally {
      setStarting(false);
    }
  }

  function sessionDescription(session: SessionInfo): string {
    if (session.kind === "match") {
      const target = session.config?.max_games ?? "持续";
      const strategyName = session.config?.strategy ?? "默认策略";
      const evaluator = session.config?.evaluator;
      const strategy = strategyName === "bot"
        ? `BOT${evaluator ? `（${evaluator}）` : ""}`
        : strategyName;
      const completed = session.result?.games ?? session.progress?.done;
      const progress = completed !== undefined
        ? ` · ${completed}/${target}局`
        : "";
      const rooms = session.result?.rooms ?? session.progress?.rooms;
      return `线上匹配 · ${strategy}${progress}${rooms !== undefined ? ` · ${rooms}房` : ""}`;
    }
    return `${session.config?.n_games ?? "?"}局 × ${session.config?.concurrency ?? "?"}并发`;
  }

  return (
    <section className="page" data-page="console">
      <h1>对战控制台</h1>

      <div className="arena-form">
        <div className="battle-mode-switch" role="tablist" aria-label="对战类型">
          <button
            type="button"
            role="tab"
            aria-selected={battleMode === "arena"}
            className={battleMode === "arena" ? "active" : ""}
            onClick={() => { setBattleMode("arena"); setError(null); }}
          >
            本地竞技场
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={battleMode === "match"}
            className={battleMode === "match" ? "active" : ""}
            onClick={() => { setBattleMode("match"); setError(null); }}
          >
            线上匹配
          </button>
        </div>
        {battleMode === "arena" ? (
          <>
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
          </>
        ) : (
          <>
            <div className="form-grid match-form-grid">
              <label>
                目标场数
                <input
                  type="number" min={1} max={10000} value={matchGames}
                  onChange={(e) => setMatchGames(Number(e.target.value))} />
                <small>按完整匹配房完成，房内不会中途退出</small>
              </label>
              <label>
                我的策略
                <select value={matchStrategy} onChange={(e) => setMatchStrategy(e.target.value)}>
                  {MATCH_STRATEGIES.map((s) => (
                    <option key={s.key} value={s.key}>{s.label}</option>
                  ))}
                </select>
              </label>
              <label>
                状态速率
                <input
                  type="number" min={1} max={64} step={1} value={matchStateRate}
                  onChange={(e) => setMatchStateRate(Number(e.target.value))} />
                <small>每秒状态请求上限</small>
              </label>
            </div>
            <div className="match-form-footer">
              <p className="match-hint">
                匹配令牌由本机设置读取，不会发送到浏览器。未配置令牌？<Link to="/settings">前往设置</Link>
              </p>
              <button className="primary" onClick={startMatch} disabled={starting}>
                {starting ? "匹配启动中…" : "开始线上匹配"}
              </button>
            </div>
          </>
        )}
        {error && <p className="error">{error}</p>}
      </div>

      <h2 className="section-title">进行中 / 历史会话</h2>
      {sessions.length === 0 && <p className="muted">暂无会话。选择本地竞技场或线上匹配开始对战。</p>}
      <ul className="session-list">
        {sessions.map((s) => {
          const running = s.status === "running";
          const progress = s.progress;
          const isMatch = s.kind === "match";
          return (
            <li key={s.id} className={`session ${s.status}`}>
              <span className="session-id">#{s.id}</span>
              <span className="session-date">{new Date(s.created_at * 1000).toLocaleString()}</span>
              <span className={`session-kind session-kind-${isMatch ? "match" : "arena"}`}>
                {isMatch ? "线上匹配" : "本地竞技场"}
              </span>
              <span className={`chip chip-${s.status}`}>{STATUS_LABEL[s.status] ?? s.status}</span>
              <span className="muted">
                {sessionDescription(s)}
                {running && progress && s.kind !== "match" ? ` · ${progress.done}/${progress.total}` : ""}
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
        本地记录落在 <code>local/arena/batch_*/</code>，线上匹配记录落在 <code>local/games/</code>，可在回放页浏览。
      </p>
    </section>
  );
}
