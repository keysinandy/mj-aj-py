import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  type ConnectionTestResult,
  type PlatformSettings,
  type SessionInfo,
  type SessionLogEntry,
} from "../service/http";
import { StrategyRuntimePanel } from "../components/StrategyRuntimePanel";
import { StrategySelectionPreview } from "../components/StrategySelectionPreview";

const STRATEGIES = [
  {
    value: "bot:legacyV2",
    label: "BOT · legacyV2（首次提交 2026-09-21）",
    description: "当前加权两步搜索版本：对公开未知牌做前瞻并用 Rust 加速；本机加权内核不可用时可能回退到 legacy。",
  },
  {
    value: "bot:legacy",
    label: "BOT · legacy（首次提交 2026-09-03）",
    description: "基础启发式基线，按既有向听数、进张和规则优先级决策；计算轻、行为稳定。",
  },
  {
    value: "bot:legacy-two-ply-v1",
    label: "BOT · legacy two-ply v1（首次提交 2026-09-21）",
    description: "旧版一摸前瞻：枚举公开未知牌，再选后续最佳弃牌；搜索值按均匀未知牌估算。",
  },
  {
    value: "bot:shape-v1",
    label: "BOT · shape-v1（首次提交 2026-09-14）",
    description: "结合向听、有效进张和手牌结构评分，作为比基础 legacy 更细的启发式评价。",
  },
  {
    value: "bot:shape-v2",
    label: "BOT · shape-v2（首次提交 2026-09-15）",
    description: "Fast EV 弃牌评价器，只覆盖普通弃牌；公开牌面信息不足时会安全回退。",
  },
  {
    value: "bot:weighted-two-ply-frontier-v1",
    label: "BOT · weighted two-ply（legacyV2 别名；首次提交 2026-09-21）",
    description: "兼容名称，与 legacyV2 是同一评价器；选择后服务端会归一化为 legacyV2。",
  },
  {
    value: "policy",
    label: "policy · 当前 ONNX 模型（首次提交 2026-09-04）",
    description: "由所选策略网络直接从合法动作中选动作；需要先在设置页导入并选择模型。",
  },
  {
    value: "policy-v3",
    label: "policy-v3 · 当前 ONNX 模型（首次提交 2026-09-16）",
    description: "带合法动作掩码与置信度保护的策略/价值运行时；低置信或异常时回退到 shape-v2、legacy。",
  },
  {
    value: "random",
    label: "随机策略（首次提交 2026-09-08）",
    description: "从当前合法动作中随机选择，适合协议/基线用途，不建议用于正式参赛。",
  },
];

const SESSION_STATUS: Record<string, string> = {
  created: "准备中",
  running: "运行中",
  finished: "已结束",
  cancelled: "已停止",
  error: "异常",
};

const TERMINATION_REASON: Record<string, string> = {
  FINISHED: "赛事完成",
  ELIMINATED: "已淘汰",
  VOID: "赛事作废",
  CLOSED: "赛事关闭",
  TOKEN_NOT_BOUND: "令牌尚未绑定锦标赛",
  AUTH_FAILED: "令牌认证失败",
  PROTOCOL_FATAL: "平台协议错误",
  INTERRUPTED: "已停止",
};

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function tournamentSessionMessage(session: SessionInfo): string {
  if (session.status === "error") return session.error ?? "会话异常";
  if (session.status !== "running") {
    const reason = session.result?.termination_reason;
    return reason ? TERMINATION_REASON[reason] ?? reason : SESSION_STATUS[session.status];
  }
  return session.progress?.message ?? "连接赛事并轮询中";
}

const LOG_ROW_HEIGHT = 40;

