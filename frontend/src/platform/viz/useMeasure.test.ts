// Thinning a time axis, and the two ways it goes wrong at the ends.
//
// `thinLabels` is read by four charts and had never been tested. Both of its
// failure modes are at the ends and both are invisible in a code review: drop
// the last label and the axis stops saying where it ends; keep it *and* the
// modulo label beside it and two dates render on top of each other, which is
// worse than either, because overlapping text reads as one wrong date.

import { describe, expect, it } from "vitest";

import { thinLabels } from "./useMeasure";
import type { Room } from "./useMeasure";

const room = (width: number): Room => ({
  width, height: 300, cramped: width < 420, tight: width < 680,
});

const kept = (items: string[], width: number, perLabel?: number) =>
  thinLabels(items, room(width), perLabel).filter((v): v is string => v !== null);

describe("thinLabels", () => {
  it("keeps everything when everything fits", () => {
    const items = ["a", "b", "c"];
    expect(thinLabels(items, room(900), 100)).toEqual(items);
  });

  it("always keeps the first and the last", () => {
    const items = Array.from({ length: 26 }, (_, i) => `w${i}`);
    const out = kept(items, 400, 100);
    expect(out[0]).toBe("w0");
    expect(out[out.length - 1]).toBe("w25");
  });

  it("never leaves a label crowded against the last one", () => {
    // Thirteen weeks in the room for four labels: the step is four, and the
    // naive rule keeps index 12 as the last *and* index 12 by modulo — but on
    // fourteen it would keep 12 and 13 side by side. This is the case that
    // drew "16 Nov" over "23 Nov" on the journey sheet.
    const items = Array.from({ length: 14 }, (_, i) => `w${i}`);
    const marked = thinLabels(items, room(400), 100);
    const indices = marked
      .map((v, i) => (v === null ? -1 : i))
      .filter((i) => i >= 0);
    const gaps = indices.slice(1).map((v, i) => v - indices[i]);
    expect(Math.min(...gaps)).toBeGreaterThan(1);
    expect(indices[indices.length - 1]).toBe(13);
  });

  it("thins harder in a narrower panel", () => {
    const items = Array.from({ length: 26 }, (_, i) => `w${i}`);
    expect(kept(items, 1200, 60).length)
      .toBeGreaterThan(kept(items, 300, 60).length);
  });

  it("has nothing to thin in an empty series", () => {
    expect(thinLabels([], room(900))).toEqual([]);
  });
});
