// The four ways a cash channel can be drawn wrong and still look right.
//
// A channel is a shape, and a shape hides its own defects. Drawn a week out of
// step it is still a smooth channel; measured between the wrong pair of edges
// it is still a plausible width; anchored at a level rather than at the datum
// it is still a continuous route — and that last one turns a movement into a
// cash position, which is the single claim this whole screen must never make.
// None of the four is visible on the sheet, so all four are pinned here.

import { describe, expect, it } from "vitest";

import {
  banded, channelOutline, extentOf, futureLegs, indexOfWeek, pastLegs,
  peakFlow, routePoints, sectionIndices, widestEnvelope,
} from "./channel-layout";
import type { FutureLeg, Row } from "./channel-layout";

/** A committed week as the server sends it. */
const week = (startsOn: string, inflow: number, outflow: number,
              cumulative: number): Row => ({
  starts_on: startsOn, inflow, outflow, net: inflow - outflow,
  cumulative, inflow_documents: 1, outflow_documents: 1,
});

const COMMITTED = [
  week("2026-09-07", 100, 40, 60),
  week("2026-09-14", 20, 90, -10),
  week("2026-09-21", 50, 10, 30),
];
const BEST = [
  week("2026-09-07", 100, 0, 100),
  week("2026-09-14", 20, 50, 70),
  week("2026-09-21", 50, 0, 120),
];
const EXPECTED = COMMITTED;
const WORST = [
  week("2026-09-07", 0, 40, -40),
  week("2026-09-14", 0, 90, -130),
  week("2026-09-21", 60, 10, -80),
];

describe("banded", () => {
  it("needs all three corners, at the committed weeks' own length", () => {
    expect(banded(COMMITTED, BEST, EXPECTED, WORST)).toBe(true);
    expect(banded(COMMITTED, [], EXPECTED, WORST)).toBe(false);
    expect(banded(COMMITTED, BEST.slice(1), EXPECTED, WORST)).toBe(false);
    expect(banded([], [], [], [])).toBe(false);
  });
});

describe("futureLegs", () => {
  it("reads each scenario onto the week it belongs to", () => {
    const legs = futureLegs(COMMITTED, BEST, EXPECTED, WORST);
    expect(legs.map((l) => l.startsOn))
      .toEqual(["2026-09-07", "2026-09-14", "2026-09-21"]);
    expect(legs.map((l) => l.best)).toEqual([100, 70, 120]);
    expect(legs.map((l) => l.worst)).toEqual([-40, -130, -80]);
    // Positional reads are only safe while the four series describe the same
    // Mondays. They do — one loop over one list builds them — and this is the
    // assertion that would catch it if they ever stopped.
    legs.forEach((leg, i) => {
      expect(String(BEST[i].starts_on)).toBe(leg.startsOn);
      expect(String(WORST[i].starts_on)).toBe(leg.startsOn);
    });
  });

  it("keeps the due-date reading separate from every measured one", () => {
    // `onTerms` is the only column that asserts nothing beyond the documents,
    // so it must survive as its own number rather than being collapsed into
    // `expected` when the two happen to coincide.
    const legs = futureLegs(COMMITTED, BEST, EXPECTED, WORST);
    expect(legs.map((l) => l.onTerms)).toEqual([60, -10, 30]);
  });

  it("leaves the envelope null, never zero, where nothing is measured", () => {
    // Zero is a width, and a width is something somebody funds a week against.
    // "We cannot measure how these parties move" is not a narrow channel.
    const legs = futureLegs(COMMITTED, [], [], []);
    expect(legs.map((l) => l.envelope)).toEqual([null, null, null]);
    expect(legs.map((l) => l.best)).toEqual([null, null, null]);
    expect(legs.every((l) => l.onTerms !== null)).toBe(true);
  });

  it("measures the envelope between the two corners and nothing else", () => {
    const legs = futureLegs(COMMITTED, BEST, EXPECTED, WORST);
    expect(legs.map((l) => l.envelope)).toEqual([140, 200, 200]);
  });
});

