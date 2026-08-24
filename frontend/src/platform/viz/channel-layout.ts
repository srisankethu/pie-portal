// The cash channel's geometry, and its reading of the projection.
//
// Split out of `CashJourney.tsx` for the reason `cycle-layout.ts` was split out
// of the cycle chart: the things this file gets wrong are invisible on screen.
// A channel drawn a week out of step, an envelope measured between the wrong
// pair of edges, or a past route that does not actually arrive at the datum all
// render as a perfectly plausible drawing. Everything here is pure — rows in,
// numbers and path strings out — and `channel-layout.test.ts` pins the parts a
// reader could not check by looking.
//
// **The datum is the origin of both halves, and it is zero.** The server hands
// back two series with the same property: the past one ends at zero at `as_of`
// (`cashflow.actual` runs its total back from the datum) and the committed one
// starts from zero there (`cashflow.project` runs its cumulative forward from
// it). Both are *movement*, never a position — PIE reads payments, not bank
// balances, so there is no opening figure for either to run from. This module
// therefore joins them at a single point of value zero and never at a level one
// of them computed.
//
// **The channel exists only where all three corners do.** `banded` is the
// single place that decides it: three scenario series, each the same length as
// the committed buckets. With anything less the drawing is a route and no
// channel, which is the honest picture of a book whose parties have too little
// settled history to measure. An envelope inferred from two edges and a guess
// at the third would be a width somebody could read a funding decision off.

export type Row = Record<string, unknown>;

export const rows = (v: unknown): Row[] => (v as Row[] | undefined) ?? [];
export const num = (v: unknown): number => Number(v ?? 0);

/** What every week on the drawing carries, whichever side of the datum it is
 *  on: the money that crossed it, and how many documents that was. */
export interface Leg {
  startsOn: string;
  /** True for the current week, which is short — it runs to the datum rather
   *  than to Sunday. A bar drawn from four days must not be read against
   *  twelve drawn from seven. */
  partial: boolean;
  inflow: number;
  outflow: number;
  net: number;
  inflowDocuments: number;
  outflowDocuments: number;
}

/** A week behind the datum: money that actually moved. */
export interface PastLeg extends Leg {
  /** The running total, measured back from the datum — the last past week is
   *  zero. "The book has moved this much cash since then", never a balance. */
  movement: number;
}

/** A week in front of it: money already promised, at up to four timings. */
export interface FutureLeg extends Leg {
  /** Every document on its own due date. The only reading that asserts nothing
   *  beyond what the source says, which is why it survives as the centre line
   *  when nothing is measured. */
  onTerms: number;
  /** Customers at their fastest, us paying at our slowest — the upper edge. */
  best: number | null;
  /** Everybody at their own median. */
  expected: number | null;
  /** The reverse of `best`, and the edge a week is funded against. */
  worst: number | null;
  /** How far apart the two edges are here. Null where the band does not
   *  exist — never zero, which would read as "certain". */
  envelope: number | null;
}

const legOf = (b: Row): Leg => ({
  startsOn: String(b.starts_on ?? ""),
  partial: Boolean(b.partial),
  inflow: num(b.inflow),
  outflow: num(b.outflow),
  net: num(b.net),
  inflowDocuments: num(b.inflow_documents),
  outflowDocuments: num(b.outflow_documents),
});

/** The route already travelled, oldest first. Empty when the server sent no
 *  history at all, which is a shorter drawing rather than a broken one. */
export function pastLegs(actual: Row | null | undefined): PastLeg[] {
  return rows(actual?.buckets).map((b) => ({
    ...legOf(b),
    movement: num(b.cumulative),
  }));
}

/** Whether the three corners are all present and aligned with the committed
 *  weeks. The one test that decides whether there is a channel to draw. */
export function banded(committed: Row[], best: Row[], expected: Row[],
                       worst: Row[]): boolean {
  return committed.length > 0
    && best.length === committed.length
    && expected.length === committed.length
    && worst.length === committed.length;
}

/** The committed weeks, with each scenario's running total on the week it
 *  belongs to.
 *
 *  The scenario rows are read positionally *after* `banded` has established
 *  that all four series are the same length — they are built by one loop over
 *  one list of Mondays in `cashflow._series`, so position is the week. The
 *  `starts_on` of each is asserted against the baseline's in the tests, which
 *  is where a server-side reordering would be caught. */
export function futureLegs(committed: Row[], best: Row[], expected: Row[],
                           worst: Row[]): FutureLeg[] {
  const has = banded(committed, best, expected, worst);
  return committed.map((b, i) => {
    const hi = has ? num(best[i].cumulative) : null;
    const lo = has ? num(worst[i].cumulative) : null;
    return {
      ...legOf(b),
      onTerms: num(b.cumulative),
      best: hi,
      expected: has ? num(expected[i].cumulative) : null,
      worst: lo,
      envelope: hi == null || lo == null ? null : hi - lo,
    };
  });
}

/** Every value the vertical scale has to hold, so no route is drawn outside
 *  its own axis.
 *
 *  Computed here rather than at the call site because the one that escapes is
 *  always the same one — the worst-case edge, which is the deepest by
 *  construction and therefore the number the drawing exists to show. Zero is
 *  always inside it: the datum is the origin of both halves and an axis that
 *  did not contain it would put the reference line off the sheet.
 */
