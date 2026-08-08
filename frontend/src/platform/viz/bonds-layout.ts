// The bond strip's geometry and its reading of the frame series.
//
// Split out of `Bonds.tsx` when the playback grew marks that depend on *two*
// frames rather than one — a trail needs last month, an arrival needs to know
// there was no last month. Those are the two places a play can start lying, and
// neither was reachable by a test while it lived inside a 900-line component
// that cannot mount without `ResizeObserver`. Everything here is pure: rows in,
// numbers and strings out. `bonds-layout.test.ts` pins the parts that could be
// wrong without anybody noticing on screen.
//
// **The three states, which the whole file rests on.** The server distinguishes
// them and the canvas cannot, so reading them correctly is this module's job:
//
//   absent    no entry in the frame at all. `bonds.py` appends `{}` for every
//             month before the counterparty's first trade and the frame builder
//             drops it — "there was no relationship yet".
//   unscored  present with `score: null`. They traded, and had fewer than
//             `min_transactions` documents by that month, so there is not
//             enough evidence to describe a rhythm.
//   scored    on the strip.
//
// `layout` skips absent and unscored alike, which is right — `Bonds.tsx`'s
// header says an unmeasured tie gets no position rather than a place at the
// weak end. But they are *not* the same fact, and a mark that treats them as
// one is the defect this module exists to prevent. "First scored" is not "new".
//
// **Each track is `[absent]* [unscored]* [scored]*`, in that order, always.**
// Both transitions are monotone in time: a first-trade date does not move, and
// a document count never decreases. So a dot can only ever enter — it never
// leaves and never re-enters. That is why there is no departure effect and no
// re-arrival: building either would mean inventing an event the data cannot
// produce. The helpers below are still written defensively, and the test pins
// the defensive path against a synthetic track the backend cannot currently
// emit.

import { scaleSqrt } from "d3-scale";

import type { EntityOrigin, Sourced } from "../types";

export type Row = Record<string, unknown>;

export const rows = (v: unknown): Row[] => (v as Row[] | undefined) ?? [];
export const num = (v: unknown): number => Number(v ?? 0);

/** How far back movement is read, in frames. Six months is long enough that a
 *  seasonal dip does not read as a decaying relationship, and short enough that
 *  a real slide shows up before it is a recovery job. */
export const MOVEMENT_LOOKBACK = 6;

/** Below this, a month's move is not drawn as a trail.
 *
 *  Stated in the caption, because a threshold nobody is told about is a claim:
 *  a dot with no trail has not necessarily held still. Three points is the
 *  smallest move that survives a single invoice landing a few days either side
 *  of a month end. */
export const TRAIL_MIN_PTS = 3;

/** Caps on the name layer. `<text>` is the most expensive primitive on the
 *  canvas, and a threshold-gated version returns forty labels on a crowded
 *  book — which is not a labelled chart, it is a wall. */
export const NAMES_PER_LANE = 5;
export const NAMES_TOTAL = 12;
/** Vertical clearance a label needs from the next seat in its own lane. Seats
 *  are `STACK_STEP` apart, so an 11px line of text crosses several of them. */
const CLEAR_PX = 14;

export const PAD_L = 34;
export const PAD_R = 18;
const STACK_STEP = 2;
const MAX_STACK = 70;

/** A plotted node. Everything the strip needs, resolved once per frame.
 *
 *  `y`, `lane` and `r` are the counterparty's identity and do not depend on the
 *  frame; `x`, `score`, `band`, `movement` and `trail` do. That split is what
 *  makes the playback readable — see the header of `Bonds.tsx`. `r` joined the
 *  fixed side when the radius was frozen: per-frame money is cumulative, so
 *  animating it made every dot on the book grow, every time, whatever was
 *  actually happening to it. */
export interface Node {
  id: string;
  label: string;
  sector: string;
  score: number;
  money: number;
  movement: number | null;
  band: string;
  side: string;
  origin?: EntityOrigin;
  overdue: boolean;
  lane: number;
  x: number;
  y: number;
  r: number;
  /** Where this dot stood last month, when the move was worth drawing. */
  trail: number | null;
}

export interface Lane { side: string; label: string; count: number; half: number }
export interface Seat { lane: number; y: number }
export interface SideData { side: string; bonds: Row[]; frames: Row[] }

/** One counterparty's entry per frame, `null` where it has none. */
export type Track = (Row | null)[];
export type FrameIndex = Map<string, Track>;

