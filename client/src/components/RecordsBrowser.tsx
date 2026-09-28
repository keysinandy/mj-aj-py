import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, type BatchSummary, type OnlineGameRef, type OnlineGamesQuery } from "../service/http";

interface Props {
  onOpenLocal: (batchId: string, game: number, label: string) => void;
  onOpenOnline: (gid: string, label: string) => void;
}

const ONLINE_PAGE_SIZE = 20;

function formatDay(day: string): string {
  const match = /^(\d{4})-?(\d{2})-?(\d{2})$/.exec(day);
  return match ? `${match[1]}年${match[2]}月${match[3]}日` : day;
}

function formatLogTime(timestamp: number | null | undefined): string {
  if (timestamp === null || timestamp === undefined || !Number.isFinite(timestamp)) {
    return "时间未知";
  }
  return new Date(timestamp * 1000).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function toTimestamp(value: string): number | undefined {
  if (!value) return undefined;
  const timestamp = new Date(value).getTime();
  return Number.isFinite(timestamp) ? timestamp / 1000 : undefined;
}

function strategyLabel(strategy?: string | null, evaluator?: string | null, modelName?: string | null): string | null {
  if (!strategy && !evaluator && !modelName) return null;
  const base = strategy === "bot"
    ? "BOT"
    : strategy === "policy" || strategy === "policy-v3"
      ? strategy
      : strategy ?? "未知策略";
  const details = [evaluator, modelName].filter(Boolean).join(" · ");
  return details ? `${base}（${details}）` : base;
}

/** 两级记录浏览:本地批次(批次→单局)+ 线上日志(日期→gid)。 */
export function RecordsBrowser({ onOpenLocal, onOpenOnline }: Props) {
  const [batches, setBatches] = useState<BatchSummary[]>([]);
  const [online, setOnline] = useState<OnlineGameRef[]>([]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [expandedOnlineDays, setExpandedOnlineDays] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [onlineError, setOnlineError] = useState<string | null>(null);
  const [onlineLoading, setOnlineLoading] = useState(true);
  const [onlineLoadingMore, setOnlineLoadingMore] = useState(false);
  const [onlineHasMore, setOnlineHasMore] = useState(false);
  const [onlineNextOffset, setOnlineNextOffset] = useState(0);
  const [fromAt, setFromAt] = useState("");
  const [toAt, setToAt] = useState("");
  const onlineRequest = useRef(0);

  useEffect(() => {
    let live = true;
    api.localBatches()
      .then((response) => {
        if (live) setBatches(response.batches);
      })
      .catch((e) => {
        if (live) setError(String((e as Error).message) || "本地记录加载失败");
      });
    return () => {
      live = false;
    };
  }, []);

  async function loadOnline(
    offset: number,
    append: boolean,
    range: OnlineGamesQuery = {},
  ): Promise<void> {
    const requestId = ++onlineRequest.current;
    setOnlineError(null);
    if (append) setOnlineLoadingMore(true);
    else setOnlineLoading(true);
    try {
      const response = await api.onlineGames({
        ...range,
        offset,
        limit: ONLINE_PAGE_SIZE,
      });
      if (requestId !== onlineRequest.current) return;
      setOnline((current) => append ? [...current, ...response.games] : response.games);
      if (!append) setExpandedOnlineDays({});
      setOnlineHasMore(Boolean(response.has_more));
      setOnlineNextOffset(response.next_offset ?? offset + response.games.length);
    } catch (e) {
      if (requestId === onlineRequest.current) {
        setOnlineError(String((e as Error).message) || "线上日志加载失败");
      }
    } finally {
      if (requestId === onlineRequest.current) {
        setOnlineLoading(false);
        setOnlineLoadingMore(false);
      }
    }
  }

  useEffect(() => {
    void loadOnline(0, false);
    return () => {
      onlineRequest.current += 1;
    };
    // loadOnline intentionally runs only once for the initial index page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onlineByDate = useMemo(() => {
    const grouped = new Map<string, OnlineGameRef[]>();
    for (const game of online) {
      const games = grouped.get(game.date) ?? [];
      games.push(game);
      grouped.set(game.date, games);
    }
    return Array.from(grouped.entries());
  }, [online]);

  function readSearchRange(): OnlineGamesQuery | null {
    const startTs = toTimestamp(fromAt);
    const rawEndTs = toTimestamp(toAt);
    // datetime-local 精度到分钟，结束时间按该分钟的末尾处理。
    const endTs = rawEndTs === undefined ? undefined : rawEndTs + 59.999;
    if (fromAt && startTs === undefined) {
      setOnlineError("开始时间格式无效");
      return null;
    }
    if (toAt && endTs === undefined) {
      setOnlineError("结束时间格式无效");
      return null;
    }
    if (startTs !== undefined && endTs !== undefined && startTs > endTs) {
      setOnlineError("开始时间不能晚于结束时间");
      return null;
    }
    return {
      ...(startTs === undefined ? {} : { startTs }),
      ...(endTs === undefined ? {} : { endTs }),
    };
  }

  function searchOnline(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const range = readSearchRange();
    if (range === null) return;
    void loadOnline(0, false, range);
  }

  function resetOnline(): void {
    setFromAt("");
    setToAt("");
    void loadOnline(0, false);
  }

  function loadMoreOnline(): void {
    if (!onlineLoadingMore && onlineHasMore) {
      const range = readSearchRange();
      if (range !== null) void loadOnline(onlineNextOffset, true, range);
    }
  }

  return (
    <div className="records-browser">
      <section className="records-section local-records">
        <div className="records-section-header">
          <div>
            <p className="section-kicker">LOCAL ARENA</p>
            <h3>本地批次</h3>
          </div>
          <span className="section-count">{batches.length} 个批次</span>
        </div>
        {error && <p className="error">{error}</p>}
        {batches.length === 0 && <p className="muted empty-state">暂无本地对局批次(先跑一个批次)。</p>}
        <div className="batch-list">
          {batches.map((batch) => {
            const games = batch.game_paths.map((path, index) => ({
              idx: index,
              name: path.split(/[\\/]/).pop() ?? path,
            }));
            return (
              <div key={batch.batch_id} className="batch">
                <button
                  type="button"
                  className="batch-head"
                  onClick={() => setExpanded((current) => ({
                    ...current,
                    [batch.batch_id]: !current[batch.batch_id],
                  }))}
                >
                  <span className="expand">{expanded[batch.batch_id] ? "▾" : "▸"}</span>
                  <strong>{batch.batch_id}</strong>
                  <span className="muted">seed0={batch.seed0 ?? "?"} · {games.length}局 · {batch.status}</span>
                  {strategyLabel(batch.strategy, batch.evaluator, batch.model_name) && (
                    <span className="record-strategy">
                      我方策略 · {strategyLabel(batch.strategy, batch.evaluator, batch.model_name)}
                    </span>
                  )}
                  {batch.stats ? (
                    <span className="muted">
                      · win={String((batch.stats as { win_rate?: unknown }).win_rate ?? "-")}
                    </span>
                  ) : null}
                </button>
                {expanded[batch.batch_id] && (
                  <ul className="games">
                    {games.map((game) => (
                      <li key={game.idx}>
                        <button
                          type="button"
                          className="record-open-button"
                          onClick={() => onOpenLocal(
                            batch.batch_id,
                            game.idx,
                            `${batch.batch_id}/${game.name}`,
                          )}
                        >
                          打开 {game.name}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      </section>

      <section className="records-section online-records" data-testid="online-records">
        <div className="records-section-header online-header">
          <div>
            <p className="section-kicker">ONLINE LOGS</p>
            <h3>线上日志 <span className="section-count">{online.length} 条</span></h3>
            <p className="section-description">按开始时间倒序展示，默认只加载最近 {ONLINE_PAGE_SIZE} 条；每天的日志默认折叠。</p>
          </div>
          <button
            type="button"
            className="secondary-button"
            onClick={() => {
              const range = readSearchRange();
              if (range !== null) void loadOnline(0, false, range);
            }}
            disabled={onlineLoading}
          >
            刷新
          </button>
        </div>

        <form className="online-filters" onSubmit={searchOnline}>
          <label>
            <span>开始时间</span>
            <input
              aria-label="开始时间"
              type="datetime-local"
              value={fromAt}
              onChange={(event) => setFromAt(event.target.value)}
            />
          </label>
          <span className="filter-separator">至</span>
          <label>
            <span>结束时间</span>
            <input
              aria-label="结束时间"
              type="datetime-local"
              value={toAt}
              onChange={(event) => setToAt(event.target.value)}
            />
          </label>
          <div className="filter-actions">
            <button type="submit" className="primary-button">搜索</button>
            <button type="button" className="secondary-button" onClick={resetOnline}>重置</button>
          </div>
        </form>

        {onlineError && <p className="error filter-error">{onlineError}</p>}
        <div className="online-result-bar">
          <span>{onlineLoading ? "正在加载线上日志…" : online.length ? `当前显示 ${online.length} 条` : "没有匹配的线上日志"}</span>
          <span className="muted">日志正文仅在打开单局时加载</span>
        </div>

        {onlineLoading && online.length === 0 && <div className="online-loading">正在读取索引…</div>}
        {!onlineLoading && online.length === 0 && <div className="online-empty">调整时间范围后重新搜索</div>}
        <div className="online-groups">
          {onlineByDate.map(([date, games]) => (
            <section key={date} className="online-day">
              <button
                type="button"
                className="online-day-header"
                aria-expanded={Boolean(expandedOnlineDays[date])}
                onClick={() => setExpandedOnlineDays((current) => ({
                  ...current,
                  [date]: !current[date],
                }))}
              >
                <span className="online-day-title">
                  <span className="online-expand" aria-hidden="true">
                    {expandedOnlineDays[date] ? "▾" : "▸"}
                  </span>
                  <strong>{formatDay(date)}</strong>
                  <span className="online-day-raw">{date}</span>
                </span>
                <span className="day-count">{games.length} 场</span>
              </button>
              {expandedOnlineDays[date] && (
                <div className="online-log-list">
                  {games.map((game) => (
                    <article
                      key={game.record_id ?? `${game.date}:${game.name}`}
                      className="online-log-row"
                    >
                      <div className="online-log-time">
                        <span className="time-dot" aria-hidden="true" />
                        <time dateTime={game.started_at ? new Date(game.started_at * 1000).toISOString() : undefined}>
                          {formatLogTime(game.started_at)}
                        </time>
                      </div>
                      <div className="online-log-main">
                        <strong title={game.name}>{game.name}</strong>
                        <span>gid · {game.gid}</span>
                        {strategyLabel(game.strategy, game.evaluator, game.model_name) && (
                          <span className="record-strategy">
                            我方策略 · {strategyLabel(game.strategy, game.evaluator, game.model_name)}
                          </span>
                        )}
                      </div>
                      <button
                        type="button"
                        className="record-open-button record-open-online"
                        aria-label={`打开 ${game.name}`}
                        onClick={() => onOpenOnline(
                          game.record_id ?? game.gid,
                          `${game.name} (${game.date})`,
                        )}
                      >
                        打开日志
                      </button>
                    </article>
                  ))}
                </div>
              )}
            </section>
          ))}
        </div>

        {onlineHasMore && (
          <button
            type="button"
            className="load-more-button"
            onClick={loadMoreOnline}
            disabled={onlineLoadingMore}
          >
            {onlineLoadingMore ? "正在加载…" : "加载更多日志"}
          </button>
        )}
      </section>
    </div>
  );
}
