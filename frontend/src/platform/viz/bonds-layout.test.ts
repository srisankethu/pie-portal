// What the bond strip's playback must never get wrong.
//
// The strip is a chart, so most of it is judged by looking at it. These are the
// parts that cannot be: a mark that fires on the wrong month, a seat that moves
// when the doctrine says it is held, a trail drawn back into a window the
// counterparty did not exist in. All of those look plausible on screen and are
// wrong in a way only a fixture can show.
//
// The fixtures below are shaped like the server's, including the distinction
// that matters most — an absent frame entry (`{}`, dropped) is not the same as
// a present one carrying `score: null`.

import { describe, expect, it } from "vitest";

import {
  MOVEMENT_LOOKBACK, TRAIL_MIN_PTS,
  arrivedAt, buildIndex, firstScoredFrame, frameLine, frameStory, laneTone,
  layout, movementOf, names, packLanes, prepare, presenceAt, sinceFrom,
  toneOf, trailFrom, xOf,
  type Row, type SideData,
} from "./bonds-layout";

const WIDTH = 1000;
const BANDS: Row[] = [
  { band: "THIN", min_score: 0 },
  { band: "LOOSENING", min_score: 30 },
  { band: "STEADY", min_score: 50 },
  { band: "ANCHORED", min_score: 70 },
];

/** `null` = the counterparty had not traded yet, so the server sends no entry
 *  at all. `undefined` score = traded, below the evidence floor. */
type Point = number | null | "unscored";

function frames(series: Record<string, Point[]>, length: number): Row[] {
  return Array.from({ length }, (_, i) => ({
    label: `M${i}`,
    end: `2026-0${(i % 9) + 1}-28`,
    bonds: Object.entries(series).flatMap(([id, points]): Row[] => {
      const p = points[i];
      if (p === null || p === undefined) return [];          // absent
      if (p === "unscored") return [{ counterparty_id: id, score: null, band: null, money: 10 }];
      return [{
        counterparty_id: id, score: p,
        band: p >= 70 ? "ANCHORED" : p >= 50 ? "STEADY" : p >= 30 ? "LOOSENING" : "THIN",
        money: 10 * (i + 1),
      }];
    }),
  }));
}

function side(series: Record<string, Point[]>, length: number,
              money: Record<string, number> = {}): SideData {
  return {
    side: "customer",
    bonds: Object.keys(series).map((id) => ({
      counterparty_id: id, label: `Party ${id}`, score: 60, band: "STEADY",
      money: money[id] ?? 1000, sector: "CUTTING_TOOLS",
    })),
    frames: frames(series, length),
  };
}

const identity = <R,>(list: R[]): R[] => list;
const track = (s: SideData, id: string) => buildIndex(s.frames).get(id);

// ── the three states ────────────────────────────────────────────────────────
describe("presence", () => {
  const s = side({ a: [null, "unscored", 40, 55] }, 4);

  it("tells apart never-traded, traded-but-unmeasured, and scored", () => {
    const t = track(s, "a");
    expect(presenceAt(t, 0)).toBe("absent");
    expect(presenceAt(t, 1)).toBe("unscored");
    expect(presenceAt(t, 2)).toBe("scored");
  });

  it("reads outside the window as absent, not as a score of zero", () => {
    const t = track(s, "a");
    expect(presenceAt(t, -1)).toBe("absent");
    expect(presenceAt(t, 99)).toBe("absent");
  });

  it("finds the first scored frame past a leading unmeasured run", () => {
    expect(firstScoredFrame(track(s, "a"))).toBe(2);
  });

  it("returns null for a counterparty that is never scored", () => {
    const never = side({ a: [null, "unscored", "unscored"] }, 3);
    expect(firstScoredFrame(track(never, "a"))).toBeNull();
  });
});

// ── arrival ─────────────────────────────────────────────────────────────────
describe("arrival", () => {
  it("fires on the first scored frame, not on the first traded one", () => {
    // Traded from frame 1, but below the evidence floor until frame 3.
    const s = side({ a: [null, "unscored", "unscored", 62, 64] }, 5);
    const t = track(s, "a");
    expect([0, 1, 2, 3, 4].map((i) => arrivedAt(t, i)))
      .toEqual([false, false, false, true, false]);
  });

  it("fires at most once across a whole play", () => {
    const s = side({ a: [null, 40, 45, 50, 55] }, 5);
    const t = track(s, "a");
    const hits = [0, 1, 2, 3, 4].filter((i) => arrivedAt(t, i));
    expect(hits).toEqual([1]);
  });

  it("does not re-fire on a hole the backend cannot currently produce", () => {
    // Defensive: the server guarantees [absent]* [unscored]* [scored]*, so this
    // series should be impossible. If it ever became possible, a mark that
    // fired twice would announce the same counterparty arriving two years
    // apart, which is the failure worth being paranoid about.
    const s = side({ a: [40, null, 45] }, 3);
    const t = track(s, "a");
    expect([0, 1, 2].filter((i) => arrivedAt(t, i))).toEqual([0]);
  });
});