export interface PreparedSide extends SideData { index: FrameIndex }

// ── the frame index ─────────────────────────────────────────────────────────

/** Turn each side's frame series into `id → entry per frame`.
 *
 *  This replaces a linear `.find()` per node per frame. It was three scans per
 *  node — the layout and both ends of the movement window — over a list that is
 *  the whole book, which is a few hundred thousand string comparisons every
 *  420ms tick before any of the new marks ask for last month as well. Built
 *  once per payload, and deliberately not keyed on the playhead.
 */
export function buildIndex(frames: Row[]): FrameIndex {
  const index: FrameIndex = new Map();
  for (let i = 0; i < frames.length; i += 1) {
    for (const entry of rows(frames[i].bonds)) {
      const id = String(entry.counterparty_id);
      let track = index.get(id);
      if (!track) { track = new Array(frames.length).fill(null); index.set(id, track); }
      track[i] = entry;
    }
  }
  return index;
}

export function prepare(sides: SideData[]): PreparedSide[] {
  return sides.map((s) => ({ ...s, index: buildIndex(s.frames) }));
}

export type Presence = "absent" | "unscored" | "scored";

/** Which of the three states a counterparty is in at frame `i`.
 *
 *  Out of range is `absent`, which is what makes the lookback arithmetic safe
 *  at the front of the window without a special case: six months before frame
 *  two is not "a score of zero", it is outside what was measured. */
export function presenceAt(track: Track | undefined, i: number): Presence {
  if (!track || i < 0 || i >= track.length) return "absent";
  const entry = track[i];
  if (entry == null) return "absent";
  if (entry.score == null) return "unscored";
  return "scored";
}

/** The first frame this counterparty was scored in, or `null` if never.
 *
 *  Scanned rather than bisected: a window is at most 36 frames, and a scan is
 *  correct even if the series ever grew a hole, where a bisection would not
 *  be. */
export function firstScoredFrame(track: Track | undefined): number | null {
  if (!track) return null;
  for (let i = 0; i < track.length; i += 1) {
    if (presenceAt(track, i) === "scored") return i;
  }
  return null;
}

/** Did this counterparty's first scored frame land exactly here?
 *
 *  The oracle the cheap per-frame test in `frameLine` is checked against. Fires
 *  at most once per counterparty over a whole play, by construction. */
export function arrivedAt(track: Track | undefined, at: number): boolean {
  return firstScoredFrame(track) === at;
}

// ── the marks that need two frames ──────────────────────────────────────────

/** Where the dot stood last month, if the move is worth a mark.
 *
 *  `null` in four distinct cases, all of them correct and all of them falling
 *  out of the same two tests: at the very first frame; where last month has no
 *  entry; where last month was traded-but-unscored; and where the move was
 *  smaller than `TRAIL_MIN_PTS`. The middle two are what makes a trail
 *  terminate at the counterparty's first scored frame rather than running back
 *  to the left edge of a window it did not exist in. */
export function trailFrom(track: Track | undefined, at: number, width: number): number | null {
  if (at <= 0) return null;
  const now = track?.[at];
  const prev = track?.[at - 1];
  if (now?.score == null || prev?.score == null) return null;
  if (Math.abs(Number(now.score) - Number(prev.score)) < TRAIL_MIN_PTS) return null;
  return xOf(Number(prev.score), width);
}

/** Where the dot stood `MOVEMENT_LOOKBACK` frames ago — the origin of the
 *  since-marker drawn behind a selected node. `null` in exactly the case the
 *  dashed ring already marks: there is no score that far back to compare with. */
export function sinceFrom(track: Track | undefined, at: number, width: number): number | null {
  const then = track?.[at - MOVEMENT_LOOKBACK];
  if (then?.score == null) return null;
  return xOf(Number(then.score), width);
}

// ── the sentence ────────────────────────────────────────────────────────────

/** Band → its rank, read from the server's own legend rather than hardcoded, so
 *  the sentence and the vertical rules on the axis cannot disagree about which
 *  way is up. */
export function bandRank(bands: Row[]): Map<string, number> {
  const ascending = [...bands].sort((a, b) => num(a.min_score) - num(b.min_score));
  return new Map(ascending.map((b, i) => [String(b.band), i]));
}

/** Truncation is stated, never silent. */
export function names(list: string[], keep = 2): string {
  return list.length <= keep
    ? list.join(", ")
    : `${list.slice(0, keep).join(", ")} +${list.length - keep} more`;
}