describe("pastLegs", () => {
  it("carries the movement the server measured back from the datum", () => {
    const past = pastLegs({
      buckets: [
        { ...week("2026-08-24", 10, 0, -50), partial: false },
        { ...week("2026-08-31", 30, 0, -20), partial: false },
        { ...week("2026-09-07", 20, 0, 0), partial: true },
      ],
    });
    expect(past.map((p) => p.movement)).toEqual([-50, -20, 0]);
    // The last past week ends at the datum, at zero. This is what makes the
    // two halves of the drawing meet at a point of value zero rather than at
    // a level, which would read as a cash position.
    expect(past[past.length - 1].movement).toBe(0);
    expect(past.map((p) => p.partial)).toEqual([false, false, true]);
  });

  it("is empty rather than broken when no history was sent", () => {
    expect(pastLegs(null)).toEqual([]);
    expect(pastLegs(undefined)).toEqual([]);
    expect(pastLegs({})).toEqual([]);
  });
});

describe("extentOf", () => {
  it("holds every route, including the worst edge", () => {
    // The worst edge is the deepest by construction, and it is the number the
    // sheet exists to show. An extent taken from the baseline alone draws it
    // off the bottom of its own axis.
    const legs = futureLegs(COMMITTED, BEST, EXPECTED, WORST);
    expect(extentOf([], legs)).toEqual([-130, 120]);
  });

  it("always contains the datum's zero", () => {
    const legs = futureLegs([week("2026-09-07", 10, 0, 10)], [], [], []);
    expect(extentOf([], legs)).toEqual([0, 10]);
  });

  it("reaches back over the travelled route too", () => {
    const past = pastLegs({
      buckets: [week("2026-08-31", 0, 0, -400), week("2026-09-07", 0, 0, 0)],
    });
    const legs = futureLegs(COMMITTED, BEST, EXPECTED, WORST);
    expect(extentOf(past, legs)).toEqual([-400, 120]);
  });
});

describe("peakFlow", () => {
  it("scales both lanes and both halves against one number", () => {
    const past = pastLegs({ buckets: [week("2026-08-31", 500, 20, 0)] });
    const legs = futureLegs(COMMITTED, BEST, EXPECTED, WORST);
    expect(peakFlow(past, legs)).toBe(500);
  });

  it("never returns zero, so a book with no flows draws no full-length arrow", () => {
    expect(peakFlow([], [])).toBe(1);
    expect(peakFlow([], futureLegs([week("2026-09-07", 0, 0, 0)], [], [], [])))
      .toBe(1);
  });
});

describe("widestEnvelope", () => {
  it("finds the widest week, not the last one", () => {
    const legs = futureLegs(COMMITTED, BEST, EXPECTED, WORST);
    const widest = widestEnvelope(legs);
    // Two weeks tie at 200; the first is kept, so the answer does not depend on
    // which way a comparison happens to break.
    expect(widest).toEqual({ index: 1, width: 200, startsOn: "2026-09-14" });
  });

  it("is null where there is no channel at all", () => {
    expect(widestEnvelope(futureLegs(COMMITTED, [], [], []))).toBeNull();
    expect(widestEnvelope([])).toBeNull();
  });
});

describe("indexOfWeek", () => {
  it("locates the server's own week rather than recomputing one", () => {
    const legs = futureLegs(COMMITTED, BEST, EXPECTED, WORST);
    expect(indexOfWeek(legs, "2026-09-14")).toBe(1);
  });

  it("returns −1 for a week the horizon does not contain", () => {
    const legs = futureLegs(COMMITTED, BEST, EXPECTED, WORST);
    expect(indexOfWeek(legs, "2026-12-25")).toBe(-1);
    expect(indexOfWeek(legs, null)).toBe(-1);
    expect(indexOfWeek(legs, "")).toBe(-1);
  });
});

