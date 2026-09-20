import type { ReplayFrame } from "../replay/frame";
import { expandHand, tileLabel, SEATS } from "../replay/frame";

interface Props {
  frame: ReplayFrame;
  /** 观察座位(本地全知可切换,0-3);线上强制为 my_seat。 */
  observeSeat: number;
}

function Tile({ t, highlight }: { t: number; highlight?: boolean }) {
  return <span className={`tile${highlight ? " tile-hl" : ""}`} data-tile={t}>{tileLabel(t)}</span>;
}

function HandTiles({
  counts,
  hidden,
  label,
}: {
  counts: number[] | null;
  hidden?: boolean;
  label?: string;
}) {
  if (hidden || counts === null) {
    return (
      <div className="hand-hand unknown-tiles" title="他家暗手不可见">
        <span className="unknown">未知</span>
      </div>
    );
  }
  const tiles = expandHand(counts);
  return (
    <div className="hand-hand" data-testid="hand-tiles">
      {label && <span className="hand-label">{label}</span>}
      {tiles.map((t, i) => (
        <Tile key={i} t={t} />
      ))}
    </div>
  );
}

function Melds({ melds }: { melds: ReplayFrame["melds"][number] }) {
  return (
    <div className="melds">
      {melds.map((m, i) => (
        <span key={i} className={`meld meld-${m.kind}`} data-meld-kind={m.kind}>
          {m.kind} [{m.tiles.map(tileLabel).join(" ")}]
        </span>
      ))}
    </div>
  );
}

function River({ river }: { river: number[] }) {
  return (
    <div className="river" data-testid="river">
      {river.map((t, i) => (
        <Tile key={i} t={t} />
      ))}
    </div>
  );
}

export function GameTable({ frame, observeSeat }: Props) {
  const fullInfo = frame.info_kind === "local" && frame.hands !== null;
  return (
    <div className="game-table" data-info-kind={frame.info_kind} data-step={frame.step}>
      <div className="table-meta">
        <span data-testid="round">第 {frame.round_no} 局</span>
        <span data-testid="wall">墙 {frame.wall_remaining}</span>
        <span data-testid="scores">{frame.scores.map((s, i) => "P" + i + " " + s).join("  ")}</span>
        <span data-testid="label">{frame.label}</span>
        {frame.seq_no !== undefined && <span data-testid="seq-no">seq {frame.seq_no ?? "-"}</span>}
        {frame.response_window && (
          <span className="response-window" data-testid="response-window">
            响应窗 P{frame.response_window.owner}
          </span>
        )}
        {frame.gap && <span className="gap-flag">(缺口)</span>}
      </div>
      <div className="table-seats">
        {Array.from({ length: SEATS }).map((_, seat) => {
          const isMy = seat === frame.my_seat;
          const isObserve = seat === observeSeat;
          const hidden = fullInfo ? false : !isMy;
          const counts = fullInfo ? (frame.hands as number[][])[seat] : isMy ? frame.my_hand : null;
          return (
            <div
              key={seat}
              className={`seat${isObserve ? " seat-observe" : ""}${isMy ? " seat-my" : ""}`}
              data-seat={seat}
            >
              <div className="seat-head">
                座位 P{seat}
                {isMy ? " (我)" : ""}
                {isObserve ? " ◈" : ""}
              </div>
              <Melds melds={frame.melds[seat]} />
              <River river={frame.discards[seat]} />
              <HandTiles counts={counts} hidden={hidden} label={isMy || fullInfo ? `手牌 P${seat}` : undefined} />
            </div>
          );
        })}
      </div>
    </div>
  );
}