export interface FrameStory {
  band: Map<string, number>;
  arrived: string[];
  up: string[];
  down: string[];
  /** Only at the opening frame, where "arrived" would describe the window's
   *  own left edge rather than anything the business did. */
  openingPopulation: number | null;
}

/** What changed between the previous frame and this one, as counts and names.
 *
 *  Counts, differences and tallies only — never a mean, a median or a
 *  percentile. Those are commercial statistics and belong in `commercial/`
 *  stamped with a thresholds version, not derived in a chart. `anchored()` and
 *  `counted()` below are the existing precedent for what a screen may count for
 *  itself: `filter().length` over rows the server already sent.
 *
 *  **The opening frame reports population, not arrivals.** Every scored
 *  counterparty is "first scored" at frame 0 under the naive test, because
 *  there is no earlier frame to have been absent from — which would fire the
 *  arrival clause on the entire book every time the play restarts, describing
 *  where the window was cut rather than anything that happened.
 */
export function frameStory(nodes: Node[], sides: PreparedSide[], at: number,
                           bands: Row[]): FrameStory {
  const rank = bandRank(bands);
  const band = new Map<string, number>();
  const arrived: string[] = [];
  const up: string[] = [];
  const down: string[] = [];

  const trackOf = (n: Node): Track | undefined =>
    sides.find((s) => s.side === n.side)?.index.get(n.id);

  for (const n of nodes) {
    band.set(n.band, (band.get(n.band) ?? 0) + 1);
    if (at === 0) continue;
    const before = presenceAt(trackOf(n), at - 1);
    // Absent and unscored both mean "not on the strip last month", which is
    // what an arrival is. They are different facts and neither is a crossing.
    if (before !== "scored") { arrived.push(n.label); continue; }
    const was = String(trackOf(n)?.[at - 1]?.band ?? "");
    if (was && was !== n.band) {
      ((rank.get(n.band) ?? 0) > (rank.get(was) ?? 0) ? up : down).push(n.label);
    }
  }

  return {
    band, arrived, up, down,
    openingPopulation: at === 0 ? nodes.length : null,
  };
}

/** The story as the sentence under the strip.
 *
 *  A quiet month says so rather than dropping the clause: a vanishing phrase
 *  reads as a rendering fault, and at two and a half frames a second it also
 *  shifts everything below it. */
export function frameLine(story: FrameStory, label: string, bands: Row[]): string {
  const ordered = [...bands]
    .sort((a, b) => num(b.min_score) - num(a.min_score))
    .map((b) => String(b.band))
    .filter((name) => (story.band.get(name) ?? 0) > 0)
    .map((name) => `${story.band.get(name)} ${name.toLowerCase()}`);

  const parts = [label];
  if (ordered.length) parts.push(ordered.join(", "));

  if (story.openingPopulation != null) {
    parts.push(`${story.openingPopulation} already scored when the window opens`);
    return `${parts.join(" — ")}.`;
  }

  if (story.arrived.length) {
    parts.push(`${story.arrived.length} first scored (${names(story.arrived)})`);
  }
  const { up, down } = story;
  if (!up.length && !down.length) {
    parts.push("nothing crossed a band");
  } else {
    const moves: string[] = [];
    if (up.length) moves.push(`${up.length} crossed up (${names(up)})`);
    if (down.length) moves.push(`${down.length} crossed down (${names(down)})`);
    parts.push(moves.join(", "));
  }
  return `${parts.join(" — ")}.`;
}

/** Per-lane movement tallies for the lane heading.
 *
 *  Reuses `toneOf`, so the words and the fill share one classifier and cannot
 *  disagree. `unmeasured` is a subset of what `toneOf` calls flat — reporting
 *  it separately is the point, because today "did not move" and "cannot be
 *  compared" are the same grey. */
export function laneTone(nodes: Node[], lane: number): {
  up: number; down: number; unmeasured: number; size: number;
} {
  const inLane = nodes.filter((n) => n.lane === lane);
  return {
    up: inLane.filter((n) => toneOf(n.movement) === "bond-up").length,
    down: inLane.filter((n) => toneOf(n.movement) === "bond-down").length,
    unmeasured: inLane.filter((n) => n.movement == null).length,
    size: inLane.length,
  };
}

// ── geometry ────────────────────────────────────────────────────────────────

