import type { ReactNode } from "react";
import type { ReplayFrame, ReplayVisibilityMode } from "../replay/frame";
import { expandHand, SEATS } from "../replay/frame";
import { MeldSvg } from "./MeldSvg";
import { TileSvg } from "./TileSvg";

interface Props {
  frame: ReplayFrame;
  /** 观察座位(本地可切换,线上强制为 my_seat)。 */
  observeSeat: number;
  /** 本地默认 player; omniscient 只对含四家手牌的本地帧生效。 */
  visibilityMode?: ReplayVisibilityMode;
  /** 放置在牌桌中心的操作区,由回放查看器提供步进控制。 */
  centerControls?: ReactNode;
}

function eventNumber(frame: ReplayFrame, ...keys: string[]): number | null {
  const event = frame.event;
  if (!event) return null;
  for (const key of keys) {
    const value = event[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

function HandTiles({
  counts,
  hidden,
  label,
  knownCount,
  size,
}: {
  counts: number[] | null;
  hidden?: boolean;
  label?: string;
  knownCount?: number | null;
  size: "large" | "medium";
}) {
  if (hidden || counts === null) {
    const backCount = knownCount == null ? 0 : Math.min(Math.max(knownCount, 0), 14);
    return (
      <div
        className="hand-hand unknown-tiles"
        title={knownCount == null ? "他家暗手不可见,张数未知" : `他家暗手不可见,共 ${knownCount} 张`}
        data-known-count={knownCount ?? "unknown"}
      >
        <span className="unknown">未知</span>
        {knownCount != null && <span className="unknown-count">{knownCount} 张</span>}
        {backCount > 0 && (
          <span className="hand-backs" aria-hidden="true">
            {Array.from({ length: backCount }, (_, index) => (
              <TileSvg key={index} size="small" faceUp={false} />
            ))}
          </span>
        )}
      </div>
    );
  }
  return (
    <div className="hand-hand" data-testid="hand-tiles">
      {label && <span className="hand-label">{label}</span>}
      <span className="hand-tiles-svg">
        {expandHand(counts).map((tile, index) => (
          <TileSvg key={`${tile}-${index}`} tile={tile} size={size} />
        ))}
      </span>
    </div>
  );
}

function Melds({
  melds,
  ownerSeat,
  highlighted,
}: {
  melds: ReplayFrame["melds"][number];
  ownerSeat: number;
  highlighted: boolean;
}) {
  return (
    <div className="melds" data-testid="melds">
      {melds.map((meld, index) => (
        <MeldSvg
          key={`${meld.kind}-${index}`}
          meld={meld}
          ownerSeat={ownerSeat}
          highlighted={highlighted && index === melds.length - 1}
        />
      ))}
    </div>
  );
}

function River({
  river,
  highlightTile,
}: {
  river: number[];
  highlightTile: number | null;
}) {
  return (
    <div className="river" data-testid="river">
      {river.map((tile, index) => (
        <TileSvg
          key={`${tile}-${index}`}
          tile={tile}
          size="small"
          highlighted={index === river.length - 1 && tile === highlightTile}
        />
      ))}
    </div>
  );
}

function TableCenter({ frame, controls }: { frame: ReplayFrame; controls?: ReactNode }) {
  const currentSeat = frame.current?.seat;
  const actor = eventNumber(frame, "actor", "seat", "player");
  const actionTile = eventNumber(frame, "tile", "discard", "action");
  const actionText = typeof frame.event?.label === "string"
    ? frame.event.label
    : frame.label;
  return (
    <div className="table-center" data-testid="table-center">
      <div className="table-round-info">第 {frame.round_no} 局 · {frame.label || "进行中"}</div>
      {frame.dora_indicators && frame.dora_indicators.length > 0 && (
        <div className="table-dora">
          宝牌
          {frame.dora_indicators.map((tile, index) => <TileSvg key={`${tile}-${index}`} tile={tile} size="small" />)}
        </div>
      )}
      <div className="table-step-info">Step {frame.step} · seqNo {frame.seq_no ?? "-"}</div>
      <div className="table-last-action">
        最近动作：{actor === null ? "-" : `P${actor}`} {actionText}
        {actionTile !== null && actionTile >= 0 && actionTile < 34 && (
          <span className="table-action-tile" aria-label="最近动作牌">
            <TileSvg tile={actionTile} size="small" />
          </span>
        )}
      </div>
      <div className="table-turn-info">
        当前轮次：{currentSeat === null || currentSeat === undefined ? "-" : `P${currentSeat}`}
      </div>
      {frame.response_window && (
        <div className="response-window" data-testid="response-window">
          响应窗 P{frame.response_window.owner}
        </div>
      )}
      {controls && <div className="table-center-controls">{controls}</div>}
    </div>
  );
}

function positionFor(seat: number, perspective: number): "bottom" | "right" | "top" | "left" {
  const offset = (seat - perspective + SEATS) % SEATS;
  if (offset === 0) return "bottom";
  if (offset === 1) return "right";
  if (offset === 2) return "top";
  return "left";
}

function PlayerArea({
  frame,
  seat,
  position,
  observeSeat,
  visibilityMode,
  recentActor,
  recentTile,
}: {
  frame: ReplayFrame;
  seat: number;
  position: "bottom" | "right" | "top" | "left";
  observeSeat: number;
  visibilityMode: ReplayVisibilityMode;
  recentActor: number | null;
  recentTile: number | null;
}) {
  const isHero = seat === frame.my_seat;
  const perspectiveSeat = frame.info_kind === "local" ? observeSeat : frame.my_seat;
  const isObserve = seat === perspectiveSeat;
  const isTurn = seat === frame.current?.seat;
  const isDealer = seat === frame.dealer;
  const localInfo = frame.info_kind === "local" && frame.hands !== null;
  const omniscient = localInfo && visibilityMode === "omniscient";
  const isPerspective = isObserve;
  const localHand = frame.hands?.[seat]
    ?? (isHero ? frame.my_hand : null);
  const counts = omniscient
    ? localHand
    : isPerspective
      ? localHand
      : null;
  const knownCount = counts === null
    ? frame.hand_counts?.[seat] ?? null
    : counts.reduce((sum, value) => sum + value, 0);
  const hidden = !omniscient && !isPerspective;
  const highlightedMeld = recentActor === seat && frame.event?.type !== "action";
  return (
    <section
      className={`seat player-area player-${position}${isObserve ? " seat-observe" : ""}${isHero ? " seat-my" : ""}${isTurn ? " seat-turn" : ""}${isDealer ? " seat-dealer" : ""}`}
      data-seat={seat}
      data-position={position}
      data-dealer={isDealer}
      data-turn={isTurn}
    >
      <header className="seat-head player-header">
        {isDealer && <span className="dealer-badge" data-testid={`dealer-badge-${seat}`}>（庄）</span>}
        <span>{`${isHero ? "（主）" : ""}座位 P${seat}`}</span>
        {isPerspective && <span className="player-self">{isHero ? "（我）" : "（观察）"}</span>}
        {isObserve && <span className="observe-mark">◈</span>}
        {isTurn && <span className="turn-badge">行动中</span>}
        <span className="player-score">{frame.scores[seat] ?? 0}</span>
      </header>
      <div className="player-content">
        <HandTiles
          counts={counts}
          hidden={hidden}
          label={isPerspective || omniscient ? `手牌 P${seat}` : undefined}
          knownCount={knownCount}
          size={position === "bottom" ? "large" : "medium"}
        />
        <Melds melds={frame.melds[seat]} ownerSeat={seat} highlighted={highlightedMeld} />
        <River river={frame.discards[seat]} highlightTile={recentActor === seat ? recentTile : null} />
      </div>
    </section>
  );
}

export function GameTable({ frame, observeSeat, visibilityMode = "player", centerControls }: Props) {
  const localInfo = frame.info_kind === "local" && frame.hands !== null;
  const omniscient = localInfo && visibilityMode === "omniscient";
  // 本地可切换观察座位;线上始终以 my_seat 为底部且不允许全知。
  const perspective = localInfo ? Math.max(0, Math.min(SEATS - 1, Math.round(observeSeat))) : frame.my_seat;
  const recentActor = eventNumber(frame, "actor", "seat", "player");
  const recentTile = eventNumber(frame, "tile", "discard", "action");
  return (
    <div
      className="game-table"
      data-info-kind={frame.info_kind}
      data-step={frame.step}
      data-perspective-seat={perspective}
      data-visibility-mode={omniscient ? "omniscient" : "player"}
    >
      <div className="table-meta">
        <span data-testid="round">第 {frame.round_no} 局</span>
        <span data-testid="wall">墙 {frame.wall_remaining}</span>
        <span data-testid="scores">{frame.scores.map((score, seat) => `P${seat} ${score}`).join("  ")}</span>
        <span data-testid="label">{frame.label}</span>
        {frame.seq_no !== undefined && <span data-testid="seq-no">seq {frame.seq_no ?? "-"}</span>}
        {frame.gap && <span className="gap-flag">(缺口)</span>}
      </div>
      <div className="table-seats" data-testid="table-seats">
        <TableCenter frame={frame} controls={centerControls} />
        {Array.from({ length: SEATS }).map((_, seat) => (
          <PlayerArea
            key={seat}
            frame={frame}
            seat={seat}
            position={positionFor(seat, perspective)}
            observeSeat={perspective}
            visibilityMode={omniscient ? "omniscient" : "player"}
            recentActor={recentActor}
            recentTile={recentTile}
          />
        ))}
      </div>
    </div>
  );
}
