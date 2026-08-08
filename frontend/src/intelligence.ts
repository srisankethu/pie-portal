/** Client for the deterministic quote intelligence service.
 *
 * One request per quote, not one per line: an RFQ of forty items is one call.
 * That is not just a network optimisation — the assessment is computed from a
 * single pass over the customer's history, so calling it per line would give
 * forty chances for the answers to disagree with each other.
 *
 * These endpoints need an organisation, which is why they take the platform
 * token. That used to be a *second* token read straight out of `localStorage`
 * by a `platformToken()` helper here, because the Quote Builder authenticated
 * separately against its own demo accounts and its own session had no
 * organisation on it. There is one session now, and the caller passes its token
 * in like every other client in this codebase does.
 */
import type { LineIntelligence, QuoteIntelligence, QuoteLossReason, QuoteOutcome,
              QuoteOutcomeStatus } from "./types";

export interface AssessLine {
  line_id: string;
  product: string;
  qty: number;
  proposed_price: number | null;
  family?: string | null;
}

async function post<T>(path: string, body: unknown, token: string): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const intelligence = {
  assess: (token: string, customer: string, lines: AssessLine[], quoteId?: string) =>
    post<QuoteIntelligence>("/api/v1/quote-intelligence/assess", { customer, lines, quote_id: quoteId }, token),

  /** Freeze the current assessment into the immutable audit trail. The server
   *  re-derives every number — nothing computed in this browser is persisted. */
  snapshot: (
    token: string,
    quoteId: string,
    customer: string,
    lines: (AssessLine & { override_reason?: string; override_reason_code?: string })[],
  ) =>
    post<{ quote_id: string; recorded: number }>(
      "/api/v1/quote-intelligence/snapshot",
      { quote_id: quoteId, customer, lines },
      token,
    ),

  /** Move a quote along the lifecycle. A LOST quote needs `lossReason` — the
   *  server refuses it otherwise, deliberately, because a loss nobody explained
   *  is a row that can be counted and never learned from. */
  outcome: (token: string, quoteId: string, status: QuoteOutcomeStatus, customer: string,
            note?: string, lossReason?: QuoteLossReason) =>
    post<QuoteOutcome>("/api/v1/quote-intelligence/outcome",
                       { quote_id: quoteId, status, customer, note,
                         loss_reason: lossReason ?? null }, token),

  /** Ask a manager or owner to sign off this line at the price on it now.
   *  Recording a reason is not the same as being allowed — this is the ask. */
  requestApproval: (
    token: string,
    body: { quote_id: string; customer: string; line_id: string; product: string;
            qty: number; proposed_price: number; reason?: string; reason_code?: string },
  ) => post<{ approval_request_id: string; status: string; required_authority: string }>(
        "/api/v1/approvals/quote-line", body, token),

  /** Whether this quote may be sent, and what is holding it. Mirrors the
   *  server-side check the send endpoint performs — it does not replace it. */
  gate: async (token: string, quoteId: string): Promise<QuoteGate> => {
    const res = await fetch(`/api/v1/approvals/quotes/${encodeURIComponent(quoteId)}/gate`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) throw new Error(res.statusText);
    return (await res.json()) as QuoteGate;
  },
};

export interface QuoteGate {
  quote_id: string;
  can_submit: boolean;
  blocked_reason: string | null;
  outcome: string;
  requests: {
    approval_request_id: string;
    subject_line_id: string | null;
    status: string;
    required_authority: string;
    decision_note: string | null;
  }[];
  policy: { require_approval_for_quotes: boolean };
}

/** Index the per-line results by the Quote Builder's own line id. */
export function byLine(data: QuoteIntelligence | null): Record<string, LineIntelligence> {
  const out: Record<string, LineIntelligence> = {};
  for (const line of data?.lines ?? []) out[line.line_id] = line;
  return out;
}

export const SEVERITY_LABEL: Record<string, string> = {
  CRITICAL: "Approval needed",
  WARNING: "Check price",
  INFO: "Context",
};
