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

export interface Loaded {
  data: Envelope | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

export function useInsight(
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
  fetcher: () => Promise<Envelope>,
  deps: unknown[],
): Loaded {
  const query = useQuery<Envelope, Error>({
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
 *  Both screen files had their own; they agreed, which is exactly why a third
 *  one written slightly differently would have been hard to spot. */
export function pct(v: number | null | undefined, d = 1): string {
  return v == null ? "—" : `${(v * 100).toFixed(d)}%`;
}
