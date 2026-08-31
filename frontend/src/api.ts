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
import type { EstimateResult, Quote, RfqDocument } from "./types";
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

async function req<T>(path: string, opts: RequestInit = {}, token?: string): Promise<T> {
  const res = await fetch(path, authInit(opts, token));
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()).detail;
      // A refusal may answer with a shape rather than a sentence — the
      // document endpoints send `{reason, detail}` so a client can branch on
      // the kind without matching prose. Read the sentence out of it: passed
      // straight to `new Error`, an object becomes the string
      // "[object Object]" on somebody's screen, which is the one message that
      // tells them nothing at all.
      if (typeof body === "string") detail = body || detail;
      else if (body && typeof body === "object" && typeof body.detail === "string") {
        detail = body.detail;
      }
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  createQuote: (t: string, customer: string, customerId?: string) =>
    req<Quote>("/api/v1/quotes", {
      method: "POST",
      // The id travels with the name. Downstream resolution tries it first,
      // which is what keeps two books' identically-named customers apart.
      body: JSON.stringify({ customer, customer_id: customerId ?? null }),
    }, t),

  getQuote: (t: string, id: string) => req<Quote>(`/api/v1/quotes/${id}`, {}, t),

  /** Store the document an RFQ arrived as, and return what was stored.
   *
   *  Its own call rather than a field on `intake`, because the upload has its
   *  own refusals and its own statuses — 413 for a size or archive ceiling, 415
   *  for a type — and folding the bytes into the intake body would make that
   *  route multipart to gain nothing and would lose the document every time an
   *  unrelated intake failure rolled it back. */
  uploadRfqDocument: (t: string, file: File, licenceNote = "") => {
    const form = new FormData();
    form.append("file", file);
    form.append("licence_note", licenceNote);
    return req<RfqDocument>("/api/v1/enquiries/documents",
                            { method: "POST", body: form }, t);
  },

  /** `channel` is what turns the pasted words into a corpus row. Sent only when
   *  the person said how the enquiry arrived — omitted, the server captures
   *  nothing, because `InboundChannel` has no "unknown" member to file it
   *  under. */
  intake: (t: string, id: string, text: string, channel?: string,
           rfqDocumentId?: string) =>
    req<Quote>(`/api/v1/quotes/${id}/intake`, {
      method: "POST",
      body: JSON.stringify({
        text,
        ...(channel ? { channel } : {}),
        // Named only when there is one. The server treats an unknown id as no
        // id — the quote is the work and the corpus link is a by-product — so
        // sending an empty string would be asking it to log a warning about a
        // document nobody attached.
        ...(rfqDocumentId ? { rfq_document_id: rfqDocumentId } : {}),
      }),
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
