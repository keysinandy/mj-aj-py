import type { ReplayStep } from "../replay/session";
import { diffFrames, displayDiffValue } from "../replay/diff";

interface Props {
  step: ReplayStep;
  previous: ReplayStep | null;
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

export function StepInspector({ step, previous }: Props) {
  const diffs = diffFrames(previous?.state ?? null, step.state);
  const eventType = step.event?.type ?? step.state.label ?? "未知";
  return (
    <aside className="step-inspector" data-testid="step-inspector">
      <div className="inspector-title">步骤检查器</div>
      <div className="inspector-step" data-testid="step-number">
        Step #{step.stepIndex}
        <span className="muted">seqNo {step.seqNo ?? "-"}</span>
      </div>

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