// ── the sentence ────────────────────────────────────────────────────────────
describe("the frame line", () => {
  const build = (series: Record<string, Point[]>, at: number, length: number) => {
    const sides = prepare([side(series, length)]);
    const { seat } = packLanes(sides, identity, WIDTH, false);
    const nodes = layout(sides, at, identity, seat, WIDTH);
    return { nodes, story: frameStory(nodes, sides, at, BANDS) };
  };

  it("reports population at the opening frame instead of arrivals", () => {
    // Everyone looks new at frame 0 under a naive test, because there is no
    // earlier frame to have been absent from. That describes where the window
    // was cut, not anything the business did.
    const { story } = build({ a: [55, 56], b: [72, 73] }, 0, 2);
    expect(story.arrived).toEqual([]);
    expect(story.openingPopulation).toBe(2);
    expect(frameLine(story, "M0", BANDS))
      .toContain("2 already scored when the window opens");
  });

  it("counts an arrival from unscored, not only from absent", () => {
    const { story } = build({ a: [55, 56], b: ["unscored", 72] }, 1, 2);
    expect(story.arrived).toEqual(["Party b"]);
  });

  it("does not count an arrival as a band crossing", () => {
    const { story } = build({ b: ["unscored", 72] }, 1, 2);
    expect(story.up).toEqual([]);
    expect(story.down).toEqual([]);
  });

  it("reads the crossing direction from the server's own band edges", () => {
    const { story } = build({ a: [45, 72], b: [72, 45] }, 1, 2);
    expect(story.up).toEqual(["Party a"]);
    expect(story.down).toEqual(["Party b"]);
  });

  it("says so on a quiet month rather than dropping the clause", () => {
    const { story } = build({ a: [55, 56] }, 1, 2);
    expect(frameLine(story, "M1", BANDS)).toContain("nothing crossed a band");
  });

  it("states its truncation instead of trailing off", () => {
    expect(names(["a", "b", "c", "d"])).toBe("a, b +2 more");
    expect(names(["a", "b"])).toBe("a, b");
  });

  it("orders the band tally strongest first and omits empty bands", () => {
    const { story } = build({ a: [72, 74], b: [55, 56] }, 1, 2);
    // Checked on the tally clause alone, because "nothing crossed a band"
    // contains the substring "thin".
    const tally = frameLine(story, "M1", BANDS).split(" — ")[1];
    expect(tally).toBe("1 anchored, 1 steady");
  });
});

// ── the trail ───────────────────────────────────────────────────────────────
describe("the trail", () => {
  it("is not drawn at the opening frame", () => {
    const s = side({ a: [40, 60] }, 2);
    expect(trailFrom(track(s, "a"), 0, WIDTH)).toBeNull();
  });

  it("is drawn for a material move", () => {
    const s = side({ a: [40, 60] }, 2);
    expect(trailFrom(track(s, "a"), 1, WIDTH)).not.toBeNull();
  });

  it("is withheld below the stated floor", () => {
    const small = TRAIL_MIN_PTS - 1;
    const s = side({ a: [40, 40 + small] }, 2);
    expect(trailFrom(track(s, "a"), 1, WIDTH)).toBeNull();
  });

  it("terminates at the first scored frame rather than running to the edge", () => {
    // Frame 1 is traded-but-unmeasured, so there is no measured position to
    // draw back to — a trail from there would be an invented starting point.
    const s = side({ a: [null, "unscored", 62, 70] }, 4);
    expect(trailFrom(track(s, "a"), 2, WIDTH)).toBeNull();
    expect(trailFrom(track(s, "a"), 3, WIDTH)).not.toBeNull();
  });

  it("starts where the dot actually stood last month", () => {
    const s = side({ a: [40, 60] }, 2);
    expect(trailFrom(track(s, "a"), 1, WIDTH)).toBeCloseTo(xOf(40, WIDTH), 6);
  });

  it("is symmetric — a fall is drawn exactly like a rise", () => {
    // The trail is never coloured by its own sign. It carries one month of
    // change, while the fill beside it carries six; two hues on two time-bases
    // in one mark is the ambiguity the palette doctrine exists to prevent.
    const up = side({ a: [40, 60] }, 2);
    const down = side({ a: [60, 40] }, 2);
    expect(trailFrom(track(up, "a"), 1, WIDTH)).toBeCloseTo(xOf(40, WIDTH), 6);
    expect(trailFrom(track(down, "a"), 1, WIDTH)).toBeCloseTo(xOf(60, WIDTH), 6);
  });
});

