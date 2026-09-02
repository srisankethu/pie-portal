# User flow paths

Every path a person — or a machine — can walk through this application, traced
from the code: the frontend route table (`frontend/src/platform/route.ts`,
`PlatformApp.tsx`), the mounted backend routers (`backend/app/main.py`), and the
role guards (`backend/app/authz.py`). Each flow lists its **trigger**, the
**steps** actually taken (screen → action → API call), every **branch** the code
can take (role gates, empty states, refusals, errors), and every way the flow
can **end**.

Conventions used throughout:

- Routes are hash paths (`#/decisions`) — the SPA uses hash routing so the API
  and bundle can be served by one FastAPI app. The Quote Builder lives at
  `#/quotes`; `#/negotiate` is a different screen (the negotiation-floor desk).
- Roles are the backend's names: **SALESPERSON**, **SALES_MANAGER**, **OWNER**
  (`authz.py`). The frontend mirror (`platform/ability.ts`) only decides what is
  *offered*; the server is always the authority, and cost/margin are **absent
  from a salesperson's responses**, never hidden client-side.
- `require_manager_or_owner` and `require_owner` name the two server dependency
  guards. "any signed-in" means `current_principal` alone.
- Terminal states include abandonment and refusal — an error message held on
  screen is an ending, not a footnote.

Related reading: `docs/architecture.md` (why the pipeline is shaped this way),
`docs/reviews/role-review-*.md` (what each role experienced end to end),
`docs/resolution-api.md` (the public API contract in depth).

---

## 1. Actors

| Actor | Who they are | What the app offers them |
|---|---|---|
| **Visitor** | Anyone signed out | Landing page, sign-in, self-serve sign-up (when enabled), read-only demo (when configured) |
| **SALESPERSON** | Runs the quoting desk | Quote Builder, decisions queue (own accounts), customers, the cost-free insight screens, own approvals, own settings. No cost, margin, supplier spend, policy, or trust surface — omitted server-side |
| **SALES_MANAGER** | Runs the desk and the team | Everything above plus economics: morning read, weather/opportunities/landscape/GMROI/suppliers, commercial drill-downs, sync controls, approval deciding (thin price), policy read, identity read, attribution ledger |
| **OWNER** | Owns the business relationship with the platform | Everything, plus: user/role management, policy editing, AI provider keys, identity decisions, connections management, trust surface (disclosure, access log, export, erasure), 30-day evaluation, below-cost approvals |
| **api-client** | An ERP/CPQ or script holding an API key or owner token | Public resolution API, enquiries API, outcomes API, trust audit chain, credential sharing |
| **operator** | Whoever runs the deployment | Bootstrap, `sync_all`, queue worker, entitlements CLI, master-health CLI, health states |
| **system** | The platform itself | Scheduled sync, analysis phases, attribution detection, queue broker |

Plan gating is separate from role gating: the decisions/insight surfaces sit
behind the `intelligence` feature (trial or paid), enforced server-side at
router inclusion. The Quote Builder stays free forever. Attribution is the
deliberate exception — a lapsed org reads its ledger frozen at trial end.

---

## 2. Screen map

Every route the SPA answers, with the roles it is offered to. "mgmt" =
SALES_MANAGER + OWNER.

| Route | Screen | Offered to | Question it answers |
|---|---|---|---|
| *(signed out)* | Landing → SignIn / SignUp cards | visitor | The front door |
| *(gate)* | ForcedPasswordChange | any with an issued password | Change it before anything else |
| `#/` | Home: SetupChecklist + Daily (mgmt) + decision head + Storyboard + Waterfall | all | What needs deciding today, and why |
| `#/decisions` | Decision queue (list) | all (scoped) | Everything raised, open and closed |
| `#/decision/:id` | Decision detail + trace + action modal | all (visibility-checked) | One decision: act on it, trace it to the ERP record |
| `#/customers` | Account directory + journey + migration | all (scoped) | Pick an account |
| `#/account/:id` | One account: timeline, commercial (mgmt), open decisions | all | How this relationship is doing |
| `#/account/:id/item/:itemId` | Customer × item drill-down | mgmt (server 403s SALES) | One relationship's economics |
| `#/accounts` | redirect → `#/customers` | all | Saved-link redirect |
| `#/quotes` | **Quotes** (the workspace) | all | Every draft in the organization; start one, send a ready one |
| `#/quotes/:id` | **Quote Builder** | all | Build, price, and send one quote |
| `#/weather` | Commercial weather | mgmt | Which parts of the business need attention |
| `#/opportunities` | Opportunity radar | mgmt | Where money is on the table |
| `#/lost-revenue` | Lost revenue by cause | mgmt | What stopped, and why |
| `#/journey` | Customer journey + migration | all | Is the base growing or churning |
| `#/simulate` | Impact simulator | mgmt | What a price change would be worth |
| `#/landscape` | Margin/product quadrants | mgmt | What is large and below the floor |
| `#/composition` | Mix shift | all | Has the mix moved, towards whom |
| `#/cadence` | Buying rhythm | all | Who missed their own cycle |
| `#/payments` | Cash: projection, settlement, owner books, credit | all (inner panels gated) | How customers actually pay |
| `#/payables` | How we pay + vendor terms | mgmt | How long we take, what we agreed |
| `#/order-to-cash` | Cycle by stage | all (no money) | Where cycle time goes |
| `#/cash-cycle` | Cash conversion per entity | mgmt | How long a rupee is tied up |
| `#/statutory` | MSME watchlist, capture backlog, 194Q | mgmt | Deadlines the tax code sets |
| `#/stock` | The shelf (`?item=` focus) | all (cost columns mgmt) | What stock costs to keep |
| `#/gmroi` | Return on stock | mgmt | What each line returns |
| `#/supply` | Suppliers + open POs | mgmt | Who the book depends on |
| `#/bonds` | Relationship bonds + playback | all (supplier half mgmt) | Who is close, who is drifting |
| `#/mix` | Product mix grid | all | Which lines each customer takes |
| `#/dependency` | Both-ends dependency | all (supplier side mgmt) | What the book leans on |
| `#/targets` | Supplier target wall | mgmt | Where each principal's number stands |
| `#/item-lines` | Item-line placement queue | mgmt | Place items into lines |
| `#/negotiate` | Negotiation desk | all (cost figure mgmt) | What can I give, measured against the floor |
| `#/quote-outcomes` | Won & lost (+ pricing panel mgmt) | all (scoped) | Win/loss rates and reasons |
| `#/unanswered-quotes` | Unanswered quotes worklist | all (scoped) | Quotes with no recorded outcome |
| `#/what-pie-changed` | Attribution: value ledger (+ owner report) | mgmt (owner panels inside) | What the platform changed |
| `#/what-your-books-hold` | Retrospective look-back | mgmt | What the books already held |
| `#/data` | Data & connection | all (controls gated) | Real books or sample data; sync |
| `#/approvals` | Approval queue | all (role-projected) | Who signs off a thin price |
| `#/identity` | Cross-connector identities | mgmt (writes owner) | Which records are the same thing |
| `#/trust` | Your data | OWNER | Disclosure, access, export, erasure |
| `#/settings` | Settings | all (sections gate progressively) | Account, people, policy, AI |
| `#/states` | Reference: unknown-states | all | How the product behaves when it does not know |
| `*` | redirect → `#/` | all | Unknown paths land home |

Navigation is four groups — **Decide / Understand / The book / Setup** — every
item a real link (ctrl/middle-click work), with count badges only on open
decisions and pending approvals. `vizPath()` in `route.ts` is the single table
that turns server-named destinations (storyboard beats, weather drills, daily
tiles) into these routes; unknown names land on home.
---

## 3. Entry, session and account lifecycle

The session model behind every flow here: the credential is an httpOnly
`pie_session` cookie (an HMAC token naming user + org + a `UserSession` row);
localStorage holds only a profile cache with the token always blanked. Every
cookie-authenticated write must carry the `X-PIE-App` header (CSRF). Role is
re-resolved from the ACTIVE membership on **every** request, so a membership
ended mid-session kills the session. Sessions die at 12 h idle / 30 d absolute,
on revocation, and when minted before a password change. Sign-up, login and
demo all return one identical envelope, so the client has exactly one way to
become signed in.

### 3.1 Visitor on the landing page

**Trigger.** Any URL opened with no session (and no pending notice).
**Path.**
1. Landing page renders (hero, proof, plans). On mount the app asks whether
   the two optional doors exist — `GET /api/v1/signup`, `GET /api/v1/demo`;
   any failure reads as "not offered".
2. CTAs: *Sign in* → sign-in card. *Get started free* → sign-up card when
   offered, else falls back to sign-in. The free panel's trial button opens
   sign-up with that plan preselected; each paid panel jumps to the enquiry
   form at the end of the section with that plan chosen (`POST
   /api/v1/contact`, public, records an ask and grants nothing). *See it on
   sample data* → demo (only when configured).

**Branches.** Self-serve sign-up off (the default) → every "get started" CTA
opens sign-in and the sign-in card drops its "Create your organization" link ·
no demo configured → the demo CTA is absent · a deep link (e.g. a shared
`#/account/x`) is captured and restored after sign-in.
**Ends.** Sign-in card · sign-up card · demo session · leaves.

### 3.2 Sign-up → owner of a fresh trial org

**Trigger.** Sign-up card (only reachable when `GET /signup` said enabled).
**Path.**
1. Four fields: company, name, work email, password (≥10 chars); optional plan
   radio built from the server's ladder — the copy states every choice creates
   the same free account with a full trial.
2. `POST /api/v1/signup` → creates Organization + OWNER user + membership,
   starts the 30-day Commercial Intelligence trial, records `requested_plan`
   (granted by nothing — an operator decides via CLI), opens a session, sets
   the cookie, returns the login envelope (201).
3. Client signs in, lands on `#/` where the **setup checklist** waits
   (connect Zoho → pull history → set margin floors → add team).

**Branches.** Sign-up off → 404 ("not a door") · >5 sign-ups/hr per address →
429 · email exists anywhere on the platform → 400 "sign in instead" · weak
password / blank fields / unknown plan → 400 with the specific sentence · a
plan above the landing tier is recorded, never charged.
**Ends.** Signed in as OWNER on `#/` with the checklist · refused on the form ·
backs out to landing.

### 3.3 Sign in

**Trigger.** Sign-in card (from landing, from sign-up's footer link, or forced
here by session expiry with a notice).
**Path.**
1. Email + password → `POST /api/v1/auth/login`.
2. Server spends exactly one PBKDF2 verification whatever the input
   (anti-enumeration timing), resolves the landing workspace (home org, else
   oldest membership), opens a session, audits `LOGIN_SUCCEEDED`, sets the
   cookie. The envelope carries role, currency, timezone,
   `must_change_password`, `is_demo`, and the full organizations list.
3. Client navigates to the captured deep link if one exists, else home, with
   `replace` — Back never returns to the card.

**Branches.** 5+ consecutive failures on a real account → 429 with exponential
backoff (checked before the password) · unknown email / wrong password /
inactive account / **no active membership anywhere** → one uniform 401
"Incorrect email or password" · home membership ended but another exists →
lands in the oldest remaining workspace · `must_change_password` →
ForcedPasswordChange replaces the shell (§3.8) · stale hash → silently
rehashed at the current work factor.
**Ends.** Signed-in shell · forced password change · 401/429 held on the card ·
backs out.

### 3.4 Demo entry (read-only)

