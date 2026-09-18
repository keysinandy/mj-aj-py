import { useReplayStore } from "../replay/replayStore";
import { entriesFromFrames } from "../replay/timeline";
import { GameTable } from "./GameTable";
import { Timeline } from "./Timeline";
import { ReplayControls } from "./ReplayControls";

/** 本地全知模式下允许切换观察座位;线上由 info_kind 决定,组件内已强制。 */
export function ReplayViewer() {
  const frames = useReplayStore((s) => s.frames);
  const index = useReplayStore((s) => s.index);
  const observeSeat = useReplayStore((s) => s.observeSeat);
  const stepForward = useReplayStore((s) => s.stepForward);
  const stepBack = useReplayStore((s) => s.stepBack);
  const jumpTo = useReplayStore((s) => s.jumpTo);
  const setObserveSeat = useReplayStore((s) => s.setObserveSeat);

  const frame = frames[index] ?? null;
  if (!frame) {
    return <p className="replay-empty">没有可回放的记录。</p>;
  }

  const entries = entriesFromFrames(frames);

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
        />
      </div>
      <div className="replay-body">
        <div className="replay-table">
          <GameTable frame={frame} observeSeat={observeSeat} />
        </div>
        <Timeline entries={entries} currentIndex={index} onSelect={(i) => jumpTo(i)} />
      </div>
    </div>
  );
}