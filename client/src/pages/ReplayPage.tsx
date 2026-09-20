import { useState } from "react";
import { useReplayStore } from "../replay/replayStore";
import { ReplayViewer } from "../components/ReplayViewer";
import { RecordsBrowser } from "../components/RecordsBrowser";
import { api } from "../service/http";
import { sessionFromResponse } from "../replay/session";

interface Opened {
  label: string;
  infoKind: "local" | "online";
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

export function ReplayPage() {
  const [opened, setOpened] = useState<Opened | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const setSession = useReplayStore((s) => s.setSession);
  const replaySession = useReplayStore((s) => s.session);

  async function openLocal(batchId: string, game: number, label: string) {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.localFrames(batchId, game);
      setSession(sessionFromResponse(resp, "local", null, resp.path));
      setOpened({ label, infoKind: "local" });
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setLoading(false);
    }
  }

  // 线上为自家视角帧(他家暗手不可见);解析走 /api/records/online/:gid/frames。
  async function openOnline(gid: string, label: string) {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.onlineFrames(gid);
      setSession(sessionFromResponse(resp, "online", gid, resp.path));
      setOpened({ label, infoKind: "online" });
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setLoading(false);
    }
  }

  function back() {
    setOpened(null);
    setSession(sessionFromResponse({ frames: [] }, "local"));
  }

  return (
    <section className="page" data-page="replay">
      <h1>回放</h1>
      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">加载对局中…</p>}
      {!opened && (
        <RecordsBrowser onOpenLocal={openLocal} onOpenOnline={openOnline} />
      )}
      {opened && (
        <>
          <div className="replay-toolbar">
            <button onClick={back}>返回记录</button>
            <span className="muted">{opened.label}</span>
            {strategyLabel(
              replaySession?.metadata.strategy,
              replaySession?.metadata.evaluator,
              replaySession?.metadata.modelName,
            ) && (
              <span className="replay-strategy">
                我方策略：{strategyLabel(
                  replaySession?.metadata.strategy,
                  replaySession?.metadata.evaluator,
                  replaySession?.metadata.modelName,
                )}
              </span>
            )}
          </div>
          <ReplayViewer />
        </>
      )}
    </section>
  );
}
