import { useEffect, useState } from "react";
import { api, type BatchSummary } from "../service/http";

interface Props {
  onOpenLocal: (batchId: string, game: number, label: string) => void;
  onOpenOnline: (gid: string, label: string) => void;
}

/** 两级记录浏览:本地批次(批次→单局)+ 线上日志(日期→gid)。 */
export function RecordsBrowser({ onOpenLocal, onOpenOnline }: Props) {
  const [batches, setBatches] = useState<BatchSummary[]>([]);
  const [online, setOnline] = useState<Array<{ date: string; gid: string; name: string }>>([]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([api.localBatches(), api.onlineGames()])
      .then(([lb, og]) => {
        if (!live) return;
        setBatches(lb.batches);
        setOnline(og.games.map((g) => ({ date: g.date, gid: g.gid, name: g.name })));
      })
      .catch((e) => live && setError(String((e as Error).message) ?? "加载失败"));
    return () => {
      live = false;
    };
  }, []);

  const onlineByDate = new Map<string, typeof online>();
  for (const g of online) {
    const arr = onlineByDate.get(g.date) ?? [];
    arr.push(g);
    onlineByDate.set(g.date, arr);
  }

  return (
    <div className="records-browser">
      <h3>本地批次</h3>
      {error && <p className="error">{error}</p>}
      {batches.length === 0 && <p className="muted">暂无本地对局批次(先跑一个批次)。</p>}
      {batches.map((b) => {
        const games = b.game_paths.map((p, i) => ({
          idx: i,
          name: p.split(/[\\/]/).pop() ?? p,
          path: p,
        }));
        return (
          <div key={b.batch_id} className="batch">
            <div className="batch-head" onClick={() => setExpanded((e) => ({ ...e, [b.batch_id]: !e[b.batch_id] }))}>
              <span className="expand">{expanded[b.batch_id] ? "▾" : "▸"}</span>
              <strong>{b.batch_id}</strong>
              <span className="muted">seed0={b.seed0 ?? "?"} · {games.length}局 · {b.status}</span>
              {b.stats ? (
                <span className="muted">
                  · win={String((b.stats as { win_rate?: unknown }).win_rate ?? "-")}
                </span>
              ) : null}
            </div>
            {expanded[b.batch_id] && (
              <ul className="games">
                {games.map((g) => (
                  <li key={g.idx}>
                    <button onClick={() => onOpenLocal(b.batch_id, g.idx, `${b.batch_id}/${g.name}`)}>
                      打开 {g.name}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}

      <h3>线上日志</h3>
      {online.length === 0 && <p className="muted">暂无线上对局日志。</p>}
      {Array.from(onlineByDate.entries()).map(([date, games]) => (
        <div key={date} className="online-day">
          <span className="muted">{date}</span>
          <ul>
            {games.map((g) => (
              <li key={g.gid}>
                <button onClick={() => onOpenOnline(g.gid, `${g.name} (${date})`)}>
                  打开 {g.name}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}