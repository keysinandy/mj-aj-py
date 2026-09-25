import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type RefObject,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import type {
  DiscardHint,
  ReplayFrame,
  ReplayVisibilityMode,
  WaitHint,
} from "../replay/frame";
import { expandHand, SEATS, tileLabel } from "../replay/frame";
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

const WAIT_GROUPS: Array<{ key: string; label: string; test: (tile: number) => boolean }> = [
  { key: "man", label: "万子", test: (tile) => tile >= 0 && tile <= 8 },
  { key: "pin", label: "筒子", test: (tile) => tile >= 9 && tile <= 17 },
  { key: "sou", label: "条子", test: (tile) => tile >= 18 && tile <= 26 },
  { key: "honor", label: "字牌", test: (tile) => tile >= 27 && tile <= 33 },
];

function waitGroups(waits: WaitHint[]) {
  return WAIT_GROUPS.map((group) => ({
    ...group,
    waits: waits.filter((wait) => group.test(wait.tile)),
  })).filter((group) => group.waits.length > 0);
}

function WaitList({
  title,
  waits,
  dimmed = false,
}: {
  title: string;
  waits: WaitHint[];
  dimmed?: boolean;
}) {
  return (
    <section className={`wait-hint-section${dimmed ? " wait-hint-section-dimmed" : ""}`}>
      <h4>{title}</h4>
      <div className="wait-hint-groups">
        {waitGroups(waits).map((group) => (
          <div className="wait-hint-group" key={group.key}>
            <span className="wait-hint-group-label">{group.label}</span>
            <span className="wait-hint-group-tiles">
              {group.waits.map((wait) => (
                <span className="wait-hint-tile" key={wait.tile}>
                  <TileSvg tile={wait.tile} size="small" />
                  <span>{wait.unseen} 张</span>
                </span>
              ))}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function DiscardHintPopover({
  tile,
  hint,
  popoverRef,
  style,
  onMouseLeave,
}: {
  tile: number;
  hint: DiscardHint;
  popoverRef: RefObject<HTMLDivElement>;
  style: CSSProperties;
  onMouseLeave: (event: MouseEvent<HTMLDivElement>) => void;
}) {
  const blocked = hint.status === "rule_blocked_tenpai";
  const waits = blocked ? hint.structural_waits : hint.legal_waits;
  const total = blocked ? hint.total_structural_unseen : hint.total_legal_unseen;
  const dead = waits.length > 0 && waits.every((wait) => wait.unseen === 0);
  return (
    <div
      className={`discard-hint-popover${blocked ? " discard-hint-blocked" : ""}`}
      ref={popoverRef}
      style={style}
      role="dialog"
      aria-label={`弃${tileLabel(tile)}后的听口`}
      data-testid="discard-hint-popover"
      data-status={hint.status}
      onMouseLeave={onMouseLeave}
    >
      <div className="wait-hint-title">
        弃 {tileLabel(tile)} 后听牌
        <span className="wait-hint-total">公开未见 {total} 张</span>
      </div>
      {blocked && (
        <p className="wait-hint-warning">
          有财必拷响：普通摸牌胡被规则阻塞，需要爆头或杠开。
        </p>
      )}
      <WaitList title={blocked ? "结构听口（规则阻塞）" : "实际可胡听口"} waits={waits} dimmed={blocked} />
      {!blocked && hint.structural_waits.length !== hint.legal_waits.length && (
        <WaitList title="结构听口" waits={hint.structural_waits} dimmed />
      )}
      {dead && <p className="wait-hint-dead">死听：上述听口均为 0 张。</p>}
      <p className="wait-hint-footnote">数量按本家手牌、四家牌河和副露计算。</p>
    </div>
  );
}

function HintTile({
  tile,
  hint,
  size,
  highlighted = false,
  contextKey,
}: {
  tile: number;
  hint?: DiscardHint;
  size: "large" | "medium";
  highlighted?: boolean;
  contextKey: string;
}) {
  const [open, setOpen] = useState(false);
  const [popoverPosition, setPopoverPosition] = useState<CSSProperties | null>(null);
  const anchorRef = useRef<HTMLSpanElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setOpen(false);
    setPopoverPosition(null);
  }, [contextKey]);

  useLayoutEffect(() => {
    if (!open) return undefined;
    const updatePosition = () => {
      const anchor = anchorRef.current?.getBoundingClientRect();
      const popover = popoverRef.current?.getBoundingClientRect();
      if (!anchor || !popover) return;

      const margin = 10;
      const width = popover.width || Math.min(400, window.innerWidth * 0.72);
      const height = popover.height || Math.min(448, window.innerHeight * 0.7);
      const maxLeft = Math.max(margin, window.innerWidth - width - margin);
      const left = Math.max(
        margin,
        Math.min(anchor.left + anchor.width / 2 - width / 2, maxLeft),
      );
      const below = anchor.bottom + margin;
      const top = below + height <= window.innerHeight - margin
        ? below
        : Math.max(margin, anchor.top - height - margin);
      setPopoverPosition({ left, top, visibility: "visible" });
    };

    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open, hint, contextKey]);

  function closeIfPointerLeavesRegion(event: MouseEvent<HTMLElement>) {
    const target = event.relatedTarget;
    if (target instanceof Node
      && (anchorRef.current?.contains(target) || popoverRef.current?.contains(target))) {
      return;
    }
    setOpen(false);
  }

  if (!hint) return <TileSvg tile={tile} size={size} highlighted={highlighted} />;

  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      setOpen(false);
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setOpen(true);
    }
  }

  return (
    <span
      ref={anchorRef}
      className="discard-hint-anchor"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={closeIfPointerLeavesRegion}
    >
      <TileSvg
        tile={tile}
        size={size}
        highlighted={highlighted}
        tabIndex={0}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
      />
      {open && createPortal(
        <DiscardHintPopover
          tile={tile}
          hint={hint}
          popoverRef={popoverRef}
          style={popoverPosition ?? { left: 0, top: 0, visibility: "hidden" }}
          onMouseLeave={closeIfPointerLeavesRegion}
        />,
        document.body,
      )}
    </span>
  );
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
  discardHints,
  drawnTile,
  drawOrigin,
  contextKey,
}: {
  counts: number[] | null;
  hidden?: boolean;
  label?: string;
  knownCount?: number | null;
  size: "large" | "medium";
  discardHints?: DiscardHint[];
  drawnTile?: number | null;
  drawOrigin?: string | null;
  contextKey: string;
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
  const hintByTile = new Map(
    (discardHints ?? []).map((hint) => [hint.discard_tile, hint]),
  );
  const hasDrawnTile = Number.isInteger(drawnTile)
    && drawnTile! >= 0 && drawnTile! < counts.length && counts[drawnTile!] > 0;
  const standingCounts = hasDrawnTile ? [...counts] : counts;
  if (hasDrawnTile) standingCounts[drawnTile!] -= 1;
  return (
    <div className="hand-hand" data-testid="hand-tiles">
      {label && <span className="hand-label">{label}</span>}
      <span className="hand-tiles-svg">
        {expandHand(standingCounts).map((tile, index) => (
          <HintTile
            key={`${tile}-${index}`}
            tile={tile}
            hint={hintByTile.get(tile)}
            size={size}
            contextKey={contextKey}
          />
        ))}
      </span>
      {hasDrawnTile && (
        <span
          className="hand-drawn-tile"
          data-testid="drawn-tile"
          data-draw-origin={drawOrigin ?? "unknown"}
          title={drawOrigin === "kong_replacement" ? "杠后补牌" : "刚摸入"}
        >
          <span className="drawn-badge">摸</span>
          <HintTile
            tile={drawnTile!}
            hint={hintByTile.get(drawnTile!)}
            size={size}
            highlighted
            contextKey={contextKey}
          />
        </span>
      )}
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
  hintContextKey,
}: {
  frame: ReplayFrame;
  seat: number;
  position: "bottom" | "right" | "top" | "left";
  observeSeat: number;
  visibilityMode: ReplayVisibilityMode;
  recentActor: number | null;
  recentTile: number | null;
  hintContextKey: string;
}) {
  const isHero = seat === frame.my_seat;
  const perspectiveSeat = frame.info_kind === "local" ? observeSeat : frame.my_seat;
  const isObserve = seat === perspectiveSeat;
  const isTurn = seat === frame.current?.seat;
  const isDealer = seat === frame.dealer;
  const isWinner = frame.winner_seats?.includes(seat) ?? false;
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
  const showHints = Boolean(
    isObserve && isTurn && frame.current?.phase === "discard"
    && (!frame.gap || frame.event?.type === "snapshot"),
  );
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
        {isWinner && <span className="winner-badge" data-testid={`winner-badge-${seat}`}>胡</span>}
        <span className="player-score">{frame.scores[seat] ?? 0}</span>
      </header>
      <div className="player-content">
        <HandTiles
          counts={counts}
          hidden={hidden}
          label={isPerspective || omniscient ? `手牌 P${seat}` : undefined}
          knownCount={knownCount}
          size={position === "bottom" ? "large" : "medium"}
          discardHints={showHints ? frame.discard_hints : undefined}
          drawnTile={frame.drawn_seat === seat ? frame.drawn_tile : null}
          drawOrigin={frame.drawn_seat === seat ? frame.draw_origin : null}
          contextKey={hintContextKey}
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
            hintContextKey={`${frame.round_no}:${frame.seq_no ?? frame.step}:${perspective}`}
          />
        ))}
      </div>
    </div>
  );
}
