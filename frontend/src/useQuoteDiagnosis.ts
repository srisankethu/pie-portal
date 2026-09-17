/** The commercial diagnosis for a whole quote, fetched once per change.
 *
 * Shaped after `useQuoteIntelligence`, which solves the same problem for the
 * same screen: N lines become one request, keyed on the content that can change
 * an answer — product, quantity, price — rather than on the quote object, so
 * opening a drawer or ticking a checkbox does not re-ask a question whose answer
 * cannot have moved.
 *
 * **Not folded into that hook.** The two ask different questions of different
 * engines: `quote-intelligence` answers "does this price breach a rule this
 * organization set", from policy; `quote-diagnosis` answers "is this price
 * unusual for this customer", from their own history. One hook returning both
 * would make a failure in either look like a failure in both, on a screen whose
 * whole job is to be trusted about prices.
 *
 * **`record: false`.** Diagnosing is a read here. The desk is still editing —
 * every keystroke on a rate would otherwise write an append-only row, and a
 * table meant to hold what somebody was shown would fill with what they typed
 * on the way to it. The Quote Builder records a diagnosis when the quote is
 * sent, not while it is being priced.
 */
import { useEffect, useMemo, useRef, useState } from "react";

import { post } from "./intelligence";
import { productRef } from "./rel";
import type { DiagnosisView } from "./components/DiagnosisCard";
import type { ErpQuoteLine, Quote } from "./types";

interface AssessResponse {
  quote_id: string;
  lines: DiagnosisView[];
}

/** One line as the engine wants it. The endpoint takes a product *code* or an
 *  id and a customer *name* or an id — see `LineIn` — which is what lets two
 *  screens holding different vocabularies ask the same question. */
export interface DiagnosisLineIn {
  line_id: string;
  product_id: string;
  customer_id: string | null;
  qty: number | null;
  quoted_unit_price: number | null;
}

/** Only lines with something to diagnose. A line with no product named cannot
 *  be compared against anything, and a line with no price is not yet a claim
 *  about what this customer should pay. */
function diagnosableLines(quote: Quote | null): DiagnosisLineIn[] {
  if (!quote) return [];
  return quote.lines
    .filter((l) => (l.supplyCode || l.reqCode) && l.quoted !== null)
    .map((l) => ({
      line_id: l.id,
      // The one rule for naming a line's product — see rel.productRef. The
      // server resolves the code through the same function the intelligence
      // endpoint uses, so both screens agree on which product a line names.
      product_id: productRef(l),
      customer_id: quote.customerId ?? quote.customer ?? null,
      qty: l.reqQty,
      quoted_unit_price: l.quoted,
    }));
}

/** The lines of an ERP quote, in the same shape.
 *
 *  A quote the ERP raised names its products by the code printed on the
 *  document and its customer by the name on it, neither of which this platform
 *  minted — which is exactly the pair `LineIn` accepts, so the ERP quote page
 *  and the Quote Builder reach one endpoint rather than two.
 *
 *  Same filter as the draft's, for the same reason: a line the ERP never priced
 *  is not a claim about what this customer should pay, and a line naming
 *  nothing cannot be compared against anything. */
export function erpDiagnosableLines(
  customerLabel: string, lines: ErpQuoteLine[],
): DiagnosisLineIn[] {
  return lines
    .filter((l) => l.item_code && l.rate !== null)
    .map((l) => ({
      line_id: String(l.line_number),
      product_id: l.item_code,
      customer_id: customerLabel || null,
      qty: l.qty,
      quoted_unit_price: l.rate,
    }));
}

export interface QuoteDiagnosisState {
  byLineId: Record<string, DiagnosisView>;
  loading: boolean;
  /** Set when the request failed. The screen says nothing rather than pretending
   *  every line came back clean — a diagnosis panel that is silent because it
   *  could not ask is indistinguishable from one that is silent because there
   *  was nothing to say, and only one of those is good news. */
  error: string | null;
}

/** The engine call itself, shared by both screens that make it.
 *
 *  Split out when the ERP quote page needed the same request against lines of a
 *  different shape. What is generic is everything below the line-building: one
 *  request for N lines, keyed on the content that can change an answer, with
 *  the out-of-order guard. A second copy of that is how one screen comes to
 *  paint a stale verdict the other has already corrected.
 */
function useDiagnosis(quoteId: string | null, lines: DiagnosisLineIn[],
                      token: string,
                      asOf?: string | null): QuoteDiagnosisState {
  // Keyed on the content, not the object: see the module docstring.
  const key = useMemo(
    () => JSON.stringify([quoteId ?? "", asOf ?? "", lines]),
    [quoteId, asOf, lines]);

  const [state, setState] = useState<QuoteDiagnosisState>({
    byLineId: {}, loading: false, error: null,
  });
  const latest = useRef(0);

  useEffect(() => {
    if (!quoteId || lines.length === 0) {
      setState({ byLineId: {}, loading: false, error: null });
      return;
    }
    const mine = ++latest.current;
    setState((s) => ({ ...s, loading: true, error: null }));

    post<AssessResponse>("/api/v1/quote-diagnosis/assess", {
      quote_id: quoteId, lines, record: false,
      // Omitted rather than sent as null for a draft being priced now: the
      // server defaults to today, and naming today explicitly would make the
      // request say something it does not mean.
      ...(asOf ? { as_of: asOf } : {}),
    }, token)
      .then((r) => {
        // Out-of-order responses: a slow answer to an old edit must not paint
        // over a fast answer to a new one, which on this screen would be a
        // stale verdict under a current price.
        if (mine !== latest.current) return;
        const byLineId: Record<string, DiagnosisView> = {};
        for (const row of r.lines) byLineId[row.line_id] = row;
        setState({ byLineId, loading: false, error: null });
      })
      .catch((e) => {
        if (mine !== latest.current) return;
        setState({ byLineId: {}, loading: false, error: (e as Error).message });
      });
    // `key` is the content hash; `lines`, `quoteId` and `asOf` are read
    // through it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, token]);

  return state;
}

export function useQuoteDiagnosis(quote: Quote | null,
                                  token: string): QuoteDiagnosisState {
  const lines = useMemo(() => diagnosableLines(quote), [quote]);
  return useDiagnosis(quote?.id ?? null, lines, token);
}

/** The same question about a quote the ERP already issued.
 *
 *  **`as_of` is the quote's own raised date, not today.** Invariant I1: the
 *  diagnosis is built from what was knowable when the quote went out, so a
 *  quote sent in July is judged against what this customer had paid by July —
 *  not against a price that moved in August. Judging a historical document
 *  against today's evidence would be the look-ahead the engine exists to
 *  refuse.
 *
 *  The server bounds how far back that date may be, and will refuse a quote
 *  older than its window. That refusal is the server's to make and its sentence
 *  is the one shown: a copy of the limit here would be a second statement of
 *  the same rule, and the one that drifts.
 *
 *  **`record: false`, from `useDiagnosis`.** This page cannot write: its whole
 *  promise is that the next sync would overwrite anything typed on it, and a
 *  screen that says so while appending a row on every visit is lying about the
 *  cheapest thing to be honest about.
 */
export function useErpQuoteDiagnosis(
  quoteRef: string, customerLabel: string, raisedOn: string | null,
  lines: ErpQuoteLine[], token: string,
): QuoteDiagnosisState {
  const built = useMemo(
    () => erpDiagnosableLines(customerLabel, lines),
    [customerLabel, lines]);
  return useDiagnosis(quoteRef || null, built, token, raisedOn);
}
