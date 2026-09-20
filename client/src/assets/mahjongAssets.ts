import tile1m from "./mahjong_graphic/1m.svg";
import tile2m from "./mahjong_graphic/2m.svg";
import tile3m from "./mahjong_graphic/3m.svg";
import tile4m from "./mahjong_graphic/4m.svg";
import tile5m from "./mahjong_graphic/5m.svg";
import tile6m from "./mahjong_graphic/6m.svg";
import tile7m from "./mahjong_graphic/7m.svg";
import tile8m from "./mahjong_graphic/8m.svg";
import tile9m from "./mahjong_graphic/9m.svg";
import tile1p from "./mahjong_graphic/1p.svg";
import tile2p from "./mahjong_graphic/2p.svg";
import tile3p from "./mahjong_graphic/3p.svg";
import tile4p from "./mahjong_graphic/4p.svg";
import tile5p from "./mahjong_graphic/5p.svg";
import tile6p from "./mahjong_graphic/6p.svg";
import tile7p from "./mahjong_graphic/7p.svg";
import tile8p from "./mahjong_graphic/8p.svg";
import tile9p from "./mahjong_graphic/9p.svg";
import tile1s from "./mahjong_graphic/1s.svg";
import tile2s from "./mahjong_graphic/2s.svg";
import tile3s from "./mahjong_graphic/3s.svg";
import tile4s from "./mahjong_graphic/4s.svg";
import tile5s from "./mahjong_graphic/5s.svg";
import tile6s from "./mahjong_graphic/6s.svg";
import tile7s from "./mahjong_graphic/7s.svg";
import tile8s from "./mahjong_graphic/8s.svg";
import tile9s from "./mahjong_graphic/9s.svg";
import tileEast from "./mahjong_graphic/1z.svg";
import tileSouth from "./mahjong_graphic/2z.svg";
import tileWest from "./mahjong_graphic/3z.svg";
import tileNorth from "./mahjong_graphic/4z.svg";
import tileWhite from "./mahjong_graphic/5z.svg";
import tileGreen from "./mahjong_graphic/6z.svg";
import tileRed from "./mahjong_graphic/7z.svg";

/**
 * Tile ids follow the replay protocol: 0-8 man, 9-17 pin, 18-26 sou,
 * 27-33 honors (east, south, west, north, red, green, white).
 */
const TILE_ASSETS: Record<number, string> = {
  0: tile1m, 1: tile2m, 2: tile3m, 3: tile4m, 4: tile5m, 5: tile6m,
  6: tile7m, 7: tile8m, 8: tile9m,
  9: tile1p, 10: tile2p, 11: tile3p, 12: tile4p, 13: tile5p, 14: tile6p,
  15: tile7p, 16: tile8p, 17: tile9p,
  18: tile1s, 19: tile2s, 20: tile3s, 21: tile4s, 22: tile5s, 23: tile6s,
  24: tile7s, 25: tile8s, 26: tile9s,
  27: tileEast, 28: tileSouth, 29: tileWest, 30: tileNorth,
  31: tileRed, 32: tileGreen, 33: tileWhite,
};

export function tileAssetFor(tile: number): string | undefined {
  return TILE_ASSETS[tile];
}