**Trigger.** Landing "See it on sample data" (rendered only when configured).
**Path.** One click, no form — `POST /api/v1/demo` opens an ordinary session
with `is_demo: true`. Every screen carries a permanent banner ("Nothing you do
is saved — the server refuses writes on this session"), and `current_principal`
refuses **every** non-GET request by method (403), including read-only POSTs
like the simulator — an accepted cost.
**Branches.** Unconfigured → 404, the client swallows it (button silently does
nothing) · >5 entries/hr per address → 429 · any write inside → 403 naming the
demo.
**Ends.** Read-only session · silent no-op · signs out to landing.

### 3.5 Session restore on page load

**Trigger.** Page load with a stored profile.
**Path.** The cached profile is schema-validated and the shell draws
immediately; one `GET /api/v1/auth/me` then confirms it against the cookie, so
a role change or revocation is noticed on load rather than at the first
failing request.
**Branches.** Cache missing/invalid → landing · `/auth/me` 401 → auth-loss flow
(§3.6); other failures are swallowed so being offline does not eject a working
shell · `must_change_password` → the gate.
**Ends.** Live shell · sign-in with expiry notice · landing.

### 3.6 Auth loss mid-session (any 401, anywhere)

**Trigger.** Any API request answers 401 (idle/absolute expiry, revocation,
password changed, membership ended).
**Path.** One transport-level handler fires before the error reaches any
screen: the current path is captured, the local session forgotten (deliberately
*without* posting logout — its own 401 would loop), and the sign-in card
renders with "Your session expired." Signing back in returns to the captured
path. Other tabs learn via the `storage` event and show "You signed out in
another tab."
**Ends.** Back where they were, re-authenticated · abandons at the card.

### 3.7 Cross-tab session sync

**Trigger.** `storage` event on the session key in a tab that did not write it.
**Path.** Cleared → forget + "You signed out in another tab." notice on the
sign-in card. Different user → adopt the new session and drop caches; the tab
re-renders as the new account (a "Signed in as {name} in another tab." notice
is recorded but has no renderer inside the signed-in shell, so nothing is
shown). Same user → no-op.
**Ends.** Tab signed out · tab re-rendered as the new account · no change.

### 3.8 Forced password change (issued passwords)

**Trigger.** Session has `must_change_password` (owner-created account, owner
reset, or seeded).
**Path.** A full-screen gate replaces the app; the server enforces it too —
every path 403s except the password change itself, logout(s), and `/auth/me`.
Current + new password → `POST /api/v1/admin/me/password` → flag cleared, all
sessions revoked, a fresh session returned so the change does not sign its own
user out → home.
**Branches.** Wrong current password → 403 · weak new password → 400 · "Sign
out" is the only other exit.
**Ends.** Fresh session on home · signed out · stuck on the gate with an error.

### 3.9 Account menu: sign out

**Trigger.** Avatar → "Sign out" (menu also shows name, workspace · role).
**Path.** Fire-and-forget `POST /api/v1/auth/logout` (revokes the server
session, audits, clears the cookie), then the local session is dropped and the
landing page renders — the local half happens even offline.
**Ends.** Landing, session revoked · landing, local-only (server unreachable).

### 3.10 Workspace switch (multi-org membership)

**Trigger.** Account menu "Switch workspace" — rendered only when the session's
organizations list holds more than one.
**Path.** `POST /api/v1/organizations/{id}/switch` → server verifies an ACTIVE
membership *inside the target tenant* (grants nothing), opens a **new** session
(the old one survives — a second tab keeps working), returns token + org +
role. The client replaces the whole envelope, clears every cached query of the
workspace being left, and lands home.
**Branches.** No membership or nonexistent org → one indistinguishable 404
("Organization not found"); the client swallows the refusal and stays exactly
where it is — the failure message lands in state that only the signed-out
sign-in card renders, so nothing visible appears · membership ended after the
switch → the next request 401s into §3.6.
**Ends.** Shell rebuilt around the new workspace · refused, unchanged.

### 3.11 Own account: password and sessions (`#/settings`, every role)

**Path.**
1. "Your account": change password (`POST /api/v1/admin/me/password`) — on
   success the returned fresh token is adopted so the user stays signed in.
2. "Where you are signed in" (`GET /api/v1/auth/sessions`): device, last used,
   "this device" chip. Per-row "Sign out" revokes one remote session
   (`DELETE /api/v1/auth/sessions/{id}`); "Sign out everywhere"
   (`POST /api/v1/auth/logout-all`) ends every session including this one and
   drops to the landing page.

**Branches.** Wrong current / weak new password → inline 403/400 · foreign
session id → uniform 404 · only one device → a one-row list marked "this
device" (the current session never gets a per-row "Sign out").
**Ends.** Password changed, still signed in · one device out · out everywhere.

### 3.12 Trial lifecycle and plan requests

**Trigger.** `TrialNotice` mounts on every screen for SALES_MANAGER and OWNER —
deliberately never for a salesperson (a worry with no lever attached).
**Path.** Reads `GET /api/v1/entitlements`. Silent until ≤10 days remain; info
until ≤3; then warning. After expiry the notice stays: the locked features are
named from the server's lists, and "quoting, margin floors and approvals carry
on — nothing was deleted." The owner-only button records a
`PlanChangeRequest` (`POST /api/v1/entitlements`) which **grants nothing** — an
operator decides via `python -m app.entitlements`; the button is then replaced
by "You asked to move to {plan} on {date}." Expiry is enforced server-side:
the decisions/insight surfaces 403 with a plan-shaped message; the quote desk
keeps working.
**Branches.** Manager sees the notice without the button · already licensed or
a request already open → 409 inline · unknown plan → 400 listing tiers ·
entitlements fetch fails → the banner is silently absent · a second org
connecting already-trialled books ends the trial early with a stated reason.
**Ends.** Absent · counting down · locked with a pending ask · request recorded.

### 3.13 First-run setup checklist (`#/` home)

**Trigger.** Home mounts `SetupChecklist` for every role, any plan.
**Path.** `GET /api/v1/onboarding` derives four steps from real rows on every
request — nothing is persisted and there is no dismiss: **connect** (required;
detail carries connection health), **pull history** (required; done only when a
finished pull actually read rows — a 0-row "success" is not a pass; a running
pull shows phase and windows read), **margin floors** (recommended; defaults
never count), **team** (recommended). Owners get an "Open" button per step
(→ `#/data` or `#/settings`).
**Branches.** Non-owner sees the panel with an explanation and no buttons ·
both required steps done → the panel removes itself even while recommended
steps stay open · fetch failure → silently absent.
**Ends.** Gone (setup complete) · guiding the owner · read-only explanation.
---

## 4. Data: connections, sync, ingestion (`#/data`)

The multi-entity model, stated once: the legal entities (SLS / 4U / UPS) are
**connections on one organization** — their rows pool into one analysis, and
the screen says so ("to keep legal entities apart, give each its own
organization"). One Zoho credential usually reaches all three companies, so a
token rotation from any card rotates the shared grant and the response names
the other companies it changed.

### 4.1 Reading the Data screen (any role)

**Trigger.** `#/data` — the nav item is unconditional; the screen answers "is
this the company's real books, or sample data?" in words.
**Path.** `GET /api/v1/data/status` renders the connection headline
(SAMPLE_DATA / NOT_CONFIGURED / ERROR / UNREACHABLE / WRONG_ORG with the
visible orgs / CONNECTED), auto-sync cadence, the last run's stage-by-stage
counters (each zero carries a tooltip naming the Zoho scope it usually means),
per-company coverage, and the read-model totals the analysis runs on.
**Branches.** SALESPERSON: the connections list 403s into an inline error, sync
controls are replaced by "Syncing is a manager or owner action", and money
fields in skip data are omitted server-side (a skipped bill line's value is a
purchase total) · no connections + owner → "Add one below"; non-owner → "Ask an
owner" · status fetch fails → error state (managers can re-fetch via the
"Refresh status" button; the error itself carries no retry control, so a
salesperson has only a reload).
**Ends.** Informed reader · degraded read-only view · error state.

### 4.2 Connect Zoho via OAuth (owner)

**Path.**
1. Add a company → Zoho tab → "Sign in with Zoho" (offered only when the
   deployment has a Zoho app registered). Pick the data centre (in/com/eu/
   com.au/jp — "a grant is not portable between them").
2. `GET /api/v1/connections/zoho/authorize?dc=…` records a single-use hashed
   state (10-min TTL) and returns the authorization URL; full-page redirect
   (deliberately not a popup).
3. User consents at Zoho; Zoho redirects to the **public** callback
   (`GET /api/v1/connections/zoho/callback`), which consumes the state exactly
   once, exchanges the code, stores/rotates the encrypted credential, mints a
   one-time handoff, and 303-redirects to `/#/data?oauth=ok&handoff=…` —
   failures also redirect (`oauth=error&reason=…`), never a JSON page.
4. Back on `#/data` the query is stripped from the URL immediately (a reload
   must not re-spend the handoff) and claimed —
   `GET /api/v1/connections/zoho/pending/{handoff}` (owner of the state's org
   only) → the flow rejoins the reuse path: list the companies the grant
   reaches, pick one, `POST /api/v1/connections`.
5. The server checks the new connection immediately (ping + per-scope probe)
   and the card appears with its health chip.

**Branches.** Unknown DC → 400 · no app registered → 503 (API-only; the tab is
not shown) · any callback failure (error, replayed state, no refresh token) →
one indistinguishable error redirect · handoff invalid/expired/foreign → 403 ·
plan gate on a second company → 403 inline.
**Ends.** Connected and checked · error reason on the add panel · abandoned at
Zoho's consent screen.

### 4.3 Connect Zoho manually (Self Client)

**Path.** Data centre + client id + secret + refresh token (password fields,
"never shown again") + Zoho org id + human label; the Access panel lists every
scope with what it buys and two copyable scope strings (full and minimum).
`POST /api/v1/connections` creates, reuses or rotates the credential, connects,
checks; the card appears. Three outcomes on the credential, keyed on the app
rather than the secret: identical secrets **attach** to the row on file; a
different secret for a client id and data centre this organization already owns
a grant for **rotates** that row (Zoho re-issues a refresh token every time a
Self Client grant is generated, so this is what reconnecting looks like);
anything else **creates** one. A grant shared *by another organization* is
never rotated from here — sharing grants use, not the right to change the key.
**Branches.** Missing secrets → 400 naming them · unsafe base URL → 400 (SSRF
guard) · plan refuses a second company → 403 · once one credential exists the
form auto-switches to "use a sign-in already on file" · base-currency mismatch
warns but does not flip the check to failed (its documents are refused at sync
time instead).
**Ends.** Card ok · card added but check failed with the named cause · refused.

### 4.4 Connect a second company through an existing grant

**Path.** Pick the credential → "Show the companies this reaches"
(`GET /api/v1/data/credentials/{id}/organizations`, live from Zoho;
already-connected ones disabled) → pick → `POST /api/v1/connections` with no
secret re-entered.
The picker offers only the sign-ins for the system being added, and the
sign-ins this organization owns that reach no company are listed below it with
a **Remove** each (`DELETE /api/v1/data/credentials/{id}`, confirm dialog).
Removing a company deliberately leaves its sign-in on file — so reconnecting
does not mean re-entering a secret — and this is the way out of that retention
rather than a reversal of it.
**Branches.** Zoho rejects the credential → 502 inline · grant sees no
companies → stated · remove a sign-in something still connects through → 409
naming the count · remove one another organization owns → 403 · cancel on
remove → nothing.
**Ends.** Second company connected sharing the grant · sign-in removed · refused
· abandoned.

### 4.5 Connect a US-market ERP (registry connector)

**Path.** The catalog (`GET /api/v1/connections/catalog`) offers the registered
connectors — NetSuite, Dynamics 365 Business Central, Acumatica, Epicor
Prophet 21, Sage X3, Sage 100 — and the form renders entirely from each spec's
field lists, nothing hardcoded per system. Dynamics 365 alone offers discovery
(`POST /api/v1/connections/erp/discover` lists the companies a candidate
credential can see before anything is stored). `POST /api/v1/connections/erp`
validates values against the spec, applies the same plan gate, and checks
(ping only — ERP permission gaps surface later as `SCOPE_NOT_GRANTED` skips at
sync time).
**Branches.** Discover on a non-discovering connector → 400 · unsafe URL → 400
· ERP errors → 502 · unknown connector → 404 · catalog fetch fails → the panel
degrades to a Zoho-only form.
**Ends.** Connected and reachable · added but check failed (fix via rotate) ·
refused.

### 4.6 Manage a connection card (owner)

**Path.** Per card: **Check** (`POST …/check` — live ping + scope probe; lists
only the scope gaps, each labelled required/optional/untested), **Rename**,
**Pause/Resume** (stops the company feeding analysis and being pulled; synced
rows and history stay), **Remove** (confirm dialog; credentials dropped,
already-synced rows deliberately stay and keep feeding totals).
**Branches.** Fixture deployment → check records "nothing was contacted" ·
ping ok but probe threw → reported as *untested grant*, not a failed connection
· authenticated but the org id is not visible to that login → named check
failure · cancel on remove → nothing.
**Ends.** Checked · renamed · paused/resumed · removed with data kept · 404 for
a foreign connection.

### 4.7 Credential rotation (expiry / revocation)

**Trigger.** A revoked refresh token surfaces as "Check failed" or a FAILED run
naming an auth error; the owner opens "Replace the token" (closed by default).
**Path.** Paste a fresh refresh token for the *same* client (an escape hatch
opens client id+secret too — Zoho reports a foreign-app token as
`invalid_client_secret`, which misleads people toward the data centre). The
warning is stated before the click: rotation changes the sign-in for **every**
company on the grant. `POST /api/v1/connections/{id}/rotate` rotates, re-checks
immediately, and names every other company that changed underneath. Non-Zoho
connectors rotate via `…/rotate-erp` with the connector's full field list —
"a half-replaced credential is how a working connection gets broken."
**Branches.** Wrong endpoint for the connector type → 400 pointing at the right
one · no stored grant → 409 · blank token → 400 · foreign credential → 403 ·
half-filled client pair → the button stays disabled.
**Ends.** Rotated + re-check ok · rotated but re-check failed (named) · refused.

### 4.8 Run a sync and watch it (manager/owner)

**Path.**
1. Per-company ("Pull from this company", with a since-date whose coverage note
   says *before the click* whether it is a cheap incremental or a priced
   backfill, plus a "re-read documents already held" checkbox) or org-wide
   ("Sync every company"). `POST /api/v1/data/sync` → 202 with `(run, started)`
   — `started=false` means an overlapping job was handed back, never an error.
2. The screen renders from persisted state, polling `GET /api/v1/data/sync`
   every 2.5 s while active: phase, ticking elapsed, and a months-read progress
   bar with an explicit caveat that it measures calendar coverage, not time
   remaining.
3. Server phases (committed at every boundary so the poller sees movement):
   connect → per-company catalogue read (once per day per connection) →
   reference pass → per monthly window "Read {Mon YYYY}" → supply pass
   (payments, POs, stock) → org-wide analysis: signals → customer×item metrics
   → business state → decisions → attribution. The first four analysis phases
   are SAVEPOINT-isolated — a failure costs that phase only and lands on the
   unresolved list as `ANALYSIS_PHASE_FAILED`; attribution is best-effort too,
   but its failure is recorded in the run's notes rather than on the
   unresolved list.
4. Managers also get the **run log** ("What this sync did", live-followed via
   cursor, problems-only filter, downloadable `log.txt`).
5. On finish the card shows the outcome and the stage counters; if rows could
   not be resolved, a worklist grouped by *what is missing* (ranked by lines
   held up, with "what to do") renders above the skipped-rows panel.

**Branches.** Schema behind → 503 naming the columns and the alembic fix,
before any insert · double-start → the existing job handed back (in-process
lock + partial unique indexes; a second click is absorbed) · process dies →
the next status read reaps the run to FAILED after a 10-min cold heartbeat,
with "anything written was kept; running it again carries on" · exception
after rows were written → **PARTIAL**, resumable, amber; FAILED only when
nothing was written; both offer "Try again" · optional scope refused → not
fatal: a `SCOPE_NOT_GRANTED` skip naming scope, endpoint and fix · an unusable
row (missing item, foreign currency) → a `SyncSkip` with a reason code, never
silently dropped · fixture source → "Finished cleanly — sample source, not
your books."
**Ends.** OK · OK-against-sample · PARTIAL (resumable) · FAILED (retry offered)
· reaped-stale FAILED · absorbed into the running job.

### 4.9 Skipped rows: drill in and export

**Trigger.** Last run has `skipped_count > 0`.
**Path.** Managers fetch the full list (`GET /api/v1/data/sync-runs/{id}/skipped`
— document, party, item, reason code + detail, qty, line value) into a
filterable grid whose header states coverage plainly ("all 1,304" vs "20 of
1,304" must never look the same); "Export to CSV" downloads the server-built
file (whole list, BOM for Excel, incompleteness note written into the file).
**Branches.** Non-manager → only the 20-row sample, no export, money reads "—"
· full fetch fails → falls back to the sample *with a visible error* · a run
predating the skip table → "recorded a count but not the rows; re-sync" ·
foreign run id → uniform 404.
**Ends.** CSV downloaded · sample-only view · 404.

### 4.10 Automatic sync

**Configure (manager/owner).** The "Automatic sync" select on `#/data`: off /
every 1–24 h → `PUT /api/v1/data/auto-sync` (0–168 accepted); helper shows the
next run time and coverage. On a fixture deployment the control is replaced by
an explanation. Out-of-range → 400.

**Scheduled run (system).** A 60-second ticker in every API process; only the
holder of the `sync-scheduler` database lease acts. Due orgs are queued through
the **exact same path a click uses** — same double-start guard, same run row,
same screens watching it. `since` is always the earliest window the org ever
covered (Zoho filters by document date; a bill dated the 3rd but entered the
11th would fall outside a narrower window) — cheap because the resume cursor
skips unchanged documents. Dispatch is a daemon thread, or a durable queue
message when `SYNC_DISPATCH=queue`.
**Ends.** Sync queued (visible on `#/data` like any manual run) · nothing due ·
tick skipped (not the lease holder / fixture).

### 4.11 Nightly full-estate pull (operator): `python -m app.sync_all`

**Path.** Finds every org with an enabled connection; per org, fans out one
pull per connection in parallel (pulls are independent; Zoho meters per
company), joins them, then runs the org-wide analysis **once** on the union —
per-connection analysis would analyse an incomplete book. Flags:
`--organization` (repeatable), `--since`, `--reconcile` (lists the whole book
to notice deletions/voids — weekly), `--full` (re-fetch everything — hours),
`--json`.
**Ends.** Exit 0 all clean · exit 1 any failure/PARTIAL (a rate-limited night
must not look clean) · exit 2 nothing to sync.

### 4.12 Queue worker (operator): `python -m app.worker`

**Path.** A dedicated container drains the durable queue (`sync.run`,
`commercial.recompute` topics) so hour-long pulls do not run inside a process
answering requests. Claims are conditional updates (two workers cannot run one
message); heartbeats while running; exponential backoff to DEAD_LETTER; a
dead worker's claim is reaped back to PENDING. Visibility for a manager asking
"why has nothing synced": `GET /api/v1/internal/queue` (mode, lease holder,
worker liveness, depth, dead letters); an owner can requeue a dead letter
(`POST /api/v1/internal/queue/{id}/retry`; 409 while the message is still
PENDING or CLAIMED — only a terminal message can be requeued).
**Ends.** Draining · refuses to start loudly (exit 2) when config declines it ·
stops clean on SIGTERM.

### 4.13 Master-health CLI (operator/analyst)

`python -m app.master_health ITEMS.csv --profile zoho` — deliberately not a
screen, not an upload endpoint, not a connector: a pure function of (export
bytes, column profile, catalogue) so it can run on a prospect's item-master
export before any OAuth exists. Reports how much of the master the product
intelligence can reach, value-weighted at **selling** price (the profile has no
cost column, so a cost cannot appear even by accident). `--list-profiles`,
`--json`, `--out`, `--policy`.
**Ends.** Report emitted · usage error (exit 2) · profile/source error.
---

## 5. The daily decision loop

### 5.1 Home: morning read, queue head, storyboard (`#/`)

**Trigger.** Sign-in lands here; the shell loads the decision list on session
start (`GET /api/v1/decisions?include_detail=true`) and polls the pending
approvals count for the nav badge.
**Path.**
1. Role-specific title, demo banner, trial notice, and the setup checklist
   above everything until required setup is done.
2. **Daily** (manager/owner only — half of it is what we owe suppliers):
   freshness banner (stale sync → warning with a Sync link to `#/data`), then
   tile bands — *Needs you / At risk / Committed* (weeks control) / *What
   moved* (date presets) — each tile with count, amount, per-company chips and
   a "Work through these →" link (`GET /api/v1/insight/daily`).
3. Decision head: "N open decisions · x high · y medium", top-5 cards, "See
   all →" to `#/decisions`.
4. **Storyboard** (`GET /api/v1/insight/storyboard`): month window, hero
   net-change figure, ordered beats (HIGH→GOOD) each with What changed / Why
   (including `UNEXPLAINED` = "No cause the data can name") / evidence chips /
   a Next button; then the revenue **Waterfall**
   (`GET /api/v1/insight/revenue-flow`) whose buckets drill to `#/account/:id`.
5. Every beat/tile/front names a destination the client resolves through
   `vizPath()` — unknown names land on home.

**Branches.** Decision list fails (non-auth) → `LoadFailed` replaces the whole
routed area ("a loading failure, not an empty queue") with Retry · any 401 →
auth-loss (§3.6) · SALESPERSON → Daily omitted entirely (not rendered-and-403'd)
and margin-based beats are absent from the storyboard response, flagged
`restricted_withheld` · daily fetch error → in-place alert, rest of home still
renders · zero open decisions → "Nothing waiting" (a statement about evidence,
not "all clear") · a beat carrying a `simulate` action adds "Model it" →
`#/simulate?scenario=…`.
**Ends.** Navigates onward via a beat/tile/drill · reads and stays · LoadFailed
· empty queue with storyboard.

### 5.2 Decision queue → detail → act / dismiss / escalate / undo

**Trigger.** `#/decisions` (nav, home "See all", or a daily tile). A
salesperson's queue is narrowed server-side to own-assigned decisions with
RESTRICTED types excluded.
**Path.**
1. Filter chips derived from the decision types actually present + company
   filter (only with ≥2 companies); grid sorted by priority: band, type,
   subject, Why (rationale or AI explanation), worth/confidence, status.
2. Row click → `#/decision/:id`; if the decision is OPEN a `VIEW` action fires
   once per open period (`POST /api/v1/decisions/{decision_id}/action`).
3. Detail: back button, type + priority + status; STATE decisions show
   impact / why / **trace** / actions / ranking panels and a "no model was
   involved" note; signal decisions show the facts table (or "No numeric facts
   are exposed at your permission level"), evidence, interpretation and the
   priority breakdown (base + bounded AI adjustment).
4. While OPEN/VIEWED the action panel offers: **Accept the recommendation**
   (only when the AI state is ok), **Do something different**, **Dismiss with
   reason**, **Escalate to management**, and "Open the account →".
5. The action modal requires a note for modify and dismiss; "Log decision" →
   `POST /api/v1/decisions/{decision_id}/action {ACT|OVERRIDE|DISMISS|ESCALATE}`.
6. A snackbar offers **Undo for 9 seconds** → `REOPEN` (itself audited).

**Branches.** Filters match nothing → empty state with "clear a chip" · id not
in the loaded view → "may have been closed, or belong to somebody else's
queue" · server-side detail build failed → a distinct "detail could not be
loaded" state (the decision exists and is yours) · decision closed → the
action panel becomes the outcome card · **escalate** with
`escalation_creates_approval` on → an approval request of kind
DECISION_ESCALATION is raised, the decision parks ESCALATED, and the approvals
badge increments · POST fails → snackbar, modal stays.
**Ends.** ACTIONED · OVERRIDDEN (reason) · DISMISSED (reason) · ESCALATED into
the approvals queue · undone back to OPEN · cancelled · not-found states.

### 5.3 Trace a state decision to its ERP source

**Trigger.** "Trace it to the source →" on a STATE-origin decision's detail.
**Path.** `GET /api/v1/decisions/{decision_id}/trace` walks the chain: business state
(with its thresholds version) → newest-first state transitions with field
changes → the bottom-of-chain ERP record (type + id + line), "event no longer
held", or "counted from the item list" for stock readings. "Show N more"
appends pages of 40.
**Branches.** Signal-origin decision → "raised from a signal … no state to
drill into" · fetch failure → inline error · navigating to another decision
resets the panel.
**Ends.** Chain read to the ERP record · unavailable explanation · closed.

### 5.4 Customers → one account → customer × item drill-down

**Trigger.** `#/customers` (nav), "Open the account →" from a decision, or any
drill from the analysis screens; `#/accounts` (legacy) redirects here.
**Path.**
1. Directory: search, active/inactive/all, sort by name/last order/12-month
   value, company filter; grid rows show last order ("never ordered"), orders,
   value, open-decision count, "Covered by" owner. Below, the whole-book
   journey + migration matrix.
2. Row click → `#/account/:id` (a URL move — Back returns to the directory):
   health timeline (revenue/orders/margin/days-to-pay on one axis — margin and
   payment rows are *absent*, not blank, when the server omits them), then
   (mgmt) the commercial portfolio — KPI band, cost-coverage notes ("missing
   data, not a zero margin"), items grid with margins, peer medians, movement,
   reasons — then this account's open decisions.
3. Item row click → `#/account/:id/item/:itemId`
   (`GET /api/v1/commercial/customers/{id}/items/{productId}`,
   manager/owner): headline KPIs, deterministic diagnosis prose, per-transaction
   series, margin periods, peer table, volume-vs-margin, full transaction
   evidence — recomputed live and stamped with its thresholds version.
4. A peer row hops to the same item on another account.

**Branches.** Directory fetch fails → error state · zero rows → context-named
empty text (no search match / none inactive / "No accounts are assigned to you
yet") · SALESPERSON on an account → the commercial block is not rendered and
the endpoint would 403; the timeline still loads without the margin row · no
transactions for that item → 404 → error state with Back · no open decisions →
"Nothing is flagged on this account right now."
**Ends.** Diagnosis read · peer hop · back to directory · empty/error states.

---

## 6. The analysis screens (Understand / The book)

All of these share one skeleton, stated here once. Each screen loads through a
single `Panel` with exactly four states — loading skeleton, error ("This did
not load" + Retry), **empty with the server's own reason verbatim** (the
client never guesses why something is empty), and ready. Screens listing rows
from several companies get the client-side **CompanyFilter** (hides rows,
never restates a server total; renders only with ≥2 companies); the two
screens whose aggregate *is* the screen (Mix, Dependency) use server-side
**CompanyScope** and refetch. "Unavailable" lists name what the server cannot
show and why. Customer-shaped rows drill to `#/account/:id`; item-shaped dots
drill to `#/stock?item=…` because there is deliberately no product screen —
item rows elsewhere render non-clickable rather than dead. Cost-bearing
screens are manager/owner; on shared screens the salesperson's *payload* omits
the cost fields.

| Screen | Who | The path through it | Writes |
|---|---|---|---|
| **Weather** `#/weather` | mgmt | Month window → fronts (colour band + band word, never colour alone) → "Look into it" drills to home/opportunities/journey/data | — |
| **Opportunities** `#/opportunities` | mgmt | Well-evidenced money headlined (weak evidence excluded, stated); ranked bars (length = money, opacity = evidence) → row click opens the account | — |
| **Lost revenue** `#/lost-revenue` | mgmt | Total lost vs prior period → causes (STOPPED_BUYING / COST_INCREASE_NOT_PASSED / MARGIN_EROSION / UNEXPLAINED — hatched, "a gap, not guessed at") → up to 5 customer links per cause | — |
| **Journey** `#/journey` | all | Monthly NEW/RECOVERED/GROWN/STABLE/SHRUNK/LOST bands → band click opens the largest movers grid → customer links; "gone quiet" dormant list below | — |
| **Simulator** `#/simulate` | mgmt | Scenario radios (price change / lift to a margin floor; `?scenario=` validated against the server list, unknown falls back) → sliders → auto-runs `POST /insight/simulate` → break-even first ("not recoverable" when no volume compensates), Today vs At-your-assumption; blocked scenarios listed with reasons | nothing persisted |
| **Landscape** `#/landscape` | mgmt | Subject seg (customer×item / item) × vertical (margin / growth) → quadrant scatter with a fenced "no margin on record" rail → quadrant focus mode (axis rescales, warned; table becomes a worklist) → dot drills to account or `stock?item=` | — |
| **Mix shift** `#/composition` | all | By customer/item × revenue/orders × months → "Biggest shift" in points → small multiples (top six + folded other, footnoted) → customer names link; item names deliberately do not | — |
| **Rhythm** `#/cadence` | all | Day-of-month wheel + "past their own cycle" grid ("usually every Nd" / "only N orders — no rhythm yet"; Overdue chip only when true) → row opens account | — |
| **Cash** `#/payments` | all | Stacked panels, each independently gated and failing alone: CashJourney (mgmt; committed cash by ISO week) · SelfFunding (owner; verdict COVERED/UNDETERMINED/NOT_GROWING/UNKNOWN, blocked until each entity's figure is confirmed in Settings) · Settlement (all; median days, late share with denominator, slowest payers → account) · OwnerBooks (per-salesperson books; unknown sorts last, never as zero) · Credit grid (owed/overdue/limit/status; "no limit recorded" is never zero) | mgmt: set/clear credit limits, assign/unassign account owners (`PUT/DELETE /insight/credit-limits`, `/account-owners`) |
| **How we pay** `#/payables` | mgmt | Same settlement furniture from the payable side (supplier rows are facts, not doors) + vendor terms grid: "Zoho says" vs "Agreed term" with schedule shift; ERP value never overwritten | record/clear agreed terms (`PUT/DELETE /insight/vendor-terms`) |
| **Order to cash** `#/order-to-cash` | all (no money) | Stage tiles (ours vs theirs), medians and slow tenth, unmeasured counted by reason, "counted backwards" data-entry findings; measurement rule stated above the grid; deliberately no drills | — |
| **Cash cycle** `#/cash-cycle` | mgmt | One DIO/DSO/DPO decomposition per legal entity, never pooled (three balance sheets); unstated months are hatched gaps with reasons | — |
| **Statutory** `#/statutory` | mgmt | MSME watchlist (deadline + basis + urgency + amount at risk) · capture backlog (suppliers worth establishing, ranked) · 194Q crossings — **silent until the org's turnover gate is confirmed in Settings** (the refusal renders; an empty table would read as "nobody crossed") | none on-screen (`PUT /insight/msme-status` is API-only) |
| **Stock** `#/stock` | all (cost columns mgmt) | KPI cards double as filters + server-defined "narrow to" chips → grid ranked by monthly holding cost (age, cover, state, action, who buys it); `?item=` focus banner from Landscape ("Showing one item", stale ids explained); operational groups below with `empty_means` rendered | — |
| **Return on stock** `#/gmroi` | mgmt | Window sentence leads (stock history starts at first snapshot) → multiples (2.40×, never a percent) by item/brand; a missing figure carries one of four distinct reason chips — a measured zero is a finding, missing evidence is a job | — |
| **Suppliers** `#/supply` | mgmt | Spend concentration (tail never folded — every supplier has a phone number), evidence-gated footnotes (lead times, credit-note rates) → "Open orders, oldest first" chase list with ageing highlights | — |
| **Bonds** `#/bonds` | all (supplier half mgmt) | 0–100 bond strip (dot size = money, colour = movement, amber ring = unmeasurable) with month playback and per-frame story → dot/row opens the five-facet breakdown → "Open the account"; ledger grid below; unscored relationships counted, never positioned | — |
| **Product mix** `#/mix` | all | Company **scope** (server refetch) → BUYS/LAPSED/NEVER heatmap (lapsed strongest) → gap selector = call list of customers *not* buying a line, with affinity shown beside it (blanks never coloured as opportunity); coverage caveats when attribution is partial | — |
| **Dependency** `#/dependency` | all (supplier side mgmt) | Company scope → revenue-share vs receivables-share top-fives → principal rows draw spend and downstream revenue on one track with target pace → customer rows open accounts; "Targets" button opens the editor | targets via editor |
| **Supplier targets** `#/targets` | mgmt | Bullet chart per principal (done vs pace marker), scheme value secured + next rung, "still winnable" (sum of uplifts, never earned rebates); behind = amber + words → add/edit targets and rebate slabs (`PUT/DELETE /insight/targets`) | targets |
| **Item lines** `#/item-lines` | mgmt | "Needs placing" queue ordered by the revenue it carries → inline select places an item into a line (`PUT /insight/catalogue/{id}`; override survives re-sync; "reset" returns to automatic sources); source chips (OVERRIDE/ZOHO/HSN/VENDOR/NONE) | placements |
| **Negotiate** `#/negotiate` | all (cost figure mgmt) | Closed-set pickers (account → that account's items → status/tool family) + quantity, agreed price, and the give levers (customer discount, vendor concession, third-party payment — disabled with an explanation on government/PSU/defence accounts — toolkit spend at half, payment timing, target contribution) → "Work it out" → `POST /insight/negotiate`: floor per unit, contribution above floor, CAF after gives, earned-if-paid-at-timing, "free to give", hold-price; warnings; nothing persisted. `negotiable:false` prints the server's reason as the headline | nothing persisted |

Every write in this table follows one idiom: commit on blur/Enter, failure as
an alert line with the value unchanged, refetch after success, and **clear is
distinct from zero** everywhere ("none recorded" ≠ ₹0).
---

## 7. The quoting loop (`#/quotes`)

The Quote Builder is the negotiation desk: paste an RFQ, resolve every line to
a quote-ready product, price it against the customer's own history, send the
estimate into their books, and record what happened. Every quote is a row in
`quote_drafts` (`app/quote_workspace.py`): the lines go back to the row on
every mutation before the response is answered, so the same draft is open on
whichever desk opens it, a server restart forgets nothing, and there is no
Save button and no copy in the browser. Everything else durable — snapshots,
sent documents, outcomes, confirmed identity mappings, approval requests —
is its own row keyed on the quote id, and the sent state is re-joined from
those rows on every read (a sent quote never looks unsent).

Quotes are numbered `QB-0001`, `QB-0002`, … per organization, from a
sequence the row carries and a unique constraint guards; a removed draft
does not give its number back.

Quote lifecycle: `DRAFT → SENT → WON | LOST` — DRAFT may also go straight to
LOST; WON and LOST are terminal ("a margin analysis has already counted it").

### 7.1 The workspace, and starting a quote

**Path.**
1. `#/quotes` is the workspace: `GET /api/v1/quotes` lists every draft in
   the organization — number, customer (or "No customer yet"), line count,
   selling total, who started it and who last changed it — with a status
   chip computed on the server by the same functions the send runs
   (`quote_workspace.readiness`): Empty · Needs attention · Needs a customer ·
   Needs approval · Awaiting approval · Ready to send · Sent. Filters group
   those into "Needs work", "Awaiting approval", "Ready to send", "Sent".
2. "New quote" → `POST /api/v1/quotes` with **no customer** creates the draft
   (the company is decided here, once; an organization reading several
   books is asked which) and opens it at `#/quotes/:id`. The header shows the
   number, a **Choose customer** control while none is chosen, and a
   "Saved hh:mm" chip that says when the server last wrote the row.
3. The customer is chosen when the desk knows — before or after the RFQ is
   pasted — through the **customer picker** (server-side debounced search of
   the directory, `GET /api/v1/accounts?q=…`) → `PUT /api/v1/quotes/{id}/customer`
   carrying both name and customer id (identically-named customers in
   different books stay apart). Lines already on the quote are resolved again
   under that customer's identity scope; a price the desk typed is kept where
   the same product came back, and the response's `note` says how many.
   The same control changes the customer later, in place.

**Branches.** A draft "Ready to send" can be sent from the list ("Send")
without opening it — the same endpoint as the builder's button · "Remove"
on an unsent draft asks first; a sent quote refuses removal (409, it is a
record) · empty directory in the picker → a second probe distinguishes
*nothing synced* (managers get a "Data & connection" button) from
*everything inactive*; a salesperson's empty reads "No accounts are assigned
to you yet" · accounts fetch fails → error state with retry · a quote with
lines and no customer shows an info alert beside the grid, and the send
refuses with "Choose a customer before sending" · a draft removed underneath
an open builder → "This quote could not be opened", back to the list · any
guarded action failing → the server's sentence as a snackbar.
**Ends.** Draft open with the empty grid · customer chosen · sent from the
list · removed.

### 7.2 Paste an RFQ: intake, resolution, enrichment

**Trigger.** "Paste RFQ" opens the intake modal.
**Path.**
1. Paste the text; optionally answer "How did this reach you?"
   (EMAIL/WHATSAPP/PDF/PORTAL/PHONE_NOTE — no "Other"; unset by default). With
   a channel the verbatim wording is kept for measuring the resolver; without
   it only resolved lines are stored.
2. `POST /api/v1/quotes/{id}/intake`: the org's AI provider reads the text
   into rows; **any** failure falls through to the regex splitter, so the
   worst case is the pre-AI behaviour. If a model was called at all, an
   `AI_CALL` audit row is appended either way. The splitter tries quantity
   patterns strongest-first and caps a bare trailing number at 4 digits so
   "DNMG 150608" is not read as qty 150608; a line whose quantity could not be
   read is flagged **proposed** ("assumed 1") rather than silently defaulted.
3. Each line resolves through pie-parser with the org's equivalence bands,
   confirmed code mappings, and the customer's cross-connector identity scope:
   AUTHORITATIVE → EXACT auto-selected · the engine's own single-candidate
   NEEDS_REVIEW → AMBIGUOUS with a confirmable `identityCandidate` ·
   AMBIGUOUS/CONFLICT → abstain with options · ranked suggestions → top becomes
   the supply with rel from the org's score bands (TECH/COMPAT/POSSIBLE) ·
   non-discriminating scores → abstain ("pick the intended product") · nothing
   → UNRESOLVED · engine failure on one line → PIE_DOWN, never failing the
   quote.
4. Lines are enriched from the quote's bound books (description, stock, list
   price, cost, tax); in-books lines auto-price at list, marked "list" until a
   person types a number. When a channel was stated, the verbatim text is
   captured into the enquiry corpus (`source_ref=quote:{id}`); a capture
   refusal never fails the intake.

**Branches.** Empty text → 400 · live books but the customer matches nothing /
credential dead → a **refusing adapter**: every line reads BOOKS OFFLINE and a
later send is refused with that sentence — never a fallback to a different
book · a registry-ERP book → catalogue refuses live price/stock but the quote
can still be sent through its writer.
**Ends.** Lines land in the grid with per-line statuses (READY · READY-SUBST ·
NO PRICE · NOT IN BOOKS · BOOKS OFFLINE · UNRESOLVED · AMBIGUOUS · PIE OFFLINE
· CONFIRM READING) · 400 · modal cancelled.

### 7.3 Review lines: filters, confirm readings, create items

**Path.**
1. Filter chips with server counts: All · Needs attention · Potential
   procurement · Missing Zoho item · Manual review · Unresolved · Substituted ·
   Commercial exceptions. Search (`/` focuses). Managers additionally get a
   **Below margin floor** chip and warning — the count is *omitted from a
   salesperson's response server-side*, not zeroed.
2. A proposed line shows CONFIRM READING, the customer's verbatim words in
   italics beside the interpretation, and an **Accept** button — one line at a
   time, deliberately no confirm-all (`POST …/confirm-reading`; any role may
   confirm).
3. Row delete removes a line (no undo). "+ Create in Zoho" creates a missing
   supply product in the books (`POST …/create-item`); failure leaves the line
   CREATE FAILED with the server's reason.

**Branches.** No lines → "Paste an RFQ to start" · filter matches nothing →
reset button · viewport <700 px → cards replace the grid (always-open price
field, tap opens the drawer) · create-item without a supply product → 400.
**Ends.** Line confirmed · deleted · item created (auto-priced at list) ·
CREATE FAILED held on the line.

### 7.4 Supply drawer: candidates, substitution, the identity gate

**Trigger.** Row click / Enter opens the drawer for one line.
**Path.**
1. Header: requested item + qty; if substituted, "requested product remains
   visible" plus a **Revert** button. Pricing card: managers see current /
   recommended / cost with deltas; a salesperson sees the current rate only.
2. Candidate list: code, rel chip, match %, grade · brand, engine reason.
   **Select** → `POST …/supply`: the server first runs the identity gate, then
   updates rel/sel (exact → EXACT/AUTO · picked candidate → its rel, USER ·
   manual code → MANUAL, flagged for review), re-enriches from the books, and
   a move to a *different* product resets the price to that product's list
   rate (a USER price survives re-reading the same product).
3. **The identity confirmation gate** (`confirm_proposed_identity` — one
   function shared with the public API): a durable `ConfirmedCodeMapping` is
   written **only** when the line has a linked customer identity scope, the
   engine itself proposed exactly one record via NEEDS_REVIEW, and the
   selected code *is* that proposal. All three refusals return silently —
   picking any other candidate is a substitution on one quote and stays one;
   scored equivalence suggestions are never confirmable (equivalence is
   policy, not identity, and must not compose).
4. A recorded confirmation flashes: "Recorded: this customer's ⟨code⟩ means
   ⟨record⟩. It will resolve on its own from now on."

**Branches.** No candidates → "the PIE engine could not resolve this line"
with the engine's notes · PIE_DOWN/AMBIGUOUS cells read "awaiting PIE" /
"select product" · Revert → back to the requested product, EXACT/AUTO.
**Ends.** Supply set (possibly substituted) · identity mapping recorded
(org-wide, durable) · reverted · drawer closed unchanged.

### 7.5 Price lines and discount

**Path.**
1. "Quoted ₹" is the one editable cell (F2/double-click; Enter commits, Tab
   moves down, Escape abandons; clearing sets NO PRICE). The "list" chip marks
   rates nobody has priced; typing sets `priceSource=USER`.
2. The sticky summary bar shows Subtotal with honesty caveats ("N not priced ·
   M still at list"), the tax line (a single rate only when every priced line
   shares one; assumed-default lines are counted, not hidden), and the total.
3. Selecting lines offers "Apply 10% discount" — off the **current** rate, so
   repeated presses compound downward and can never raise a price; lines with
   no rate fall back to list; lines with neither are skipped and not counted.
4. Managers see a Margin column (red below floor) with a tooltip saying
   whether it is measured (bill cost) or indicative (catalogue cost); a
   salesperson's payload has no economics at all.

**Ends.** All lines priced by a person · left partially priced with the caveat
stating exactly what the total omits.

### 7.6 Quote intelligence: deterministic assessment and role projection

**Trigger.** Automatic — any change to a line's product, qty or price re-asks;
opening a drawer does not.
**Path.** One `POST /api/v1/quote-intelligence/assess` for all lines. Exception
codes include NEGATIVE_MARGIN, BELOW_MIN_MARGIN, BELOW_MARGIN_FLOOR,
BELOW_LAST_PRICE, BELOW_BAND_PRICE, PEER_MEDIAN deviations,
COST_INCREASE_NOT_PASSED, MARGIN_EROSION, NO_COST_BASIS, NO_PRICE_SET,
NEW_RELATIONSHIP, THIN_HISTORY. For a salesperson, any rule whose boundary
names a withheld value is replaced by **one fixed CRITICAL
`APPROVAL_REQUIRED`** — the fact that a named rule fired is a predicate a
caller could walk, so the boundary is withheld with it; cost-based references
are absent and counted (`references_withheld`). The grid shows the worst
exception as a chip (approval / blocking / check price / context / clear); the
drawer shows the full panel — confidence, quantity band, exception cards,
"Compared against" references, economics (mgmt only; "not recorded" when no
bill covers the item), data-quality reasons, and the thresholds-version
provenance footer.
**Anti-walk guards** on the endpoint: customer scoped by visibility (an
out-of-scope name degrades to an empty reference, indistinguishable from a new
customer — never 403), `as_of` bounded to ±90 days, ≤200 lines, ≤4 distinct
prices per product per request ("Assess the quote you are sending"), and
server-held line costs are read only when the caller holds the quote.
**Branches.** Loading → skeleton · failed → the server's sentence · no history
for the product → "No sales history matches ⟨ref⟩" (resolved:false — empty is
the correct answer) · a resolved customer+product offers "Full analysis →" to
`#/account/:id/item/:itemId`.

### 7.7 Override, or request approval

**Trigger.** A non-INFO exception on a line.
**Path.** The capture form takes a reason code (VOLUME_COMMITMENT /
STRATEGIC_ACCOUNT / COMPETITIVE_PRESSURE / CLEARING_STOCK / CONTRACTED_PRICE /
OTHER) + optional note. Two shapes:
- **Record why this price is right** (no approval needed) →
  `POST /api/v1/quote-intelligence/snapshot` freezes a server-re-derived
  snapshot (nothing browser-computed is persisted) into the append-only
  `quote_decisions` trail and opens/refreshes the DRAFT outcome row.
- **Request approval** (`requires_approval`) → snapshot first, then
  `POST /api/v1/approvals/quote-line`. The server re-assesses at the line's
  stored cost (never the request body), refuses 400 "within policy" when the
  exception no longer fires, and computes the authority: **below cost →
  OWNER**, otherwise manager per policy.
A live banner tracks the request (PENDING / APPROVED / REJECTED /
CHANGES_REQUESTED + note) — re-fetched on every quote change, so an approval
granted in another tab appears without reload. Re-pricing back within policy
**auto-withdraws** a now-unneeded pending request; conversely an approval
covers the price it was granted at — a lower re-price un-covers it and the
gate re-blocks.
**Branches.** No price yet → "Set a price first" · a salesperson snapshotting
a quote another desk holds → uniform 404 (first save attributes the quote;
strangers' quotes are refused indistinguishably).
**Ends.** Override recorded · approval PENDING (send now blocked) · refused
within-policy · cancelled.

### 7.8 Send the estimate

**Trigger.** "Create Zoho estimate" (label adapts per connector; disabled
while busy, lineless, gate-blocked, or already sent unchanged).
**Path.** The server works through, in order:
1. **Technical blockers** — every CONFIRM READING / UNRESOLVED / AMBIGUOUS /
   INCOMPATIBLE / PIE OFFLINE line refuses the send, naming the lines; the
   client switches to the Needs-attention filter.
2. **Unpriced lines** refuse the same way — an unrated line would let the ERP
   price it from its own item card.
3. **Assess and record** — the quote's assessment snapshot is written first,
   so the gate judges every quote actually sent.
4. **Approval gate** — any line needing approval without a covering APPROVED
   request → 403 with the reason ("price changed since approval" after an
   uncovered re-price). The gate also folds in the screen's own below-floor
   lines (screen margin uses catalogue cost; the assessment uses bill cost —
   the parameter closes the gap).
5. **Idempotency** — a fingerprint of (supply : qty : rate) per line is
   compared against the last persisted document row (survives restarts):
   unchanged content answers "already covers this quote", creating nothing.
6. **The write** — exactly three possible answers: created ·
   `SourceWriteRefused` (the named lines + the source's sentence) ·
   `SourceWriteUnknown` (a reference to search for). Never a claimed-created
   estimate that may not exist.
7. **Bookkeeping** — the document row is persisted and the outcome moves
   DRAFT→SENT with the ERP's own estimate id as the durable join; bookkeeping
   failure never undoes a real send.
The summary bar then shows a durable "Sent · ⟨system⟩ · ⟨number⟩" chip that
turns "amended since" once the priced content moves, and the button becomes
"Send the amended quote".
**Branches.** Approval policy off → no approval gating (blockers and pricing
checks still apply) · client-side gate pre-check saves a certain refusal but
the server is the authority (an approval granted in another tab lets the send
proceed) · refusing adapter → the binding failure held in the alert.
**Ends.** Sent (chip + number) · already-existed · refused: unresolved /
unpriced / 403 awaiting approval / source-refused · client-side block.

### 7.9 Record the outcome (WON / LOST)

**Trigger.** The outcome bar renders once an outcome row exists (first
snapshot → DRAFT; send → SENT).
**Path.** "Mark won" records immediately; "Mark lost…" opens a dialog whose
reason list is the **server's** vocabulary (UNKNOWN excluded) plus optional
"who won it" — LOST without a reason is the server's rule (422 naming every
choice). Scoping: a salesperson may only move a quote they hold (attribution
via outcome row → recorder → snapshot trail; everything else answers one
uniform 404); managers/owners are unnarrowed. WON/LOST are terminal — 409 on
any later transition; a loss recorded before the vocabulary existed renders
"Not recorded — decided before the reason was asked for."
**Ends.** WON · LOST with reason · refusal shown, state unchanged · cancelled.

### 7.10 Per-line decision support (facts + AI reading)

**Trigger.** The drawer opens; re-fetched when the line's product or price
changes.
**Path.** `POST /api/v1/quote-support` returns deterministic FACTS (customer
and item facts; "restricted"-badged fields are mgmt-only and *absent* from a
salesperson's response) strictly separated from the AI block, which is
status-driven: OK (recommendation + explanation + caveat) · DEGRADED ("shown
from the deterministic facts — the model response was not used") · FAILED
("you can still decide") · SUPPRESSED/PENDING (withheld — evidence too thin,
judgement withheld rather than manufactured). "Accept recommendation" is
offered **only** on OK with a real recommendation (a card where accepting was
impossible must not count as declined); Accept → ACT, Modify → OVERRIDE with a
note, Set aside → DISMISS with a note (`POST /api/v1/decisions/{decision_id}/action`).
None of these change price or product.
**Ends.** Decision captured · facts read, nothing captured · error state.

### 7.11 Draft persistence

Every quote change is written to the draft's row (`quote_drafts`) before the
response is answered; the "Saved HH:MM" chip in the header is the server's
`savedAt`, and there is no Save button and no copy in the browser (the old
`localStorage` key is removed on the next visit to the workspace). The same
draft opens on whichever desk follows its link, "New quote" starts another
without touching this one, and a backend restart changes nothing. "Remove"
archives the row — the number is never minted again — and is refused on a
quote that has been sent.
---

## 8. The outcome and value loop

### 8.1 Won & lost analysis (`#/quote-outcomes`)

**Trigger.** Nav "Won & lost" (every role; a salesperson's data is scoped to
their accounts server-side).
**Path.** `GET /api/v1/insight/quote-outcomes`: win rate (a dash below the
minimum decided count — "a rate is not computed until ⟨floor⟩"), value
won/lost (quotes with no priced lines counted and named), loss-reason mix with
owners (PRICING / SUPPLY / POSITION / NOT_OURS / UNKNOWN), a "Waiting on the
customer" grid with per-row **Record outcome**, win-rate slices by customer /
principal / product line / month, and every decided quote. Managers
additionally get the **pricing panel** — "Are we losing on price?" (won vs
lost median price per item and quantity band) — a deliberately **separate
request** (`GET /insight/quote-pricing`) so margin never reaches a
salesperson's browser; for a salesperson the panel is absent, never
rendered-and-403'd.
**Branches.** Load fails / plan lacks intelligence → error state with retry ·
nothing decided yet → the server's own empty sentence · too few observations →
dash with the floor stated.
**Ends.** Read · hand-off into the outcome dialog · empty · error.

### 8.2 Record an outcome from the analysis screens

**Trigger.** "Record outcome" on Won & lost, or "Record…" on Unanswered.
**Path.** The one shared dialog: outcome restricted to the row's server-sent
`allowed_next` (a DRAFT may only be marked LOST; SENT may be WON or LOST); a
loss requires a reason from the server's catalogue (NOT_RECORDED is a reading
state, filtered from every choice list) plus a note, under the warning that a
decided quote cannot be reopened. `POST /api/v1/quote-intelligence/outcome` →
snackbar, reload.
**Branches.** 422 LOST-without-reason (names every choice) · 409 terminal
transition / outcome already recorded against a different quote / **ambiguous
ERP reference** (the quote number answers to two connected books — nothing the
body could carry fixes it) · uniform 404 for a quote this principal does not
hold · cancel keeps the typed state until the next successful record.
**Ends.** Recorded (terminal) · cancelled · refusal held in the open dialog.

### 8.3 Unanswered quotes worklist (`#/unanswered-quotes`)

**Trigger.** Nav "Unanswered" (every role; deliberately no count badge).
**Path.** `GET /api/v1/insight/unrecorded-quotes?limit=100` — the ranked pile
of ERP quotes with no recorded outcome. Headlines are counted over the whole
pile ("Showing the top 100. The other N are real and not on this page");
value at stake excludes quotes with no total *and says so*; groups are the
server's own (Lapsed / No expiry recorded / Still open). Grid rows carry the
ERP's verbatim status, "Lapsed for" (never rendered as 0), quoted value
("No total on the quote", never ₹0), and Record… into the shared dialog —
recorded rows leave the pile on reload. This flow records against the ERP's
`quote_document_ref` — the platform never priced these quotes, so there is no
platform quote id.
**Ends.** Outcome recorded, pile shrinks · pile read · empty ("no quotes
synced" distinguished from "every quote decided") · error with retry.

### 8.4 Attribution: what PIE changed (`#/what-pie-changed`)

**Trigger.** Nav (manager/owner); a salesperson's bookmarked URL gets a
closed-door explanation and **no requests are made** — there is no salesperson
projection of a gross-profit ledger.
**Path.**
1. `GET /api/v1/attribution/summary` + `GET /api/v1/attribution/events`:
   window chips (trial days left; "Frozen at trial end" for a lapsed org),
   **evidence gaps printed above the figures**, headline tiles (a null renders
   "Not measured" in words, never a dash), the verdict (measured-zero
   distinguished from unmeasured), identified-vs-realized kept apart with the
   server's never-sum-these sentence, productivity counts (counted, never
   valued), and the ledger grid — each event drills to its computation basis
   and the records it was computed over, per-row thresholds version (an event
   with no evidence refs renders "a fault worth reporting").
2. **Owner-only panels**: the 30-day evaluation
   (`GET /api/v1/attribution/evaluation?pie_cost=…`) — the owner types what
   PIE costs (nothing stored), sees attributed value, net value (the one
   browser-side subtraction, both operands on screen), and value-per-rupee
   which renders UNKNOWN, never 0×, without a cost or a baseline; and the
   month-by-month rollup (`GET /api/v1/attribution/rollup`) — complete months
   only, ROI refused outright when any complete month has no detection on
   record, the in-progress month drawn under the table and never in the total.
3. A manager sees, in place of the two owner panels, a note that the 30-day
   report is owner-only and nothing in the ledger above is withheld from them.

**Plan behaviour (deliberate).** Attribution is *not* plan-gated at inclusion:
a lapsed org reads its ledger **frozen at trial end** forever; an org with no
plan and no trial gets 403.
**Ends.** Read; owner optionally computes ROI · closed door · plan refusal ·
frozen view · error with retry.

### 8.5 Retrospective: what your books already hold (`#/what-your-books-hold`)

**Trigger.** Nav (manager/owner; the server refuses a salesperson outright — a
finding count is a margin fact).
**Path.** `GET /api/v1/retrospective`: the **verdict comes first**
(UNEXAMINED / PARTIAL / EXAMINED — coverage before findings), then history
denominators (invoice lines beside purchase-cost lines — a zero there is why
margin checks cannot speak), then the four detector panels (customer decline,
dormancy, margin deterioration, cost pass-through) each with judged-share,
finding chip, and withheld reasons. Under an UNEXAMINED verdict the finding
counts are suppressed entirely — "0 found" beside "0 % judged" would invite
the wrong zero.
**Ends.** Read · empty (no history synced) · error/refusal.

### 8.6 Enquiry corpus

**Capture via the desk.** The Quote Builder's intake modal is the only UI door
(§7.2): a stated channel captures the raw text byte-intact into the corpus
with `source_ref=quote:{id}`; no channel, no row.

**API lifecycle** (`api-client` / adapters; no dedicated screen):
- `GET /api/v1/enquiries/channels` publishes the two closed sets (channels and
  dispositions) so no client hardcodes a copy.
- `POST /api/v1/enquiries` captures one line exactly as it arrived — raw text
  unnormalised, channel required with no default, always appends (two
  identical asks a week apart are two enquiries; redelivery is the adapter's
  problem on `source_ref`).
- `POST /api/v1/enquiries/{id}/disposition` decides or corrects a line —
  closed set QUOTED / ABSTAINED / NO_STOCK / NO_PRICE / LOST / NO_RESPONSE (no
  PENDING: an undecided line has no row). Three outcomes: append · idempotent
  no-op (`written:false`, the re-runnable-job guard) · supersede-and-append
  (both rows stay readable).
- `GET /api/v1/enquiries/{id}` returns the line, its live disposition (null =
  undecided, not "answered with nothing") and the superseded history.
- `GET /api/v1/enquiries` exports the whole corpus (manager/owner — every word
  the tenant's customers wrote); counted before loading and refused with 413
  past 50,000 lines rather than truncated ("a truncated corpus is a benchmark
  run against a prefix").

**Branches.** Empty text / unknown channel → 400 CaptureRefusal · missing or
cross-tenant line → one uniform 404 · unknown disposition → 400.

### 8.7 Decision outcome tracker (API-only)

`GET /api/v1/outcomes` — the decision cards' afterlife, with no frontend
consumer today. Every accepted decision's outcome snapshot is evaluated fresh
on read (never stored, so a late re-sync changes the answer): PENDING (horizon
not elapsed — asserts nothing) · REALISED (the delta is a measurement) ·
UNKNOWN (names exactly what evidence is missing, never a zero). A typo'd
status filter is a 400 naming the valid values — silently matching nothing
would read as "no outcomes". Salesperson rows are filtered to their own
decisions with restricted metrics recursively redacted. Plan-gated with the
queue it measures.

### 8.8 Attribution detection on sync (system)

Every completed sync run ends with attribution detection — ungated by plan
(detection keeps running for lapsed orgs; *reading* past the trial is what the
plan restores). Detectors run over rows the desk already wrote; events upsert
on a stable key so double-counting is structurally impossible; a missing
operand yields no event; a detection failure is logged and never fails the
sync.
---

## 9. Governance: settings, approvals, identity, trust

### 9.1 Owner manages members and roles (`#/settings`)

**Trigger.** "People and roles" — managers see it read-only (`can_manage`
comes from the server's users response, deliberately not the client mirror);
every mutation is owner-only; a salesperson gets only the account panels and
no org fetch is even attempted.
**Path.**
1. `GET /api/v1/admin/users` + `GET /api/v1/admin/policy` load in parallel.
2. Add a member (email / name / role) → `POST /api/v1/admin/users`; the
   temporary password is shown **once** ("stored only as a hash… asked to
   change it at first sign-in").
3. Grid actions per member: change role (inline select, writes on change),
   deactivate/reactivate (the login, everywhere), remove/reinstate (this
   workspace's membership only — the person's other workspaces are untouched),
   reset password (new temp shown once; every session that account had open is
   retired). The owner's own row is read-only with a "you" chip and no
   actions.

**Branches.** Email exists anywhere on the platform → 409 (no silent
cross-tenant attach) · changing own role → 400, even as owner · anything that
would leave the org without an active owner → 409 · self-deactivation /
self-removal → 400 · foreign or unknown user id → uniform 404 · a removed
member's next request stops resolving → their session dies (401), login and
other workspaces untouched.
**Ends.** Member added (temp password handed over out-of-band) · changed ·
refused with the specific sentence · removed.

### 9.2 Owner edits the approval policy

Five labelled toggles, each writing immediately (`PATCH /api/v1/admin/policy`):
require approval for quotes · below-cost requires owner · require approval
below the review floor · allow self-approval · escalation creates approval.
Managers see the toggles disabled ("a manager who could widen their own
authority would not have any"). The first-ever read creates the policy row
with enforcement **on** by default.

### 9.3 Owner edits the margin policy (versioned)

**Path.**
1. The section shows the version in force (`ci_…` content hash) and
   last-edited time. Fields are typed by kind — ratio (typed 24, sent 0.24),
   money, days, flags as switches, band edges, per-family target rows,
   retained-PAT rows per entity — each showing default/overridden with a
   per-field reset.
2. Validation runs per keystroke; the floor ladder (approval ≤ review ≤
   target) flips to a red explanation when inverted; Save stays disabled while
   any problem exists.
3. **Backtest before saving**: "See what this would have done" replays every
   recorded quote line against the draft floors
   (`GET /api/v1/admin/margin-policy/backtest`) — newly-gated lines, revenue
   affected, no-longer-gated count, and the unjudgeable no-cost lines beside
   the findings, never below them. Nothing is saved. The endpoint is
   owner-only **by arithmetic**: shortfall plus a caller-supplied margin gives
   cost in closed form.
4. Save sends a sparse PATCH (`PATCH /api/v1/admin/margin-policy`); the server
   validates the *whole* combined policy, mints a new version, and answers
   with the honest consequence, shown verbatim: "Existing metrics keep the
   version they were computed with until the next recompute."

**Branches.** Combined policy invalid → 400 with the rule broken · nothing
changed → short-circuited · manager → all inputs disabled, no backtest ·
the fixed analysis internals are read-only for everyone, deliberately.
**Ends.** New version minted (old rows keep their stamp) · refused · discarded
· backtest read and abandoned.

### 9.4 Approvals — requester side (usually SALES)

**Trigger.** A quote line crossed a policy boundary; the ask is raised from
the Quote Builder (§7.7).
**Path.** On `#/approvals` the salesperson sees **only their own requests**,
projected: economics omitted entirely, authority collapsed to one fixed
"APPROVAL_REQUIRED", title and reason to fixed sentences — so no field flips
at `proposed_price == unit_cost` and cost cannot be bisected. Their one action
on an open request is **Withdraw**. The quote's send button pre-reads the same
gate the send enforces (`GET /api/v1/approvals/quotes/{id}/gate`). After
CHANGES_REQUESTED, re-pricing and asking again **reuses the same request**
(back to PENDING, thread appends RESUBMITTED) — no duplicate queue items.
**Branches.** Within policy at raise time → 400, nothing created · re-priced
back within policy → the open request auto-withdraws ("no longer required") ·
approval granted then price lowered → the gate re-blocks ("price changed since
approval" — an approval is for a number, not a line) · opening someone else's
request → 403; foreign org → 404 · withdrawing someone else's → 403.
**Ends.** APPROVED (gate opens at that price) · REJECTED with note ·
CHANGES_REQUESTED (resubmit) · WITHDRAWN (by hand or automatically) · refused
at raise.

### 9.5 Approvals — approver side (manager/owner)

**Trigger.** The error-coloured nav badge counts `pending_for_me` — computed
through the **same** refusal predicate as the card buttons, so it never counts
work the viewer cannot decide.
**Path.** The queue shows what this role can decide (a manager's list excludes
owner-authority requests except their own; the owner sees everything). Each
card renders the full subject for approvers — quoted price, effective cost,
margin (red when below cost), line value, the requester's reason, the
append-only thread. Three actions over one note box: **Approve** (a written
rationale is required when an owner signs below cost), **Ask for a different
price** (note required), **Reject** (note required) →
`POST /api/v1/approvals/{id}/decide`. Every decision appends to the tenant's
hash-chained audit log — who, when, rationale given, never the price. Deciding
a DECISION_ESCALATION writes back into the decisions queue: APPROVED →
ACTIONED (with outcome capture), REJECTED → DISMISSED, CHANGES_REQUESTED →
back to OPEN.
**Branches.** Below-cost as a manager, or own request with self-approval off →
no buttons; the server's sentence renders instead · empty note where required
→ 400 · already decided → 409 · authority too low → 403 · self-approval
allowed by policy → a manager may decide their own manager-authority request ·
empty queue → "Nothing waiting."
**Ends.** APPROVED · REJECTED · CHANGES_REQUESTED · refusal, card unchanged ·
queue left untouched.

### 9.6 AI settings: bring-your-own-key (owner)

**Trigger.** Settings "AI layer" (owner-only; every `/api/v1/ai/*` route is
`require_owner`).
**Path.** Readiness + 7-day telemetry cards ("configuration is a claim,
telemetry is the fact" — the Live chip turns "calls failing" on real
failures). Per provider (Anthropic, OpenAI, Google, OpenRouter): save/rotate a
key (`PUT /api/v1/ai/providers/{p}` — write-only; plaintext never returns,
only a last-4 hint), **Test** (`POST …/test` — one live fixed-word ping;
"the key is saved" and "the key works" are different facts; both outcomes are
audited), choose which provider runs the layer (`PUT /api/v1/ai/active`;
empty restores the deployment default), and **Remove** — a two-click arm that
warns removal is irreversible and, when the provider is active, that decisions
fall back to the deployment default.
**Branches.** Unknown provider → 404 · test with no key anywhere → 400 ·
choosing an unusable provider → 400 · non-owner → the section is not rendered
and the server would 403.
**Ends.** Key saved/rotated · tested ok/failed (audited either way) · provider
switched · key removed (irreversible) · refused.

### 9.7 Identity review: cross-connector linking (`#/identity`)

**Trigger.** Nav (manager/owner). Suggestions are produced by syncs when an
exact GSTIN (customers) or SKU (items) — or last-resort name — matches across
connectors and auto-link is off.
**Path.**
1. Two kind tabs (Customers / Items) × two views (To review / All).
2. **Auto-link policy** per kind (owner): on = exact matches link without
   asking — "unrecoverable when the match was a group trading under one
   registration"; NAME-strategy matches are **never** auto-linked regardless.
3. A suggestion card shows the just-imported record beside the existing
   identity with the evidence string (evidence, not a score; name matches
   carry a "weaker evidence" chip). Owner: **Link** (records linked — nothing
   merged or copied) or **Keep separate** (recorded; the pair is never
   re-proposed). A manager sees "An owner decides these."
4. All-identities view: search, "seen in more than one system" filter; each
   identity lists its connector records with provenance. Owner: rename,
   or **Unlink** a record onto its own fresh identity (hidden when it is the
   only record).

Separately — and deliberately one function — `confirm_proposed_identity` in
`identity/service.py` is the single gate for *confirmed code mappings*
(asserted identity), reached from the Quote Builder and the public resolution
API (§7.4, §10.3); this screen never touches it.
**Branches.** Empty review queue → three distinct empties (nothing imported /
everything keyed and distinct / N records unkeyable so name matching covers
them) · already decided / unknown suggestion → 400 · retired identity or bad
refs → 400 · a salesperson's typed URL → every fetch 403s (the server is the
gate) · manual link by id exists API-only (`POST /identity/{kind}/link`).
**Ends.** Linked (margins roll up across connectors) · kept separate (never
re-proposed) · unlinked / renamed · auto-link toggled · refused.

### 9.8 Trust surface (`#/trust`, owner only)

**Path.** Four parallel reads (skipped entirely for a non-owner — no four 403s
in the console):
- **Disclosure**: what reaches a model — served from the same constants the
  outbound payload checker enforces, so statement and behaviour cannot drift.
- **Payload check**: 0 payloads → "Nothing has been sent yet"; all clean →
  "N checked, none flagged"; any flagged → an error naming each ("a defect,
  not a statistic"). `?reveal=true` decrypts the actual sent text for its
  owner.
- **Access log**: every break-glass grant (with the stated reason), each use,
  each revocation — unfilterable and unsuppressable by design; the empty state
  is "the log itself saying so, not an absence of logging." (Break-glass has
  no tenant-facing write surface — granting is vendor-side; this read is the
  owner's window.)
- **Export**: `GET /api/v1/trust/export` builds `pie-portal-export.json` in
  the browser — including the cross-connector identity graph, deliberately:
  "an export without it would quietly be the export that keeps you."
- **Erasure** — the only irreversible action in the application. Stage 1 states
  exactly what dies (everything under the tenant data key, in every backup
  too) and what survives in plaintext; stage 2 requires typing the org id
  exactly plus a ≥10-char reason. `POST /api/v1/trust/erasure` destroys the
  key and returns a **signed receipt** (re-verified on every later read)
  attesting both halves. Idempotent — a second call returns the existing
  status; an id mismatch → 400 with nothing changed. A receipt whose
  `key_destroyed` is false renders "that is a defect — report it."

**Ends.** Read and leave · export saved · erased (the panel permanently
becomes the receipt) · refused/abandoned · error with retry (never dressed as
"nothing to see").

### 9.9 Audit chain verification (API-only, owner)

`GET /api/v1/trust/audit` (entries + a verdict computed over the **whole**
chain, not the returned page), `…/audit/verify` (the pollable check — reports
the first break or a clean bill), `…/audit/export` (JSON or CSV carrying every
signed field so a third party can recompute the hashes without the vendor).
Owner-only is load-bearing: the chain carries margin-policy transitions, and a
per-role filtered chain would not verify.
**Ends.** Clean verdict · a named first break (an incident) · export
downloaded · 403 for any non-owner.
---

## 10. Machine users and operations

### 10.1 Owner mints and revokes an API key

There is **no screen** for resolution API keys — the flow runs over HTTP with
an owner session token (`docs/resolution-api.md`, "Getting a key"); a key
cannot mint another key by design.
**Path.** `POST /api/v1/api-keys {name?, role?, rate_limit_per_minute?}` —
role defaults to SALESPERSON (the narrowest, never the creator's); the 201
response carries the full secret (`pie_<key_id>_<secret>`) **once** ("stored
as a hash and cannot be shown again"). `GET /api/v1/api-keys` lists hint,
role, rate limit, last-used — never a secret. `DELETE /api/v1/api-keys/{id}`
revokes, idempotently. All three audited.
**Branches.** Non-owner → 403 · unknown/foreign key on revoke → one 404 ·
rate limit outside 0–6000 → 422.

### 10.2 API client resolves a line (`POST /api/v1/resolve`)

**Trigger.** A machine holding a `pie_` key sends one line of enquiry text
(≤512 chars — a whole document is refused with advice to split). The contract
itself is public: `GET /api/v1/resolve/openapi.json` is served without
authentication ("a contract you need a credential to read is not published").
**Path.**
1. Credential as `Authorization: Bearer pie_…` or `X-API-Key` (one door, two
   handles). The key resolves to an **ordinary Principal** with the key's
   role, so cost withholding and the APPROVAL_REQUIRED substitution reach
   machine callers through the exact code path a browser session uses. Every
   response carries `X-RateLimit-*` headers.
2. The same engine path as the Quote Builder: org equivalence bands, confirmed
   mappings, customer identity scope — any setup load failure degrades to
   packaged defaults, never fails the request.
3. One document shape always. `RESOLVED`: the chosen record with per-attribute
   provenance and spans, ranked alternatives, relationship
   (EXACT/TECH/COMPAT/POSSIBLE under this org's bands), and an
   `identity_proposal` when confirmable. `ABSTAINED` with five named reasons:
   **NO_MATCH** (evidence — record the gap) · **AMBIGUOUS** (choose from the
   ranked alternatives) · **NEEDS_CONFIRMATION** (the only abstention with an
   action: go to `/resolve/confirm`) — all 200 with
   `is_evidence_about_the_input: true`; **CATALOGUE_UNAVAILABLE** and
   **ENGINE_ERROR** → 503, explicitly *not* evidence about the item, so a
   transport failure is never written into master data as "no such product".
4. With a `proposed_price`, a commercial block is assessed — one line, one
   price, today (no `as_of`, no batch, no multi-price: the walk-guard surface
   does not exist here) and projected by the key's role; `customer_ref` is
   visibility-scoped and degrades silently to unscoped; the item-master cost
   fallback is deliberately absent, so a costless product answers
   NO_COST_BASIS at every price.

**Branches.** No credential → 401 with `WWW-Authenticate` · malformed /
unknown / wrong-secret / revoked → **one uniform** 401 "Invalid or revoked API
key" · >10 failed attempts on a key id per minute → that id is refused for the
window *even with the right secret* (mint a new key rather than retry) ·
allowance spent → 429 with `Retry-After: 60` (in-process counters — a speed
bump against price-bisection sweeps, not a metered quota) · empty/oversized
text → 422.
**Ends.** 200 RESOLVED (± commercial ± proposal) · 200 ABSTAINED
NO_MATCH/AMBIGUOUS/NEEDS_CONFIRMATION · 503 (retry, record nothing) · 401 ·
429 · 422.

### 10.3 API client confirms a customer's code (`POST /api/v1/resolve/confirm`)

**Trigger.** A previous resolve answered NEEDS_CONFIRMATION with an
`identity_proposal`.
**Path.** Body: the same text, `customer_ref` (required — a mapping needs a
customer to scope to), `record_id`. The line is **re-resolved server-side** (a
caller cannot name its own proposal) and passed through the same
`confirm_proposed_identity` gate as the Quote Builder. Success:
`recorded: true` — that customer's code resolves authoritatively from now on.
**Branches.** Every refusal — no identity scope, no proposal, or a different
record (including one legitimately picked off `alternatives`: that is a
substitution on one quote, and filing it would make an approximate match exact
by storage) — answers **200 `recorded: false` with one uniform reason**: a
status distinguishing "not the proposal" from "no proposal" would confirm half
a guess. Auth/rate/validation errors as in §10.2.

### 10.4 Operator: health diagnosis (`GET /api/health`)

Public, checked live on every poll (with a 30-second cache per revision), so
migrating a running deployment goes green without a restart. The body carries
the migration state, pending revisions, and a one-sentence schema gap. The
states and their fixes:

| State | Meaning | The fix |
|---|---|---|
| `CURRENT` (200) | At head, no gap | Not a migration problem — look elsewhere |
| `EMPTY` (503) | No tables | `alembic upgrade head` |
| `UNSTAMPED` (503) | Built outside Alembic | Stamp only if genuinely current; else drop and migrate |
| `BEHIND` (503) | Older than head | `alembic upgrade head` — the one case it fixes |
| `UNKNOWN_REV` (503) | A revision this code does not have | Deploy the owning code. **Do not upgrade** |
| current-but-gapped (503) | Hand-edited schema under an unchanged stamp | Named tables/columns + the command |

Database unreachable → 503 with the exception type. Without a running app the
same diagnosis is `python3 -c "…inspect_database(engine).summary"`.
`GET /api/v1/internal/health` is the simpler liveness sibling (SELECT 1 +
source mode; no migration awareness).

### 10.5 Operator: bootstrap (`python -m app.bootstrap`)

**Path.** Ensure the data dir → inspect state → `alembic upgrade head` (the
only thing permitted to create this schema) → seed org/users → the
demo-dataset decision → threshold-registry backfill (idempotent) → print the
report. Also runs non-fatally inside app startup in dev (`AUTO_BOOTSTRAP=1`);
production never auto-migrates or auto-seeds, fails boot on default secrets,
and disables `/docs` and the app-wide OpenAPI (the resolution router's own
contract stays public).
**Branches.** UNSTAMPED but exactly matching the models → `stamp head` with a
warning · UNSTAMPED and mismatched → a refusal naming the incomplete tables
("back up, drop, migrate empty — a wrong stamp is worse") · production → demo
refused · live Zoho source → demo disabled · demo seeding failure → logged,
never fatal · startup bootstrap failure → the app still starts degraded and
`/api/health` reports honestly.

### 10.6 Monitoring: Prometheus scrape

`GET /api/v1/internal/observability/prometheus`, authorised by a static scrape
token compared as bytes (`hmac.compare_digest`; the empty-token guard runs
first) — unset and wrong tokens are indistinguishable 401s, and the token is
accepted on no other route. The response is one worker's in-memory counters
(worker-labelled; counters sum across workers, percentiles do not), touches no
database, and exposes no cost/price/customer/tenant series.

### 10.7 Operator/manager: observability and the queue

`GET /api/v1/internal/observability/*` (dashboard, health, capacity, metrics,
api-performance, database, jobs, syncs, tenants, caches — manager/owner):
jobs/syncs read persisted run rows (cold heartbeats reported as *stalled*;
uncomputable throughput is null with a basis, never a lying zero); the
in-process facets state their one-worker scope in the payload. The dashboard
React component exists but **no route mounts it** in the current build — the
surface is API-first. Queue work: `GET /api/v1/internal/queue` (dispatch mode,
scheduler lease holder, worker liveness, depth, dead letters),
`GET /queue/{id}` (one message with payload; foreign tenant → 404),
`POST /queue/{id}/retry` (owner; 409 unless DEAD_LETTER — retrying a live
message would mean two workers on one job). Owner-only analytics:
`ai-metrics`, `detector-outcomes`, `queue-adoption` (value-at-risk is cost
information), `ai-readiness`. The adjacent paid triggers
(`POST /internal/detectors/run`, `/internal/decisions/generate`) are
plan-gated per endpoint.

### 10.8 Endpoints with no frontend consumer (API-only today)

`/api/v1/outcomes` · `/api/v1/enquiries*` (beyond the intake side-capture) ·
`/api/v1/trust/audit*` · `/api/v1/api-keys` · `/insight/capital` ·
`/insight/pass-through` · `/insight/customer-financing` ·
`/insight/entity-routing` · `/insight/wallet/{id}` · `POST /insight/tenders` ·
`PUT /insight/msme-status` · `/internal/observability/*` (the dashboard
component is unmounted) · the legacy single-connection endpoints under
`/api/v1/data/connection` and credential sharing under
`/api/v1/data/credentials/{id}/share`. Legacy unversioned prefixes
(`/api/quotes`, `/api/attribution`) answer 307 redirects to `/api/v1/…` — a
deploy aid for stale tabs, not for integrators.

---

## 11. Cross-cutting behaviours

These hold on every flow above and are easy to miss reading any one of them:

- **One auth-loss path.** Every 401 anywhere funnels through one handler:
  forget the local session, capture the path, sign-in card with "Your session
  expired", return to the captured path after. Tabs agree via the `storage`
  event.
- **Role projection is server-side omission.** A salesperson's payloads *lack*
  cost/margin fields, below-floor counts, supplier halves, and any rule whose
  boundary is cost (replaced by one fixed APPROVAL_REQUIRED). The client
  ability mirror only decides what is *offered* — a nav item that would always
  403 is absent, never broken.
- **Anti-enumeration.** Foreign and absent ids answer identically (quotes,
  sessions, users, organizations, sync runs, queue messages, enquiry lines,
  API keys); login failures are one sentence; the resolve-confirm refusal is
  deliberately uninformative.
- **Empty is an answer.** Every insight panel renders the server's own empty
  reason verbatim; "no cost on record" is UNKNOWN or a named refusal, never a
  benign default; a measured zero is distinguished from unmeasured everywhere
  (attribution verdict, GMROI reason chips, retrospective's suppressed counts,
  win-rate floors); "none recorded" is never rendered as ₹0.
- **Undo semantics, in one place.** Decision actions: 9-second snackbar undo
  (REOPEN, audited). Quote outcomes: deliberately no undo. Credit limits /
  vendor terms / targets / item placements: undo by *clearing*, which is
  distinct from zero. Temporary passwords and API-key secrets: shown once,
  never recoverable. BYOK keys: removal irreversible. Identity links:
  reversible (unlink); rejections permanent-by-memory. Approvals: decided is
  decided (409) — re-raise instead. Erasure: irreversible by construction, in
  every backup.
- **Deterministic vs interpreted.** Every number on every screen was computed
  in `commercial/` or `signals/` and stamped with a thresholds version; the AI
  layer phrases and recommends but never computes one, and its four visible
  states (OK / DEGRADED / FAILED / SUPPRESSED) are documented on `#/states`.
- **Plan vs role.** Plan gates (the `intelligence` feature) decide *which
  surfaces exist*; role gates decide *what is in the payload*. They are never
  interchanged: cost non-disclosure is always a role rule.
---

## Appendix: endpoint inventory

Every HTTP endpoint reachable in the flows above, deduplicated. Guard column:
the effective server-side gate (plan gates noted as +plan; role projection on
shared endpoints is described in the flows). Scoped to the flows on purpose, so
it is smaller than the mounted route table: FastAPI's own `/docs`, `/redoc` and
`/openapi.json`, and the legacy `/api/quotes` and `/api/attribution` catch-all
shims, are mounted but are not flows and are not listed here.

| Method | Path | Guard | Purpose |
|---|---|---|---|
| GET | `/api/health` | public | Live migration state (EMPTY/UNSTAMPED/BEHIND/UNKNOWN_REV/CURRENT) + schema-gap check + db dialect/pool; 200 healthy, 503 otherwise with the named fix |
| GET | `/api/v1/accounts` | signed-in | Customer-account directory (identity, assignment, operational trade figures; no cost/margin) — note: business accounts, not user accounts |
| GET | `/api/v1/accounts/{customer_id}/items` | signed-in | Items this customer bought, for pickers (identity only) |
| PATCH | `/api/v1/admin/margin-policy` | owner | Sparse edit of EDITABLE margin-policy fields (+clear list to reset overrides); validated as a whole; mints a new threshold version and returns the… |
| GET | `/api/v1/admin/margin-policy/backtest` | owner | Read-only replay of every recorded quote line against a variant approval/review floor before saving it; reports newly-gated, no-longer-gated,… |
| POST | `/api/v1/admin/me/password` | signed-in | Change own password with current-password proof; revokes ALL sessions and returns a fresh token+cookie so the caller stays signed in |
| GET | `/api/v1/admin/policy` | manager/owner | Approval policy toggles + full margin policy description (fields, versions, defaults) + fixed analysis internals; can_manage flags owner |
| PATCH | `/api/v1/admin/policy` | owner | Toggle the five approval-policy flags (require_approval_for_quotes, below_cost_requires_owner, require_approval_below_review_floor,… |
| GET | `/api/v1/admin/users` | manager/owner | List all members of the org (ended memberships included, greyed in UI); returns roles list and can_manage (true only for OWNER) |
| POST | `/api/v1/admin/users` | owner | Add a member: create identity (409 if email exists anywhere) + membership; temporary password returned exactly once; must_change_password set |
| PATCH | `/api/v1/admin/users/{user_id}` | owner | Change name/role/active(login)/member(this-org membership); refuses self role-change, self-deactivation, self-removal, and orphaning the last active… |
| POST | `/api/v1/admin/users/{user_id}/reset-password` | owner | Issue a new temporary password (returned once), set must_change_password, and retire the account's existing sessions via password_changed_at |
| PUT | `/api/v1/ai/active` | owner | Choose which provider runs the AI layer; empty string restores the deployment default; 400 if unusable |
| GET | `/api/v1/ai/providers` | owner | BYOK view: active choice, environment provider, per-provider key-on-file/last-4 hint/model/env-key-present; plaintext key never returned |
| PUT | `/api/v1/ai/providers/{provider}` | owner | Save/rotate an org API key (encrypted, write-only) and optional model; 404 unknown provider |
| DELETE | `/api/v1/ai/providers/{provider}` | owner | Remove the org key (irreversible — nothing to restore from); 404 if none on file |
| POST | `/api/v1/ai/providers/{provider}/test` | owner | One live fixed-word ping with the stored (or environment) key; ok/failed + provider detail; audited on both outcomes (AI_CALL/CONNECTION_TEST); 400… |
| GET | `/api/v1/api-keys` | owner | List this org's API keys - 4-char hint, role, rate limit, last_used_at; never a secret |
| POST | `/api/v1/api-keys` | owner | Mint a key (201); secret returned once; role defaults SALESPERSON, rate limit defaults 60/min (0=unlimited, max 6000); audited API_KEY_CREATED |
| DELETE | `/api/v1/api-keys/{key_id}` | owner | Revoke a key (idempotent); unknown and foreign ids are one 404; audited API_KEY_REVOKED |
| GET | `/api/v1/approvals` | signed-in | The approval queue: salesperson = own requests (subject/economics stripped, authority collapsed to APPROVAL_REQUIRED); manager = manager-authority +… |
| POST | `/api/v1/approvals/quote-line` | signed-in | Raise sign-off for one quote line at its current price; server re-derives economics; 400 if within policy; reuses/reopens an existing open request… |
| GET | `/api/v1/approvals/quotes/{quote_id}/gate` | signed-in | Read-only window onto the send gate: can_submit, blocked_reason, outcome status, this quote's approval requests, approval policy flag — same… |
| GET | `/api/v1/approvals/{request_id}` | signed-in | One request with role-projected fields |
| POST | `/api/v1/approvals/{request_id}/decide` | manager/owner per approvals.decide autho | APPROVED/REJECTED/CHANGES_REQUESTED/WITHDRAWN with note; approving below-cost requires a written rationale (400 RationaleRequired); appends to… |
| GET | `/api/v1/attribution/evaluation` | owner | 30-day report: trial vs pre-trial baseline; optional pie_cost query (ge=0) — omitted, roi is null and roi_is_unknown true (render UNKNOWN, never 0x) |
| GET | `/api/v1/attribution/events` | manager/owner | The value ledger, filterable by validated event_type/value_class (400 on unknown), paged (default 100, max 500); each row carries basis operands and… |
| GET | `/api/v1/attribution/rollup` | owner | Value month by month over months=1..36 (default 12) with optional monthly_cost rate; ROI refused (unknown) when any complete month is unmeasured;… |
| GET | `/api/v1/attribution/summary` | manager/owner | The What-PIE-Changed headline for the measured window: ATTRIBUTED alone, POTENTIAL/REALIZED apart, evidence gaps, empty_reason sentence |
| POST | `/api/v1/auth/login` | public | Password sign-in; uniform 401 message, constant-cost PBKDF2 (sentinel hash), exponential throttle after 5 failures (429), lands in a… |
| POST | `/api/v1/auth/logout` | signed-in | Revoke THIS server session, audit SESSION_ENDED, clear cookie |
| POST | `/api/v1/auth/logout-all` | signed-in | Revoke every session for the account including this one; returns count ended |
| GET | `/api/v1/auth/me` | signed-in | Who the cookie belongs to — rebuilds the client session on boot (token field deliberately empty) |
| GET | `/api/v1/auth/sessions` | signed-in | The account's live sessions (device, issued, last seen, current flag) for the Settings sessions list |
| DELETE | `/api/v1/auth/sessions/{session_id}` | signed-in | End one named session ('sign out that other laptop') |
| GET | `/api/v1/commercial/customers/{customer_id}/items/{product_id}` | manager/owner | Customer×Item drill-down: headline, deterministic diagnosis, series, peers, volume periods, transaction evidence; recomputed live under current… |
| GET | `/api/v1/commercial/customers/{customer_id}/portfolio` | manager/owner | Customer's items ranked by economic materiality; thresholds_version(s) the rows carry |
| POST | `/api/v1/commercial/recompute` | manager/owner | Rebuild derived metrics (optionally one customer; background=true refused 409 when no queue worker) |
| GET | `/api/v1/connections` | manager/owner | List connections (health, last sync, coverage, suggested since) + usable credentials + can_manage + pooling note |
| POST | `/api/v1/connections` | owner | Add a Zoho company — reuse a credential_id or supply fresh secrets; checks the connection immediately |
| GET | `/api/v1/connections/catalog` | manager/owner | Connector catalog: Zoho entry + registry (netsuite, dynamics365, acumatica, prophet21, sagex3, sage100) with field specs, permissions, scope… |
| POST | `/api/v1/connections/erp` | owner | Connect one company of a registered ERP; validates values against the spec; checks immediately |
| POST | `/api/v1/connections/erp/discover` | owner | List companies a candidate ERP credential can see before anything is stored (dynamics365 only; SSRF-guarded) |
| GET | `/api/v1/connections/zoho/authorize` | owner | Start Zoho OAuth: record single-use hashed state (10-min TTL), return authorization_url; 503 when no app registered, 400 unknown data centre |
| GET | `/api/v1/connections/zoho/callback` | public | Zoho's redirect target: exchange code for tokens, store credential, redirect to /#/data?oauth=ok&handoff=… or oauth=error&reason=… |
| GET | `/api/v1/connections/zoho/pending/{handoff}` | owner | Claim a completed authorization → {credential_id, label}; flow then rejoins the manual reuse path |
| PATCH | `/api/v1/connections/{connection_id}` | owner | Rename or pause/resume (enabled) a connection |
| DELETE | `/api/v1/connections/{connection_id}` | owner | Remove a connection; already-synced rows deliberately stay |
| POST | `/api/v1/connections/{connection_id}/check` | owner | Live check: ping + per-scope probe (Zoho) or ping only (ERP); records result; fills org timezone/country and connection base_currency |
| POST | `/api/v1/connections/{connection_id}/rotate` | owner | Replace the Zoho refresh token (optional client pair); re-checks immediately; names every other company on the same grant |
| POST | `/api/v1/connections/{connection_id}/rotate-erp` | owner | Replace a registered-connector credential (all fields); same shared-grant disclosure as /rotate |
| PUT | `/api/v1/data/auto-sync` | manager/owner | Set the automatic sync cadence (hours 0–168) on Organization.config |
| PUT | `/api/v1/data/connection` | owner | Legacy: connect/replace the org's Zoho credentials in one call; response pings, never echoes secrets |
| DELETE | `/api/v1/data/connection` | owner | Legacy: unlink the Zoho connection; read-model rows untouched |
| POST | `/api/v1/data/connection/use-credential` | owner | Legacy: point this org at a Zoho company using an existing grant |
| GET | `/api/v1/data/catalog/companies` | signed-in | Every connected company, its chosen pack and its catalogue's state, plus the packs a company may choose from; readable by any signed-in user, because which catalogue answered a resolution is the same entitlement as knowing when the books last arrived |
| POST | `/api/v1/data/catalog/companies/{connection_id}/corpus` | owner | Store one of the files this company's catalogue is built from — CSV or Excel, kept as a row rather than a file because the container filesystem is ephemeral; append-only, so an upload supersedes rather than overwrites and a catalogue already built keeps a real referent. `source_key` replaces that one file; without it, the whole export is replaced |
| PUT | `/api/v1/data/catalog/companies/{connection_id}/sources/{source_key}/mapping` | owner | Correct which of one file's columns hold the record id, description and grade; re-reads the stored bytes and refuses a mapping naming a column the file lacks, so a mapping that cannot build is never stored |
| DELETE | `/api/v1/data/catalog/companies/{connection_id}/sources/{source_key}` | owner | Stop building from one file. Superseded rather than deleted, and the built catalogue is left alone — it goes out of date, because rebuilding here would replace what a company resolves against as a side effect of tidying a file list |
| GET | `/api/v1/data/catalog/companies/{connection_id}/pack-fit` | owner | Try every shipped pack against a sample of this company's files and report the parser's own counts for each, so the pack is chosen on evidence rather than by its identifier. Writes nothing, and runs only packs the engine ships |
| PUT | `/api/v1/data/catalog/companies/{connection_id}/pack` | owner | Choose the pack this company decodes through; validated against what the pinned engine ships, and refused rather than stored when it names nothing |
| POST | `/api/v1/data/catalog/companies/{connection_id}/build` | owner | Decode every file this company has uploaded through its chosen pack, merged into one corpus and de-duplicated by part number (newest file wins, overlaps counted). Synchronous — the response carries the finished result, so there is no job to poll |
| GET | `/api/v1/data/credentials` | owner | Every Zoho grant this org may connect through, with used_by and sharing info |
| GET | `/api/v1/data/credentials/{credential_id}/organizations` | owner | Live list of Zoho companies one grant reaches, marked already_connected (502 if Zoho rejects it) |
| POST | `/api/v1/data/credentials/{credential_id}/share` | owner | Full-replace which other organizations may connect through this grant |
| DELETE | `/api/v1/data/credentials/{credential_id}` | owner | Remove a sign-in nothing is connected through; 409 while any connection still uses it, 403 unless this org owns it |
| GET | `/api/v1/data/status` | signed-in | Connection state headline, auto_sync, last_sync run dict, per-company coverage, live sync state, read-model counts, can_sync/can_manage_connection |
| GET | `/api/v1/data/sync` | signed-in | Sync state for polling: active run(s), busy_connections, last, last_successful_at, can_start |
| POST | `/api/v1/data/sync` | manager/owner | Queue a pull (since/full/connection_id) → 202; returns the existing job instead of erroring on overlap; 503 if sync_runs schema is behind |
| GET | `/api/v1/data/sync-runs/{sync_run_id}/log` | manager/owner | The run's own log page (after_seq cursor, problems_only filter, next_seq, note explaining an empty log) |
| GET | `/api/v1/data/sync-runs/{sync_run_id}/log.txt` | manager/owner | Whole log as a downloadable text file with header (status, error) |
| GET | `/api/v1/data/sync-runs/{sync_run_id}/skipped` | manager/owner | Every skipped row of one run (org-scoped; 404 across tenants) with completeness note |
| GET | `/api/v1/data/sync-runs/{sync_run_id}/skipped.csv` | manager/owner | Same rows as a server-built CSV (BOM'd; caveat row when incomplete) |
| GET | `/api/v1/decisions` | signed-in | Scoped decision queue; ?include_detail=true folds each card's role-projected detail in (detail failures marked detail_unavailable, never silently… |
| GET | `/api/v1/decisions/{decision_id}` | signed-in | One decision summary |
| POST | `/api/v1/decisions/{decision_id}/action` | signed-in | Record a human action: VIEW, ACT, OVERRIDE(+reason), DISMISS(+reason), ESCALATE (raises an approval request when policy says so), REOPEN (the undo) |
| GET | `/api/v1/decisions/{decision_id}/detail` | signed-in | Card detail: facts/impact/interpretation/actions/ranking |
| GET | `/api/v1/decisions/{decision_id}/trace` | signed-in | Decision → state → transitions → business event → ERP record chain; offset-paged newest-first; signal decisions return 'unavailable' text |
| GET | `/api/v1/demo` | public | Whether a demonstration workspace exists (yes/no only) |
| POST | `/api/v1/demo` | public | Sign a stranger into the read-only demo workspace (is_demo session; every unsafe method 403s) |
| GET | `/api/v1/enquiries` | manager/owner | Whole-tenant corpus export, raw text byte-intact with disposition histories; counted before load, 413 past the 50,000-line ceiling rather than… |
| POST | `/api/v1/enquiries` | signed-in | Capture one enquiry line verbatim (raw_text a bare str, no normalisation); 201, always appends; 400 CaptureRefusal for empty text or unknown channel |
| GET | `/api/v1/enquiries/channels` | signed-in | The two closed sets — InboundChannel and LineDisposition — published so no client hardcodes a copy |
| GET | `/api/v1/enquiries/{inbound_line_id}` | signed-in | One line, its live disposition (null = undecided) and the full superseded disposition history |
| POST | `/api/v1/enquiries/{inbound_line_id}/disposition` | signed-in | Decide or correct a line (supersede-not-mutate); returns written flag so re-runnable callers can tell a write from an idempotent no-op; 400 for… |
| GET | `/api/v1/entitlements` | signed-in | Plan, effective plan, trial view (incl. ended state with ended_reason), features map, loses_on_expiry, locked, pending_request |
| POST | `/api/v1/entitlements` | owner | Record a PlanChangeRequest (grants nothing; operator decides via CLI); 400 unknown plan, 409 same-plan or duplicate open request; returns whole… |
| GET | `/api/v1/identity/settings/policy` | manager/owner | Auto-link flags per kind + can_manage + the explanatory note (declared before /{kind} so it is not swallowed as kind='settings') |
| PATCH | `/api/v1/identity/settings/policy` | owner | Toggle auto_link_customers / auto_link_items |
| GET | `/api/v1/identity/{kind}` | manager/owner | Active identities with their connector records, search q, linked_only filter, pending_suggestions count, can_manage |
| POST | `/api/v1/identity/{kind}/link` | owner | Manually attach a connector record to an identity with a reason; nothing merged or copied; 400 IdentityError on bad refs/retired identity |
| GET | `/api/v1/identity/{kind}/suggestions/pending` | manager/owner | Sync-found matches waiting for a person, each with its evidence string and strategy, plus review_coverage so an empty queue says which kind of empty… |
| POST | `/api/v1/identity/{kind}/suggestions/{suggestion_id}` | owner | Accept (link) or reject (keep separate, never re-proposed) a suggestion; 400 if already decided/unknown |
| POST | `/api/v1/identity/{kind}/unlink` | owner | Split a record onto its own fresh identity; record itself unchanged |
| GET | `/api/v1/identity/{kind}/{identity_id}` | manager/owner | One identity with records and full action history; foreign/unknown → 404 |
| PATCH | `/api/v1/identity/{kind}/{identity_id}` | owner | Rename (label) an identity; empty label reverts to the derived display name |
| PUT | `/api/v1/insight/account-owners` | manager/owner | Assign an account to a salesperson (overrides the Zoho-invoice fallback) |
| DELETE | `/api/v1/insight/account-owners/{customer_id}` | manager/owner | Withdraw an assignment (falls back to Zoho's salesperson) |
| GET | `/api/v1/insight/bonds` | signed-in | Bond scores, five facets, monthly frames for playback, unscored reasons |
| GET | `/api/v1/insight/cadence` | signed-in | Day-of-month order wheel + per-customer own-cycle overdue |
| GET | `/api/v1/insight/capital` | manager/owner | Capital employed (no frontend consumer found — API-only) |
| GET | `/api/v1/insight/cash-cycle` | manager/owner | CCC decomposition (DIO/DSO/DPO) per legal entity, gaps stated |
| GET | `/api/v1/insight/cashflow` | manager/owner | Committed cash by ISO week (CashJourney) |
| GET | `/api/v1/insight/catalogue` | manager/owner | Item-line placement queue (unplaced_only toggle), resolution sources, unplaced revenue share |
| PUT | `/api/v1/insight/catalogue/{product_id}` | manager/owner | Place an item in a line (override; survives re-sync) |
| DELETE | `/api/v1/insight/catalogue/{product_id}` | manager/owner | Reset an override back to automatic sources |
| GET | `/api/v1/insight/composition` | signed-in | Revenue mix / order flow small multiples with biggest-mover |
| GET | `/api/v1/insight/credit` | signed-in | Credit limits & exposure per account with owner assignment and status vocabulary |
| PUT | `/api/v1/insight/credit-limits` | manager/owner | Set a customer's credit limit |
| DELETE | `/api/v1/insight/credit-limits/{customer_id}` | manager/owner | Clear a credit limit (back to 'none recorded', not zero) |
| GET | `/api/v1/insight/customer-financing` | manager/owner | Customer financing view (no frontend consumer found — API-only) |
| GET | `/api/v1/insight/customers/{customer_id}/timeline` | signed-in | One account's monthly revenue/orders(/margin/days-to-pay — fields omitted per role/data) |
| GET | `/api/v1/insight/daily` | manager/owner | Morning read: freshness, Needs-you / At-risk / Committed(weeks) / Moved(date-range) tile bands with routes |
| GET | `/api/v1/insight/dependency` | signed-in | Both-ends dependency: principals (spend vs downstream revenue, targets) and customers (revenue vs receivables concentration) |
| GET | `/api/v1/insight/entity-routing` | manager/owner | Which-entity determinants per connected company, no recommendation (no frontend consumer found) |
| GET | `/api/v1/insight/gmroi` | manager/owner | GMROI per SKU and brand over the actually-covered window, with per-row refusal reasons |
| GET | `/api/v1/insight/journey` | signed-in | Monthly customer-state bands with per-band member lists and dormant customers |
| GET | `/api/v1/insight/landscape` | manager/owner | Quadrant scatter (customer×item or item; margin or momentum vertical) |
| GET | `/api/v1/insight/lost-revenue` | manager/owner | Lost revenue decomposed by cause incl. UNEXPLAINED bucket |
| GET | `/api/v1/insight/migration` | signed-in | Period-over-period size-band migration matrix with named movers |
| GET | `/api/v1/insight/mix` | signed-in | Customer × line/principal BUYS/LAPSED/NEVER grid with affinity; server-side company scope |
| GET | `/api/v1/insight/msme-capture-backlog` | manager/owner | Which suppliers are worth establishing status for, ranked |
| PUT | `/api/v1/insight/msme-status` | manager/owner | Record a supplier's established MSME position (no writer on the Statutory screen itself) |
| GET | `/api/v1/insight/msme-watchlist` | manager/owner | Bills near/past the MSME 45-day cliff, with basis and amount at risk |
| POST | `/api/v1/insight/negotiate` | signed-in | Deterministic deal arithmetic against the floor: contribution, CAF, collection factor, free-to-give, hold price, warnings, third-party legality |
| GET | `/api/v1/insight/opportunities` | manager/owner | Opportunity radar: money at stake × evidence confidence |
| GET | `/api/v1/insight/order-to-cash` | signed-in | Order→invoice→payment cycle by stage with unknown reasons and measurement rule |
| GET | `/api/v1/insight/pass-through` | manager/owner | Cost pass-through pricing view (no frontend consumer found — API-only) |
| GET | `/api/v1/insight/payables` | manager/owner | Same measurement from the payable side (vendors) |
| GET | `/api/v1/insight/payments` | signed-in | Receivable settlement behaviour: distribution, patterns, slowest payers, per-owner books |
| GET | `/api/v1/insight/quote-outcomes` | signed-in | Win/loss analysis: rates by customer/principal/product-line/month, loss-reason mix with reason_catalogue and owner mapping, decided quotes, awaiting… |
| GET | `/api/v1/insight/quote-pricing` | manager/owner | Losing vs winning median price per item/quantity band, margin on wins vs losses, gap vs the customer's own price history — the margin behind the… |
| GET | `/api/v1/insight/revenue-flow` | signed-in | Revenue waterfall buckets with top movers for drill |
| GET | `/api/v1/insight/schemes` | manager/owner | Target wall: progress vs pace, secured/at-stake scheme value, projection or refusal |
| GET | `/api/v1/insight/self-funding` | owner | Retained PAT vs revenue growth per entity; blocked until each entity's figure is confirmed |
| POST | `/api/v1/insight/simulate` | manager/owner | Run PRICE_CHANGE / MARGIN_FLOOR scenario; deterministic, same inputs same answer |
| GET | `/api/v1/insight/simulate/scenarios` | manager/owner | Which scenarios can run, and which are blocked on data with reasons |
| GET | `/api/v1/insight/stock` | signed-in | Shelf: KPIs, server-defined filters, per-item drain/health/buyers, operational groups, unavailable list |
| GET | `/api/v1/insight/storyboard` | signed-in | Home briefing beats: what changed / why / where to go |
| GET | `/api/v1/insight/supply` | manager/owner | Supplier concentration, lead times/credit rates/price spreads where evidenced, open POs |
| GET | `/api/v1/insight/targets` | manager/owner | List principal targets |
| PUT | `/api/v1/insight/targets` | manager/owner | Create/update a principal target with scheme slabs |
| DELETE | `/api/v1/insight/targets/{target_id}` | manager/owner | Delete a target |
| POST | `/api/v1/insight/tenders` | manager/owner | Record a tender result observation (no frontend consumer found — API-only) |
| GET | `/api/v1/insight/unrecorded-quotes` | signed-in | Ranked worklist of ERP quotes with no recorded outcome: three server-published groups (PAST_EXPIRY / EXPIRY_NOT_RECORDED / STILL_OPEN), totals over… |
| GET | `/api/v1/insight/vendor-terms` | manager/owner | Agreed payment terms beside Zoho's, with schedule shift |
| PUT | `/api/v1/insight/vendor-terms` | manager/owner | Record/replace one supplier's agreed term (days + basis + note) |
| DELETE | `/api/v1/insight/vendor-terms/{vendor_id}` | manager/owner | Clear an agreed term (back to ERP dates) |
| GET | `/api/v1/insight/wallet/{customer_id}` | signed-in | Share of wallet for one customer (no frontend consumer found — API-only) |
| GET | `/api/v1/insight/weather` | manager/owner | Weather fronts with band + drill_to destination |
| GET | `/api/v1/insight/withholding-crossings` | manager/owner | 194Q threshold crossings; silent until the org's turnover gate is confirmed in Settings |
| GET | `/api/v1/internal/ai-metrics` | owner | 7-day AI telemetry (calls, cost, latency, degraded/failed rates, failure reasons, health band) — feeds the AI layer panel |
| GET | `/api/v1/internal/ai-readiness` | owner | Which provider would really run, next-run call count and cost estimate — feeds the AI layer panel |
| POST | `/api/v1/internal/decisions/generate` | manager/owner +plan | Turn latest signals into persisted decisions via the AI layer |
| POST | `/api/v1/internal/demo-seed` | manager/owner | Seed the demo dataset and run the pipeline (dev/demo only) |
| GET | `/api/v1/internal/detector-outcomes` | owner | Per-detector signal volume vs what humans did with it (dismissal rates) |
| POST | `/api/v1/internal/detectors/run` | manager/owner +plan | Run the deterministic Signal Engine; 403 PlanRefused without the plan |
| GET | `/api/v1/internal/health` | public | Liveness: SELECT 1 + zoho_source; no migration awareness |
| GET | `/api/v1/internal/observability/api-performance` | manager/owner | This worker's API counters since process start (window_minutes parameter removed as a lie) |
| GET | `/api/v1/internal/observability/caches` | manager/owner | In-process cache stats for this replica |
| GET | `/api/v1/internal/observability/capacity` | manager/owner | Capacity analysis and safe headroom |
| GET | `/api/v1/internal/observability/dashboard` | manager/owner | Full ops dashboard payload: health, load, api, database, jobs, syncs, capacity, tenants (tenant scope reported as OWN_ORGANIZATION vs… |
| GET | `/api/v1/internal/observability/database` | manager/owner | Database performance/status metrics |
| GET | `/api/v1/internal/observability/health` | manager/owner | Component health statuses |
| GET | `/api/v1/internal/observability/jobs` | manager/owner | Background job status (the ERP sync is the only recorded kind, stated in job_kinds) |
| GET | `/api/v1/internal/observability/metrics` | manager/owner | One worker's raw metric counters, with scope/worker/composition fields stating how to combine workers |
| GET | `/api/v1/internal/observability/prometheus` | scrape token | Prometheus text exposition of one worker's process-level counters; no DB touch, no tenant or margin data |
| GET | `/api/v1/internal/observability/syncs` | manager/owner | ERP sync status: active by connection, 24h outcomes, throughput (null = unknown, never 0), issues |
| GET | `/api/v1/internal/observability/tenants` | manager/owner | Signal counts per organization with an honest scope field |
| GET | `/api/v1/internal/queue` | manager/owner | Org-scoped queue state: dispatch mode, scheduler lease holder, worker_running, depth, recent, dead_letters (payloads omitted) |
| GET | `/api/v1/internal/queue-adoption` | owner | Whether the decision queue is worked; per-user acceptance; includes cost-derived value-at-risk (hence owner-only) |
| GET | `/api/v1/internal/queue/{message_id}` | manager/owner | One queued message with its payload |
| POST | `/api/v1/internal/queue/{message_id}/retry` | owner | Requeue a dead-lettered message; 409 if not actually failed |
| POST | `/api/v1/internal/sync/zoho` | manager/owner | Legacy synchronous inline sync; returns the report in the response |
| GET | `/api/v1/internal/zoho/check` | manager/owner | Verify Zoho credentials without pulling data; distinguishes no-connection / wrong DC / bad token / invisible org |
| GET | `/api/v1/onboarding` | signed-in | Setup checklist: connect (required), history (required), policy, team — derived from real rows on every request, no stored flag |
| GET | `/api/v1/organizations` | signed-in | Workspaces this identity may open, with role in each, plus current — feeds the switcher |
| GET | `/api/v1/organizations/current` | signed-in | Current workspace identity: name, currency, timezone, country, caller's role |
| POST | `/api/v1/organizations/{organization_id}/switch` | signed-in | Open a NEW session against another workspace (old session untouched); returns token, org, name, role |
| GET | `/api/v1/outcomes` | signed-in | Every visible decision-outcome snapshot with its evaluation (PENDING/REALISED/UNKNOWN) computed on read; optional validated status_filter (400 on… |
| POST | `/api/v1/quote-intelligence/assess` | signed-in | Whole-quote deterministic assessment: per-line references, exceptions, economics (mgmt), quantity bands, outcome echo; walk-guards: as_of ±90d, ≤200… |
| POST | `/api/v1/quote-intelligence/outcome` | signed-in | The one writer moving a quote along DRAFT→SENT→WON/LOST, by platform quote_id or ERP quote_document_ref; 422 LOST-without-reason (names choices) or… |
| GET | `/api/v1/quote-intelligence/quotes/{quote_id}` | signed-in | Full audit trail: outcome row + every snapshot ever recorded, oldest first |
| POST | `/api/v1/quote-intelligence/snapshot` | signed-in | Freeze the re-derived assessment (with optional override reason/code) into the append-only quote_decisions trail; releases no-longer-needed… |
| GET | `/api/v1/quote-intelligence/thresholds` | signed-in | The pricing policy in force: band edges + tolerance for everyone; target/min/floor margins for managers/owners |
| POST | `/api/v1/quote-support` | signed-in | QUOTE_CONTEXT decision support: deterministic facts + separated AI recommendation; persists a decision for later accept/modify/reject |
| GET | `/api/v1/quotes` | signed-in | The workspace: every draft in the organization with number, customer, line count, selling total, who started/changed it, and the send gate's readiness |
| POST | `/api/v1/quotes` | signed-in | Create a draft — customer optional and empty by default; number minted from the org's sequence (QB-0001…); stamped with the principal's organization_id |
| DELETE | `/api/v1/quotes/{quote_id}` | signed-in | Remove an unsent draft; 409 once a document has been written for it |
| GET | `/api/v1/quotes/{quote_id}` | signed-in | Read one quote, serialized via Quote.to_dict(mgmt) — economics/marginFloor/MFLOOR absent for a salesperson; 'estimate' block joined from the… |
| PUT | `/api/v1/quotes/{quote_id}/customer` | signed-in | Say who the quote is for, or change it; lines already on it are re-resolved under the customer's identity scope, typed prices kept where the product is unchanged |
| POST | `/api/v1/quotes/{quote_id}/discount` | signed-in | Apply a percentage discount to selected line ids, off the current quoted rate; returns 'applied' count |
| POST | `/api/v1/quotes/{quote_id}/estimate` | signed-in | The send: blocker/unpriced refusals naming lines, assess_and_record snapshot, quote_submission_block (incl. screen's below-floor lines), fingerprint… |
| POST | `/api/v1/quotes/{quote_id}/intake` | signed-in | Paste RFQ text: AI reading with regex fallback, per-line pie-parser resolution + Zoho enrichment, AI_CALL audit, optional enquiry-corpus capture… |
| DELETE | `/api/v1/quotes/{quote_id}/lines/{line_id}` | signed-in | Remove a line from the quote |
| POST | `/api/v1/quotes/{quote_id}/lines/{line_id}/confirm-reading` | signed-in | Clear the proposed flag on one AI/heuristic-read line — one at a time by design |
| POST | `/api/v1/quotes/{quote_id}/lines/{line_id}/create-item` | signed-in | Create the supply product in the books; failure returns createItemError and leaves the line CREATE FAILED |
| GET | `/api/v1/quotes/{quote_id}/lines/{line_id}/options` | signed-in | Ranked supply candidates for one line (the supply drawer's data) |
| POST | `/api/v1/quotes/{quote_id}/lines/{line_id}/price` | signed-in | Set/clear the quoted rate on one line (priceSource USER/LIST) |
| POST | `/api/v1/quotes/{quote_id}/lines/{line_id}/supply` | signed-in | Select/revert a supply product; runs the identity-confirmation gate (confirm_proposed_identity) and re-enriches the line; note in response when a… |
| POST | `/api/v1/resolve` | api-key | Resolve one enquiry line (≤512 chars) into the public provenance document; 200 for answers and evidence-bearing abstentions… |
| POST | `/api/v1/resolve/confirm` | api-key | Confirm a customer's code → record mapping through the same confirm_proposed_identity gate as the Quote Builder; line re-resolved server-side; every… |
| GET | `/api/v1/resolve/openapi.json` | public | Generated OpenAPI contract for the resolution routes; shapes only, built once per process |
| GET | `/api/v1/retrospective` | manager/owner +plan | First-run look-back: verdict (UNEXAMINED/PARTIAL/EXAMINED), history coverage, per-detector judged/withheld/found |
| POST | `/api/v1/contact` | public | Record an enquiry from the landing page's form — name, company, email, ERP, the plan they were reading about; creates nothing and licenses nothing (202) |
| GET | `/api/v1/signup` | public | Whether self-serve sign-up is offered; trial length, landing plan, plan ladder for the form |
| POST | `/api/v1/signup` | public | Create organization + owner + trial, record requested plan (never granted), sign the owner straight in (login envelope, 201) |
| GET | `/api/v1/trust/access` | owner | Every break-glass grant, per-use access, and revocation against this tenant — no filter, no suppression |
| GET | `/api/v1/trust/audit` | owner | Hash-chained audit entries newest first, with a whole-chain verification verdict and action counts; ?action filter |
| GET | `/api/v1/trust/audit/export` | owner | The whole chain as JSON (with method statement) or CSV attachment, independently re-verifiable |
| GET | `/api/v1/trust/audit/verify` | owner | Walk the chain; report the first break or a clean bill — the pollable incident check |
| GET | `/api/v1/trust/disclosure` | owner | The published what-reaches-a-model statement, served from the same ALLOWED/NEVER constants the payload checker enforces |
| GET | `/api/v1/trust/erasure` | owner | Erasure status + key_destroyed flag + the signed receipt (signature re-verified on every read) when erased |
| POST | `/api/v1/trust/erasure` | owner | Destroy the tenant data key and issue a signed receipt listing both what died and what survives in plaintext; irreversible, idempotent if already… |
| GET | `/api/v1/trust/export` | owner | Everything the organization owns as JSON, including the cross-connector identity graph no single source system holds |
| GET | `/api/v1/trust/payloads` | owner | Every model payload logged for this org with findings summary; ?reveal=true decrypts the actual sent text for its owner |
