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
import type {
  LineIntelligence, QuoteIntelligence, QuoteLossReason, QuoteOutcome,
  QuoteOutcomeStatus, QuoteOutcomeSubject,
} from "./types";
import { authInit } from "./authFetch";

export interface AssessLine {
  line_id: string;
  product: string;
  qty: number;
  proposed_price: number | null;
  family?: string | null;
}

async function post<T>(path: string, body: unknown, token: string): Promise<T> {
  const res = await fetch(path, authInit({
    method: "POST",
    body: JSON.stringify(body),
  }, token));
  if (!res.ok) {
    // The server's own sentence is the whole value of a failure here, so it is
    // read once and kept whatever form it arrives in. Two of this file's
    // refusals are useless without it: a LOST outcome with no reason comes back
    // 422 naming every reason a person may choose, and an ERP reference that
    // answers to two connected books comes back 409 naming both books. Replacing
    // either with "Unprocessable Entity" leaves the reader with a form that says
    // no and will not say what would make it say yes.
    //
    // Reading the body as text first, then trying JSON, is `api.ts`'s `req`
    // treatment and it is here for the reason given there: a crash that escapes
    // FastAPI's handlers is plain text, and parsing as JSON and giving up threw
    // that description away.
    let detail = res.statusText;
    const raw = await res.text().catch(() => "");
    if (raw) {
      try {
        const parsed = JSON.parse(raw);
        detail = parsed.detail || parsed.message || raw;
      } catch {
        detail = raw.slice(0, 500);
      }
    }
    // Carried so a caller can tell the two refusals apart without matching on
    // the message — 422 is "one field is missing and here are its choices",
    // 409 is "nothing you could put in the body fixes this". `isAuthError` in
    // `platform/api.ts` reads the same field, so a retired session throwing out
    // of this client is now recognisable as one rather than as a screen error.
    const err = new Error(detail) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return (await res.json()) as T;
}

/** Move a quote along DRAFT → SENT → WON/LOST, whoever raised it.
 *
 *  One writer for both kinds of quote, because every rule on the way is shared:
 *  a loss must say which kind of loss it was, a decided quote is terminal, and
 *  the body carries the same six fields either way. `set_outcome` was extended
 *  rather than sibling-ed server-side for exactly this reason — "a second writer
 *  would be a second place for *a loss must say why* to be forgotten" — and a
 *  second body-builder in the browser would be that second place at one remove.
 *
 *  The two exported entry points below differ only in which id space they take,
 *  and they are separate names rather than one `string` parameter so a Zoho
 *  estimate id cannot be handed to the platform-quote path by a call site that
 *  type-checks. See `QuoteOutcomeSubject`. */
function postOutcome(
  token: string, subject: QuoteOutcomeSubject, status: QuoteOutcomeStatus,
  customer: string, note?: string, lossReason?: QuoteLossReason, lostTo?: string,
) {
  return post<QuoteOutcome>(
    "/api/v1/quote-intelligence/outcome",
    { quote_id: subject.quoteId, quote_document_ref: subject.documentRef,
      status, customer, note, loss_reason: lossReason, lost_to: lostTo },
    token,
  );
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

  /** Move a quote along DRAFT -> SENT -> WON/LOST.
   *
   *  A LOST call without `lossReason` is refused with a 422 by design, and the
   *  server owns that rule rather than this client: a competitor taking the
   *  order and the requirement going away are opposite facts about what the
   *  customer buys elsewhere, and only the person recording the loss knows
   *  which it was. Take `loss_reasons` off the outcome rather than listing the
   *  choices here — a second copy of that list is one that drifts. */
  outcome: (
    token: string, quoteId: string, status: QuoteOutcomeStatus, customer: string,
    note?: string, lossReason?: QuoteLossReason, lostTo?: string,
  ) => postOutcome(token, { quoteId }, status, customer, note, lossReason, lostTo),

  /** The same move, on a quote the ERP raised and this platform never priced.
   *
   *  Which is most of the book — roughly three quarters of the estimates on it —
   *  and until now nothing could ask about one: `OutcomeRequest.quote_id` was a
   *  required `str`, so no body could name an ERP quote at all. `documentRef` is
   *  the source system's own id, the `quote_document_ref` every row of
   *  `/insight/unrecorded-quotes` carries, never the surrogate a re-sync
   *  re-mints.
   *
   *  `customer` is passed through rather than derived here. The server resolves
   *  it under the caller's own scope and degrades to "we do not know this
   *  customer" instead of refusing, so a label off an unattributed quote is safe
   *  to send and a name outside this reader's book cannot be used to confirm
   *  that the account exists.
   *
   *  Three refusals reach the caller as an `Error` carrying the server's own
   *  sentence and a `status`, and the sentence is the useful half of each:
   *  **422** — LOST with no reason, naming every reason a person may choose, or
   *  a body that named neither key; **409** — a reference answering to two of
   *  this organization's connected books, naming both, which nothing in this
   *  body can resolve and which is refused rather than silently written onto
   *  whichever row came back first; **409** again — an outcome already recorded
   *  about a different ERP quote, which is not moved. Show the message. A
   *  generic "could not record that" throws away the only part that tells
   *  somebody what to do next. */
  documentOutcome: (
    token: string, documentRef: string, status: QuoteOutcomeStatus,
    customer = "", note?: string, lossReason?: QuoteLossReason, lostTo?: string,
  ) => postOutcome(token, { documentRef }, status, customer, note, lossReason,
                   lostTo),

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
    const res = await fetch(`/api/v1/approvals/quotes/${encodeURIComponent(quoteId)}/gate`,
      authInit({}, token));
    if (!res.ok) throw new Error(res.statusText);
    return (await res.json()) as QuoteGate;
  },
};

export interface QuoteGate {
  quote_id: string;
  can_submit: boolean;
  blocked_reason: string | null;
  outcome: string;
  /** The live approval requests on this quote.
   *
   *  `requested_by`, `requested_at` and `decided_by` were served by
   *  `approvals.to_dict` all along — the same serializer the approvals screen
   *  reads — and this interface simply did not declare them, so the browser
   *  threw them away. The cost fell on the one state a salesperson is most
   *  exposed in: having asked for an approval, the drawer could say only
   *  "waiting on a manager", never who or since when. `requested_by` is a
   *  resolved name rather than an id, so nothing here needs looking up. */
  requests: {
    approval_request_id: string;
    subject_line_id: string | null;
    status: string;
    required_authority: string;
    decision_note: string | null;
    requested_by: string | null;
    requested_at: string | null;
    decided_by: string | null;
    decided_at: string | null;
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
