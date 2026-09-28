import type { ReplayStep } from "../replay/session";
import { diffFrames, displayDiffValue } from "../replay/diff";
import { tileLabel } from "../replay/frame";
import type { StrategySnapshot, DecisionAudit } from "../strategy/types";
import { StrategyRuntimePanel } from "./StrategyRuntimePanel";

interface Props {
  step: ReplayStep;
  previous: ReplayStep | null;
  strategySnapshot?: StrategySnapshot | null;
  strategySnapshots?: Array<{ seat: number; snapshot: StrategySnapshot }>;
}
function seqSourceLabel(source: string): string {
  if (source === "server_event") return "服务端事件";
  if (source === "snapshot") return "服务端快照";
  if (source === "local_action") return "本地动作（派生）";
  if (source === "local_initial") return "初始状态";
  return source;
}

function requestLabel(request: ReplayStep["localRequests"][number]): string {
  const type = request.type ?? request.kind ?? "LOCAL";
  const status = request.status === undefined || request.status === null
    ? ""
    : ` · ${request.status}`;
  const phase = typeof request.phase === "string" ? ` · ${request.phase}` : "";
  return `${type}${status}${phase}`;
}

function auditValue(value: boolean | null | undefined): string {
  if (value === true) return "是";
  if (value === false) return "否";
  return "未知";
}

function scopeLabel(scope: string): string {
  const labels: Record<string, string> = {
    baotou_scope: "财神专用分支",
    weighted_two_ply: "Weighted Two-Ply",
    legacy: "Legacy",
    reaction_v2: "Reaction V2",
    kong_continuation: "KONG continuation",
    fallback: "Fallback",
    policy: "Policy",
    "policy-v3": "Policy V3",
    random: "Random",
    unknown: "未知",
  };
  return labels[scope] ?? scope;
}

function decisionAudit(step: ReplayStep): DecisionAudit | null {
  const request = [...step.localRequests].reverse().find((item) =>
    (item.type ?? item.kind) === "DECISION");
  return request?.decision_audit ?? null;
}

function auditAction(audit: DecisionAudit, step: ReplayStep): string {
  const action = audit.result?.selected ?? audit.result?.action;
  if (action === -1) return "过 / PASS";
  if (typeof action === "number" && action >= 0 && action <= 33 &&
      audit.phase === "draw") return tileLabel(action);
  if (typeof step.event?.label === "string") return step.event.label;
  return String(action ?? "未知");
}

function AuditFeature({ label, feature }: {
  label: string;
  feature: DecisionAudit["features"][string];
}) {
  return (
    <li>
      <strong>{label}</strong>
      <span>配置 {auditValue(feature.configured)}</span>
      <span>满足 {auditValue(feature.eligible)}</span>
      <span>进入 {auditValue(feature.entered)}</span>
      {feature.completed !== undefined && <span>完成 {auditValue(feature.completed)}</span>}
      {feature.candidate_count !== undefined && feature.candidate_count !== null &&
        <small>候选数：{feature.candidate_count}</small>}
      {feature.draw_nodes !== undefined && feature.draw_nodes !== null &&
        <small>展开节点：{feature.draw_nodes}</small>}
      {feature.winner !== undefined && feature.winner !== null &&
        <small>Winner：{typeof feature.winner === "number" &&
          feature.winner >= 0 && feature.winner <= 33
          ? tileLabel(feature.winner) : String(feature.winner)}</small>}
      {feature.skip_reason && <small>跳过：{feature.skip_reason}</small>}
      {feature.intent && <small>意图：{Array.isArray(feature.intent) ? feature.intent.join(" / ") : feature.intent}</small>}
    </li>
  );
}

