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
import type { ErpQuoteBook, ErpQuoteLines, EstimateResult, ItemSearch, Quote, QuoteDraftSummary,
  QuoteFieldDefinition, QuoteOwner, RfqDocument } from "./types";
import { authInit } from "./authFetch";

/** The key the builder used to keep one draft under in `localStorage`.
 *
 *  Drafts are rows on the server now — every one of them, for everyone in
 *  the organization — so nothing reads this any more. It is removed on
 *  load rather than left behind, because a stale copy of a quote that has
 *  since been priced by a colleague is the kind of thing that gets pasted
 *  into an email. */
const LEGACY_DRAFT_KEY = "pie_portal_draft";

export function forgetLegacyDraft(): void {
  try {
    localStorage.removeItem(LEGACY_DRAFT_KEY);
  } catch {
    /* storage unavailable — nothing to forget */
  }
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
      // A refusal may answer with a shape rather than a sentence, and there
      // are now two of those. Test the typed one first: the 422 that names
      // the companies to choose from is recognised by shape and thrown as
      // `CompanyRequired`, so a caller can offer the choice instead of
      // printing it.
      if (body && typeof body === "object" && Array.isArray(body.companies)) {
        throw new CompanyRequired(String(body.message ?? detail), body.companies);
      }
      // Everything else is a message to show. The document endpoints send
      // `{reason, detail}` so a client can branch on the kind without
      // matching prose, so read the sentence out of it rather than assigning
      // the object: passed straight to `new Error`, an object becomes the
      // string "[object Object]" on somebody's screen, which is the one
      // message that tells them nothing at all.
      if (typeof body === "string") detail = body || detail;
      else if (body && typeof body === "object" && typeof body.detail === "string") {
        detail = body.detail;
      }
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
  createQuote: (t: string, customer = "", customerId?: string,
                connectionId?: string) =>
    req<Quote>("/api/v1/quotes", {
      method: "POST",
      // The id travels with the name. Downstream resolution tries it first,
      // which is what keeps two books' identically-named customers apart.
      // Both may be empty: a quote starts with no customer.
      body: JSON.stringify({ customer, customer_id: customerId ?? null,
                             connection_id: connectionId ?? null }),
    }, t),

  /** Open a blank quote form. **Creates nothing.**
   *
   *  This is what "New quote" calls. `createQuote` above writes a row and mints
   *  a number on the spot, which is what put an empty QB-0001 on the whole
   *  desk's list every time somebody opened the builder and changed their mind.
   *  The form is server-side scratch — the builder's work is done on the server
   *  and a browser-held draft would carry every line's cost to a salesperson —
   *  but it is not a quote: no number, in no listing, and `saved` is false
   *  until `saveQuote` below.
   *
   *  The company is still settled here, because the first pasted RFQ line needs
   *  a catalogue to resolve against. Refuses with `CompanyRequired` exactly as
   *  `createQuote` does where the organization reads several books. */
  createQuoteForm: (t: string, customer = "", customerId?: string,
                    connectionId?: string) =>
    req<Quote>("/api/v1/quotes/form", {
      method: "POST",
      body: JSON.stringify({ customer, customer_id: customerId ?? null,
                             connection_id: connectionId ?? null }),
    }, t),

  /** Save the form: the quote is created here and nowhere else.
   *
   *  Idempotent on the server under a unique constraint, so a double-click or a
   *  retried request answers with the quote the first one made rather than
   *  minting a second number. The button is disabled while this is in flight
   *  as well, but the guarantee does not depend on that. */
  saveQuote: (t: string, formId: string) =>
    req<Quote>(`/api/v1/quotes/form/${formId}/save`, { method: "POST" }, t),

  /** Throw an unsaved form away. Nothing was ever written to the workspace, so
   *  there is nothing left behind — and discarding one twice is not an error. */
  discardQuoteForm: (t: string, formId: string) =>
    req<{ ok: boolean }>(`/api/v1/quotes/form/${formId}`, { method: "DELETE" }, t),

  /** The workspace: every draft in the organization. */
  listQuotes: (t: string) =>
    req<{ quotes: QuoteDraftSummary[] }>("/api/v1/quotes", {}, t)
      .then((r) => r.quotes),

  /** Every quote the connected ERP raised — read-only, and a different list
   *  from `listQuotes` above. Served by the insight router because that is
   *  where the role scoping and the other reads of this table live. */
  listErpQuotes: (t: string, limit = 200) =>
    req<ErpQuoteBook>(`/api/v1/insight/quote-book?limit=${limit}`, {}, t),

  /** What was on one ERP quote. Fetched when somebody opens it rather than
   *  carried on every row: a hundred quotes with their lines is a payload
   *  nobody reads most of. */
  erpQuoteLines: (t: string, ref: string) =>
    req<ErpQuoteLines>(
      `/api/v1/insight/quote-book/${encodeURIComponent(ref)}/lines`, {}, t),

  getQuote: (t: string, id: string) => req<Quote>(`/api/v1/quotes/${id}`, {}, t),

  /** Say who the quote is for, or change it. The server resolves the lines
   *  already on the quote again under the new customer and says what it kept
   *  in `note`. */
  setCustomer: (t: string, id: string, customer: string, customerId?: string) =>
    req<Quote>(`/api/v1/quotes/${id}/customer`, {
      method: "PUT",
      body: JSON.stringify({ customer, customer_id: customerId ?? null }),
    }, t),

  /** Remove an unsent draft. A sent quote is refused with the reason. */
  deleteQuote: (t: string, id: string) =>
    req<{ ok: boolean }>(`/api/v1/quotes/${id}`, { method: "DELETE" }, t),

  /** The quote-level fields this organization asks for. */
  fieldDefinitions: (t: string) =>
    req<{ fields: QuoteFieldDefinition[] }>("/api/v1/quotes/field-definitions", {}, t)
      .then((r) => r.fields),

  /** Save the quote-level details. A value the definition refuses comes back
   *  as an error naming the field; nothing is saved then. */
  setFields: (t: string, id: string, fields: Record<string, string | number>) =>
    req<Quote>(`/api/v1/quotes/${id}/fields`, {
      method: "PUT", body: JSON.stringify({ fields }),
    }, t),

  /** Who a quote can be handed to. */
  assignees: (t: string) =>
    req<{ members: QuoteOwner[] }>("/api/v1/quotes/assignees", {}, t)
      .then((r) => r.members),

  /** Hand the quote to another member. */
  setOwner: (t: string, id: string, userId: string) =>
    req<Quote>(`/api/v1/quotes/${id}/owner`, {
      method: "PUT", body: JSON.stringify({ user_id: userId }),
    }, t),

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

  /** Find an item by hand — the catalogue and the books at once.
   *
   *  Scoped to the quote rather than to a company id, because the quote is
   *  where the company was decided (once, at creation) and a second place to
   *  name one is a second place to name the wrong one. */
  searchItems: (t: string, id: string, q: string) =>
    req<ItemSearch>(
      `/api/v1/quotes/${id}/item-search?q=${encodeURIComponent(q)}`, {}, t),

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

  /** Record — or clear, with `null` — the cost price a person sourced for this
   *  line. Every role that may edit the quote: the books answer what we have
   *  paid for an item, and on a first-time part they answer nothing, so the
   *  person holding the supplier's offer is the one at the desk. */
  setCustomCost: (t: string, id: string, lineId: string,
                  cost: number | null, note = "") =>
    req<Quote>(
      `/api/v1/quotes/${id}/lines/${lineId}/cost`,
      { method: "POST", body: JSON.stringify({ cost, note }) },
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
