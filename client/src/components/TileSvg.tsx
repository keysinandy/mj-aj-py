import { tileLabel } from "../replay/frame";
import { tileAssetFor } from "../assets/mahjongAssets";

export type TileSize = "large" | "medium" | "small";

export interface TileSvgProps {
  tile?: number;
  size?: TileSize;
  faceUp?: boolean;
  rotated?: boolean;
  dimmed?: boolean;
  highlighted?: boolean;
  isBack?: boolean;
  redDora?: boolean;
  className?: string;
}

/** 基础牌面。正面使用本地打包的 mahjong_graphic SVG,牌背保留轻量 CSS/SVG 占位。 */
export function TileSvg({
  tile,
  size = "medium",
  faceUp = true,
  rotated = false,
  dimmed = false,
  highlighted = false,
  isBack = !faceUp,
  redDora = false,
  className = "",
}: TileSvgProps) {
  const label = tile === undefined || isBack ? "未知牌" : tileLabel(tile);
  const asset = !isBack && tile !== undefined ? tileAssetFor(tile) : undefined;
  const isCai = !isBack && tile === 33;
  const classes = [
    "tile-svg",
    `tile-svg-${size}`,
    rotated ? "tile-svg-rotated" : "",
    dimmed ? "tile-svg-dimmed" : "",
    highlighted ? "tile-svg-highlighted" : "",
    isBack ? "tile-svg-back" : "",
    isCai ? "tile-svg-cai" : "",
    redDora ? "tile-svg-red-dora" : "",
    className,
  ].filter(Boolean).join(" ");

  if (!isBack) {
    return (
      <span
        className={classes}
        role="img"
        aria-label={label}
        data-tile={tile}
        data-face-up="true"
        data-rotated={rotated}
        title={label}
      >
        {asset ? (
          <img className="tile-svg-image" src={asset} alt="" draggable={false} />
        ) : (
          <span className="tile-svg-missing" aria-hidden="true" />
        )}
        {redDora && <span className="tile-svg-red-mark" aria-hidden="true" />}
      </span>
    );
  }

  return (
    <svg
      className={classes}
      viewBox="0 0 44 62"
      role="img"
      aria-label={label}
      data-tile={tile}
      data-face-up={!isBack}
      data-rotated={rotated}
      focusable="false"
    >
      <rect x="2" y="2" width="40" height="58" rx="4" className="tile-svg-back-face" />
      <path d="M8 13h28M8 24h28M8 35h28M8 46h28" className="tile-svg-back-line" />
    </svg>
  );
}
