// A cycle line must never cross a month nobody could compute.
//
// The server withholds the whole cycle for any month whose stock, collection or
// supplier leg it cannot state — most often because the platform was not yet
// writing stock snapshots down. The chart is the last place that refusal can be
// undone, and undoing it does not look like a bug: a polyline through a gap is
// a smooth, confident line with an invented value at every month it crosses.

import { describe, expect, it } from "vitest";

import { extentOf, statedRuns, type Point } from "./cycle-layout";

describe("statedRuns", () => {
  it("breaks the line at every unstated month", () => {
    const points: Point[] = [10, 12, null, null, 20, 22, 21];
    expect(statedRuns(points)).toEqual([[0, 1], [4, 5, 6]]);
  });

  it("keeps a stated month standing alone between two gaps", () => {
    // Dropped, the shortest real series — one observed month — would render as
    // nothing, which reads as "no data" rather than "one month of data".
    expect(statedRuns([null, 14, null])).toEqual([[1]]);
  });

  it("never joins across a gap, however narrow", () => {
    const runs = statedRuns([5, null, 6]);
    expect(runs).toEqual([[0], [2]]);
    expect(runs.some((r) => r.includes(0) && r.includes(2))).toBe(false);
  });

  it("draws nothing when nothing was stated", () => {
    expect(statedRuns([null, null, null])).toEqual([]);
  });

  it("treats a zero-day cycle as a reading, not a gap", () => {
    // `Number(null)` is 0, so a renderer that tested truthiness instead of
    // nullity would silently delete a real and interesting month: a business
    // whose suppliers exactly fund its working capital.
    expect(statedRuns([0, 0])).toEqual([[0, 1]]);
  });
});

describe("extentOf", () => {
  it("holds the stacks and the line together", () => {
    expect(extentOf([40, 60], [30, 20], [10, 40])).toEqual([-30, 60]);
  });

  it("reaches below zero for a negative cycle", () => {
    // Suppliers funding more than stock and collections tie up. The line goes
    // under the rule, and an extent taken from the bars alone would draw it off
    // the bottom of its own axis — which is the one reading that most needs
    // showing.
    expect(extentOf([20], [90], [-70])).toEqual([-90, 20]);
  });

  it("always includes zero, so the rule is never off-chart", () => {
    expect(extentOf([30], [0], [30])).toEqual([0, 30]);
  });

  it("ignores unstated months rather than reading them as zero", () => {
    expect(extentOf([50], [10], [null, 50])).toEqual([-10, 50]);
  });
});
