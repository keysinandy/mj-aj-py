import { useState } from "react";
import { useReplayStore } from "../replay/replayStore";
import { ReplayViewer } from "../components/ReplayViewer";
import { RecordsBrowser } from "../components/RecordsBrowser";
import { api } from "../service/http";

interface Opened {
  label: string;
  infoKind: "local" | "online";
}

export function ReplayPage() {
  const [opened, setOpened] = useState<Opened | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const setFrames = useReplayStore((s) => s.setFrames);

  async function openLocal(batchId: string, game: number, label: string) {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.localFrames(batchId, game);
      setFrames(resp.frames as never);
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
      setFrames(resp.frames as never);
      setOpened({ label, infoKind: "online" });
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setLoading(false);
    }
  }

  function back() {
    setOpened(null);
    setFrames([]);
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
          </div>
          <ReplayViewer />
        </>
      )}
    </section>
  );
}