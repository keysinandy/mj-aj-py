import { useEffect } from "react";
import { useReplayStore } from "../replay/replayStore";
import { entriesFromFrames, entriesFromSteps } from "../replay/timeline";
import { GameTable } from "./GameTable";
import { Timeline } from "./Timeline";
import { ReplayControls } from "./ReplayControls";
import { StepInspector } from "./StepInspector";

/** 本地全知模式下允许切换观察座位;线上由 info_kind 决定,组件内已强制。 */
export function ReplayViewer() {
  const frames = useReplayStore((s) => s.frames);
  const index = useReplayStore((s) => s.index);
  const observeSeat = useReplayStore((s) => s.observeSeat);
  const stepForward = useReplayStore((s) => s.stepForward);
  const stepBack = useReplayStore((s) => s.stepBack);
  const jumpTo = useReplayStore((s) => s.jumpTo);
  const setObserveSeat = useReplayStore((s) => s.setObserveSeat);
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
      </div>
      <div className="replay-body">
        <div className="replay-table">
          <GameTable frame={frame} observeSeat={observeSeat} />
        </div>
        <Timeline entries={entries} currentIndex={index} onSelect={(i) => jumpTo(i)} />
        <StepInspector step={currentStep} previous={previousStep} />
      </div>
    </div>
  );
}
