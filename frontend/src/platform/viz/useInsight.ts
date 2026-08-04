// One loader for every insight view: same states, same retry, same shape.
//
// This was written twice — once in `Screens.tsx` and once, verbatim, in
// `Tier2.tsx` when the second batch of screens was added. Two copies of a
// loading hook is two places a retry, an auth-loss check or a request-cancel
// would have to be added, and the second copy is the one that gets forgotten.
// It lives here so every screen's four states come from the same code.

import { useCallback, useEffect, useState } from "react";

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
  fetcher: () => Promise<Envelope>,
  deps: unknown[],
): Loaded {
  const [data, setData] = useState<Envelope | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetcher());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
    // The fetcher closes over the deps the caller lists; re-creating it on every
    // render would loop, so the deps are the contract.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => { void load(); }, [load]);
  return { data, loading, error, reload: load };
}

/** A ratio as a percentage, with an em dash for "not known".
 *
 *  Both screen files had their own; they agreed, which is exactly why a third
 *  one written slightly differently would have been hard to spot. */
export function pct(v: number | null | undefined, d = 1): string {
  return v == null ? "—" : `${(v * 100).toFixed(d)}%`;
}