describe("sectionIndices", () => {
  it("spaces the cuts evenly and ends on the horizon's last week", () => {
    const legs = futureLegs(
      Array.from({ length: 13 }, (_, i) => week(`w${i}`, 0, 0, 0)),
      Array.from({ length: 13 }, (_, i) => week(`w${i}`, 0, 0, i)),
      Array.from({ length: 13 }, (_, i) => week(`w${i}`, 0, 0, 0)),
      Array.from({ length: 13 }, (_, i) => week(`w${i}`, 0, 0, -i)));
    expect(sectionIndices(legs, 2)).toEqual([6, 12]);
    expect(sectionIndices(legs, 1)).toEqual([12]);
  });

  it("never cuts through a week with no width to dimension", () => {
    // A section through a route is a line. Dimensioning it would print a
    // figure for a width the server refused to state.
    const legs = futureLegs(COMMITTED, [], [], []);
    expect(sectionIndices(legs, 2)).toEqual([]);
  });

  it("has nothing to cut on a one-week horizon", () => {
    expect(sectionIndices(futureLegs([COMMITTED[0]], [], [], []), 2))
      .toEqual([]);
    expect(sectionIndices([], 2)).toEqual([]);
  });
});

describe("channelOutline", () => {
  const x = (i: number) => 10 + i * 10;
  const y = (v: number) => 100 - v;

  it("opens at the datum, at zero, on both edges", () => {
    const legs = futureLegs(COMMITTED, BEST, EXPECTED, WORST);
    const d = channelOutline(legs, x, y, 5);
    // At `as_of` nothing has moved under any timing, so the mouth of the
    // channel is a point on the datum rather than an already-open gap.
    expect(d.startsWith("M5,100 ")).toBe(true);
    expect(d.endsWith("L5,100 Z")).toBe(true);
  });

  it("runs out along the best edge and back along the worst", () => {
    const legs = futureLegs(COMMITTED, BEST, EXPECTED, WORST);
    const d = channelOutline(legs, x, y, 5);
    expect(d).toBe(
      "M5,100 L10,0 L20,30 L30,-20 L30,180 L20,230 L10,140 L5,100 Z");
  });

  it("draws nothing rather than a sliver when there is no band", () => {
    expect(channelOutline(futureLegs(COMMITTED, [], [], []), x, y, 5)).toBe("");
    expect(channelOutline([], x, y, 5)).toBe("");
  });
});

describe("routePoints", () => {
  const x = (i: number) => i * 10;
  const y = (v: number) => 50 - v;

  it("leaves the datum before the first week, so the route is continuous", () => {
    expect(routePoints([10, 20], x, y, { x: -5, value: 0 }))
      .toBe("-5,50 0,40 10,30");
  });

  it("draws nothing where any value is missing", () => {
    // Not a line through the gap: an interpolated segment asserts a value for
    // every week it crosses, which is the refusal the server just made.
    const values: (number | null)[] = [10, null, 30];
    expect(routePoints(values, x, y)).toBe("");
    expect(routePoints([], x, y)).toBe("");
  });

  it("treats zero as a reading rather than as a gap", () => {
    const values: (number | null)[] = [0, 0];
    expect(routePoints(values, x, y)).toBe("0,50 10,50");
  });
});

describe("the two halves of one drawing", () => {
  it("meet at zero, so neither half states a level", () => {
    // The property the whole sheet rests on. The past series ends at zero
    // because `cashflow.actual` runs its total back from the datum, and the
    // committed one opens from zero because the projection runs forward from
    // it. Anything else — an opening balance, a carried-over level — would be
    // a cash position, and this platform reads payments rather than balances.
    const past = pastLegs({
      buckets: [week("2026-08-31", 0, 0, -80), week("2026-09-07", 0, 0, 0)],
    });
    const legs: FutureLeg[] = futureLegs(COMMITTED, BEST, EXPECTED, WORST);
    expect(past[past.length - 1].movement).toBe(0);
    const first = legs[0];
    // The first committed week is a step from zero, not from a level: its
    // running total is exactly that week's own net.
    expect(first.onTerms).toBe(first.net);
  });
});
