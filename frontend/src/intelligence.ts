/** Client for the deterministic quote intelligence service.
 *
 * One request per quote, not one per line: an RFQ of forty items is one call.
 * That is not just a network optimisation — the assessment is computed from a
 * single pass over the customer's history, so calling it per line would give
 * forty chances for the answers to disagree with each other.
 *
 * The Quote Builder and the Decision Platform authenticate separately (the
 * builder has its own demo accounts; the platform is database-backed and
 * org-scoped). The platform token is the one that carries an organisation, so
 * it is the one these endpoints need. When it is absent the panel says so
 * rather than silently showing a quote with no commercial context.
 */
import type { LineIntelligence, QuoteIntelligence, QuoteOutcome, QuoteOutcomeStatus } from "./types";

export function platformToken(): string | null {
  try {
    const raw = localStorage.getItem("pie_platform_session");
    return raw ? JSON.parse(raw).token || null : null;
  } catch {
    return null;
  }
}

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

  outcome: (token: string, quoteId: string, status: QuoteOutcomeStatus, customer: string, note?: string) =>
    post<QuoteOutcome>("/api/v1/quote-intelligence/outcome", { quote_id: quoteId, status, customer, note }, token),
};

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
