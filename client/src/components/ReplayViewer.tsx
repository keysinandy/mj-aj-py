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
  const rounds = useReplayStore((s) => s.rounds);
  const activeRoundId = useReplayStore((s) => s.activeRoundId);
  const selectRound = useReplayStore((s) => s.selectRound);
  const previousRound = useReplayStore((s) => s.previousRound);
  const nextRound = useReplayStore((s) => s.nextRound);
  const actorFilter = useReplayStore((s) => s.actorFilter);
  const setActorFilter = useReplayStore((s) => s.setActorFilter);
  const previousFilteredStep = useReplayStore((s) => s.previousFilteredStep);
  const nextFilteredStep = useReplayStore((s) => s.nextFilteredStep);
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
  const activeRound = rounds.find((round) => round.roundId === activeRoundId) ?? rounds[0] ?? null;
  const roundStart = activeRound?.startStepIndex ?? 0;
  const roundEnd = activeRound?.endStepIndex ?? Math.max(0, frames.length - 1);
  const roundTotal = Math.max(0, roundEnd - roundStart + 1);
  const roundIndex = Math.max(0, index - roundStart);
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
  const previousStep = index > roundStart ? steps[index - 1] ?? null : null;
  const allEntries = steps.length ? entriesFromSteps(steps) : entriesFromFrames(frames);
  const roundEntries = allEntries.filter((entry) => entry.index >= roundStart && entry.index <= roundEnd);
  const mineEntries = roundEntries.filter((entry) => entry.isMine);
  const entries = actorFilter === "mine" ? mineEntries : roundEntries;
  const canPreviousMine = mineEntries.some((entry) => entry.index < index);
  const canNextMine = mineEntries.some((entry) => entry.index > index);
  const canUseOmniscient = frame.info_kind === "local" && frame.hands !== null;

  return (
    <div className="replay-viewer">
      {rounds.length > 1 && activeRound && (
        <div className="replay-round-nav" data-testid="round-navigation">
          <button
            type="button"
            onClick={() => previousRound()}
            disabled={activeRound.ordinal <= 1}
            data-testid="btn-previous-round"
          >上一场</button>
          <label>
            <span>第 {activeRound.ordinal} / {rounds.length} 场</span>
            <select
              aria-label="场次"
              data-testid="round-select"
              value={activeRound.roundId}
              onChange={(event) => selectRound(event.target.value)}
            >
              {rounds.map((round) => (
                <option key={round.roundId} value={round.roundId}>
                  第 {round.ordinal} 场 · round {round.roundNo}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => nextRound()}
            disabled={activeRound.ordinal >= rounds.length}
            data-testid="btn-next-round"
          >下一场</button>
        </div>
      )}
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
                index={roundIndex}
                total={roundTotal}
                onStepBack={() => stepBack()}
                onStepForward={() => stepForward()}
                onJump={(i) => jumpTo(roundStart + i)}
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
        <div className="timeline-panel">
          <div className="timeline-toolbar" aria-label="时间线筛选与导航">
            <div className="actor-filter" role="group" aria-label="时间线动作筛选">
              <button
                type="button"
                aria-pressed={actorFilter === "all"}
                className={actorFilter === "all" ? "active" : ""}
                onClick={() => setActorFilter("all")}
                data-testid="actor-filter-all"
              >全部</button>
              <button
                type="button"
                aria-pressed={actorFilter === "mine"}
                className={actorFilter === "mine" ? "active" : ""}
                onClick={() => setActorFilter("mine")}
                data-testid="actor-filter-mine"
              >我方动作</button>
            </div>
            <div className="filtered-step-controls">
              <button type="button" onClick={() => previousFilteredStep()} disabled={!canPreviousMine} data-testid="btn-previous-mine">
                上一我方
              </button>
              <button type="button" onClick={() => nextFilteredStep()} disabled={!canNextMine} data-testid="btn-next-mine">
                下一我方
              </button>
            </div>
          </div>
          <Timeline entries={entries} currentIndex={index} onSelect={(i) => jumpTo(i)} />
        </div>
        <StepInspector step={currentStep} previous={previousStep} />
      </div>
    </div>
  );
}
