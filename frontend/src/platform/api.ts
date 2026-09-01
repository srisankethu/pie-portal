import type { AccessReport, Account, AccountItem, AiByokView, AiKeyTestResult, AiMetricsReport, AiReadiness, ApprovalRequest, AttributionEvaluation, AttributionEvents, AttributionRollup, AttributionSummary, CompanyCatalogue, CompanyCatalogues, ConnectionCheck, ConnectionsView, ConnectorCatalog, CustomerItemDetail, CustomerPortfolio, DataStatus, DecisionDetail, DecisionSummary, DecisionTrace, DemoOffer, DisclosureStatement, Entitlements, EntityKind, ErasureState, ErpConnectInput, ErpDiscoveredCompany, FixedThresholds, FloorBacktest, Identity, IdentityCoverage, IdentityPolicy, IdentitySuggestion, MarginPolicy, MarginPolicyPatch, NewConnectionInput, OnboardingView, OrgPolicy, PackFit, PayloadsReport, PlatformSession, PlatformUser, QuoteGate, Retrospective, Role, SignupOffer, SkippedRows, StatusFilter, SyncOptions, SyncRunLogPage, SyncStartResponse, SyncState, ThresholdView, UnrecordedQuotes, ZohoConnection, ZohoConnectionInput, ZohoCredential, ZohoVisibleOrg } from "./types";

import { setMoneyCurrency } from "../money";
import { setBusinessTimezone } from "../when";
import { parseStoredSession } from "./schemas";
import { authInit } from "../authFetch";

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
  // The token is deliberately dropped on the way to storage. It lives in an
  // httpOnly cookie now, which script on this page cannot read and therefore
  // cannot leak — the whole reason for the move. What stays here is the part
  // that is not a secret: who you are, what role, which currency and zone, so
  // the shell can draw itself on the first frame instead of flashing empty
  // while `/auth/me` answers.
  //
  // Kept as a field rather than removed from the type because `req` already
  // treats an empty token as "no Authorization header", so every call site
  // passes `session.token` unchanged and the cookie authenticates instead.
  localStorage.setItem(KEY, JSON.stringify({ ...s, token: "" }));
  setMoneyCurrency(s.currency);
  setBusinessTimezone(s.timezone);
}
export function clearPlatformSession() {
  localStorage.removeItem(KEY);
}

/** Watch for the session changing in *another* tab.
 *
 *  `localStorage` is shared across every tab of a profile, and one session per
 *  browser is the intended model — but nothing propagated a change, so the tabs
 *  disagreed. Signing out in one left the others drawing a full signed-in shell
 *  whose every request would 401; signing in as somebody else left the first tab
 *  showing the previous person's name and role until it happened to reload,
 *  which on a shared machine is the wrong person's screen.
 *
 *  The `storage` event only fires in the *other* tabs, never the one that wrote,
 *  so this cannot loop back on itself. Returns its own unsubscribe. */
export function onSessionChangedElsewhere(fn: (next: PlatformSession | null) => void): () => void {
  const handler = (e: StorageEvent) => {
    if (e.key !== null && e.key !== KEY) return;   // null = storage cleared wholesale
    fn(e.newValue ? loadPlatformSession() : null);
  };
  window.addEventListener("storage", handler);
  return () => window.removeEventListener("storage", handler);
}

/** Told once, from the shell, what to do when the session stops being valid.
 *
 *  This is registered rather than thrown-and-caught because catching it was the
 *  bug: `isAuthError` existed and exactly three call sites used it, all three on
 *  the decision queue. Every other screen — Customers, Cash, Quotes, Settings —
 *  turned a retired token into its ordinary "this did not load" panel, inside a
 *  shell that still drew the user's name and role. The session was gone and the
 *  app looked signed in, and the way out was to notice the pattern and press
 *  Sign out.
 *
 *  A 401 is not a per-screen error. It is a statement about the session that
 *  every request can make, so the transport is the only place that can hear all
 *  of them. Screens keep their catch blocks and still get the throw; they no
 *  longer have to remember to ask whether this particular failure ended the
 *  session. */
let onAuthLoss: (() => void) | null = null;

export function setAuthLossHandler(fn: (() => void) | null): void {
  onAuthLoss = fn;
}

/** Fired for any 401, before the error reaches the caller. Never throws: a
 *  handler that fails must not replace the real error with its own. */
function noteAuthLoss(status: number): void {
  if (status !== 401 || !onAuthLoss) return;
  try {
    onAuthLoss();
  } catch {
    /* the throw below is the more useful signal */
  }
}

async function req<T>(path: string, opts: RequestInit = {}, token?: string): Promise<T> {
  const res = await fetch(path, authInit(opts, token));
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
    noteAuthLoss(res.status);
    throw err;
  }
  return (await res.json()) as T;
}

