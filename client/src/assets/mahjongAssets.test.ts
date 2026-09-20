import { describe, expect, it } from "vitest";
import { tileAssetFor } from "./mahjongAssets";

describe("mahjong_graphic 牌面资源", () => {
  it("为协议中的 34 个牌 id 都提供本地资源", () => {
    for (let tile = 0; tile < 34; tile += 1) {
      expect(tileAssetFor(tile), `tile ${tile}`).toMatch(/^data:image\/svg\+xml|\/assets\//);
    }
  });

  it("不为未知牌 id 伪造牌面资源", () => {
    expect(tileAssetFor(-1)).toBeUndefined();
    expect(tileAssetFor(34)).toBeUndefined();
  });
});
