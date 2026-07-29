import type { Quote, Session } from "./types";

const TOKEN_KEY = "pie_portal_session";
const DRAFT_KEY = "pie_portal_draft";

export function loadSession(): Session | null {
  const raw = localStorage.getItem(TOKEN_KEY);
  return raw ? (JSON.parse(raw) as Session) : null;
}
export function saveSession(s: Session) {
  localStorage.setItem(TOKEN_KEY, JSON.stringify(s));
}
export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
}

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
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { ...opts, headers: { ...headers, ...(opts.headers || {}) } });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  login: (email: string, password: string) =>
    req<Session>("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),

  createQuote: (t: string, customer: string) =>
    req<Quote>("/api/quotes", { method: "POST", body: JSON.stringify({ customer }) }, t),

  getQuote: (t: string, id: string) => req<Quote>(`/api/quotes/${id}`, {}, t),

  intake: (t: string, id: string, text: string) =>
    req<Quote>(`/api/quotes/${id}/intake`, { method: "POST", body: JSON.stringify({ text }) }, t),

  selectSupply: (t: string, id: string, lineId: string, code: string, manual = false) =>
    req<Quote>(
      `/api/quotes/${id}/lines/${lineId}/supply`,
      { method: "POST", body: JSON.stringify({ code, manual }) },
      t,
    ),

  setPrice: (t: string, id: string, lineId: string, price: number | null) =>
    req<Quote>(
      `/api/quotes/${id}/lines/${lineId}/price`,
      { method: "POST", body: JSON.stringify({ price }) },
      t,
    ),

  deleteLine: (t: string, id: string, lineId: string) =>
    req<Quote>(`/api/quotes/${id}/lines/${lineId}`, { method: "DELETE" }, t),

  discount: (t: string, id: string, lineIds: string[], percent: number) =>
    req<Quote & { applied: number }>(
      `/api/quotes/${id}/discount`,
      { method: "POST", body: JSON.stringify({ lineIds, percent }) },
      t,
    ),

  createItem: (t: string, id: string, lineId: string) =>
    req<Quote>(`/api/quotes/${id}/lines/${lineId}/create-item`, { method: "POST" }, t),

  /** Create the Zoho estimate.
   *
   *  ``platformToken`` is the org-scoped Decisions identity, sent in a second
   *  header because the Quote Builder's own login carries no organization and
   *  the approval gate needs one. The server refuses to send when the policy
   *  requires approvals and this header is missing — a client that simply
   *  omitted it would otherwise be the way around every approval. */
  createEstimate: (t: string, id: string, platformToken?: string | null) =>
    req<{ ok: boolean; estimateNumber: string | null; lineCount: number | null; blockers: string[]; message: string }>(
      `/api/quotes/${id}/estimate`,
      {
        method: "POST",
        headers: platformToken ? { "X-Platform-Authorization": `Bearer ${platformToken}` } : {},
      },
      t,
    ),
};
