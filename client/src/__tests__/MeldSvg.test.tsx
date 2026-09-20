import { describe, expect, it } from "vitest";
import { render, within } from "@testing-library/react";
import { MeldSvg } from "../components/MeldSvg";

function rotatedIndexes(fromSeat?: number) {
  const { container } = render(
    <MeldSvg
      ownerSeat={0}
      meld={{ kind: "pong", tiles: [5, 5, 5], from_seat: fromSeat }}
    />,
  );
  const meld = container.querySelector("[data-meld-kind='pong']")!;
  return Array.from(within(meld as HTMLElement).getAllByRole("img"))
    .map((tile) => tile.getAttribute("data-rotated") === "true");
}

describe("MeldSvg 横牌位置", () => {
  it("下家来源放在副露右侧", () => {
    expect(rotatedIndexes(1)).toEqual([false, false, true]);
  });

  it("对家来源放在副露中间", () => {
    expect(rotatedIndexes(2)).toEqual([false, true, false]);
  });

  it("上家来源放在副露左侧", () => {
    expect(rotatedIndexes(3)).toEqual([true, false, false]);
  });

  it("来源未知时不臆造横牌位置", () => {
    expect(rotatedIndexes()).toEqual([false, false, false]);
  });
});