/** Marks sized by area, not by diameter — the same rule `Patterns.tsx` uses.
 *  Scaling the radius makes a counterparty with twice the revenue look four
 *  times as important.
 *
 *  **The domain is passed in, not derived, so the caller can pin it to the
 *  whole book.** Deriving it from the rows handed over re-domains on whatever
 *  subset is being drawn, which was harmless only while the whole book was
 *  always drawn. It is wrong per lane — the same money is a different size in
 *  "By line" than in "Together" — and it is wrong the moment somebody watches
 *  six accounts: those six re-domain onto themselves, every one draws at 13px,
 *  and the reading the size channel exists for is gone. A big dot on the left
 *  is material money in a weakening relationship; that only means anything if
 *  "big" is measured against the book. */
export function radiusScale(biggest: number) {
  return scaleSqrt().domain([0, Math.max(biggest, 1)]).range([3, 13]);
}

/** The largest counterparty on the canvas, before any narrowing. What
 *  ``radiusScale`` should be pinned to — and the packer and the renderer must
 *  pass the same one, or a dot is drawn at one size and seated at another. */
export function biggestMoney(
  sides: { bonds: Row[] }[],
): number {
  return Math.max(1, ...sides.flatMap(({ bonds }) => bonds.map((b) => num(b.money))));
}

/** Score → x. The axis is the full panel width, which is the entire point of
 *  the strip: a radial had ~130px to spend on the same 0–100. */
export function xOf(score: number, width: number): number {
  const s = Math.min(100, Math.max(0, score));
  return PAD_L + (s / 100) * Math.max(1, width - PAD_L - PAD_R);
}

/** The lanes, every counterparty's fixed seat inside one, and which of them
 *  carry a name on the canvas.
 *
 *  Computed from the *current* scores and never from the displayed frame. This
 *  is the whole reason the play reads as movement rather than as churn: a dot's
 *  row is its identity, and only its horizontal position is the measure. The
 *  label set is chosen here for exactly the same reason — ranking names per
 *  frame would make them flicker on and off as cumulative money crossed a
 *  boundary, which is the row-hopping noise the fixed seat exists to prevent,
 *  moved into the text layer. The signature takes no frame, which is what
 *  enforces it.
 */
export function packLanes(
  sides: SideData[],
  apply: <R extends Sourced>(list: R[]) => R[],
  width: number,
  branch: boolean,
): { lanes: Lane[]; seat: Map<string, Seat>; labels: Set<string> } {
  const seat = new Map<string, Seat>();
  const lanes: Lane[] = [];
  const perLane: { key: string; money: number }[] = [];
  // Pinned before any narrowing — see `radiusScale`. Per lane and per selection
  // this used to re-domain, so the same money drew at different sizes depending
  // on what else was on screen.
  const biggest = biggestMoney(sides);

  sides.forEach(({ side, bonds, frames }) => {
    // Only a counterparty the server sent frames for can be drawn — the frame
    // series is where a per-month score comes from. The lane label counts
    // exactly these, so it can never claim more dots than are on the canvas.
    const covered = new Set(
      frames.flatMap((f) => rows(f.bonds).map((e) => String(e.counterparty_id))));
    const eligible = (apply(bonds as Sourced[]) as Row[])
      .filter((b) => b.score != null && covered.has(String(b.counterparty_id)));
    if (!eligible.length) return;

    for (const group of splitBy(eligible, side, branch)) {
      perLane.push(...packOne(group, side, seat, lanes, width, biggest));
    }
  });

  // Trimmed globally as well as per lane, in the same money order: five names
  // in each of six lanes is thirty labels, which is the wall the per-lane cap
  // alone does not prevent.
  const labels = new Set(
    perLane.sort((a, b) => b.money - a.money).slice(0, NAMES_TOTAL).map((p) => p.key));
  return { lanes, seat, labels };
}

/** One lane's worth: the swarm packing, the lane it produces, and its
 *  candidates for a name. */
