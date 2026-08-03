import type { Account, ApprovalRequest, ConnectionCheck, ConnectionsView, FixedThresholds, MarginPolicy, MarginPolicyPatch, NewConnectionInput, ZohoConnection, ZohoCredential, ZohoVisibleOrg, CustomerItemDetail, CustomerPortfolio, DataStatus, DecisionDetail, DecisionSummary, OrgPolicy, PlatformSession, PlatformUser, QuoteGate, Role, SyncOptions, SyncRun, ThresholdView, ZohoConnectionInput } from "./types";

const KEY = "pie_platform_session";

export function loadPlatformSession(): PlatformSession | null {
  const raw = localStorage.getItem(KEY);
  return raw ? (JSON.parse(raw) as PlatformSession) : null;
}
export function savePlatformSession(s: PlatformSession) {
  localStorage.setItem(KEY, JSON.stringify(s));
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
      detail =
        "The server hit an error it could not describe. Its log has the traceback — " +
        "if this followed a deployment, the usual cause is a pending `alembic upgrade head`.";
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

  act: (t: string, id: string, body: { action: string; note?: string; reason?: string }) =>
    req<DecisionSummary>(`/api/v1/decisions/${id}/action`, { method: "POST", body: JSON.stringify(body) }, t),

  /** Undo a human action — returns the decision to the queue. The reopen is
   *  itself recorded, so the audit trail keeps both the action and its reversal. */
  reopen: (t: string, id: string) =>
    req<DecisionSummary>(`/api/v1/decisions/${id}/action`,
      { method: "POST", body: JSON.stringify({ action: "REOPEN", note: "Undone by the user" }) }, t),

  dataStatus: (t: string) => req<DataStatus>("/api/v1/data/status", {}, t),

  runSync: (t: string, opts: SyncOptions = {}) =>
    req<{ run: SyncRun; connection: DataStatus["connection"];
         demo_data_removed?: Record<string, number> }>(
      "/api/v1/data/sync", { method: "POST", body: JSON.stringify(opts) }, t),

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

  rotateCredential: (t: string, credentialId: string, refresh_token: string,
                     client_id?: string, client_secret?: string) =>
    req<{ credential: ZohoCredential }>(
      `/api/v1/data/credentials/${credentialId}/rotate`,
      { method: "POST", body: JSON.stringify({ refresh_token, client_id, client_secret }) }, t),

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

  listAccounts: (t: string, q = "") =>
    req<Account[]>(`/api/v1/accounts${q ? `?q=${encodeURIComponent(q)}` : ""}`, {}, t),

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