/** A file endpoint, fetched with the token and handed back as bytes.
 *
 *  `req` is not usable here: it parses the body as JSON, and these responses are
 *  a spreadsheet. The filename comes from the server's `Content-Disposition`
 *  when it sends one, so the run's date and id are on the saved file rather
 *  than on a name the client guessed — two exports from two runs must not
 *  arrive as `skipped-rows.csv` and `skipped-rows (1).csv`.
 *
 *  Error handling routes through `req`'s: a failed download is reported by the
 *  screen the same way a failed fetch is, rather than saving a file containing
 *  the error message. */
async function download(path: string, token: string, fallbackName: string):
    Promise<{ blob: Blob; filename: string }> {
  const res = await fetch(path, authInit({}, token));
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    let detail = res.statusText;
    if (body) {
      try {
        detail = JSON.parse(body).detail || body;
      } catch {
        detail = body.slice(0, 500);
      }
    }
    const err = new Error(detail) as Error & { status?: number };
    err.status = res.status;
    noteAuthLoss(res.status);
    throw err;
  }
  const disposition = res.headers.get("Content-Disposition") || "";
  const match = /filename="?([^";]+)"?/.exec(disposition);
  return { blob: await res.blob(), filename: match?.[1] || fallbackName };
}

interface SignUpBody {
  company: string;
  name: string;
  email: string;
  password: string;
  /** Which plan this business wants. Recorded for the operator, granted by
   *  nothing — every sign-up lands on the free plan whatever this says. */
  plan?: string;
  /** What this organization trades in. Unlike `plan`, this one is *kept*: it
   *  denominates every figure the organization will ever see, and nothing in
   *  the product changes it afterwards. Omitting it is how every self-serve
   *  tenant ended up on the API's INR default. */
  currency?: string;
}

interface LoginResp {
  token: string;
  role: PlatformSession["role"];
  name: string;
  /** The address signed in with. `LoginResponse` defaults it to "" server-side,
   *  so an older backend simply sends nothing and the field stays unset. */
  email?: string;
  user_id: string;
  organization_id: string;
  currency: string;
  timezone: string;
  must_change_password: boolean;
}

/** One live session, as `/auth/sessions` returns it. */
export interface PlatformSessionRow {
  session_id: string;
  issued_at: string;
  last_seen_at: string;
  user_agent: string;
  /** The session this browser is reading the list with. */
  current: boolean;
}

