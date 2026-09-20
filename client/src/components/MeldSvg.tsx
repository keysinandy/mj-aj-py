import type { MeldEntry } from "../replay/frame";
import { TileSvg } from "./TileSvg";

export type MeldKind = "chow" | "pong" | "kong_open" | "kong_closed" | "kong_add";

const KIND_LABEL: Record<string, string> = {
  chow: "吃",
  chi: "吃",
  pong: "碰",
  peng: "碰",
  kong_open: "明杠",
  kan_open: "明杠",
  kong_closed: "暗杠",
  kan_closed: "暗杠",
  kong_add: "加杠",
  kan_added: "加杠",
};

function normalizedKind(kind: string): MeldKind {
  if (kind === "chi") return "chow";
  if (kind === "peng") return "pong";
  if (kind === "kan_open") return "kong_open";
  if (kind === "kan_closed") return "kong_closed";
  if (kind === "kan_added") return "kong_add";
  return (kind as MeldKind) || "pong";
}

function visibleTiles(meld: MeldEntry): number[] {
  const kind = normalizedKind(meld.kind);
  const tiles = [...meld.tiles];
  const expected = kind === "chow" ? 3 : kind.startsWith("kong") ? 4 : 3;
  if (tiles.length === 1) return Array.from({ length: expected }, () => tiles[0]);
  return tiles.slice(0, expected);
}

function calledIndex(meld: MeldEntry, ownerSeat: number): number | null {
  const explicit = meld.called_index ?? (meld.called_tile === undefined
    ? undefined
    : meld.tiles.indexOf(meld.called_tile));
  if (explicit !== undefined && explicit >= 0) return explicit;
  if (normalizedKind(meld.kind) === "kong_closed") return null;
  // 没有来源家时不要臆造横牌位置;旧日志仍可安全展示三张竖牌。
  if (meld.from_seat === undefined || meld.from_seat === null) return null;
  // 来源家规则固定为相对位置映射：右家/对家/左家分别落在右/中/左。
  const relative = (meld.from_seat - ownerSeat + 4) % 4;
  if (relative === 1) return 2;
  if (relative === 2) return 1;
  if (relative === 3) return 0;
  return null;
}

export interface MeldSvgProps {
  meld: MeldEntry;
  ownerSeat: number;
  highlighted?: boolean;
}

export function MeldSvg({ meld, ownerSeat, highlighted = false }: MeldSvgProps) {
  const kind = normalizedKind(meld.kind);
  const tiles = visibleTiles(meld);
  const called = calledIndex(meld, ownerSeat);
  const kindLabel = KIND_LABEL[meld.kind] ?? meld.kind;
  const sourceLabel = meld.from_seat === undefined ? "" : ` · 来自 P${meld.from_seat}`;
  return (
    <div
      className={`meld-svg meld-svg-${kind}${highlighted ? " meld-svg-highlighted" : ""}`}
      data-meld-kind={kind}
      data-from-seat={meld.from_seat ?? ""}
      data-called-index={called ?? ""}
      role="group"
      aria-label={`${kindLabel}${sourceLabel}`}
      title={`${kindLabel}${sourceLabel}`}
    >
      <span className="meld-svg-tiles">
        {tiles.map((tile, index) => (
          <TileSvg
            key={`${tile}-${index}`}
            tile={kind === "kong_closed" && (index === 0 || index === tiles.length - 1) ? undefined : tile}
            isBack={kind === "kong_closed" && (index === 0 || index === tiles.length - 1)}
            size="small"
            rotated={called === index}
            highlighted={highlighted}
          />
        ))}
      </span>
    </div>
  );
}
