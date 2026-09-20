import { useEffect } from "react";
import { useReplayStore } from "../replay/replayStore";
import { entriesFromFrames, entriesFromSteps } from "../replay/timeline";
import { GameTable } from "./GameTable";
import { Timeline } from "./Timeline";
import { ReplayControls } from "./ReplayControls";
import { StepInspector } from "./StepInspector";

/** 本地允许切换观察座位与信息模式;线上由 info_kind 强制为玩家视角。 */
export function ReplayViewer() {
  const frames = useReplayStore((s) => s.frames);
  const index = useReplayStore((s) => s.index);
  const observeSeat = useReplayStore((s) => s.observeSeat);
  const visibilityMode = useReplayStore((s) => s.visibilityMode);
  const stepForward = useReplayStore((s) => s.stepForward);
  const stepBack = useReplayStore((s) => s.stepBack);
  const jumpTo = useReplayStore((s) => s.jumpTo);
  const setObserveSeat = useReplayStore((s) => s.setObserveSeat);
  const setVisibilityMode = useReplayStore((s) => s.setVisibilityMode);
  const firstStep = useReplayStore((s) => s.firstStep);
  const lastStep = useReplayStore((s) => s.lastStep);
  const jumpToSeqNo = useReplayStore((s) => s.jumpToSeqNo);
  const playing = useReplayStore((s) => s.playing);
  const speed = useReplayStore((s) => s.speed);
  const togglePlaying = useReplayStore((s) => s.togglePlaying);
  const session = useReplayStore((s) => s.session);

  useEffect(() => {
    if (!playing || frames.length < 2) return undefined;
    const timer = window.setInterval(() => stepForward(), Math.max(100, 1000 / speed));
    return () => window.clearInterval(timer);
  }, [frames.length, playing, speed, stepForward]);

  const frame = frames[index] ?? null;
  if (!frame) {
    return <p className="replay-empty">没有可回放的记录。</p>;
  }

  const steps = session?.steps ?? [];
  const currentStep = steps[index] ?? {
    stepIndex: index,
    seqNo: frame.seq_no ?? frame.step ?? index,
    seqSource: frame.seq_source ?? "derived",
    timestamp: frame.timestamp ?? null,
    event: frame.event ?? null,
    localRequests: frame.local_requests ?? [],
    diagnostics: frame.diagnostics ?? [],
    state: frame,
  };
  const previousStep = index > 0 ? steps[index - 1] ?? null : null;
  const entries = steps.length ? entriesFromSteps(steps) : entriesFromFrames(frames);
  const canUseOmniscient = frame.info_kind === "local" && frame.hands !== null;

  return (
    <div className="replay-viewer">
      <div className="replay-control-row">
        <div className="observe-switch">
          观察座位:
          {[0, 1, 2, 3].map((s) => (
            <button
              key={s}
              onClick={() => setObserveSeat(s)}
              className={s === observeSeat ? "active" : ""}
            >
              P{s}
            </button>
          ))}
        </div>
        <div className="visibility-switch" aria-label="信息视角">
          <span>信息视角:</span>
          <button
            type="button"
            className={visibilityMode === "player" ? "active" : ""}
            onClick={() => setVisibilityMode("player")}
          >
            玩家视角
          </button>
          <button
            type="button"
            className={visibilityMode === "omniscient" ? "active" : ""}
            onClick={() => setVisibilityMode("omniscient")}
            disabled={!canUseOmniscient}
            title={canUseOmniscient ? "显示本地记录中已知的四家手牌" : "当前记录固定为玩家视角"}
          >
            全知视角
          </button>
          {frame.info_kind === "online" && <span className="visibility-locked">线上固定</span>}
        </div>
      </div>
      <div className="replay-body">
        <div className="replay-table">
          <GameTable
            frame={frame}
            observeSeat={observeSeat}
            visibilityMode={visibilityMode}
            centerControls={(
              <ReplayControls
                index={index}
                total={frames.length}
                onStepBack={() => stepBack()}
                onStepForward={() => stepForward()}
                onJump={(i) => jumpTo(i)}
                onFirst={() => firstStep()}
                onLast={() => lastStep()}
                playing={playing}
                onTogglePlaying={() => togglePlaying()}
                seqNo={currentStep.seqNo}
                onJumpSeqNo={(seqNo) => jumpToSeqNo(seqNo)}
              />
            )}
          />
        </div>
        <Timeline entries={entries} currentIndex={index} onSelect={(i) => jumpTo(i)} />
        <StepInspector step={currentStep} previous={previousStep} />
      </div>
    </div>
  );
}