function packOne(group: { label: string; rows: Row[] }, side: string,
                 seat: Map<string, Seat>, lanes: Lane[], width: number,
                 biggest: number): { key: string; money: number }[] {
  // Ascending, so the packer places the crowded left end first and the sparse
  // right end settles around it rather than the other way round.
  const scored = group.rows.slice().sort((a, b) => num(a.score) - num(b.score));
  const r = radiusScale(biggest);
  const lane = lanes.length;
  const placed: { x: number; y: number; r: number }[] = [];
  let extent = 0;

  for (const b of scored) {
    const x = xOf(num(b.score), width);
    const rr = r(num(b.money));
    // Nearest free seat to the lane's spine, tried outward in both directions.
    // A dot that cannot find one at all stays on the spine and overlaps rather
    // than being pushed off the canvas — an overlap is survivable, a mark drawn
    // outside its own lane is not.
    let y = 0;
    for (let k = 0; k <= MAX_STACK; k += 1) {
      const options = k === 0 ? [0] : [k * STACK_STEP, -k * STACK_STEP];
      const free = options.find((cy) => placed.every((p) => {
        const dx = p.x - x, dy = p.y - cy;
        const reach = p.r + rr + 1;
        return dx * dx + dy * dy >= reach * reach;
      }));
      if (free !== undefined) { y = free; break; }
    }
    placed.push({ x, y, r: rr });
    extent = Math.max(extent, Math.abs(y) + rr);
    seat.set(`${side}:${String(b.counterparty_id)}`, { lane, y });
  }

  lanes.push({
    side, label: group.label, count: scored.length,
    half: Math.max(extent + 6, 26),
  });

  return pickLabels(scored, placed, side, width);
}

/** Which dots in this lane carry a name: the biggest, in money order.
 *
 *  **The collision that matters is label against label, not label against dot.**
 *  That is what the halo is for — a name crossing a mark stays readable, and the
 *  mark under it is one of two hundred. Two names crossing each other are both
 *  destroyed, and no halo helps.
 *
 *  This was written the other way first — drop any candidate whose seat has no
 *  vertical clearance from its neighbours — and rendering it showed the flaw at
 *  once: the largest counterparties sit in the crowded middle of the mound,
 *  precisely where clearance fails, so the rule filtered out every dot it
 *  existed to label and handed the names to small isolated ones instead. It
 *  selected for loneliness while claiming to select for size.
 *
 *  Placement is judged at present-day x, the same trade the seat makes, with
 *  the same known limit: a labelled dot that slides a long way during the play
 *  can end up beside one it did not sit beside at rest. Resolving that per
 *  frame would put back the flicker the fixed set exists to prevent.
 */
function pickLabels(scored: Row[], placed: { x: number; y: number; r: number }[],
                    side: string, width: number): { key: string; money: number }[] {
  const seatOf = new Map(scored.map((b, i) => [String(b.counterparty_id), placed[i]]));
  const taken: { x0: number; x1: number; y: number }[] = [];
  const out: { key: string; money: number }[] = [];

  for (const b of [...scored].sort((a, z) => num(z.money) - num(a.money))) {
    if (out.length >= NAMES_PER_LANE) break;
    const mine = seatOf.get(String(b.counterparty_id));
    if (!mine) continue;
    // The same box the renderer will draw: flipped to the left of the dot near
    // the right edge, so a name never runs off the canvas.
    const text = String(b.label ?? "");
    const run = Math.min(text.length, 22) * 6.2;
    const right = mine.x > width * 0.75;
    const x0 = right ? mine.x - mine.r - 4 - run : mine.x + mine.r + 4;
    const box = { x0, x1: x0 + run, y: mine.y };
    const clashes = taken.some((t) =>
      Math.abs(t.y - box.y) < CLEAR_PX && t.x0 < box.x1 && box.x0 < t.x1);
    if (clashes) continue;
    taken.push(box);
    out.push({ key: `${side}:${String(b.counterparty_id)}`, money: num(b.money) });
  }
  return out;
}

/** One group per lane: the whole side, or one per line of the business.
 *
 *  Splitting is opt-in because it costs vertical space and only earns it when
 *  the question is about mix. Lines are ordered by population so the crowded
 *  ones lead, and a counterparty whose trade is entirely uncategorised falls in
 *  a named lane rather than being dropped off the chart.
 */
export function splitBy(list: Row[], side: string, branch: boolean,
                        ): { label: string; rows: Row[] }[] {
  const whole = side === "vendor" ? "Suppliers" : "Customers";
  if (!branch) return [{ label: whole, rows: list }];

  const groups = new Map<string, Row[]>();
  for (const r of list) {
    const key = String(r.sector ?? "UNCATEGORISED");
    groups.set(key, [...(groups.get(key) ?? []), r]);
  }
  return [...groups.entries()]
    .sort((a, b) => b[1].length - a[1].length)
    .map(([key, group]) => ({
      label: `${whole} · ${LINE_LABEL[key] ?? key}`,
      rows: group,
    }));
}

