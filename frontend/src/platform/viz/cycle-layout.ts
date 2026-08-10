// The one part of the cycle chart that cannot be judged by looking at it.
//
// A trend line drawn across a month the server refused to state *asserts a
// value for that month* — smoothly, plausibly, and in whichever direction its
// neighbours happen to point. It is the exact failure the module behind this
// screen exists to prevent, arriving through the renderer instead of through
// the arithmetic, and on a chart it looks like a chart.
//
// So the line is built as runs of consecutive stated months and nothing else.
// Extracted here rather than left inline for the reason `bonds-layout.ts` was:
// a fixture can show a line that spans a gap, and an eye cannot.

/** A month's cycle, in days, or `null` where a leg could not be stated. */
export type Point = number | null;

/**
 * Indices of consecutive stated months, in runs.
 *
 * A run of one is kept rather than dropped: a stated month between two gaps is
 * a real reading, and the caller draws it as a dot. Dropping it here would make
 * the shortest possible series — one observed month — render as nothing at all,
 * which reads as "no data" rather than as "one month of data".
 */
export function statedRuns(points: readonly Point[]): number[][] {
  const runs: number[][] = [];
  let run: number[] = [];
  points.forEach((value, i) => {
    if (value == null) {
      if (run.length) runs.push(run);
      run = [];
      return;
    }
    run.push(i);
  });
  if (run.length) runs.push(run);
  return runs;
}

/** The vertical extent the chart has to hold: both stacks and the line itself.
 *
 *  Computed here rather than at the call site because forgetting one of the
 *  three draws that series outside its own axis — and the cycle line is the one
 *  that goes negative, which is exactly the reading the screen exists to show.
 */
export function extentOf(
  up: readonly number[], down: readonly number[], line: readonly Point[],
): [number, number] {
  const stated = line.filter((v): v is number => v != null);
  const low = Math.min(0, ...down.map((v) => -v), ...stated);
  // `-0` is what negating a zero-day supplier leg produces, and it survives
  // straight into d3's domain. Harmless to the scale and not harmless to a
  // reader of a failing test, so it is normalised at the boundary.
  return [low === 0 ? 0 : low, Math.max(0, ...up, ...stated)];
}