export function extentOf(past: readonly PastLeg[],
                         future: readonly FutureLeg[]): [number, number] {
  const values: number[] = [0];
  past.forEach((p) => values.push(p.movement));
  future.forEach((f) => {
    values.push(f.onTerms);
    [f.best, f.expected, f.worst].forEach((v) => {
      if (v != null) values.push(v);
    });
  });
  return [Math.min(...values), Math.max(...values)];
}

/** The largest single week's flow, either direction, across the whole drawing.
 *
 *  One scale for both lanes and both sides of the datum: arrows that were
 *  scaled per-lane would make a quiet week of payments look like a heavy one
 *  purely because nothing else went out that quarter. Never zero — a divisor
 *  of zero is how every arrow ends up full length on a book with no flows. */
export function peakFlow(past: readonly Leg[],
                         future: readonly Leg[]): number {
  const all = [...past, ...future].flatMap((l) => [l.inflow, l.outflow]);
  return Math.max(1, ...all);
}

/** Where the widest uncertainty in the committed horizon sits.
 *
 *  Null when there is no band. The *widest* rather than the last, because the
 *  channel narrows as often as it opens — a horizon whose final week happens
 *  to be pinched is not a book that can be planned to the week before it. */
export function widestEnvelope(
  future: readonly FutureLeg[],
): { index: number; width: number; startsOn: string } | null {
  let at = -1;
  let width = -Infinity;
  for (let i = 0; i < future.length; i += 1) {
    const envelope = future[i].envelope;
    if (envelope != null && envelope > width) {
      at = i;
      width = envelope;
    }
  }
  return at < 0 ? null : { index: at, width, startsOn: future[at].startsOn };
}

/** The position of a named week on the committed side, or −1.
 *
 *  The screen locates the server's own pressure point this way rather than
 *  computing its own argmin. Two answers to "which week is deepest" is one
 *  more than a drawing can survive: the metric would print the server's week
 *  and the marker would sit on the client's, and only one of them would be
 *  wrong at a time. */
export function indexOfWeek(future: readonly FutureLeg[],
                            startsOn: string | null | undefined): number {
  if (!startsOn) return -1;
  return future.findIndex((leg) => leg.startsOn === startsOn);
}

/** Which committed weeks get a cross-section mark, evenly spaced and ending on
 *  the horizon's last week.
 *
 *  Deterministic and derived from the length alone: a section taken at
 *  "wherever the channel looks interesting" is a section nobody can take
 *  again. Weeks with no band are skipped — a cross-section through a route
 *  with no width is a line, and drawing it dimensioned would assert certainty
 *  the server refused. */
export function sectionIndices(future: readonly FutureLeg[],
                               count: number): number[] {
  const last = future.length - 1;
  if (last < 1 || count < 1) return [];
  const picked: number[] = [];
  for (let i = 1; i <= count; i += 1) {
    const at = Math.round((i * last) / count);
    if (at > 0 && !picked.includes(at) && future[at]?.envelope != null) {
      picked.push(at);
    }
  }
  return picked;
}

/** Section letters, in drawing order. Two by default; a third only if a wider
 *  sheet ever asks for one. */
export const SECTION_LETTERS = ["A", "B", "C"];

/** The channel outline: out along the best edge, back along the worst.
 *
 *  One closed path rather than two lines and a fill between them, because the
 *  *area* is the message — a filled shape is what makes a narrowing channel
 *  read as a narrowing channel. Empty string where there is no band, which
 *  renders as nothing rather than as a degenerate sliver at the datum. */
export function channelOutline(
  future: readonly FutureLeg[],
  x: (index: number) => number,
  y: (value: number) => number,
  datumX: number,
): string {
  const edges = future.filter((f) => f.best != null && f.worst != null);
  if (edges.length !== future.length || !future.length) return "";
  const zero = y(0);
  const out = future.map((f, i) => `L${x(i)},${y(f.best as number)}`);
  const back = future
    .map((f, i) => ({ f, i }))
    .reverse()
    .map(({ f, i }) => `L${x(i)},${y(f.worst as number)}`);
  // Both edges leave the datum from the same point, which is what makes the
  // channel a channel: at `as_of` nothing has moved yet under any timing, so
  // best and worst are the same number and the mouth of it is a point.
  return [`M${datumX},${zero}`, ...out, ...back, `L${datumX},${zero}`, "Z"]
    .join(" ");
}

/** A route as an SVG points list, from the datum through each week it has a
 *  value for. `values` may hold nulls; a run that is missing any of them is
 *  drawn as nothing rather than interpolated across. */
export function routePoints(
  values: readonly (number | null)[],
  x: (index: number) => number,
  y: (value: number) => number,
  from?: { x: number; value: number },
): string {
  if (values.some((v) => v == null) || !values.length) return "";
  const head = from ? [`${from.x},${y(from.value)}`] : [];
  return [...head,
          ...values.map((v, i) => `${x(i)},${y(v as number)}`)].join(" ");
}