/** Category code → the words the server uses for it. Kept beside the chart
 *  rather than fetched, because the strip renders before the label list would
 *  arrive and a lane briefly titled "CUTTING_TOOLS" is a lane that looks
 *  broken. Overridden by the server's own list where it is present. */
export const LINE_LABEL: Record<string, string> = {
  CUTTING_TOOLS: "Cutting tools",
  COOLANTS: "Coolants & lubricants",
  CONSUMABLES: "Consumables",
  METROLOGY: "Metrology",
  MACHINES: "Machines",
  UNCATEGORISED: "Not categorised",
};

export function layout(
  sides: PreparedSide[],
  at: number,
  apply: <R extends Sourced>(list: R[]) => R[],
  seat: Map<string, Seat>,
  width: number,
): Node[] {
  const out: Node[] = [];
  // The same pinned domain the packer used, and it has to be the same one or a
  // dot would be drawn at one size and seated at another.
  const r = radiusScale(biggestMoney(sides));
  sides.forEach(({ side, bonds, index }) => {
    const visible = apply(bonds as Sourced[]) as Row[];
    for (const b of visible) {
      const id = String(b.counterparty_id);
      const here = seat.get(`${side}:${id}`);
      if (!here) continue;
      const track = index.get(id);
      const point = track?.[at] ?? null;
      // Unscored *in this frame* means absent from it, not parked at zero.
      if (point?.score == null) continue;
      out.push({
        id, label: String(b.label), sector: String(b.sector ?? "Other"), side,
        score: Number(point.score),
        money: num(point.money ?? b.money),
        movement: movementOf(track, at),
        band: String(point.band ?? b.band ?? "THIN"),
        origin: b.origin as EntityOrigin | undefined,
        overdue: Boolean(b.overdue),
        lane: here.lane,
        y: here.y,
        x: xOf(Number(point.score), width),
        // Lifetime money, and deliberately not the frame's. Per-frame money is
        // revenue accumulated since the window opened, so it only ever rises —
        // an animated radius grew every dot on the book every month, including
        // the ones being lost, which is the one story the play must not tell.
        r: r(num(b.money)),
        trail: trailFrom(track, at, width),
      });
    }
  });
  return out;
}

/** Score now minus score `MOVEMENT_LOOKBACK` frames back, in points.
 *
 *  `null` when there is nothing to compare against — a relationship that did
 *  not exist six months ago has not weakened, and drawing it as a full-strength
 *  gain would be just as wrong. */
export function movementOf(track: Track | undefined, at: number): number | null {
  const now = track?.[at]?.score;
  const then = track?.[at - MOVEMENT_LOOKBACK]?.score;
  if (now == null || then == null) return null;
  return Number(now) - Number(then);
}

/** Movement → colour class. Neutral inside a band that is not worth a claim:
 *  a two-point drift over six months is noise, and colouring it would put a
 *  red dot next to a relationship nothing has happened to.
 *
 *  `null` also lands on flat, which is why it needs the dashed ring beside it:
 *  "did not move" and "cannot be compared" are the same grey otherwise, and a
 *  reader has no way to tell a stable relationship from an unmeasurable one. */
export function toneOf(movement: number | null): string {
  if (movement == null) return "bond-flat";
  if (movement >= 5) return "bond-up";
  if (movement <= -5) return "bond-down";
  return "bond-flat";
}

export function signed(v: number): string {
  return `${v > 0 ? "+" : ""}${v.toFixed(0)} pts`;
}

export function counted(list: Row[], noun: string): string {
  const n = list.filter((b) => b.score != null).length;
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

export function anchored(list: Row[]): number {
  return list.filter((b) => b.band === "ANCHORED").length;
}

/** The earliest frame in which anything at all was scored, across both sides.
 *
 *  A book that started trading last year has a long dead run at the front of
 *  its window, and scrubbing through it by hand to find where the picture
 *  begins is a worse first experience than being taken there. */
export function firstScored(...series: Row[][]): number | null {
  const length = Math.max(...series.map((s) => s.length), 0);
  for (let i = 0; i < length; i += 1) {
    const any = series.some((frames) =>
      rows(frames[i]?.bonds).some((e) => e.score != null));
    if (any) return i;
  }
  return null;
}
