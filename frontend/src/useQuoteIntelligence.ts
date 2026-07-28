/** Commercial intelligence for a whole quote, fetched once per change.
 *
 * The Quote Builder holds N lines; this hook makes N lines into one request.
 * It is deliberately keyed on the *content* that affects an assessment —
 * product, quantity, price — rather than on the quote object, so that opening
 * a drawer or ticking a checkbox does not re-ask the server a question whose
 * answer cannot have changed.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LineIntelligence, Quote, QuoteIntelligence } from "./types";
import { AssessLine, byLine, intelligence, platformToken } from "./intelligence";

function assessLines(quote: Quote | null): AssessLine[] {
  if (!quote) return [];
  return quote.lines
    .filter((l) => l.supplyCode || l.reqCode)
    .map((l) => ({
      line_id: l.id,
      product: l.supplyCode || l.reqCode,
      qty: l.reqQty,
      proposed_price: l.quoted,
    }));
}

export interface QuoteIntelligenceState {
  data: QuoteIntelligence | null;
  byLineId: Record<string, LineIntelligence>;
  loading: boolean;
  error: string | null;
  connected: boolean;
  recordOverride: (lineId: string, reasonCode: string, reason: string) => Promise<void>;
  refresh: () => void;
}

export function useQuoteIntelligence(quote: Quote | null): QuoteIntelligenceState {
  const token = platformToken();
  const [data, setData] = useState<QuoteIntelligence | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const lines = useMemo(() => assessLines(quote), [quote]);
  // A stable key over exactly the inputs that change an assessment.
  const key = useMemo(
    () => JSON.stringify(lines.map((l) => [l.line_id, l.product, l.qty, l.proposed_price])),
    [lines],
  );
  const customer = quote?.customer ?? "";
  const quoteId = quote?.id;
  const latest = useRef(lines);
  latest.current = lines;

  useEffect(() => {
    if (!token || !customer || lines.length === 0) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    intelligence
      .assess(token, customer, latest.current, quoteId)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError((e as Error).message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, customer, quoteId, key, nonce]);

  const recordOverride = useCallback(
    async (lineId: string, reasonCode: string, reason: string) => {
      if (!token || !quoteId) throw new Error("Not connected to the Decisions platform");
      const line = latest.current.find((l) => l.line_id === lineId);
      if (!line) throw new Error("That line is no longer on the quote");
      await intelligence.snapshot(token, quoteId, customer, [
        { ...line, override_reason_code: reasonCode, override_reason: reason },
      ]);
    },
    [token, quoteId, customer],
  );

  return {
    data,
    byLineId: useMemo(() => byLine(data), [data]),
    loading,
    error,
    connected: !!token,
    recordOverride,
    refresh: () => setNonce((n) => n + 1),
  };
}
