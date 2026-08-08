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
import { AssessLine, QuoteGate, byLine, intelligence } from "./intelligence";

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
  recordOverride: (lineId: string, reasonCode: string, reason: string) => Promise<void>;
  requestApproval: (lineId: string, reasonCode: string, reason: string) => Promise<void>;
  gate: QuoteGate | null;
  refresh: () => void;
}

/** @param token the signed-in platform session's token. Passed in rather than
 *  re-read from storage: one session, one place that owns it. */
export function useQuoteIntelligence(quote: Quote | null, token: string): QuoteIntelligenceState {
  const [data, setData] = useState<QuoteIntelligence | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [gate, setGate] = useState<QuoteGate | null>(null);

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
    if (!customer || lines.length === 0) {
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

  // The gate is re-read whenever the quote changes or something is submitted:
  // an approval granted in another tab must show up here without a reload.
  useEffect(() => {
    if (!quoteId) return;
    let cancelled = false;
    intelligence
      .gate(token, quoteId)
      .then((g) => !cancelled && setGate(g))
      .catch(() => !cancelled && setGate(null));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, quoteId, key, nonce]);

  const recordOverride = useCallback(
    async (lineId: string, reasonCode: string, reason: string) => {
      if (!quoteId) throw new Error("There is no quote open to record this against");
      const line = latest.current.find((l) => l.line_id === lineId);
      if (!line) throw new Error("That line is no longer on the quote");
      await intelligence.snapshot(token, quoteId, customer, [
        { ...line, override_reason_code: reasonCode, override_reason: reason },
      ]);
      setNonce((n) => n + 1);
    },
    [token, quoteId, customer],
  );

  /** Record the reason *and* raise the request. Recording alone was the old
   *  behaviour, and it let a below-floor price go out with a note attached. */
  const requestApproval = useCallback(
    async (lineId: string, reasonCode: string, reason: string) => {
      if (!quoteId) throw new Error("There is no quote open to record this against");
      const line = latest.current.find((l) => l.line_id === lineId);
      if (!line) throw new Error("That line is no longer on the quote");
      if (line.proposed_price === null) throw new Error("Set a price first");
      await intelligence.snapshot(token, quoteId, customer, [
        { ...line, override_reason_code: reasonCode, override_reason: reason },
      ]);
      await intelligence.requestApproval(token, {
        quote_id: quoteId, customer, line_id: lineId, product: line.product,
        qty: line.qty, proposed_price: line.proposed_price,
        reason, reason_code: reasonCode,
      });
      setNonce((n) => n + 1);
    },
    [token, quoteId, customer],
  );

  return {
    data,
    byLineId: useMemo(() => byLine(data), [data]),
    loading,
    error,
    recordOverride,
    requestApproval,
    gate,
    refresh: () => setNonce((n) => n + 1),
  };
}
