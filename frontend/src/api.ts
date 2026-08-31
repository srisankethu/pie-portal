/** The Quote Builder's endpoints, on the platform's session.
 *
 * There is no login here any more, and no session of its own. Every call takes
 * the platform token — the same one `platform/api.ts` holds — because the
 * server now authenticates one identity for the whole product. What used to be
 * here was a second `Session` in `localStorage` under its own key, minted by a
 * second login against two fixed demo accounts, plus an `X-Platform-
 * Authorization` header carrying the *real* identity alongside it. Two tokens
 * on one request is how a screen ends up displaying one person's name while
 * deciding what to show from another's role.
 */
import type { EstimateResult, Quote } from "./types";
import { authInit } from "./authFetch";

const DRAFT_KEY = "pie_portal_draft";

export function loadDraftQuote(): Quote | null {
  const raw = localStorage.getItem(DRAFT_KEY);
  return raw ? (JSON.parse(raw) as Quote) : null;
}

export function saveDraftQuote(quote: Quote | null) {
  if (!quote) {
    localStorage.removeItem(DRAFT_KEY);
    return;
  }
  localStorage.setItem(DRAFT_KEY, JSON.stringify(quote));
}

export function clearDraftQuote() {
  localStorage.removeItem(DRAFT_KEY);
}

/** One connected company, as the server names it when it refuses to choose. */
export interface QuoteCompany {
  connection_id: string;
  label: string;
}

/** The organization reads several companies' books and the request named none.
 *
 *  Its own class because the caller must *do* something different — ask which
 *  company and retry — rather than show a message. Each company decodes its own
 *  item master, so the server refuses instead of picking one; the ids come back
 *  with the refusal so the screen can offer exactly the valid answers.
 */
export class CompanyRequired extends Error {
  companies: QuoteCompany[];

  constructor(message: string, companies: QuoteCompany[]) {
    super(message);
    this.name = "CompanyRequired";
    this.companies = companies;
  }
}

async function req<T>(path: string, opts: RequestInit = {}, token?: string): Promise<T> {
  const res = await fetch(path, authInit(opts, token));
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()).detail;
      // A structured refusal rather than a sentence: the 422 that names the
      // companies to choose from. Recognised by shape, so an ordinary string
      // detail still becomes an ordinary Error below.
      if (body && typeof body === "object" && Array.isArray(body.companies)) {
        throw new CompanyRequired(String(body.message ?? detail), body.companies);
      }
      detail = body || detail;
    } catch (e) {
      if (e instanceof CompanyRequired) throw e;
      /* an unparseable body leaves the status text */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  /** `connectionId` is the company the quote is raised from, and therefore
   *  whose decoded catalogue its lines resolve against. Omitted where the
   *  organization reads one company's books — the server answers without it,
   *  and refuses with `CompanyRequired` where there is a real choice. */
  createQuote: (t: string, customer: string, customerId?: string,
                connectionId?: string) =>
    req<Quote>("/api/v1/quotes", {
      method: "POST",
      // The id travels with the name. Downstream resolution tries it first,
      // which is what keeps two books' identically-named customers apart.
      body: JSON.stringify({ customer, customer_id: customerId ?? null,
                             connection_id: connectionId ?? null }),
    }, t),

  getQuote: (t: string, id: string) => req<Quote>(`/api/v1/quotes/${id}`, {}, t),

  /** `channel` is what turns the pasted words into a corpus row. Sent only when
   *  the person said how the enquiry arrived — omitted, the server captures
   *  nothing, because `InboundChannel` has no "unknown" member to file it
   *  under. */
  intake: (t: string, id: string, text: string, channel?: string) =>
    req<Quote>(`/api/v1/quotes/${id}/intake`, {
      method: "POST",
      body: JSON.stringify(channel ? { text, channel } : { text }),
    }, t),

  /** One line at a time, deliberately — see store.confirm_reading. */
  confirmReading: (t: string, id: string, lineId: string) =>
    req<Quote>(`/api/v1/quotes/${id}/lines/${lineId}/confirm-reading`,
               { method: "POST" }, t),

  selectSupply: (t: string, id: string, lineId: string, code: string, manual = false) =>
    req<Quote>(
      `/api/v1/quotes/${id}/lines/${lineId}/supply`,
      { method: "POST", body: JSON.stringify({ code, manual }) },
      t,
    ),

  setPrice: (t: string, id: string, lineId: string, price: number | null) =>
    req<Quote>(
      `/api/v1/quotes/${id}/lines/${lineId}/price`,
      { method: "POST", body: JSON.stringify({ price }) },
      t,
    ),

  deleteLine: (t: string, id: string, lineId: string) =>
    req<Quote>(`/api/v1/quotes/${id}/lines/${lineId}`, { method: "DELETE" }, t),

  discount: (t: string, id: string, lineIds: string[], percent: number) =>
    req<Quote & { applied: number }>(
      `/api/v1/quotes/${id}/discount`,
      { method: "POST", body: JSON.stringify({ lineIds, percent }) },
      t,
    ),

  createItem: (t: string, id: string, lineId: string) =>
    req<Quote>(`/api/v1/quotes/${id}/lines/${lineId}/create-item`, { method: "POST" }, t),

  /** Create the Zoho estimate.
   *
   *  The approval gate is enforced server-side against the organization on this
   *  token. It used to read a second, optional header for the organization,
   *  which meant a client that simply omitted it was a client with no approvals
   *  to satisfy. */
  createEstimate: (t: string, id: string) =>
    req<EstimateResult>(
      `/api/v1/quotes/${id}/estimate`,
      { method: "POST" },
      t,
    ),
};
