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
import type { CoverageView, RollupView } from "./components/QuoteDiagnosisPanel";
import type { Quote } from "./types";

interface AssessResponse {
  quote_id: string;
  lines: DiagnosisView[];
  /** What was checked on this quote and what could not be. Served to both
   *  roles — the type behind it declares no money field at all. */
  coverage?: CoverageView;
  /** What the quote comes to, and the lines its total does not show.
   *  **Absent from a salesperson's response**, not empty: the server puts this
   *  key on one branch and there is no key on the other. */
  rollup?: RollupView;
}

/** One line as `/assess` wants it. It takes a product *code* or an id and a
 *  customer *name* or an id — see `LineIn` — which is what lets the desk post
 *  what it is holding rather than what the database calls it.
 *
 *  Not exported: the ERP quote page sends no lines at all. It names a document
 *  and the server reads them, which is what let that page reach quotes older
 *  than `/assess` will diagnose. */
interface DiagnosisLineIn {
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

export interface QuoteDiagnosisState {
  byLineId: Record<string, DiagnosisView>;
  /** What the server said it checked on this quote, or `null` where it was
   *  never asked. **The two are different answers** — a panel that cannot tell
   *  them apart reads "nothing was checked" as "nothing was wrong", which is the
   *  failure `CLAUDE.md` §1 names three times — so this is carried explicitly
   *  rather than left to `byLineId` being empty. */
  coverage: CoverageView | null;
  /** The quote's own totals, and the lines those totals do not show. `null` for
   *  a salesperson, whose response carries no such key, and for a request that
   *  was never made. */
  rollup: RollupView | null;
  loading: boolean;
  /** Set when the request failed. The screen says nothing rather than pretending
   *  every line came back clean — a diagnosis panel that is silent because it
   *  could not ask is indistinguishable from one that is silent because there
   *  was nothing to say, and only one of those is good news. */
  error: string | null;
}

/** The engine call itself, shared by both screens that make it.
 *
 *  Split out when the ERP quote page needed the same handling against a
 *  different endpoint. What is generic is everything except what gets posted:
 *  one request, keyed on what can change an answer, with the out-of-order
 *  guard. A second copy of that is how one screen comes to paint a stale
 *  verdict the other has already corrected.
 *
 *  `path` is `null` when there is nothing to ask — an unsaved quote, or a
 *  document whose lines have not been read. A hook cannot be called
 *  conditionally, so the decision arrives as an argument.
 */
function useDiagnosis(path: string | null, body: unknown,
                      token: string): QuoteDiagnosisState {
  // Keyed on the content, not the object: see the module docstring.
  const key = useMemo(() => JSON.stringify([path, body]), [path, body]);

  const [state, setState] = useState<QuoteDiagnosisState>({
    byLineId: {}, coverage: null, rollup: null, loading: false, error: null,
  });
  const latest = useRef(0);

  useEffect(() => {
    if (!path) {
      setState({ byLineId: {}, coverage: null, rollup: null,
                 loading: false, error: null });
      return;
    }
    const mine = ++latest.current;
    setState((s) => ({ ...s, loading: true, error: null }));

    post<AssessResponse>(path, body, token)
      .then((r) => {
        // Out-of-order responses: a slow answer to an old edit must not paint
        // over a fast answer to a new one, which on this screen would be a
        // stale verdict under a current price.
        if (mine !== latest.current) return;
        const byLineId: Record<string, DiagnosisView> = {};
        for (const row of r.lines) byLineId[row.line_id] = row;
        // Carried rather than derived from the lines. Both are quote-level
        // answers the server computed against a versioned policy, and a browser
        // that re-counted them would be the second answer that drifts.
        setState({ byLineId, coverage: r.coverage ?? null,
                   rollup: r.rollup ?? null, loading: false, error: null });
      })
      .catch((e) => {
        if (mine !== latest.current) return;
        setState({ byLineId: {}, coverage: null, rollup: null,
                   loading: false, error: (e as Error).message });
      });
    // `key` is the content hash; `path` and `body` are read through it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, token]);

  return state;
}

export function useQuoteDiagnosis(quote: Quote | null,
                                  token: string): QuoteDiagnosisState {
  const lines = useMemo(() => diagnosableLines(quote), [quote]);
  const body = useMemo(
    // `record: false` — diagnosing is a read here. See the module docstring.
    () => ({ quote_id: quote?.id ?? "", lines, record: false }),
    [quote?.id, lines]);
  return useDiagnosis(
    quote && lines.length > 0 ? "/api/v1/quote-diagnosis/assess" : null,
    body, token);
}

/** The same question about a quote the ERP already issued.
 *
 *  **The caller sends no lines and no date.** Both are read from the quote by
 *  `routers.quote_diagnosis.assess_erp_quote`, and that is the whole reason this
 *  endpoint exists rather than posting to `/assess` like the draft side does.
 *  `as_of` on `/assess` is caller-supplied and therefore bounded — a date a
 *  caller can move a day at a time reads an item's price history out of the
 *  answers — so every quote older than that window came back refused. Naming a
 *  quote instead of a date leaves nothing to walk: one document, one date, and
 *  `erp_quotes` is written by the sync and by nothing else.
 *
 *  Invariant I1 holds either way: the diagnosis is built from what was knowable
 *  when the quote went out, never from today.
 *
 *  `hasLines` rather than reading them: the server decides which lines are
 *  diagnosable now, and a copy of that filter here would be the second answer
 *  that drifts. This only needs to know whether to ask at all. */
export function useErpQuoteDiagnosis(quoteRef: string, hasLines: boolean,
                                     token: string): QuoteDiagnosisState {
  const path = quoteRef && hasLines
    ? `/api/v1/quote-diagnosis/erp-quote/${encodeURIComponent(quoteRef)}`
    : null;
  // A body the endpoint does not read. `post` sends JSON and FastAPI is happy
  // with an empty object; the alternative is a second request helper for the
  // one call in this app that has nothing to say.
  const body = useMemo(() => ({}), []);
  return useDiagnosis(path, body, token);
}
