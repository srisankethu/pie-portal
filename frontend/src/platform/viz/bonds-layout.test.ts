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
  arrivedAt, biggestMoney, buildIndex, firstScoredFrame, frameLine, frameStory,
  laneTone,
  layout, movementOf, nameBox, names, packLanes, prepare, presenceAt,
  radiusScale,
  sinceFrom,
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

// ── the name layer ──────────────────────────────────────────────────────────
//
// A chart is judged by looking at it, and this is the one part of the strip
// where looking was not enough: the names were placed by a rule that tested a
// label against other *labels* only, on the argument that a halo carries a name
// across a mark. It does not. Three of four names on a four-customer book were
// drawn through a dot, at 1440px, and that shipped — because every fixture here
// described where a dot sits and none described where its name goes.
//
// These fixtures set each bond's own `score` to its last frame's, which the
// ones above do not. `packLanes` reads the bond and `layout` reads the frame,
// and the two are the same number on real data — the strip is packed from
// today's scores. A fixture that leaves them apart stacks every dot at one x
// and quietly stops testing placement at all.
describe("where a name is drawn", () => {
  function book(scores: Record<string, number>, money: Record<string, number>): SideData {
    const s = side(Object.fromEntries(
      Object.entries(scores).map(([id, v]) => [id, [v] as Point[]])), 1, money);
    return {
      ...s,
      bonds: s.bonds.map((b) => ({
        ...b, score: scores[String(b.counterparty_id)],
      })),
    };
  }

  /** Every labelled name, with the box it occupies and the dot it belongs to. */
  function placeOf(sides: ReturnType<typeof prepare>) {
    const { seat, labels } = packLanes(sides, identity, WIDTH, false);
    const nodes = layout(sides, 0, identity, seat, WIDTH);
    const placed = [...labels].map(([key, at]) => {
      const node = nodes.find((n) => n.id === key.split(":")[1]);
      if (!node) throw new Error(`labelled ${key} is not on the canvas`);
      return { id: node.id, box: nameBox(node.label, at), node };
    });
    return { nodes, placed };
  }

  // Six counterparties bunched into the middle of the scale, which is where the
  // mound packs tightest and where the old rule failed.
  const crowd = placeOf(prepare([book(
    { a: 62, b: 64, c: 66, d: 68, e: 70, f: 72 },
    { a: 9000, b: 8000, c: 7000, d: 6000, e: 5000, f: 4000 })]));

  it("still hands the names to the biggest, not to the loneliest", () => {
    // The rule this replaced was written the other way once already — drop any
    // candidate without vertical clearance — and it selected for loneliness
    // while claiming to select for size. The crowd above is exactly the shape
    // that catches it: the largest sit in the tightest part of the mound.
    expect(crowd.placed.length).toBeGreaterThan(0);
    expect(crowd.placed.map((p) => p.id)).toContain("a");
  });

  it("never draws a name across another counterparty's dot", () => {
    for (const { box, node } of crowd.placed) {
      for (const other of crowd.nodes) {
        if (other.id === node.id) continue;
        const hits = other.x + other.r > box.x0 && box.x1 > other.x - other.r
          && other.y + other.r > box.y0 && box.y1 > other.y - other.r;
        expect(`${node.id} over ${other.id}: ${hits}`).toBe(`${node.id} over ${other.id}: false`);
      }
    }
  });

  it("never draws one name across another", () => {
    for (let i = 0; i < crowd.placed.length; i += 1) {
      for (let j = i + 1; j < crowd.placed.length; j += 1) {
        const a = crowd.placed[i].box, b = crowd.placed[j].box;
        const hits = a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
        expect(hits).toBe(false);
      }
    }
  });

  it("never runs a name off either edge of the canvas", () => {
    // The anchored end is the crowded one on a healthy book, and a name placed
    // to the right of a dot at 99 has nowhere to go.
    const edge = placeOf(prepare([book({ x: 99, y: 97, z: 2 },
                                       { x: 9000, y: 8000, z: 7000 })]));
    for (const { box } of edge.placed) {
      expect(box.x0).toBeGreaterThanOrEqual(0);
      expect(box.x1).toBeLessThanOrEqual(WIDTH);
    }
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

// ── the size channel, pinned ────────────────────────────────────────────────
//
// A dot's radius is the counterparty's revenue. The scale used to re-domain on
// whatever rows it was handed, which was harmless only while the whole book was
// always drawn. It is wrong per lane — the same money drew at different sizes
// in "By line" than in "Together" — and it became a real defect the moment the
// strip could be narrowed to a handful of watched accounts: those few re-domain
// onto themselves, every one draws at the maximum, and the reading the channel
// exists for is gone. A big dot on the left is material money in a weakening
// relationship, and "big" only means anything measured against the book.
describe("radiusScale", () => {
  it("scales area, not radius, with money", () => {
    const r = radiusScale(100);
    // sqrt: four times the revenue is twice the radius, so the circle a reader
    // perceives is four times the area. Linear would have made it sixteen.
    expect((r(100) - 3) / (r(25) - 3)).toBeCloseTo(2, 5);
  });

  it("gives the same money the same size however much is filtered out", () => {
    // The invariant narrowing depends on. Pinned to the book's largest, one
    // account watched alone keeps the size it had among two hundred.
    const book = radiusScale(1_000_000);
    expect(book(70_000)).toBeLessThan(book(1_000_000));
    expect(radiusScale(1_000_000)(70_000)).toBe(book(70_000));
  });

  it("keeps the smallest visible and the largest bounded", () => {
    const r = radiusScale(1_000_000);
    expect(r(0)).toBe(3);              // never an invisible zero-radius mark
    expect(r(1_000_000)).toBe(13);
  });

  it("survives a book with no money in it", () => {
    expect(radiusScale(0)(0)).toBe(3);
  });
});

describe("biggestMoney", () => {
  it("reads across both sides, so a supplier and a customer share one scale", () => {
    expect(biggestMoney([
      { bonds: [{ money: 10 }, { money: 40 }] },
      { bonds: [{ money: 90 }] },
    ])).toBe(90);
  });

  it("never returns zero, so the scale cannot divide by an empty domain", () => {
    expect(biggestMoney([])).toBe(1);
    expect(biggestMoney([{ bonds: [] }])).toBe(1);
  });
});