export const papi = {
  login: (email: string, password: string) =>
    req<LoginResp>("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),

  /** Who the session cookie belongs to, asked on boot.
   *
   *  The stored profile is a cache for the first frame; this is the truth. It
   *  is also how a session revoked from another device is noticed at load
   *  rather than at whichever request happens to fail first. */
  me: () => req<LoginResp>("/api/v1/auth/me"),

  /** End this session on the server, not just in this browser. */
  logout: () => req<{ ok: boolean }>("/api/v1/auth/logout", { method: "POST" }),

  /** End every session for this account, this device included. */
  logoutAll: () =>
    req<{ ok: boolean; ended: number }>("/api/v1/auth/logout-all", { method: "POST" }),

  /** Where this account is signed in. */
  sessions: () => req<PlatformSessionRow[]>("/api/v1/auth/sessions"),

  /** Sign out one other device. */
  revokeSession: (sessionId: string) =>
    req<{ ok: boolean }>(`/api/v1/auth/sessions/${encodeURIComponent(sessionId)}`,
                         { method: "DELETE" }),

  /** Whether this deployment lets a company create its own tenant.
   *
   *  Asked before the landing page offers the button, so a single-tenant
   *  install — where sign-up is off, which is the default — never shows a "Get
   *  started free" that leads to a form that always refuses. Public: no token.
   */
  signupOffer: () => req<SignupOffer>("/api/v1/signup"),

  /** Create an organization and its owner, and come back signed in.
   *
   *  Types as `LoginResp` because the server returns exactly the login shape —
   *  deliberately, so `signIn` stores a sign-up with the same code that stores
   *  a sign-in rather than a second path that forgets the currency. */
  signUp: (body: SignUpBody) =>
    req<LoginResp>("/api/v1/signup", { method: "POST", body: JSON.stringify(body) }),

  /** Is there a demonstration workspace here, and entering it. The POST needs
   *  no credential and returns the same envelope a sign-in does, so the client
   *  stores it with the same code — one way to become signed in, not two. */
  demoOffer: () => req<DemoOffer>("/api/v1/demo"),

  /** Ask to move plan. Owner only, and it grants nothing — a person decides it.
   *  Returns the whole entitlement view so the caller re-renders from one
   *  answer instead of deciding locally that it worked. */
  requestPlan: (t: string, plan: string, note = "") =>
    req<Entitlements>("/api/v1/entitlements",
                      { method: "POST", body: JSON.stringify({ plan, note }) }, t),
  enterDemo: () => req<LoginResp>("/api/v1/demo", { method: "POST" }),

  /** What this organization still has to do before the screens have anything
   *  to say. Derived server-side from connections, sync runs, the policy row
   *  and the user list — never a stored "setup complete" flag. */
  onboarding: (t: string) => req<OnboardingView>("/api/v1/onboarding", {}, t),

  /** This organization's plan, and how long any trial has left.
   *
   *  Any signed-in role, and the server decides what it says. Read by the
   *  trial notice in the shell — before this the endpoint had no caller at
   *  all, so a tenant's free month simply ran out one day with no warning. */
  entitlements: (t: string) => req<Entitlements>("/api/v1/entitlements", {}, t),

  /** Act for another workspace this identity belongs to.
   *
   *  Returns a fresh session envelope, exactly as a sign-in does, because that
   *  is what it is: switching mints a new session against the target workspace
   *  rather than repointing the one in hand — see `routers/organizations.py`.
   *  The server looks the membership up inside the target tenant and answers
   *  404 without one, so nothing here decides who may go where. */
  switchOrganization: (t: string, organizationId: string) =>
    req<{ token: string; organization_id: string; name: string; role: Role }>(
      `/api/v1/organizations/${encodeURIComponent(organizationId)}/switch`,
      { method: "POST" }, t),

  listDecisions: (t: string, q: { type?: string; status_filter?: string; include_detail?: boolean } = {}) => {
    const p = new URLSearchParams();
    if (q.type) p.set("type", q.type);
    if (q.status_filter) p.set("status_filter", q.status_filter);
    if (q.include_detail) p.set("include_detail", "true");
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
  /** The morning read. One request for the whole landing page's top.
   *
   *  `movedFrom`/`movedTo` set the window the **What moved** band reports over
   *  — that band and no other. The rest are states as of now, not periods, so
   *  a page-wide date control would be answering a question three of the four
   *  bands cannot be asked. */
  daily: (t: string, movedFrom?: string, movedTo?: string,
          committedWeeks?: number) => {
    const p = new URLSearchParams();
    if (movedFrom) p.set("moved_from", movedFrom);
    if (movedTo) p.set("moved_to", movedTo);
    if (committedWeeks && committedWeeks !== 1) {
      p.set("committed_weeks", String(committedWeeks));
    }
    const qs = p.toString();
    return req<Record<string, unknown>>(
      `/api/v1/insight/daily${qs ? "?" + qs : ""}`, {}, t);
  },

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

  // The same measurement from the other end of the ledger. Manager and above,
  // scoped like `supply` rather than like `payments`: which customers pay us
  // slowly is a call list, which suppliers we are stringing along is a
  // commercial position.
  payables: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/payables", {}, t),

  // The whole cycle the two above sit inside: order → invoice → payment. Dates
  // and day counts only, so it is scoped like `payments` rather than like
  // `payables` — how long we take to bill is not a commercial position, it is
  // the half of the wait this business controls.
  orderToCash: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/order-to-cash", {}, t),

  // Which quotes were won and which were lost. No cost anywhere in it, so
  // every role reads it — a salesperson sees their own accounts, scoped by the
  // server exactly as `/api/v1/accounts` is.
  quoteOutcomes: (t: string, months = 12) =>
    req<Record<string, unknown>>(
      `/api/v1/insight/quote-outcomes?months=${months}`, {}, t),

  /** The quotes the ERP holds no outcome for, in the order worth asking about.
   *
   *  Every role, for the reason `quoteOutcomes` is: nothing in it is derived
   *  from cost. `value` is each quote's own selling total — the number that
   *  went to the customer — and the rest is dates, the ERP's own status word
   *  and counts of them. A salesperson is narrowed to their own accounts by the
   *  server, which is where role lives.
   *
   *  `limit` pages the rows and **not** the counts: `totals` is taken over the
   *  whole pile server-side, so `count` says how big it is and `listed` says
   *  how much of it came back. Raising the limit shows more rows; it never
   *  changes the headline.
   *
   *  Typed, where its neighbours above return `Record<string, unknown>`. See
   *  the note above `UnrecordedQuoteGroup` in `types.ts`: this payload's whole
   *  point is that a missing expiry and a missing total are facts, and an
   *  untyped response is what makes `Number(v ?? 0)` the shortest path to
   *  rendering one. */
  unrecordedQuotes: (t: string, limit = 50) =>
    req<UnrecordedQuotes>(
      `/api/v1/insight/unrecorded-quotes?limit=${limit}`, {}, t),

  // Where a losing price sat, against what wins and against what that customer
  // has paid — and the margin behind both. Manager and above, scoped like
  // `payables`: a win rate is a fact about a relationship, where the margin
  // sits on the ones we lose is a commercial position.
  quotePricing: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/quote-pricing", {}, t),

  // What we actually agreed to pay a supplier in, which Zoho's fixed dropdown
  // often cannot express. Zoho's own value is never overwritten — both travel
  // together, because the gap between them is the thing worth seeing.
  vendorTerms: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/vendor-terms", {}, t),

  setVendorTerm: (t: string, vendorId: string, days: number, basis: string,
                  note?: string | null) =>
    req<Record<string, unknown>>("/api/v1/insight/vendor-terms", {
      method: "PUT",
      body: JSON.stringify({ vendor_id: vendorId, days, basis,
                             note: note ?? null }),
    }, t),

  /** Withdraw the agreement and fall back to the ERP's date. Deliberately not
   *  "set it to Zoho's current number": that would freeze a value Zoho may
   *  later change. */
  clearVendorTerm: (t: string, vendorId: string) =>
    req<Record<string, unknown>>(
      `/api/v1/insight/vendor-terms/${encodeURIComponent(vendorId)}`,
      { method: "DELETE" }, t),

  // What each account owes against the limit it was given, and whose book it
  // is in. Every role: a limit and a balance are money already billed, not
  // cost, and chasing your own overdue accounts is the salesperson's job. The
  // response is scoped to that person's book; a manager gets the organization.
  credit: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/credit", {}, t),

  /** Record how much credit an account has. Manager and above — reading a
   *  limit is everyone's business, deciding one is a commercial position. */
  setCreditLimit: (t: string, customerId: string, amount: number,
                   note?: string | null) =>
    req<Record<string, unknown>>("/api/v1/insight/credit-limits", {
      method: "PUT",
      body: JSON.stringify({ customer_id: customerId, amount,
                             note: note ?? null }),
    }, t),

  /** Withdraw the limit. Deliberately not "set it to zero": a withdrawn limit
   *  means nobody has decided, and a zero one is a standing hold. */
  clearCreditLimit: (t: string, customerId: string) =>
    req<Record<string, unknown>>(
      `/api/v1/insight/credit-limits/${encodeURIComponent(customerId)}`,
      { method: "DELETE" }, t),

  /** Put an account in somebody's book. Zoho's own salesperson is never
   *  overwritten — both travel together. */
  setAccountOwner: (t: string, customerId: string, userId: string) =>
    req<Record<string, unknown>>("/api/v1/insight/account-owners", {
      method: "PUT",
      body: JSON.stringify({ customer_id: customerId, user_id: userId }),
    }, t),

  /** Withdraw the assignment and fall back to Zoho's salesperson. */
  clearAccountOwner: (t: string, customerId: string) =>
    req<Record<string, unknown>>(
      `/api/v1/insight/account-owners/${encodeURIComponent(customerId)}`,
      { method: "DELETE" }, t),
  // Statutory payment timing. Manager and above, scoped like `payables` for the
  // same reason: every row is a supplier balance, and the watchlist additionally
  // carries a cost estimate derived from the organization's tax rate.
  msmeWatchlist: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/msme-watchlist", {}, t),

  /** Which suppliers are worth establishing a status for, ranked. The watchlist
   *  is only as good as its coverage, coverage is collected by a person one
   *  supplier at a time, so the question itself is prioritised. */
  msmeCaptureBacklog: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/msme-capture-backlog", {}, t),

  /** Record what was established about a supplier. `written_agreement` is
   *  tri-state on the wire as well as in the column: omitting it says "not
   *  established", which is not the same as sending false. */
  setMsmeStatus: (t: string, body: {
    vendor_id: string; classification: string; enterprise_activity: string;
    evidence: string; written_agreement?: boolean | null;
    agreed_days?: number | null; udyam_number?: string | null;
    note?: string | null;
  }) =>
    req<Record<string, unknown>>("/api/v1/insight/msme-status", {
      method: "PUT", body: JSON.stringify(body),
    }, t),

  msmeWithholding: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/withholding-crossings", {}, t),

  // Manager and above. The inflow half is receivables, but the outflow half is
  // what we owe suppliers — purchase cost by another name — so the endpoint is
  // scoped like `supply` and the panel is hidden rather than 403'd.
  cashflow: (t: string, weeks: number) =>
    req<Record<string, unknown>>(
      `/api/v1/insight/cashflow?weeks=${weeks}`, {}, t),

  // Owner only, and not merely manager: what three legal entities kept after
  // tax is entity economics rather than a commercial figure. The panel is
  // hidden for everyone else rather than 403'd, the same way `supply` is.
  selfFunding: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/self-funding", {}, t),
  // Manager and above, and scoped like `cashflow` for a stronger reason: two of
  // the three legs are denominated in what stock cost. Removing them would
  // leave a composite that answers nothing, so the panel is hidden rather than
  // 403'd — the same treatment `supply` gets.
  cashCycle: (t: string, months: number) =>
    req<Record<string, unknown>>(
      `/api/v1/insight/cash-cycle?months=${months}`, {}, t),

  stock: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/stock", {}, t),

  supply: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/supply", {}, t),

  // What each line returns on the cash it ties up. Manager and above, and
  // permanently: GMROI is gross profit ÷ purchase cost with nothing else in it,
  // so there is no version of the screen with the economics removed. The nav
  // item follows the endpoint rather than 403-ing, and `/stock` carries the
  // withholding notice for the roles that cannot open this.
  gmroi: (t: string, months: number) =>
    req<Record<string, unknown>>(
      `/api/v1/insight/gmroi?months=${months}`, {}, t),

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
  // `connectionId` scopes both halves on the server. Like the mix grid and
  // unlike the row filters: this screen's figures are shares of a total, so
  // narrowing has to recompute them.
  dependency: (t: string, connectionId?: string) =>
    req<Record<string, unknown>>(
      "/api/v1/insight/dependency"
      + (connectionId ? `?connection_id=${encodeURIComponent(connectionId)}` : ""),
      {}, t),

  // Vendor targets, and the rebate scheme attached to each. The one thing in
  // the platform that is typed rather than synced, so it has a write path.
  targets: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/targets", {}, t),

  // What hitting those numbers is worth: every live target with its scheme,
  // what is secured, what is at stake and — above the evidence floor — where
  // the period lands. Manager and above, like everything denominated in
  // purchase spend.
  schemes: (t: string) =>
    req<Record<string, unknown>>("/api/v1/insight/schemes", {}, t),

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

  // ── what a different approval floor would have done ───────────────────────
  // Owner only, and asked for explicitly rather than on every keystroke: each
  // call replays every recorded quote line twice.
  floorBacktest: (t: string, minMargin: number, marginFloor?: number | null) => {
    const q = new URLSearchParams({ min_margin: String(minMargin) });
    if (marginFloor !== undefined && marginFloor !== null) {
      q.set("margin_floor", String(marginFloor));
    }
    return req<FloorBacktest>(
      `/api/v1/admin/margin-policy/backtest?${q.toString()}`, {}, t);
  },

  // ── what the books already held, before PIE did anything ──────────────────
  retrospective: (t: string) => req<Retrospective>("/api/v1/retrospective", {}, t),

  // ── what PIE changed: the value-attribution ledger ────────────────────────
  // Manager and above for the summary and the ledger, owner only for the two
  // that price the platform itself, and all four behind the `intelligence`
  // plan — the same gate the insight surface sits behind, because every row of
  // this ledger is gross-profit arithmetic over the rows those screens read.
  //
  // Not under `/api/v1/`: `routers/attribution.py` mounts at `/api/v1/attribution`.
  attributionSummary: (t: string) =>
    req<AttributionSummary>("/api/v1/attribution/summary", {}, t),

  /** One page of the ledger. Rows, never a rollup — `page_is_not_a_total`
   *  travels with them, and the headline comes from the summary. */
  attributionEvents: (t: string, q: {
    eventType?: string | null; valueClass?: string | null;
    limit?: number; offset?: number;
  } = {}) => {
    const p = new URLSearchParams();
    if (q.eventType) p.set("event_type", q.eventType);
    if (q.valueClass) p.set("value_class", q.valueClass);
    if (q.limit) p.set("limit", String(q.limit));
    if (q.offset) p.set("offset", String(q.offset));
    const qs = p.toString();
    return req<AttributionEvents>(
      `/api/v1/attribution/events${qs ? "?" + qs : ""}`, {}, t);
  },

  /** The 30-day report. Owner only.
   *
   *  `pieCost` is passed because the platform holds no price for its own
   *  plans — it is the owner's own figure. Omitted, `roi` comes back `null`
   *  with `roi_is_unknown` true, which is UNKNOWN and never 0x. A default
   *  here would put a return figure nobody entered on a screen somebody
   *  signs against. */
  attributionEvaluation: (t: string, pieCost?: string | null) =>
    req<AttributionEvaluation>(
      "/api/v1/attribution/evaluation"
      + (pieCost ? `?pie_cost=${encodeURIComponent(pieCost)}` : ""), {}, t),

  /** Value month by month over a span, and the return on it. Owner only.
   *
   *  `monthlyCost` is a **rate** — what one month of the plan costs — because
   *  the span is many months. Passing a total here would divide a year of value
   *  by a month of cost. Omitted, `roi` comes back `null` with
   *  `roi_is_unknown` true: UNKNOWN, never 0x.
   *
   *  This is the figure `attributionEvaluation` cannot give after the trial
   *  ends, which is when a renewal is actually decided. */
  attributionRollup: (t: string, q: {
    months?: number; monthlyCost?: string | null;
  } = {}) => {
    const p = new URLSearchParams();
    if (q.months) p.set("months", String(q.months));
    if (q.monthlyCost) p.set("monthly_cost", q.monthlyCost);
    const qs = p.toString();
    return req<AttributionRollup>(
      `/api/v1/attribution/rollup${qs ? "?" + qs : ""}`, {}, t);
  },

  dataStatus: (t: string) => req<DataStatus>("/api/v1/data/status", {}, t),

  /** Every connected company's catalogue, and the packs one may be built with. */
  companyCatalogues: (t: string) =>
    req<CompanyCatalogues>("/api/v1/data/catalog/companies", {}, t),

  /** Store a company's item-master export.
   *
   *  Sent as a raw body rather than a multipart form: the server takes the
   *  bytes directly, so this needs no `python-multipart` on the backend — the
   *  dependency `master_health/__init__.py` refuses stays refused. `File` is
   *  a `Blob`, so `body: file` streams it without reading it into a string. */
  /** `sourceKey` names which of the company's files this one is, so uploading
   *  a second price list replaces that source and leaves the item master
   *  alone. Omitted, the server keeps the older meaning and replaces the whole
   *  export — which is what "Replace export" on a single-file company means. */
  uploadCompanyCorpus: (t: string, connectionId: string, file: File,
                        sourceKey?: string) =>
    req<CompanyCatalogue>(
      `/api/v1/data/catalog/companies/${encodeURIComponent(connectionId)}/corpus`
      + `?filename=${encodeURIComponent(file.name)}`
      + (sourceKey ? `&source_key=${encodeURIComponent(sourceKey)}` : ""),
      { method: "POST", body: file,
        headers: { "Content-Type": file.type || "text/csv" } }, t),

  /** Correct which columns of one source file the parse reads. Refused when it
   *  names a column the file does not have, so a mapping that cannot build is
   *  never stored. */
  setSourceMapping: (t: string, connectionId: string, sourceKey: string,
                     mapping: { record_id: string; description: string;
                                grade?: string | null }) =>
    req<CompanyCatalogue>(
      `/api/v1/data/catalog/companies/${encodeURIComponent(connectionId)}`
      + `/sources/${encodeURIComponent(sourceKey)}/mapping`,
      { method: "PUT", body: JSON.stringify(mapping) }, t),

  /** Stop building from one file. Superseded, not deleted, and the built
   *  catalogue is left alone — it goes OUT OF DATE until somebody rebuilds. */
  removeCompanySource: (t: string, connectionId: string, sourceKey: string) =>
    req<CompanyCatalogue>(
      `/api/v1/data/catalog/companies/${encodeURIComponent(connectionId)}`
      + `/sources/${encodeURIComponent(sourceKey)}`,
      { method: "DELETE" }, t),

  /** Try every shipped pack against a sample of this company's files, so the
   *  pack is chosen on the parser's own counts rather than on its name. */
  companyPackFit: (t: string, connectionId: string) =>
    req<PackFit>(
      `/api/v1/data/catalog/companies/${encodeURIComponent(connectionId)}/pack-fit`,
      {}, t),

  /** Choose which shipped pack decodes this company's export. */
  setCompanyPack: (t: string, connectionId: string, packId: string) =>
    req<CompanyCatalogue>(
      `/api/v1/data/catalog/companies/${encodeURIComponent(connectionId)}/pack`,
      { method: "PUT", body: JSON.stringify({ pack_id: packId }) }, t),

  /** Decode this company's stored corpus. Synchronous; the response is the
   *  finished state. */
  buildCompanyCatalog: (t: string, connectionId: string) =>
    req<CompanyCatalogue>(
      `/api/v1/data/catalog/companies/${encodeURIComponent(connectionId)}/build`,
      { method: "POST" }, t),

  /** Choose the automatic pull's cadence, in hours; 0 switches it off. */
  setAutoSync: (t: string, hours: number) =>
    req<{ auto_sync: DataStatus["auto_sync"] }>(
      "/api/v1/data/auto-sync",
      { method: "PUT", body: JSON.stringify({ hours }) }, t),

  /** Queue a pull. Returns immediately with a job to watch — 202, not a result.
   *  If one is already running this hands that one back (`started: false`)
   *  rather than erroring, so the screen shows the live job. */
  runSync: (t: string, opts: SyncOptions = {}) =>
    req<SyncStartResponse>("/api/v1/data/sync",
      { method: "POST", body: JSON.stringify(opts) }, t),

  /** The current sync state. Polled while a job is in flight. */
  syncState: (t: string) => req<SyncState>("/api/v1/data/sync", {}, t),

  /** Every row one pull could not fully resolve — the whole list, not the
   *  twenty the run row carries for the status card. Manager or owner only:
   *  a skipped bill line's value is a purchase value. */
  syncSkipped: (t: string, runId: string) =>
    req<SkippedRows>(
      `/api/v1/data/sync-runs/${encodeURIComponent(runId)}/skipped`, {}, t),

  /** The same rows as a CSV, built server-side so the file is the whole list
   *  rather than the page the grid is showing. */
  syncSkippedCsv: (t: string, runId: string) =>
    download(`/api/v1/data/sync-runs/${encodeURIComponent(runId)}/skipped.csv`,
             t, "skipped-rows.csv"),

  /** What one sync actually did, line by line — the log the run kept of itself.
   *
   *  `afterSeq` is what makes a live pull watchable: the panel asks for what it
   *  has not seen rather than re-fetching an hour of log every few seconds.
   *  Manager or owner only, for the reason the skipped rows are. */
  syncRunLog: (t: string, runId: string,
               opts: { afterSeq?: number; problemsOnly?: boolean } = {}) => {
    const q = new URLSearchParams();
    if (opts.afterSeq != null) q.set("after_seq", String(opts.afterSeq));
    if (opts.problemsOnly) q.set("problems_only", "true");
    const query = q.toString();
    return req<SyncRunLogPage>(
      `/api/v1/data/sync-runs/${encodeURIComponent(runId)}/log${query ? `?${query}` : ""}`,
      {}, t);
  },

  /** The whole log as a text file, to read elsewhere or send on. */
  syncRunLogText: (t: string, runId: string) =>
    download(`/api/v1/data/sync-runs/${encodeURIComponent(runId)}/log.txt`,
             t, "sync-log.txt"),

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
   *  response says which other companies share the grant and changed with it.
   *
   *  The client pair is optional and normally omitted — a rotation usually
   *  replaces the token under the same app. It is accepted because the one
   *  failure a token-only rotation *causes* is a token issued by a different
   *  client, and Zoho answers that with `invalid_client_secret`: without this,
   *  the fix for the most likely rotation failure is off this screen. */
  rotateConnectionToken: (
    t: string, connectionId: string, refresh_token: string,
    client?: { client_id: string; client_secret: string },
  ) =>
    req<Record<string, unknown>>(
      `/api/v1/connections/${connectionId}/rotate`,
      { method: "POST", body: JSON.stringify({ refresh_token, ...client }) }, t),

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

  /** Change a member. `member: false` ends their membership of this
   *  organization; `active` is the wider act of switching the login off
   *  everywhere. Two fields because they are two decisions — see
   *  `routers/admin.py`. */
  updateUser: (t: string, id: string,
               body: { role?: Role; active?: boolean; member?: boolean; name?: string }) =>
    req<PlatformUser>(`/api/v1/admin/users/${id}`,
      { method: "PATCH", body: JSON.stringify(body) }, t),

  resetUserPassword: (t: string, id: string) =>
    req<{ user_id: string; temporary_password: string }>(
      `/api/v1/admin/users/${id}/reset-password`, { method: "POST" }, t),

  // Returns a fresh token: changing the password retires the one used to make
  // the change, so a caller that keeps the old one is signed out by its own
  // success. Callers must swap it in.
  changeOwnPassword: (t: string, current_password: string, new_password: string) =>
    req<{ ok: boolean; token: string }>("/api/v1/admin/me/password",
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
    req<{ suggestions: IdentitySuggestion[]; can_manage: boolean;
          coverage: IdentityCoverage }>(
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

  // ── the customer-facing Zoho authorization ────────────────────────────────
  // Two calls, not three. `authorizeZoho` starts it; `claimZohoAuthorization`
  // turns the code the redirect came back with into a credential id. From
  // there the flow rejoins the manual path exactly — `credentialOrganizations`
  // to choose a company, `addConnection` to connect it — because an
  // authorization's only product is a sign-in, and a second company picker
  // built for this flow would be the copy that stops agreeing with that one.
  authorizeZoho: (t: string, dc: string) =>
    req<{ authorization_url: string; expires_in_seconds: number }>(
      `/api/v1/connections/zoho/authorize?dc=${encodeURIComponent(dc)}`, {}, t),

  claimZohoAuthorization: (t: string, handoff: string) =>
    req<{ credential_id: string; label: string }>(
      `/api/v1/connections/zoho/pending/${encodeURIComponent(handoff)}`, {}, t),

  // ── registered ERP connectors (NetSuite, Business Central, Acumatica, P21,
  //    Sage) — the form renders from this catalog, never from hardcoded fields
  connectorCatalog: (t: string) =>
    req<ConnectorCatalog>("/api/v1/connections/catalog", {}, t),

  addErpConnection: (t: string, body: ErpConnectInput) =>
    req<ConnectionCheck>("/api/v1/connections/erp",
      { method: "POST", body: JSON.stringify(body) }, t),

  discoverErpCompanies: (t: string, connector: string, values: Record<string, string>) =>
    req<{ connector: string; companies: ErpDiscoveredCompany[] }>(
      "/api/v1/connections/erp/discover",
      { method: "POST", body: JSON.stringify({ connector, values }) }, t),

  rotateErpConnection: (t: string, id: string, values: Record<string, string>) =>
    req<ConnectionCheck & { rotated: boolean; note: string }>(
      `/api/v1/connections/${id}/rotate-erp`,
      { method: "POST", body: JSON.stringify({ values }) }, t),

  // ── the AI layer, from the outside (owner only) ───────────────────────────
  /** Which provider will really run, and what the next decision run would
   *  cost. Safe to call whenever the panel is open: it sends nothing. */
  aiReadiness: (t: string) => req<AiReadiness>("/api/v1/internal/ai-readiness", {}, t),

  /** What the AI has actually cost and how often it degraded. */
  aiMetrics: (t: string) => req<AiMetricsReport>("/api/v1/internal/ai-metrics", {}, t),

  /** The BYOK card: which providers hold an organization key, and which runs.
   *  No response from any of these ever contains a key — only the hint. */
  aiProviders: (t: string) => req<AiByokView>("/api/v1/ai/providers", {}, t),

  saveAiKey: (t: string, provider: string, body: { api_key: string; model: string }) =>
    req<AiByokView>(`/api/v1/ai/providers/${provider}`,
                    { method: "PUT", body: JSON.stringify(body) }, t),

  removeAiKey: (t: string, provider: string) =>
    req<AiByokView>(`/api/v1/ai/providers/${provider}`, { method: "DELETE" }, t),

  /** One live round trip with the stored key — a fixed ping, no business data. */
  testAiKey: (t: string, provider: string) =>
    req<AiKeyTestResult>(`/api/v1/ai/providers/${provider}/test`, { method: "POST" }, t),

  /** "" restores the deployment default. */
  setActiveAiProvider: (t: string, provider: string) =>
    req<AiByokView>("/api/v1/ai/active",
                    { method: "PUT", body: JSON.stringify({ provider }) }, t),

  // ── the trust surface (owner only) ────────────────────────────────────────
  // Every endpoint under here is `require_owner` server-side. The nav item is
  // gated too, for the reason `ability.ts` gives — the gate is the server's.

  /** What may reach a model and what never does, served from the constants the
   *  outbound checker enforces rather than from a static document. */
  disclosure: (t: string) => req<DisclosureStatement>("/api/v1/trust/disclosure", {}, t),

  /** Every payload logged for this organization. `reveal` decrypts the text;
   *  left false the list is metadata, which is all the summary needs. */
  payloads: (t: string, limit = 50, reveal = false) =>
    req<PayloadsReport>(
      `/api/v1/trust/payloads?limit=${limit}&reveal=${reveal}`, {}, t),

  /** Break-glass grants, uses and revocations. No filter — deliberately. */
  accessLog: (t: string, limit = 200) =>
    req<AccessReport>(`/api/v1/trust/access?limit=${limit}`, {}, t),

  /** Everything this organization owns, as JSON, including the identity graph. */
  trustExport: (t: string) => req<Record<string, unknown>>("/api/v1/trust/export", {}, t),

  erasureState: (t: string) => req<ErasureState>("/api/v1/trust/erasure", {}, t),

  /** Irreversible. The organization id is required in the body by the server as
   *  deliberate friction, and this passes through whatever the owner typed so a
   *  mismatch is refused there rather than smoothed over here. */
  erase: (t: string, confirmOrganizationId: string, reason: string) =>
    req<ErasureState>("/api/v1/trust/erasure", {
      method: "POST",
      body: JSON.stringify({ confirm_organization_id: confirmOrganizationId, reason }),
    }, t),
};

/** True when a request failed because the session is no longer valid. */
export function isAuthError(e: unknown): boolean {
  return (e as { status?: number })?.status === 401;
}
