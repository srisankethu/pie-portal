// The size channel, and the one property that makes narrowing the strip safe.
//
// A dot's radius is the counterparty's revenue. The scale used to re-domain on
// whatever rows it was handed, which was harmless only while the whole book was
// always drawn: the moment somebody watches six accounts, re-domaining draws
// all six at the maximum and destroys the reading the channel exists for — a
// big dot on the left is material money in a weakening relationship.
//
// So the domain is pinned to the whole book and passed in. These are the two
// claims that has to keep: area is proportional to money, and the same money is
// the same size whatever is filtered.
import { describe, expect, it } from "vitest";

import { radiusScale } from "./Bonds";

describe("radiusScale", () => {
  it("scales area, not radius, with money", () => {
    const r = radiusScale(100);
    // sqrt: a customer four times the size is twice the radius, so the circle
    // a reader actually perceives is four times the area. A linear scale would
    // have made it sixteen.
    const [r25, r100] = [r(25) - 3, r(100) - 3];   // less the 3px floor
    expect(r100 / r25).toBeCloseTo(2, 5);
  });

  it("gives the same money the same size however much is filtered out", () => {
    // The invariant narrowing depends on. Pinned to the book's largest, one
    // account drawn alone keeps the size it had among two hundred.
    const whole = radiusScale(1_000_000);
    const stillTheWholeBook = radiusScale(1_000_000);
    expect(stillTheWholeBook(70_000)).toBe(whole(70_000));
    // And it is genuinely smaller than the biggest — the bug this replaced
    // would have drawn a lone 70k account at the maximum.
    expect(whole(70_000)).toBeLessThan(whole(1_000_000));
  });

  it("keeps the smallest dot visible and the largest bounded", () => {
    const r = radiusScale(1_000_000);
    expect(r(0)).toBe(3);              // never a zero-radius, invisible mark
    expect(r(1_000_000)).toBe(13);
  });

  it("survives an empty or zero-money book without dividing by zero", () => {
    const r = radiusScale(0);
    expect(Number.isFinite(r(0))).toBe(true);
    expect(r(0)).toBe(3);
  });
});
