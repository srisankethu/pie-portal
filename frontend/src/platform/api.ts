import type { Account, AccountItem, StatusFilter, ApprovalRequest, EntityKind, Identity, IdentityPolicy, IdentitySuggestion, ConnectionCheck, ConnectionsView, FixedThresholds, MarginPolicy, MarginPolicyPatch, NewConnectionInput, ZohoConnection, ZohoCredential, ZohoVisibleOrg, CustomerItemDetail, CustomerPortfolio, DataStatus, DecisionDetail, DecisionSummary, DecisionTrace, OrgPolicy, PlatformSession, PlatformUser, QuoteGate, Role, SyncOptions, SyncStartResponse, SyncState, ThresholdView, ZohoConnectionInput } from "./types";

import { setMoneyCurrency } from "../money";
import { setBusinessTimezone } from "../when";
import { parseStoredSession } from "./schemas";

const KEY = "pie_platform_session";

// Both entry paths — a fresh sign-in and a restore from storage — go through
// these two, which is why the currency is applied here rather than in a
// component: a screen mounted before any policy fetch would otherwise render
// the first few amounts in the default currency and then change them.
export function loadPlatformSession(): PlatformSession | null {
  const raw = localStorage.getItem(KEY);
  if (!raw) return null;
  // Validated rather than cast. This value comes out of `localStorage`, which
  // the type-checker has no view of at all: it may have been written by a
  // previous version of this app, edited by hand, or left by an extension. The
  // old `as PlatformSession` believed all of it — a stored session missing its
  // token booted the shell signed-in and then failed every request with a 401
  // nobody could explain, and one carrying an unknown role fell through every
  // role check to the narrowest view.
  const parsed = parseStoredSession(raw);
  if (!parsed) {
    // Not a session. Clear it so the next load is not the same puzzle, and
    // show the door — which the app already does well.
    localStorage.removeItem(KEY);
    return null;
  }
  setMoneyCurrency(parsed.currency);
  setBusinessTimezone(parsed.timezone);
  return parsed as PlatformSession;
}
export function savePlatformSession(s: PlatformSession) {
  localStorage.setItem(KEY, JSON.stringify(s));
  setMoneyCurrency(s.currency);
  setBusinessTimezone(s.timezone);
}
export function clearPlatformSession() {
  localStorage.removeItem(KEY);
}

async function req<T>(path: string, opts: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { ...opts, headers: { ...headers, ...(opts.headers || {}) } });
  if (!res.ok) {
    // A crash that escapes FastAPI's handlers comes back as plain text, not
    // JSON — so parsing as JSON and giving up threw away the only description
    // of what went wrong and reported a bare "Internal Server Error" instead.
    // Read the body once, then decide how to interpret it.
    let detail = res.statusText;
    const body = await res.text().catch(() => "");
    if (body) {
      try {
        const parsed = JSON.parse(body);
        detail = parsed.detail || parsed.message || body;
      } catch {
        detail = body.slice(0, 500);
      }
    }
    if (res.status >= 500 && detail === "Internal Server Error") {
      // The server names the exception and gives a searchable id now, so this
      // only fires for a 500 that got past its handler.
      //
      // It used to name migrations. First as "the usual cause is a pending
      // `alembic upgrade head`", then — after that proved wrong — as "check
      // /api/health, it says what to run". Both are the same mistake in
      // different clothes: the client has no evidence about the cause and was
      // supplying one anyway. An owner followed the second version for a
      // failing "Add company", got CURRENT, and was left exactly where they
      // started with a confident wrong lead. Say what is known, which is
      // nothing beyond where to look.
      detail =
        "The server hit an error and did not say what it was. " +
        "Its log has the traceback for this request.";
    }
    const err = new Error(detail) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return (await res.json()) as T;
}

interface LoginResp {
  token: string;
  role: PlatformSession["role"];
  name: string;
  user_id: string;
  organization_id: string;
  currency: string;
  timezone: string;
  must_change_password: boolean;
}