// ── movement and the since-marker ───────────────────────────────────────────
describe("movement", () => {
  const long = Array.from({ length: 10 }, (_, i) => 40 + i * 2) as Point[];

  it("is null until there is a score a full lookback back", () => {
    const s = side({ a: long }, long.length);
    const t = track(s, "a");
    expect(movementOf(t, MOVEMENT_LOOKBACK - 1)).toBeNull();
    expect(movementOf(t, MOVEMENT_LOOKBACK)).toBe(12);
  });

  it("gives the since-marker nothing to draw exactly when movement is null", () => {
    const s = side({ a: long }, long.length);
    const t = track(s, "a");
    expect(sinceFrom(t, MOVEMENT_LOOKBACK - 1, WIDTH)).toBeNull();
    expect(sinceFrom(t, MOVEMENT_LOOKBACK, WIDTH)).not.toBeNull();
  });

  it("colours only a move worth a claim, and leaves the unmeasurable flat", () => {
    expect(toneOf(null)).toBe("bond-flat");
    expect(toneOf(2)).toBe("bond-flat");
    expect(toneOf(6)).toBe("bond-up");
    expect(toneOf(-6)).toBe("bond-down");
  });

  it("counts the unmeasurable separately from the unmoved in a lane", () => {
    const sides = prepare([side({ a: [40, 42], b: [60, 61] }, 2)]);
    const { seat } = packLanes(sides, identity, WIDTH, false);
    const nodes = layout(sides, 1, identity, seat, WIDTH);
    const tone = laneTone(nodes, 0);
    // Neither has a six-month lookback, so both are flat AND unmeasurable.
    expect(tone.unmeasured).toBe(2);
    expect(tone.up + tone.down).toBe(0);
  });
});

// ── the doctrine: identity is held, only the measure moves ──────────────────
describe("the fixed seat", () => {
  const series: Record<string, Point[]> = {
    a: [30, 45, 60, 75], b: [70, 65, 55, 40], c: [50, 50, 52, 51],
  };
  const sides = prepare([side(series, 4, { a: 5000, b: 2000, c: 900 })]);
  const { seat, labels } = packLanes(sides, identity, WIDTH, false);
  const at = (i: number) => layout(sides, i, identity, seat, WIDTH);

  it("holds every seat identical in every frame", () => {
    // The single invariant the whole playback rests on. If y moves, the play
    // reads as churn and hides the one thing it exists to show.
    const seats = [0, 1, 2, 3].map((i) =>
      Object.fromEntries(at(i).map((n) => [n.id, n.y])));
    for (const later of seats.slice(1)) expect(later).toEqual(seats[0]);
  });

  it("holds every radius identical in every frame", () => {
    // Per-frame money is cumulative, so a radius read from it only ever grows —
    // every dot on the book inflating every month, including the ones being
    // lost. Size is identity here, like the seat.
    const radii = [0, 1, 2, 3].map((i) =>
      Object.fromEntries(at(i).map((n) => [n.id, n.r])));
    for (const later of radii.slice(1)) expect(later).toEqual(radii[0]);
  });

  it("moves x, which is the measure", () => {
    const first = at(0).find((n) => n.id === "a");
    const last = at(3).find((n) => n.id === "a");
    expect(last?.x).toBeGreaterThan(first?.x ?? 0);
  });

  it("chooses the name set without reference to any frame", () => {
    // packLanes takes no playhead — the signature is what enforces it. Ranking
    // names per frame would flicker them on and off as cumulative money crossed
    // a boundary, which is the seat's churn moved into the text layer.
    const again = packLanes(sides, identity, WIDTH, false);
    expect([...again.labels]).toEqual([...labels]);
    expect(labels.has("customer:a")).toBe(true);
  });

  it("caps the names it hands out", () => {
    const many = Object.fromEntries(
      Array.from({ length: 40 }, (_, i) => [`p${i}`, [50 + (i % 7)] as Point[]]));
    const big = prepare([side(many, 1)]);
    const packed = packLanes(big, identity, WIDTH, false);
    expect(packed.labels.size).toBeLessThanOrEqual(12);
  });
});

// ── what never reaches the canvas ───────────────────────────────────────────
describe("what is left off", () => {
  it("draws no dot for a counterparty that has not traded yet", () => {
    const sides = prepare([side({ a: [null, null, 55] }, 3)]);
    const { seat } = packLanes(sides, identity, WIDTH, false);
    expect(layout(sides, 0, identity, seat, WIDTH)).toHaveLength(0);
    expect(layout(sides, 2, identity, seat, WIDTH)).toHaveLength(1);
  });

  it("draws no dot for a counterparty trading below the evidence floor", () => {
    // An unmeasured tie is not a weak one, so it gets no position rather than a
    // place at the left end where it would read as a measured zero.
    const sides = prepare([side({ a: ["unscored", "unscored"] }, 2)]);
    const { seat } = packLanes(sides, identity, WIDTH, false);
    expect(layout(sides, 1, identity, seat, WIDTH)).toHaveLength(0);
  });
});