function VirtualTournamentLogs({
  logs,
  selectedLogId,
  onSelect,
}: {
  logs: SessionLogEntry[];
  selectedLogId: number | null;
  onSelect: (entry: SessionLogEntry) => void;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const followLatest = useRef(true);
  const viewportHeight = 360;
  const overscan = 8;
  const latestLogId = logs.length > 0 ? logs[logs.length - 1].id : null;
  const start = Math.max(0, Math.floor(scrollTop / LOG_ROW_HEIGHT) - overscan);
  const end = Math.min(
    logs.length,
    Math.ceil((scrollTop + viewportHeight) / LOG_ROW_HEIGHT) + overscan,
  );

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || !followLatest.current) return;
    viewport.scrollTop = viewport.scrollHeight;
    setScrollTop(viewport.scrollTop);
  }, [latestLogId]);

  return (
    <div
      ref={viewportRef}
      className="tournament-log-viewport"
      role="log"
      aria-label="锦标赛运行日志"
      aria-live="off"
      onScroll={(event) => {
        const viewport = event.currentTarget;
        setScrollTop(viewport.scrollTop);
        followLatest.current = viewport.scrollHeight - viewport.scrollTop
          - viewport.clientHeight < LOG_ROW_HEIGHT * 2;
      }}
    >
      {logs.length === 0 ? (
        <p className="muted tournament-log-empty">等待锦标赛日志…</p>
      ) : (
        <div style={{ height: logs.length * LOG_ROW_HEIGHT, position: "relative" }}>
          <div
            className="tournament-log-window"
            style={{ transform: `translateY(${start * LOG_ROW_HEIGHT}px)` }}
          >
            {logs.slice(start, end).map((entry) => (
              <button
                key={entry.id}
                type="button"
                className={`tournament-log-row level-${entry.level}${selectedLogId === entry.id ? " selected" : ""}`}
                style={{ height: LOG_ROW_HEIGHT }}
                title={entry.message}
                onClick={() => onSelect(entry)}
              >
                <time>{new Date(entry.timestamp * 1000).toLocaleTimeString()}</time>
                <span className="tournament-log-level">{entry.level === "error" ? "错误" : entry.level === "warning" ? "提醒" : "信息"}</span>
                <span className="tournament-log-source">{entry.source}</span>
                <span className="tournament-log-message">{entry.message}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

type ServerProbeState = {
  connected: boolean | null;
  status: string;
  message: string;
  checkedAt: number | null;
};

function describeServerState(state: ServerProbeState) {
  if (state.status === "checking") return "检测中";
  if (state.status === "auth_failed") return "服务器已联通，Key 鉴权失败";
  if (state.status === "reachable_error") return "服务器已响应，但请求失败";
  if (state.connected === true) return "已联通";
  if (state.connected === false) return "无法联通";
  return "尚未检测";
}

export function TournamentPage() {
  const [settings, setSettings] = useState<PlatformSettings | null>(null);
  const [tournamentKey, setTournamentKey] = useState("");
  const [strategy, setStrategy] = useState("");
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [starting, setStarting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [serverProbe, setServerProbe] = useState<ServerProbeState>({
    connected: null,
    status: "unknown",
    message: "尚未检测目标服务器",
    checkedAt: null,
  });
  const [logs, setLogs] = useState<SessionLogEntry[]>([]);
  const [logsTruncated, setLogsTruncated] = useState(false);
  const [logsError, setLogsError] = useState<string | null>(null);
  const [selectedLog, setSelectedLog] = useState<SessionLogEntry | null>(null);
  const logCursor = useRef(0);

  const refreshSessions = useCallback(() => {
    api.listSessions()
      .then(({ sessions: current }) => setSessions(
        current.filter((session) => session.kind === "tournament"),
      ))
      .catch(() => {});
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([api.getSettings(), api.listSessions()])
      .then(([loadedSettings, loadedSessions]) => {
        if (!active) return;
        setSettings(loadedSettings);
        setTournamentKey(loadedSettings.tokens.tournament ?? "");
        setSessions(loadedSessions.sessions.filter(
          (session) => session.kind === "tournament",
        ));
      })
      .catch((reason) => {
        if (active) setError(errorText(reason, "读取锦标赛设置失败"));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, []);

  const activeSession = useMemo(
    () => sessions.find((session) => session.status === "running" || session.status === "created"),
    [sessions],
  );
  const latestSession = useMemo(
    () => sessions.reduce<SessionInfo | null>(
      (latest, session) => !latest || session.created_at > latest.created_at
        ? session : latest,
      null,
    ),
    [sessions],
  );
  const statusSession = activeSession ?? latestSession;
  const statusSessionId = statusSession?.id;
  const statusSessionRunning = Boolean(
    statusSession && (statusSession.status === "running" || statusSession.status === "created"),
  );
  const selectedStrategy = STRATEGIES.find((item) => item.value === strategy);
  const selectedStrategyConfig = useMemo(() => {
    if (!strategy) return null;
    const [strategyName, evaluator] = strategy.split(":");
    return { strategy: strategyName, ...(evaluator ? { evaluator } : {}) };
  }, [strategy]);
  const statusProgress = statusSession?.progress;
  const strategyStatus = statusProgress?.strategy_status;
  const strategyLoaded = statusProgress?.strategy_loaded;
  const strategyStateLabel = strategyLoaded === true
    ? statusSessionRunning ? "已加载（运行中）" : "本次会话已加载"
    : strategyLoaded === false || strategyStatus === "error"
      ? "加载失败"
      : strategyStatus === "loading"
        ? "加载中"
        : "尚未启动";
  const strategyStateMessage = statusProgress?.strategy_name
    ? `${statusProgress.strategy_name}${statusProgress.evaluator ? ` · ${statusProgress.evaluator}` : ""}${statusProgress.model_name ? ` · ${statusProgress.model_name}` : ""}`
    : strategy ? STRATEGIES.find((item) => item.value === strategy)?.label ?? strategy
      : "启动锦标赛会话后显示加载状态";
  const sessionServerConnected = statusSessionRunning
    ? statusProgress?.server_connected : undefined;
  const sessionServerStatus = statusSessionRunning
    ? statusProgress?.server_status : undefined;
  const serverState: ServerProbeState = (
    typeof sessionServerConnected === "boolean" ||
    Boolean(sessionServerStatus && sessionServerStatus !== "checking")
  ) ? {
    connected: sessionServerConnected ?? null,
    status: sessionServerStatus ?? "unknown",
    message: (statusSessionRunning ? statusProgress?.server_message : null)
      ?? "未收到服务器状态",
    checkedAt: statusProgress?.last_server_check_at
      ? statusProgress.last_server_check_at * 1000 : null,
  } : serverProbe;

  const probeServer = useCallback(async (
    server: string,
    token: string,
  ): Promise<ConnectionTestResult> => {
    setServerProbe({
      connected: null,
      status: "checking",
      message: "正在检测目标服务器",
      checkedAt: null,
    });
    try {
      const result = await api.testConnection({ mode: "tournament", server, token });
      const status = result.ok
        ? "connected"
        : result.status === 0
          ? "unreachable"
          : result.status === 401 || result.status === 403
            ? "auth_failed"
            : "reachable_error";
      setServerProbe({
        connected: result.status !== 0,
        status,
        message: result.message,
        checkedAt: Date.now(),
      });
      return result;
    } catch (reason) {
      const text = errorText(reason, "连接检测失败");
      setServerProbe({
        connected: false,
        status: "unreachable",
        message: text,
        checkedAt: Date.now(),
      });
      throw reason;
    }
  }, []);

  useEffect(() => {
    if (!activeSession) return;
    const timer = window.setInterval(refreshSessions, 1000);
    return () => window.clearInterval(timer);
  }, [activeSession, refreshSessions]);

  useEffect(() => {
    if (loading) return;
    const server = settings?.server?.trim() ?? "";
    const token = settings?.tokens.tournament?.trim() ?? "";
    if (!server || !token) {
      setServerProbe({
        connected: null,
        status: "unknown",
        message: "请配置服务器和锦标赛 Key 后检测",
        checkedAt: null,
      });
      return;
    }
    let current = true;
    let checking = false;
    const check = async () => {
      if (checking) return;
      checking = true;
      try {
        const result = await api.testConnection({ mode: "tournament", server, token });
        if (!current) return;
        const status = result.ok
          ? "connected"
          : result.status === 0
            ? "unreachable"
            : result.status === 401 || result.status === 403
              ? "auth_failed"
              : "reachable_error";
        setServerProbe({
          connected: result.status !== 0,
          status,
          message: result.message,
          checkedAt: Date.now(),
        });
      } catch (reason) {
        if (!current) return;
        setServerProbe({
          connected: false,
          status: "unreachable",
          message: errorText(reason, "无法连接目标服务器"),
          checkedAt: Date.now(),
        });
      } finally {
        checking = false;
      }
    };
    void check();
    const timer = window.setInterval(check, 15_000);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, [loading, settings?.server, settings?.tokens.tournament]);

  useEffect(() => {
    if (!statusSessionId) {
      logCursor.current = 0;
      setLogs([]);
      setLogsTruncated(false);
      setLogsError(null);
      setSelectedLog(null);
      return;
    }
    const sessionId = statusSessionId;
    let current = true;
    let fetching = false;
    logCursor.current = 0;
    setLogs([]);
    setLogsTruncated(false);
    setLogsError(null);
    setSelectedLog(null);

    const fetchLogs = async () => {
      if (fetching) return;
      fetching = true;
      try {
        let pages = 0;
        let hasMore = false;
        do {
          const page = await api.getSessionLogs(sessionId, logCursor.current, 500);
          if (!current) return;
          if (page.truncated) setLogsTruncated(true);
          if (page.logs.length > 0) {
            setLogs((previous) => [...previous, ...page.logs].slice(-5000));
          }
          logCursor.current = page.next_cursor;
          hasMore = page.has_more;
          pages += 1;
        } while (hasMore && pages < 10);
        if (current) setLogsError(null);
      } catch (reason) {
        if (current) setLogsError(errorText(reason, "读取锦标赛日志失败"));
      } finally {
        fetching = false;
      }
    };

    void fetchLogs();
    if (!statusSessionRunning) {
      return () => { current = false; };
    }
    const timer = window.setInterval(fetchLogs, 1000);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, [statusSessionId, statusSessionRunning]);

  async function persistTournamentKey(): Promise<PlatformSettings> {
    if (!settings) throw new Error("设置尚未读取完成");
    const saved = await api.saveSettings({
      server: settings.server,
      tokens: { ...settings.tokens, tournament: tournamentKey.trim() },
    });
    setSettings(saved);
    setTournamentKey(saved.tokens.tournament);
    return saved;
  }

  async function saveKey() {
    setSaving(true);
    setMessage(null);
    setError(null);
    try {
      await persistTournamentKey();
      setMessage("锦标赛 Key 已安全保存到本机设置");
    } catch (reason) {
      setError(errorText(reason, "保存锦标赛 Key 失败"));
    } finally {
      setSaving(false);
    }
  }

  async function testKey() {
    setTesting(true);
    setMessage(null);
    setError(null);
    try {
      if (!settings?.server) throw new Error("请先在设置页配置服务器地址");
      if (!tournamentKey.trim()) throw new Error("请填写锦标赛 Key");
      const result = await probeServer(settings.server, tournamentKey.trim());
      if (result.ok) setMessage(result.message);
      else setError(result.message);
    } catch (reason) {
      setError(errorText(reason, "锦标赛连接测试失败"));
    } finally {
      setTesting(false);
    }
  }

  async function startTournament() {
    setStarting(true);
    setMessage(null);
    setError(null);
    try {
      if (!settings?.server) throw new Error("请先在设置页配置服务器地址");
      if (!tournamentKey.trim()) throw new Error("请填写锦标赛 Key");
      if (!strategy) throw new Error("请显式选择锦标赛策略");
      if ((strategy === "policy" || strategy === "policy-v3") && !settings.selected_model) {
        throw new Error("请先在设置页导入并选择 ONNX 模型");
      }
      await persistTournamentKey();
      const [strategyName, evaluator] = strategy.split(":");
      await api.createSession("tournament", {
        strategy: strategyName,
        ...(evaluator ? { evaluator } : {}),
        state_rate: 16,
        record: true,
      });
      await refreshSessions();
      setMessage("锦标赛会话已启动；开赛前会持续轮询，有对局后自动入局");
    } catch (reason) {
      setError(errorText(reason, "启动锦标赛失败"));
    } finally {
      setStarting(false);
    }
  }

  if (loading) {
    return <section className="page settings-page tournament-page" data-page="tournament"><p>正在读取锦标赛设置…</p></section>;
  }

  return (
    <section className="page settings-page tournament-page" data-page="tournament">
      <div className="settings-header">
        <div>
          <p className="section-kicker">FORMAL TOURNAMENT</p>
          <h1>锦标赛</h1>
          <p className="section-description">设置参赛 Key 与策略，持续等待赛事并自动进入本人对局。</p>
        </div>
      </div>

      {message && <p className="settings-notice" role="status">{message}</p>}
      {error && <p className="error settings-error" role="alert">{error}</p>}

      <div className="settings-grid tournament-grid">
        <section className="settings-card" aria-labelledby="tournament-config-title">
          <div className="settings-card-header">
            <div>
              <h2 id="tournament-config-title">参赛配置</h2>
              <p className="muted">Key 保存在本机平台设置中，不会写入会话记录。</p>
            </div>
          </div>
          <label className="settings-field tournament-field">
            锦标赛 Key
            <input
              type="text"
              autoComplete="off"
              aria-label="锦标赛 Key"
              value={tournamentKey}
              onChange={(event) => setTournamentKey(event.target.value)}
            />
          </label>
          <p className="muted tournament-server">
            平台：{settings?.server || <><span>尚未配置。</span> <Link to="/settings">前往设置</Link></>}
          </p>
          <label className="settings-field tournament-field">
            参赛策略
            <select
              aria-label="锦标赛策略"
              value={strategy}
              onChange={(event) => setStrategy(event.target.value)}
            >
              <option value="" disabled>选择参赛策略</option>
              {STRATEGIES.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <p className="muted tournament-strategy-info" aria-live="polite">
            {selectedStrategy
              ? selectedStrategy.description
              : "选项日期按仓库首次提交记录标注，不代表实际投入工时。"}
          </p>
          {selectedStrategyConfig && (
            <StrategySelectionPreview
              title="锦标赛 · 参赛策略"
              config={selectedStrategyConfig}
            />
          )}
          {(strategy === "policy" || strategy === "policy-v3") && (
            <p className="muted tournament-model">
              当前模型：{settings?.selected_model ?? "未选择"} · 可在<Link to="/settings">设置</Link>中管理 ONNX 模型
            </p>
          )}
          <div className="tournament-actions">
            <button className="secondary-button" type="button" onClick={saveKey} disabled={saving || starting}>
              {saving ? "保存中…" : "保存 Key"}
            </button>
            <button className="secondary-button" type="button" onClick={testKey} disabled={testing || saving || starting}>
              {testing ? "测试中…" : "测试 Key"}
            </button>
            <button
              className="primary-button"
              type="button"
              onClick={startTournament}
              disabled={starting || saving || testing || Boolean(activeSession)}
            >
              {starting ? "启动中…" : activeSession ? "锦标赛会话运行中" : "启动并等待赛事"}
            </button>
          </div>
          {activeSession && <p className="muted tournament-running-note">已有运行中的锦标赛会话，可在下方停止。</p>}
        </section>

        <section className="settings-card tournament-poll-card" aria-labelledby="tournament-poll-title">
          <div className="settings-card-header">
            <div>
              <h2 id="tournament-poll-title">运行状态</h2>
              <p className="muted">策略加载和目标服务器状态会随会话更新。</p>
            </div>
          </div>
          <div className="tournament-runtime-status">
            <div className="tournament-runtime-item">
              <span className="muted">参赛策略</span>
              <strong className={`runtime-state ${strategyLoaded === true ? "connected" : strategyStatus === "error" ? "failed" : "pending"}`}>
                {strategyStateLabel}
              </strong>
              <span className="muted tournament-runtime-detail">{strategyStateMessage}</span>
            </div>
            <div className="tournament-runtime-item">
              <span className="muted">目标服务器</span>
              <strong className={`runtime-state ${serverState.status === "auth_failed" || serverState.status === "reachable_error" ? "warning" : serverState.connected === true ? "connected" : serverState.connected === false ? "failed" : "pending"}`}>
                {describeServerState(serverState)}
              </strong>
              <span className="muted tournament-runtime-detail">{serverState.message}</span>
              <span className="muted tournament-runtime-time">
                {serverState.checkedAt
                  ? `最近检测 ${new Date(serverState.checkedAt).toLocaleTimeString()}`
                  : "尚无检测时间"}
              </span>
            </div>
            <div className="tournament-runtime-item tournament-runtime-poll">
              <span className="muted">锦标赛轮询</span>
              <strong>{statusSession ? SESSION_STATUS[statusSession.status] : "未启动"}</strong>
              <span className="muted tournament-runtime-detail">
                {statusSession
                  ? tournamentSessionMessage(statusSession)
                  : "启动会话后每秒检查绑定和赛事阶段。"}
              </span>
              {statusSession?.progress?.tournament_status && (
                <span className="muted tournament-runtime-time">
                  平台状态：{statusSession.progress.tournament_status}
                  {statusSession.progress.stage !== undefined && statusSession.progress.stage !== null
                    ? ` · 阶段 ${String(statusSession.progress.stage)}` : ""}
                </span>
              )}
            </div>
          </div>
          <StrategyRuntimePanel
            snapshot={statusProgress?.strategy_snapshot}
            status={strategyStatus}
            message={statusProgress?.message ?? strategyStateMessage}
          />
          <ol className="tournament-steps">
            <li>报名与准备阶段：自动报名、准备并等待开赛。</li>
            <li>赛事进行阶段：持续查询本人活跃且属于本赛事的对局。</li>
            <li>出现可加入对局：自动启动 BOT，直至赛事结束或手动停止。</li>
          </ol>
          {!settings?.server && <p className="error">需要先在<Link to="/settings">设置</Link>中填写平台服务器。</p>}
        </section>

        <section className="settings-card tournament-log-card" aria-labelledby="tournament-log-title">
          <div className="settings-card-header">
            <div>
              <h2 id="tournament-log-title">锦标赛日志</h2>
              <p className="muted">
                {statusSession
                  ? `会话 #${statusSession.id} · ${logs.length} 条${logsTruncated ? " · 已滚动淘汰较早日志" : ""}`
                  : "显示策略加载、服务器连接、轮询、赛事阶段和错误信息。"}
              </p>
            </div>
          </div>
          {logsError && <p className="error tournament-log-error" role="alert">{logsError}</p>}
          {statusSession ? (
            <>
              {statusSession.error && (
                <p className="error tournament-log-error" role="alert">
                  会话错误：{statusSession.error}
                </p>
              )}
              <VirtualTournamentLogs
                logs={logs}
                selectedLogId={selectedLog?.id ?? null}
                onSelect={setSelectedLog}
              />
              {selectedLog && (
                <div className={`tournament-log-detail level-${selectedLog.level}`}>
                  <strong>{selectedLog.level === "error" ? "错误详情" : "日志详情"}</strong>
                  <pre>{selectedLog.message}</pre>
                </div>
              )}
            </>
          ) : (
            <div className="tournament-log-empty-state">启动锦标赛后，这里会显示实时日志。</div>
          )}
        </section>
      </div>

      <h2 className="section-title">锦标赛会话</h2>
      {sessions.length === 0 ? (
        <p className="muted">暂无锦标赛会话。</p>
      ) : (
        <ul className="session-list tournament-session-list">
          {sessions.map((session) => {
            const running = session.status === "running" || session.status === "created";
            const reason = session.result?.termination_reason;
            const statusLabel = reason && reason !== "INTERRUPTED"
              ? TERMINATION_REASON[reason] ?? reason
              : SESSION_STATUS[session.status] ?? session.status;
            return (
              <li key={session.id} className={`session ${session.status}`}>
                <span className="session-id">#{session.id}</span>
                <span className={`chip chip-${session.status}`}>{statusLabel}</span>
                <strong className="tournament-session-message">{tournamentSessionMessage(session)}</strong>
                <span className="muted">
                  {session.config.strategy === "bot"
                    ? `BOT · ${session.config.evaluator ?? "legacyV2"}`
                    : session.config.strategy ?? "未记录策略"}
                  {session.progress?.games !== undefined ? ` · 已完成 ${session.progress.games} 局` : ""}
                  {session.progress?.actions !== undefined ? ` · ${session.progress.actions} 次动作` : ""}
                </span>
                {session.error && <span className="error">{session.error}</span>}
                {running && (
                  <button className="stop" type="button" onClick={() => api.stopSession(session.id).then(refreshSessions)}>
                    停止
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
