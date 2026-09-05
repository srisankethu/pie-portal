// One loader for every insight view: same states, same retry, same shape.
//
// This was written twice — once in `Screens.tsx` and once, verbatim, in
// `Patterns.tsx` when the second batch of screens was added. Two copies of a
// loading hook is two places a retry, an auth-loss check or a request-cancel
// would have to be added, and the second copy is the one that gets forgotten.
// It lives here so every screen's four states come from the same code.
//
// TanStack Query underneath, with the interface deliberately unchanged so the
// swap touched no screen. It buys request cancellation (two month-picker
// changes used to race, and the stale answer could win), deduplication across
// panels asking for one window, and a cache so navigating back shows the last
// answer immediately.
//
// `staleTime` is the judgement call: these screens read a projection rebuilt at
// the end of a sync, so anything shorter re-asks a question whose answer cannot
// have moved.

import { useQuery } from "@tanstack/react-query";

/** The common envelope every `/api/v1/insight/*` endpoint returns: the payload,
 *  plus `currency` and `empty_reason`. Untyped on purpose — the payloads differ
 *  per view and each screen reads the fields it needs. */
export type Envelope = Record<string, unknown>;

/** The four states, over whatever shape the endpoint returns.
 *
 *  Generic with `Envelope` as the default so every existing caller is unchanged
 *  — they read untyped payloads and branch on the keys they need. A caller that
 *  has a declared interface for its response (the attribution surface does)
 *  passes it and keeps it, rather than casting an interface through
 *  `Record<string, unknown>` at the call site, which throws away exactly the
 *  check `tsc` is there to make. */
export interface Loaded<T = Envelope> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

export function useInsight<T = Envelope>(
  /** Which view this is. Part of the cache key, and the reason it exists.
   *
   * The first version keyed on `deps` alone, reasoning that "the deps the
   * caller already lists are the identity of the request". They are not: they
   * are its *parameters*. Six screens whose only dep was the session token —
   * stock, supply, payments, cadence, opportunities, the simulator's scenario
   * list — therefore shared one cache entry, and four more collided on
   * `[token, months]`.
   *
   * That is not a stale-data annoyance. Whichever screen loaded first won, and
   * the others rendered *its* payload: Suppliers, opened by a salesperson whose
   * request had correctly 403'd, read the cached Stock envelope, found no
   * `suppliers` key, and reported "0 suppliers, ₹0 ordered · 0 orders still
   * open" — a refusal displayed as a fact about the business. Exactly the
   * failure `LoadFailed` exists to prevent, arriving through the cache instead.
   *
   * Required, not optional with a default: a default is a thing the next caller
   * forgets, and the symptom is a screen quietly showing another screen's data.
   */
  view: string,
  fetcher: () => Promise<T>,
  deps: unknown[],
): Loaded<T> {
  const query = useQuery<T, Error>({
    queryKey: ["insight", view, ...deps],
    queryFn: fetcher,
  });

  return {
    // `null` rather than `undefined`, because every screen already branches on
    // it and changing that would have been a change to twenty files.
    data: query.data ?? null,
    // Not `isFetching`: a background refresh of a cached answer must not put
    // the screen back into its skeleton state, which is the whole benefit.
    loading: query.isPending,
    error: query.error ? query.error.message : null,
    reload: () => { void query.refetch(); },
  };
}

/** A ratio as a percentage, with an em dash for "not known".
 *
 *  Re-exported from `platform/format`, not implemented again. This docstring
 *  once said "both screen files had their own; they agreed, which is exactly
 *  why a third one written slightly differently would have been hard to spot"
 *  — and by 2026-09 there were six, one of which (`CatalogLearning`) had
 *  indeed drifted to whole percent. The five viz screens that import it from
 *  here keep working; there is one implementation behind them now. */
export { pct } from "../format";

/** A percentage-POINT movement, signed. Never a percent change of a percent.
 *
 *  §1: movement is percentage points. `Patterns` rendered a share change with
 *  `pct()` and the word "%" — "down 76% of share" for a move from 100% to 24%,
 *  which is −75.6 pp. The two coincide only when the starting share is 100%, and
 *  the same payload's next contributor moved from 0% to 28%, where a percent
 *  change of a percent is not defined at all.
 *
 *  A third private copy of this existed in `CommercialScreens`; both now call
 *  this, for the reason the docstring above gives about `pct`. */
export function pp(v: number | null | undefined): string {
  if (v == null) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(1)} pp`;
}
