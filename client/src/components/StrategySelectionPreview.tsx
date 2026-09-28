import { useEffect, useState } from "react";
import { api, type StrategySnapshotPreview as Preview } from "../service/http";
import { StrategyRuntimePanel } from "./StrategyRuntimePanel";

type State =
  | { status: "loading" }
  | { status: "ready"; preview: Preview }
  | { status: "error"; message: string };

interface Props {
  title: string;
  config: Record<string, unknown>;
}

export function StrategySelectionPreview({ title, config }: Props) {
  const requestBody = JSON.stringify(config);
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let active = true;
    setState({ status: "loading" });
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(requestBody) as Record<string, unknown>;
    } catch {
      setState({ status: "error", message: "策略配置无法解析" });
      return () => { active = false; };
    }
    void api.strategySnapshot(parsed).then((preview) => {
      if (active) setState({ status: "ready", preview });
    }).catch((error: unknown) => {
      if (!active) return;
      setState({
        status: "error",
        message: error instanceof Error ? error.message : "读取策略配置失败",
      });
    });
    return () => { active = false; };
  }, [requestBody]);

  return (
    <section className="strategy-selection-preview" aria-live="polite">
      <div className="strategy-selection-preview-head">
        <strong>{title}</strong>
        <span>{state.status === "ready" ? "当前选择" :
          state.status === "loading" ? "读取配置中…" : "读取失败"}</span>
      </div>
      {state.status === "loading" ? (
        <p className="muted">正在读取服务端解析后的策略配置…</p>
      ) : state.status === "error" ? (
        <p className="error" role="alert">{state.message}</p>
      ) : (
        <>
          {state.preview.model_required && !state.preview.model_available && (
            <p className="error" role="status">
              需要先在设置中选择模型；当前策略配置尚无可用模型。
            </p>
          )}
          <StrategyRuntimePanel
            snapshot={state.preview.snapshot}
            status="configured"
          />
          <small className="muted strategy-selection-preview-note">
            这是当前选择的配置预览；启动后以会话报告的实际加载快照为准。
          </small>
        </>
      )}
    </section>
  );
}