export const papi = {
  login: (email: string, password: string) =>
    req<LoginResp>("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),

  listDecisions: (t: string, q: { type?: string; status_filter?: string } = {}) => {
    const p = new URLSearchParams();
    if (q.type) p.set("type", q.type);
    if (q.status_filter) p.set("status_filter", q.status_filter);
    const qs = p.toString();
    return req<DecisionSummary[]>(`/api/v1/decisions${qs ? "?" + qs : ""}`, {}, t);
  },

  getDetail: (t: string, id: string) => req<DecisionDetail>(`/api/v1/decisions/${id}/detail`, {}, t),

  /** Why a decision exists, all the way down to the ERP record.
   *
   *  Fetched on demand rather than with the card: the chain can run to
   *  hundreds of transitions and almost nobody opens it, so paying for it on
   *  every card view would be paying for the exception. */
  getTrace: (t: string, id: string, offset = 0) =>
    req<DecisionTrace>(`/api/v1/decisions/${id}/trace?offset=${offset}`, {}, t),

  act: (t: string, id: string, body: { action: string; note?: string; reason?: string }) =>
    req<DecisionSummary>(`/api/v1/decisions/${id}/action`, { method: "POST", body: JSON.stringify(body) }, t),

  /** Undo a human action — returns the decision to the queue. The reopen is
   *  itself recorded, so the audit trail keeps both the action and its reversal. */
  reopen: (t: string, id: string) =>
    req<DecisionSummary>(`/api/v1/decisions/${id}/action`,
      { method: "POST", body: JSON.stringify({ action: "REOPEN", note: "Undone by the user" }) }, t),

  // ── the visualization layer ───────────────────────────────────────────────
  // Every one of these returns the same envelope: data, currency, and an
  // `empty_reason` written where the query happened. The client never decides
  // why something is empty — it could only guess, and the server knows.
  /** The morning read. One request for the whole landing page's top. */
  daily: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/daily", {}, t),

  storyboard: (t: string, months = 3) =>
    req<Record<string, unknown>>(`/api/v1/insight/storyboard?months=${months}`, {}, t),

  revenueFlow: (t: string, months = 3) =>
    req<Record<string, unknown>>(`/api/v1/insight/revenue-flow?months=${months}`, {}, t),

  weather: (t: string, months = 3) =>
    req<Record<string, unknown>>(`/api/v1/insight/weather?months=${months}`, {}, t),

  journey: (t: string, months = 12) =>
    req<Record<string, unknown>>(`/api/v1/insight/journey?months=${months}`, {}, t),

  migration: (t: string, months = 3) =>
    req<Record<string, unknown>>(`/api/v1/insight/migration?months=${months}`, {}, t),

  opportunities: (t: string, limit = 100) =>
    req<Record<string, unknown>>(`/api/v1/insight/opportunities?limit=${limit}`, {}, t),

  lostRevenue: (t: string, months = 3) =>
    req<Record<string, unknown>>(`/api/v1/insight/lost-revenue?months=${months}`, {}, t),

  customerTimeline: (t: string, customerId: string, months = 18) =>
    req<Record<string, unknown>>(
      `/api/v1/insight/customers/${encodeURIComponent(customerId)}/timeline?months=${months}`, {}, t),

  // ── Patterns: three endpoints covering five specified views, because two
  // pairs of them are the same chart with a different measure.
  landscape: (t: string, subject = "relationship", measure = "margin") =>
    req<Record<string, unknown>>(
      `/api/v1/insight/landscape?subject=${subject}&measure=${measure}`, {}, t),

  composition: (t: string, dimension = "customer", measure = "revenue", months = 12) =>
    req<Record<string, unknown>>(
      `/api/v1/insight/composition?dimension=${dimension}&measure=${measure}&months=${months}`,
      {}, t),

  cadence: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/cadence", {}, t),

  // ── The book itself: the shelf, the suppliers and the cash. Three things the book
  // always knew and the platform did not read until now.
  payments: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/payments", {}, t),

  // Manager and above. The inflow half is receivables, but the outflow half is
  // what we owe suppliers — purchase cost by another name — so the endpoint is
  // scoped like `supply` and the panel is hidden rather than 403'd.
  cashflow: (t: string, weeks: number) =>
    req<Record<string, unknown>>(
      `/api/v1/insight/cashflow?weeks=${weeks}`, {}, t),

  stock: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/stock", {}, t),

  supply: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/supply", {}, t),

  // Relationship bonds — the one view that reads both sides of the book.
  //
  // Not scoped like `supply` even though it carries a supplier half: the
  // customer half contains no cost and no margin, so a salesperson gets a real
  // answer rather than a 403. The server omits the supplier half from their
  // response entirely, which is why this takes no `side` parameter — asking is
  // not what decides, the role is.
  bonds: (t: string, months: number) =>
    req<Record<string, unknown>>(`/api/v1/insight/bonds?months=${months}`, {}, t),

  // Product mix — who takes which lines of the business, and which they do not.
  // Every role: the grid is revenue and dates, and the conversation it exists
  // for is a salesperson's.
  // Two pivots, one endpoint: lines of the business, or principals.
  // `connectionId` scopes the whole grid to one connected company on the
  // server, rather than filtering rows in the browser: the headline counts are
  // what this screen is for, so narrowing has to recompute them.
  mix: (t: string, months: number, by = "category", connectionId?: string) =>
    req<Record<string, unknown>>(
      `/api/v1/insight/mix?months=${months}&by=${by}`
      + (connectionId ? `&connection_id=${encodeURIComponent(connectionId)}` : ""),
      {}, t),

  // What this book leans on, at both ends. The supplier half is manager+ and
  // is omitted from a salesperson's response rather than 403-ing the screen.
  dependency: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/dependency", {}, t),

  // Vendor targets. The one thing in the platform that is typed rather than
  // synced, so it has a write path.
  targets: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/targets", {}, t),

  setTarget: (t: string, body: Record<string, unknown>) =>
    req<Record<string, unknown>>("/api/v1/insight/targets",
      { method: "PUT", body: JSON.stringify(body) }, t),

  // The catalogue's last mile: which line an item belongs to, set by hand.
  // Manager and above — placing an item moves every mix figure downstream.
  catalogue: (t: string, unplacedOnly: boolean) =>
    req<Record<string, unknown>>(
      `/api/v1/insight/catalogue?unplaced_only=${unplacedOnly}`, {}, t),

  setItemLine: (t: string, productId: string, category: string) =>
    req<Record<string, unknown>>(
      `/api/v1/insight/catalogue/${encodeURIComponent(productId)}`,
      { method: "PUT", body: JSON.stringify({ category }) }, t),

  clearItemLine: (t: string, productId: string) =>
    req<unknown>(`/api/v1/insight/catalogue/${encodeURIComponent(productId)}`,
      { method: "DELETE" }, t),

  deleteTarget: (t: string, targetId: string) =>
    req<unknown>(`/api/v1/insight/targets/${encodeURIComponent(targetId)}`,
      { method: "DELETE" }, t),

  // The negotiation desk. A POST because it computes on what the salesperson
  // is proposing, not on what is stored — nothing is persisted by asking.
  negotiate: (t: string, body: Record<string, unknown>) =>
    req<Record<string, unknown>>("/api/v1/insight/negotiate",
      { method: "POST", body: JSON.stringify(body) }, t),

  simulationScenarios: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/simulate/scenarios", {}, t),

  simulate: (t: string, body: Record<string, unknown>) =>
    req<Record<string, unknown>>("/api/v1/insight/simulate",
      { method: "POST", body: JSON.stringify(body) }, t),

  dataStatus: (t: string) => req<DataStatus>("/api/v1/data/status", {}, t),

  /** Queue a pull. Returns immediately with a job to watch — 202, not a result.
   *  If one is already running this hands that one back (`started: false`)
   *  rather than erroring, so the screen shows the live job. */
  runSync: (t: string, opts: SyncOptions = {}) =>
    req<SyncStartResponse>("/api/v1/data/sync",
      { method: "POST", body: JSON.stringify(opts) }, t),

  /** The current sync state. Polled while a job is in flight. */
  syncState: (t: string) => req<SyncState>("/api/v1/data/sync", {}, t),

  setZohoConnection: (t: string, body: ZohoConnectionInput) =>
    req<{ connection: DataStatus["connection"] }>(
      "/api/v1/data/connection", { method: "PUT", body: JSON.stringify(body) }, t),

  // ── Zoho credentials, shared across organizations on purpose ─────────────
  // A refresh token belongs to a Zoho *user*, not a company, so one grant
  // already reaches every company that user can see. Re-entering it per legal
  // entity only creates copies for a future rotation to miss.
  listCredentials: (t: string) =>
    req<{ credentials: ZohoCredential[]; organizations: { organization_id: string; name: string }[] }>(
      "/api/v1/data/credentials", {}, t),

  credentialOrganizations: (t: string, credentialId: string) =>
    req<{ credential_id: string; visible_organizations: ZohoVisibleOrg[] }>(
      `/api/v1/data/credentials/${credentialId}/organizations`, {}, t),

  connectWithCredential: (t: string, credential_id: string, zoho_organization_id: string) =>
    req<{ connection: DataStatus["connection"] }>(
      "/api/v1/data/connection/use-credential",
      { method: "POST", body: JSON.stringify({ credential_id, zoho_organization_id }) }, t),

  /** Replace the Zoho grant one connection signs in with.
   *
   *  On the connection rather than on a credential of its own: a revocable
   *  refresh token is a Zoho mechanism, not something every connector has. The
   *  response says which other companies share the grant and changed with it. */
  rotateConnectionToken: (t: string, connectionId: string, refresh_token: string) =>
    req<Record<string, unknown>>(
      `/api/v1/connections/${connectionId}/rotate`,
      { method: "POST", body: JSON.stringify({ refresh_token }) }, t),

  shareCredential: (t: string, credentialId: string, organization_ids: string[]) =>
    req<{ credential: ZohoCredential }>(
      `/api/v1/data/credentials/${credentialId}/share`,
      { method: "POST", body: JSON.stringify({ organization_ids }) }, t),

  clearZohoConnection: (t: string) =>
    req<{ removed: boolean; connection: DataStatus["connection"] }>(
      "/api/v1/data/connection", { method: "DELETE" }, t),

  customerPortfolio: (t: string, customerId: string) =>
    req<CustomerPortfolio>(
      `/api/v1/commercial/customers/${encodeURIComponent(customerId)}/portfolio`, {}, t),

  customerItemDetail: (t: string, customerId: string, productId: string) =>
    req<CustomerItemDetail>(
      `/api/v1/commercial/customers/${encodeURIComponent(customerId)}` +
      `/items/${encodeURIComponent(productId)}`, {}, t),

  listAccounts: (t: string, q = "", status: StatusFilter = "active") => {
    const p = new URLSearchParams({ status });
    if (q) p.set("q", q);
    return req<Account[]>(`/api/v1/accounts?${p}`, {}, t);
  },

  /** The items one account has bought, newest first. Lets a screen offer a name
   *  where it would otherwise demand an id nobody can recognise. */
  listAccountItems: (t: string, customerId: string,
                     status: StatusFilter = "active") =>
    req<AccountItem[]>(
      `/api/v1/accounts/${encodeURIComponent(customerId)}/items?status=${status}`,
      {}, t),

  demoSeed: (t: string) => req<Record<string, unknown>>("/api/v1/internal/demo-seed", { method: "POST" }, t),

  // ── approvals ─────────────────────────────────────────────────────────────
  listApprovals: (t: string, status?: string) =>
    req<{ requests: ApprovalRequest[]; pending_for_me: number }>(
      `/api/v1/approvals${status ? `?status=${status}` : ""}`, {}, t),

  decideApproval: (t: string, id: string, status: string, note?: string) =>
    req<ApprovalRequest>(`/api/v1/approvals/${id}/decide`,
      { method: "POST", body: JSON.stringify({ status, note }) }, t),

  requestQuoteLineApproval: (
    t: string,
    body: { quote_id: string; customer: string; line_id: string; product: string;
            qty: number; proposed_price: number; reason?: string; reason_code?: string },
  ) => req<ApprovalRequest>("/api/v1/approvals/quote-line",
        { method: "POST", body: JSON.stringify(body) }, t),

  quoteGate: (t: string, quoteId: string) =>
    req<QuoteGate>(`/api/v1/approvals/quotes/${encodeURIComponent(quoteId)}/gate`, {}, t),

  // ── administration (owner is super admin) ─────────────────────────────────
  listUsers: (t: string) =>
    req<{ users: PlatformUser[]; roles: Role[]; can_manage: boolean }>(
      "/api/v1/admin/users", {}, t),

  createUser: (t: string, body: { email: string; name: string; role: Role }) =>
    req<{ user: PlatformUser; temporary_password: string }>(
      "/api/v1/admin/users", { method: "POST", body: JSON.stringify(body) }, t),

  updateUser: (t: string, id: string, body: { role?: Role; active?: boolean; name?: string }) =>
    req<PlatformUser>(`/api/v1/admin/users/${id}`,
      { method: "PATCH", body: JSON.stringify(body) }, t),

  resetUserPassword: (t: string, id: string) =>
    req<{ user_id: string; temporary_password: string }>(
      `/api/v1/admin/users/${id}/reset-password`, { method: "POST" }, t),

  changeOwnPassword: (t: string, current_password: string, new_password: string) =>
    req<{ ok: boolean }>("/api/v1/admin/me/password",
      { method: "POST", body: JSON.stringify({ current_password, new_password }) }, t),

  getPolicy: (t: string) =>
    req<{ policy: OrgPolicy; can_manage: boolean; margin_policy: MarginPolicy;
          fixed: FixedThresholds; thresholds?: ThresholdView }>(
      "/api/v1/admin/policy", {}, t),

  updatePolicy: (t: string, body: Partial<OrgPolicy>) =>
    req<OrgPolicy>("/api/v1/admin/policy", { method: "PATCH", body: JSON.stringify(body) }, t),

  /** Owner only. Validated as a whole policy, not as a diff — one edit that is
   *  fine alone can invert the floor ladder against what is already saved. */
  updateMarginPolicy: (t: string, body: MarginPolicyPatch) =>
    req<{ margin_policy: MarginPolicy; note: string }>(
      "/api/v1/admin/margin-policy", { method: "PATCH", body: JSON.stringify(body) }, t),

  // ── identity: link records across connectors, never merge them ───────────
  listIdentities: (t: string, kind: EntityKind, q = "", linkedOnly = false) =>
    req<{ identities: Identity[]; total: number; can_manage: boolean;
          pending_suggestions: number }>(
      `/api/v1/identity/${kind}?q=${encodeURIComponent(q)}&linked_only=${linkedOnly}`, {}, t),

  getIdentity: (t: string, kind: EntityKind, id: string) =>
    req<Identity>(`/api/v1/identity/${kind}/${id}`, {}, t),

  listSuggestions: (t: string, kind: EntityKind) =>
    req<{ suggestions: IdentitySuggestion[]; can_manage: boolean }>(
      `/api/v1/identity/${kind}/suggestions/pending`, {}, t),

  decideSuggestion: (t: string, kind: EntityKind, id: string, accept: boolean) =>
    req<{ suggestion_id: string; status: string }>(
      `/api/v1/identity/${kind}/suggestions/${id}`,
      { method: "POST", body: JSON.stringify({ accept }) }, t),

  linkRecord: (t: string, kind: EntityKind, record_id: string, identity_id: string,
               reason = "") =>
    req<{ record_id: string; identity_id: string }>(`/api/v1/identity/${kind}/link`,
      { method: "POST", body: JSON.stringify({ record_id, identity_id, reason }) }, t),

  unlinkRecord: (t: string, kind: EntityKind, record_id: string, reason = "") =>
    req<{ record_id: string; identity_id: string }>(`/api/v1/identity/${kind}/unlink`,
      { method: "POST", body: JSON.stringify({ record_id, reason }) }, t),

  relabelIdentity: (t: string, kind: EntityKind, id: string, label: string) =>
    req<Identity>(`/api/v1/identity/${kind}/${id}`,
      { method: "PATCH", body: JSON.stringify({ label }) }, t),

  identityPolicy: (t: string) =>
    req<IdentityPolicy>("/api/v1/identity/settings/policy", {}, t),

  updateIdentityPolicy: (t: string, body: Partial<IdentityPolicy>) =>
    req<{ auto_link_customers: boolean; auto_link_items: boolean }>(
      "/api/v1/identity/settings/policy",
      { method: "PATCH", body: JSON.stringify(body) }, t),

  // ── connections: many Zoho companies per organization ─────────────────────
  listConnections: (t: string) => req<ConnectionsView>("/api/v1/connections", {}, t),

  addConnection: (t: string, body: NewConnectionInput) =>
    req<ZohoConnection>("/api/v1/connections",
      { method: "POST", body: JSON.stringify(body) }, t),

  editConnection: (t: string, id: string, body: { label?: string; enabled?: boolean }) =>
    req<ZohoConnection>(`/api/v1/connections/${id}`,
      { method: "PATCH", body: JSON.stringify(body) }, t),

  removeConnection: (t: string, id: string) =>
    req<{ removed: boolean; connection_id: string; note: string }>(
      `/api/v1/connections/${id}`, { method: "DELETE" }, t),

  checkConnection: (t: string, id: string) =>
    req<ConnectionCheck>(`/api/v1/connections/${id}/check`, { method: "POST" }, t),
};

/** True when a request failed because the session is no longer valid. */
export function isAuthError(e: unknown): boolean {
  return (e as { status?: number })?.status === 401;
}
