import type { StrategySnapshot } from "../strategy/types";

interface SnapshotEntry {
  seat: number;
  snapshot: StrategySnapshot;
}

interface Props {
  snapshot?: StrategySnapshot | null;
  snapshots?: SnapshotEntry[] | null;
  status?: string;
  message?: string | null;
  compact?: boolean;
}

const FEATURE_LABELS: Record<string, string> = {
  weighted_two_ply: "Weighted Two-Ply",
  stage_b: "Stage B",
  shape_guard: "Shape Guard",
  shape_quality: "Shape Quality",
  big_hand_intent: "BigHandIntent",
  plus_one: "plus_one",
  baotou_scope: "Baotou Scope",
  piao_wall_guard: "Piao Wall Guard",
  reaction_v2: "Reaction V2",
  kong_continuation: "KONG Continuation",
};

const FEATURE_STATE: Record<string, string> = {
  enabled: "开启",
  disabled: "关闭",
  not_applicable: "不适用",
  unknown: "未知",
};

function statusLabel(status?: string): string {
  if (status === "loaded") return "已加载";
  if (status === "loading") return "加载中";
  if (status === "error") return "加载失败";
  if (status === "configured") return "配置已解析";
  return "运行时信息";
}

function oneSnapshot(snapshot: StrategySnapshot, heading?: string) {
  const profile = snapshot.profile_config ?? {};
  const runtime = snapshot.runtime ?? {};
  const bigHand = profile.big_hand && typeof profile.big_hand === "object"
    ? profile.big_hand as Record<string, unknown> : {};
  const settings: Array<[string, unknown]> = [
    ["frontier", profile.max_frontier_candidates],
    ["soft budget", profile.soft_budget_ms],
    ["hard budget", profile.hard_budget_ms],
    ["BigHandIntent plus_one", bigHand.plus_one_enabled],
  ];
  return (
    <div className="strategy-runtime-snapshot" key={heading ?? snapshot.config_hash}>
      {heading && <h4>{heading}</h4>}
      <div className="strategy-runtime-identity">
        <strong>{snapshot.evaluator ?? snapshot.strategy}</strong>
        {snapshot.profile && <span>{snapshot.profile}</span>}
        {snapshot.model_name && <span>model {snapshot.model_name}</span>}
      </div>
      <div className="strategy-runtime-features">
        {Object.entries(snapshot.features ?? {}).map(([key, feature]) => (
          <span className="strategy-runtime-feature" key={key}>
            <span>{FEATURE_LABELS[key] ?? key}</span>
            <strong className={`strategy-feature-${feature.status}`}>
              {FEATURE_STATE[feature.status] ?? feature.status}
            </strong>
            {typeof feature.min_live === "number" && (
              <small>≥{feature.min_live}</small>
            )}
          </span>
        ))}
      </div>
      <div className="strategy-runtime-params">
        {settings.filter(([, value]) => value !== undefined && value !== null)
          .map(([label, value]) => (
            <span key={label}>{label}: <strong>{String(value)}</strong></span>
          ))}
      </div>
      {typeof runtime.weighted_kernel === "string" && (
        <div className={`strategy-runtime-kernel${runtime.degraded ? " degraded" : ""}`}>
          Kernel: {runtime.weighted_kernel}
          {runtime.weighted_kernel_version
            ? ` · ${String(runtime.weighted_kernel_version)}` : ""}
          {runtime.weighted_kernel_compatible === true ? " · compatible"
            : runtime.weighted_kernel_compatible === false ? " · degraded" : ""}
          {runtime.reason ? ` · ${String(runtime.reason)}` : ""}
        </div>
      )}
      <div className="strategy-runtime-hash">
        config {snapshot.config_hash || "unknown"}
        {snapshot.commit_sha ? ` · build ${snapshot.commit_sha.slice(0, 8)}` : ""}
      </div>
    </div>
  );
}

export function StrategyRuntimePanel({
  snapshot,
  snapshots,
  status,
  message,
  compact = false,
}: Props) {
  const entries = snapshots?.length ? snapshots : null;
  const hasSnapshot = Boolean(snapshot || entries);
  return (
    <details className={`strategy-runtime-panel${compact ? " compact" : ""}`} open={!compact}>
      <summary>
        <span>策略运行时</span>
        <strong className={`strategy-load-${status ?? "unknown"}`}>
          {statusLabel(status)}
        </strong>
      </summary>
      {!hasSnapshot ? (
        <p className="muted strategy-runtime-empty">
          {message ?? (status === "error" ? "策略加载失败，未生成有效配置快照。"
            : status === "loading" ? "正在解析并加载策略配置。"
              : "未启动，或这条旧记录没有保存运行时配置。")}
        </p>
      ) : entries ? (
        entries.map((entry) => oneSnapshot(entry.snapshot, `座位 ${entry.seat}`))
      ) : snapshot ? oneSnapshot(snapshot) : null}
    </details>
  );
}
