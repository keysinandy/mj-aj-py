import { useConnectionStore } from "../service/connectionStore";

const LABELS: Record<string, string> = {
  connecting: "连接中",
  connected: "已连接",
  reconnecting: "重连中",
  disconnected: "已断开",
};

export function ConnectionStatus() {
  const status = useConnectionStore((s) => s.status);
  const attempts = useConnectionStore((s) => s.attempts);
  return (
    <div className="connection-status" data-status={status}>
      <span className={`dot dot-${status}`} />
      {LABELS[status] ?? status}
      {status === "reconnecting" && attempts > 1 ? `(${attempts})` : ""}
    </div>
  );
}