export function StepInspector({
  step, previous, strategySnapshot, strategySnapshots,
}: Props) {
  const diffs = diffFrames(previous?.state ?? null, step.state);
  const eventType = step.event?.type ?? step.state.label ?? "未知";
  const audit = decisionAudit(step);
  const actionSeat = step.event?.actor;
  const actionSnapshot = typeof actionSeat === "number"
    ? strategySnapshots?.find((entry) => entry.seat === actionSeat)
    : undefined;
  const displayedSnapshot = strategySnapshots?.length &&
    typeof actionSeat === "number"
    ? actionSnapshot?.snapshot ??
      (actionSeat === step.state.my_seat ? strategySnapshot : null)
    : strategySnapshot;
  return (
    <aside className="step-inspector" data-testid="step-inspector">
      <div className="inspector-title">步骤检查器</div>
      <div className="inspector-step" data-testid="step-number">
        Step #{step.stepIndex}
        <span className="muted">seqNo {step.seqNo ?? "-"}</span>
      </div>

      <section className="inspector-section">
        <h3>Decision Inspector</h3>
        {audit ? (
          <div className="decision-audit" data-testid="decision-audit">
            <div><span>Scope</span><strong>{scopeLabel(audit.decision_scope)}</strong></div>
            <div><span>结果</span><strong>{auditAction(audit, step)}</strong></div>
            <div><span>耗时</span><strong>{audit.runtime.elapsed_ms ?? "未知"} ms</strong></div>
            <div><span>Fallback / Timeout / Partial</span><strong>
              {auditValue(audit.runtime.fallback)} / {auditValue(audit.runtime.timeout)} / {auditValue(audit.runtime.partial)}
            </strong></div>
            <ul className="decision-audit-features">
              {([
                ["weighted_two_ply", "Weighted Two-Ply"],
                ["stage_a", "Stage A"],
                ["stage_b", "Stage B"],
                ["shape_guard", "Shape Guard"],
                ["big_hand_intent", "BigHandIntent"],
                ["baotou_scope", "Baotou Scope"],
                ["reaction_v2", "Reaction V2"],
                ["kong_continuation", "KONG Continuation"],
              ] as const).map(([key, label]) => {
                const feature = audit.features[key];
                return feature ? <AuditFeature key={key} label={label} feature={feature} /> : null;
              })}
            </ul>
            {audit.runtime.fallback_reason && (
              <p className="muted">Fallback reason: {audit.runtime.fallback_reason}</p>
            )}
            {audit.decision_id !== undefined && audit.decision_id !== null && (
              <small className="muted">Decision #{audit.decision_id} · config {audit.strategy_config_hash ?? "unknown"}</small>
            )}
          </div>
        ) : (
          <div className="muted" data-testid="decision-audit-unavailable">
            当前步骤没有记录 Decision Audit；旧记录不会根据评价器名称补推执行路径。
          </div>
        )}
      </section>

      <section className="inspector-section">
        <h3>本局策略配置</h3>
        <StrategyRuntimePanel
          snapshot={actionSnapshot ? undefined : displayedSnapshot}
          snapshots={actionSnapshot ? [actionSnapshot] : undefined}
          status={displayedSnapshot ? "loaded" : "unknown"}
          message={strategySnapshots?.length && typeof actionSeat === "number" &&
            actionSeat !== step.state.my_seat
            ? "当前动作座位没有保存可展示的策略快照。"
            : undefined}
          compact
        />
      </section>

      <section className="inspector-section">
        <h3>服务端事件</h3>
        <div data-testid="step-event">{eventType}</div>
        <div className="muted">序号来源：{seqSourceLabel(step.seqSource)}</div>
      </section>

      <section className="inspector-section">
        <h3>本地记录</h3>
        {step.localRequests.length === 0 ? (
          <div className="muted" data-testid="no-local-evidence">无关联旁路请求</div>
        ) : (
          <ul className="inspector-list" data-testid="local-evidence">
            {step.localRequests.map((request, index) => (
              <li key={`${request.type ?? request.kind ?? "record"}-${index}`}>
                {requestLabel(request)}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="inspector-section">
        <h3>诊断</h3>
        {step.diagnostics.length === 0 ? (
          <div className="muted">无</div>
        ) : (
          <ul className="inspector-list" data-testid="step-diagnostics">
            {step.diagnostics.map((diagnostic, index) => (
              <li key={`${diagnostic.code}-${index}`} className={`diagnostic-${diagnostic.severity ?? "warn"}`}>
                {diagnostic.code}{diagnostic.message ? ` · ${diagnostic.message}` : ""}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="inspector-section">
        <h3>与上一步 Diff</h3>
        {diffs.length === 0 ? (
          <div className="muted" data-testid="empty-diff">无变化</div>
        ) : (
          <ul className="inspector-list diff-list" data-testid="state-diff">
            {diffs.map((diff) => (
              <li key={diff.path}>
                <span>{diff.label}</span>
                <span className="diff-value">{displayDiffValue(diff.before)} → {displayDiffValue(diff.after)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </aside>
  );
}
