# Role review — Sales manager

**Role tested:** `SALES_MANAGER` · `m.rao@pie.example` · user id `usr_manager`
**Reviewed:** 2026-08-08 · commit `9f9b7e8`
**Method:** real browser (Chromium via Playwright) against `localhost:5173`, with the
API at `localhost:8000` used to probe boundaries and to recompute figures.
Screenshots under `/tmp/shots/` (not committed) are cited per finding.

> **Credential note.** `must_change_password` was true on first sign-in. I changed
> the password through Settings → Your account. The value it was set to is not
> recorded here and has since been rotated again. The salesperson account
> `r.nair@pie.example` was left on `change-me-now`; it was used only to raise
> approval requests for me to act on.


> ## Status — read this before acting on anything below
>
> **This is a point-in-time record, not an open bug list.** It was taken against
> build `9f9b7e8`-era `main` and every finding below is preserved exactly as
> written, including the ones that are now fixed. Nothing here has been edited to
> match what the code does today, because a review rewritten after the fact stops
> being evidence of what a role actually experienced. The one exception is that
> passwords the reviewer set have been redacted — they are credentials, and they
> were rotated again during the fix work, so printing them was both unsafe and
> wrong. The seed default `change-me-now` stays, because it is documented in the
> README and is part of every reproduction below.
>
> The findings were triaged into five change slices, whose commits carry the
> reasoning. The table below is the disposition of every finding in this
> document, and the **Open** rows are the only ones still true.
>
> | Finding | Disposition |
> |---|---|
> | 1 · weather divides costed profit by uncosted revenue | **Fixed** — one `aggregate_margin`, Σ profit ÷ Σ costed revenue |
> | 2 · "Approvals waiting" counted three ways | **Fixed** — one role-scoped `pending_count` |
> | 3 · forced first-sign-in change does not exist | **Fixed** |
> | 4 · unparsed RFQ quantity silently becomes 1 | **Fixed** — all five shapes in the table above now parse; a stated-but-unread quantity is flagged, not defaulted. Fixing it also closed an unreported hazard in the same rule: `DNMG 150608` was read as code `DNMG` at quantity 150,608 |
> | 5 · no team surface, attribution never rendered | **Partly, by decision** — attribution now renders on the grid and the card and the page is retitled; the per-rep roll-up is deliberately deferred while there is one salesperson to roll up |
> | 6 · `can_decide` ignores the self-approval rule | **Fixed** — one `refusal_for`, used by the check and the button |
> | 7 · product mix's empty reasons on the wrong conditions | **Fixed** |
> | 8 · Supply/Cash/How-we-pay blame a sync that is not missing | **Fixed** — `_no_data` names what is actually absent |
> | 9 · one margin printed 17.3% and 17.4% | **Fixed** — rounded once, before formatting |
> | 10 · Opportunities names a control the manager lacks | **Fixed** — "Ask an owner to lower the floor" for a manager, matching the Data screen's phrasing. (This table said Open in its first version; that was a bad search on my part, not a missing fix.) |
> | 11 · `thresholds_version` absent from most manager payloads | **Fixed** — `_envelope` takes the thresholds object rather than a currency string, so every insight response is stamped and a new endpoint cannot omit it. The two `commercial/` payloads the finding singles out are stamped too, each with the version that actually judged its numbers |
> | 12 · decision-support sends a UI placeholder as a product id | **Already fixed** on `main` in #47 — `rel.productRef` |
> | 13 · `ui-standards.md` raw-`<table>` enumeration stale | **Fixed** — the census became a rule |

---

## 1. Verdict

A sales manager can do the two things this role exists for, and both are built
properly: the approval queue enforces real authority — below-cost escalates to the
owner, self-approval is refused, and an approval granted at ₹380 provably cannot be
spent at ₹250 — and the economics surfaces carry cost, margin and purchase rate with
arithmetic I recomputed and found exact, including the `Σ gross_profit ÷ Σ revenue`
rule that a mean-of-lines shortcut would have missed by 0.0008. Every owner-only
write I attempted was refused with a 403; there is no path from this role to widening
its own authority. Against that, the manager's own landing page cannot count its own
queue — "Approvals waiting" says 3, the nav badge says 2, and exactly 1 is actually
decidable — and the Commercial weather screen reports the book's margin as 7.8% and
bands it POOR by dividing profit earned on costed revenue by *all* revenue, when the
honest figure over the relationships that have cost is 19.7%, comfortably above the
15% review floor it is being judged against. The largest gap is not a bug: there is
no team surface at all. `assigned_user_id` is carried on the wire and rendered on no
screen, so a manager cannot see who covers which account or who a decision belongs
to — on a page called "Team focus". **Today a manager can run the approval queue and
argue a price with confidence; they cannot run a team, and they should not trust the
weather screen's margin band.**

---

## 2. What was tested

**Setup.** Followed the recipe verbatim: `PIE_PARSER_ROOT=/home/user/pie-parser`,
both requirements files, `build_catalog.py` (6717 products), `python3 -m app.bootstrap`,
uvicorn on 8000, Vite on 5173. `/api/health` returned `migration.state == CURRENT` at
head `b2d95e11c74a`. The repository was not present in the container and was cloned
fresh from `srisankethu/pie-portal`; the `./pie-parser` submodule directory was empty
as described and the sibling clone was used.

**Screens — 29 routes, every entry in `frontend/src/platform/route.ts` plus both
parameterised patterns.** All 29 rendered. No page threw, no route 403'd, and no
screen made a failing API call. Walked with a console/pageerror listener and a
response listener attached per route (`/tmp/shots/00-…` through `28-settings.png`):

| Group | Routes |
|---|---|
| Decide | `/` `/decisions` `/decision/:id` `/negotiate` `/simulate` `/quotes` `/approvals` |
| Understand | `/weather` `/opportunities` `/lost-revenue` `/landscape` `/composition` `/cadence` `/bonds` `/mix` `/dependency` `/targets` |
| The book | `/customers` `/account/cst_rane` `/account/cst_brakes/item/prd_cnmg` `/journey` `/stock` `/supply` `/payments` `/payables` |
| Setup | `/item-lines` `/data` `/identity` `/states` `/settings` |

**Endpoints.** 59 GET paths from `/openapi.json` (99 paths total) exercised with a
manager token: **45 → 200**, 8 → 403 (all correctly owner-only), 6 → 404 (five
data-shaped, one my own bad path parameter). 15 write endpoints probed for authority
(§3). Plus the working calls: `me/password`, `approvals/quote-line`,
`approvals/{id}/decide` ×5, `commercial/recompute`, `decisions/{id}/action`,
`insight/simulate`, `insight/negotiate`, `quote-intelligence/assess`, `snapshot` ×2,
and the `/api/v1/quotes` intake → options → supply → price chain.

**Approval lifecycle.** 4 requests raised (3 by the salesperson, 1 by me), 1 approved,
3 denial paths exercised, plus the send gate.

**Number integrity.** 3 screen figures recomputed independently from `sales_txns`,
`cost_records` and `customer_item_metrics` in the SQLite read model (§4).

**Not driven by hand:** `/simulate` and `/negotiate` were exercised through their
POST endpoints rather than by completing the on-screen forms (the Negotiation desk's
customer/item Autocomplete did not accept a scripted selection). Both screens rendered
and both correctly refused to compute without their inputs.

---

## 3. Authority boundaries — what the server actually did

Every attempt below was made with a live `SALES_MANAGER` bearer token. **No 200
appeared anywhere it should not have.**

### Policy, thresholds, users, roles — all refused

| Attempt | Status | Body |
|---|---|---|
| `PATCH /api/v1/admin/policy {"allow_self_approval":true}` | **403** | `{"detail":"Owner role required"}` |
| `PATCH /api/v1/admin/policy {"below_cost_requires_owner":false}` | **403** | `{"detail":"Owner role required"}` |
| `PATCH /api/v1/admin/margin-policy {"approval_floor":0.01}` | **403** | `{"detail":"Owner role required"}` |
| `PATCH /api/v1/admin/margin-policy {"target_margin":0.05}` | **403** | `{"detail":"Owner role required"}` |
| `POST /api/v1/admin/users {…,"role":"OWNER"}` | **403** | `{"detail":"Owner role required"}` |
| `PATCH /api/v1/admin/users/usr_manager {"role":"OWNER"}` | **403** | `{"detail":"Owner role required"}` |
| `PATCH /api/v1/admin/users/usr_sales {"role":"SALES_MANAGER"}` | **403** | `{"detail":"Owner role required"}` |
| `POST /api/v1/admin/users/usr_sales/reset-password` | **403** | `{"detail":"Owner role required"}` |
| `PATCH /api/v1/identity/settings/policy` | **403** | `{"detail":"Owner role required"}` |
| `POST /api/v1/identity/customer/link` | **403** | `{"detail":"Owner role required"}` |
| `POST /api/v1/trust/erasure` | **403** | `{"detail":"Owner role required"}` |
| `POST /api/v1/connections` | **403** | `{"detail":"Owner role required"}` |
| `PUT /api/v1/data/connection` · `DELETE /api/v1/data/connection` | **403** | `{"detail":"Owner role required"}` |
| `GET /api/v1/internal/ai-metrics` | **403** | `{"detail":"Owner role required"}` |
| `GET /api/v1/trust/{access,disclosure,export,payloads}` | **403** | `{"detail":"Owner role required"}` |

The two deliberate reads work as designed: `GET /api/v1/admin/policy` returns 200 with
`"can_manage": false`, and the **Settings screen is reachable by URL and by nav for a
manager, correctly, in read-only form** — every approval-policy checkbox and every
margin-policy input carries `disabled=true` in the DOM (verified by evaluating
`input.disabled` across the screen; `/tmp/shots/03-settings-manager.png`). The screen
says so in as many words: *"Owner only — a manager who could widen their own authority
would not have any."* This is the right answer to "should `/settings` be owner-only?" —
it is not hidden, it is inert, and the manager can read the policy their prices are
judged against.

### Below-cost requires the owner — holds

Salesperson raised ₹300 on an item whose effective cost is ₹349:

```
POST /api/v1/approvals/quote-line  → 201   required_authority: "OWNER"   below_cost: true
POST /api/v1/approvals/{id}/decide {"status":"APPROVED"}  (manager token)
  → 403 {"detail":"Selling below what the item cost us is the owner's decision"}
```

It is also correctly kept out of the manager's actionable queue: `GET /api/v1/approvals`
returned only the MANAGER-authority ids. The manager can still *read* it by direct id
(200) — right, since they may need to know why a quote is blocked.

### A manager cannot approve their own request — holds

I raised a thin-price request as the manager, then tried all three approver decisions:

```
POST /api/v1/approvals/{id}/decide {"status":"APPROVED"}         → 403 "You cannot approve your own request"
POST /api/v1/approvals/{id}/decide {"status":"REJECTED"}         → 403 "You cannot approve your own request"
POST /api/v1/approvals/{id}/decide {"status":"CHANGES_REQUESTED"}→ 403 "You cannot approve your own request"
```

Clicking **Approve** on that card in the real UI produced the same 403 and the message
was shown to the user (`/tmp/shots/32-self-approve-attempt.png`). The button is offered
anyway — see Finding 6.

### Snapshot semantics — holds, and the gate holds with it

The strongest single result in this review. Salesperson snapshots a line at ₹380 and
raises the request; manager opens it; salesperson re-prices the same line to ₹250;
manager re-reads the *same* request:

```
C) manager opens it.        subject.quoted_unit_price = 380.0   margin = 0.081579
D) salesperson re-prices the same line to 250          → 201
E) manager re-reads it.     subject.quoted_unit_price = 380.0   margin = 0.081579   FROZEN
F) manager approves at the frozen 380 → 200  decided_by "M. Rao"  decided_at 2026-08-08T16:29:30Z
G) GET /api/v1/approvals/quotes/{id}/gate
   can_submit: false
   blocked_reason: "1 line(s) need approval before this quote can be sent:
                    CNMG 120408-MP insert (price changed since approval)."
```

The request holds a copy, and the approval granted at ₹380 provably cannot be spent at
₹250. What is captured on approval: status, `decided_by_user_id`, `decided_at`, the
decision note, and an append-only `thread` carrying both the REQUESTED and APPROVED
entries with actor id, name, timestamp and note.

### Role separation, spot-checked

`GET /api/v1/decisions` — manager sees **5** (including the 3 restricted-type ones and
the 2 assigned to the salesperson); salesperson sees **2**, types
`CUSTOMER_DECLINE, CUSTOMER_DORMANCY` only. `GET /api/v1/approvals/{id}` returns the
`subject` block (cost, margin, gross profit) to the manager and **omits the key entirely**
for the salesperson who raised it — absent, not masked, exactly as `CLAUDE.md` requires.
`POST /insight/simulate` and `GET /insight/weather` both 403 for a salesperson.

---

## 4. Number integrity

### The three figures I recomputed

**(1) Storyboard / Weather headline — "₹1,02,449 less than the period before (55.8%)"**
Recomputed by summing `sales_txns.line_revenue` over the two windows the API names:

```
current  2026-05-01..2026-07-31 = 81,291     (API current_total  81,291.0)   MATCH
previous 2026-02-01..2026-04-30 = 183,740    (API previous_total 183,740.0)  MATCH
delta = -102,449                             (API delta -102,449.0)          MATCH
pct   = -0.5575759…                          (API pct -0.5576, shown 55.8%)  MATCH
```

The waterfall buckets also reconcile: −64,800 − 37,829 + 180 = −102,449, and the payload
says so itself (`reconciles: true`).

**(2) Aggregated margin — Brakes India × CNMG 120408-MP, `margin_12m`**
This is the decisive test of the `Σ gross_profit ÷ Σ revenue` invariant. Recomputed from
the six raw `sales_txns` lines against the cost record effective on each line's date:

```
Σ revenue = 74,340   Σ gross_profit = 14,130
Σgp / Σrev             = 0.1900726392251816   ← stored margin_12m = 0.1900726392251816  MATCH
mean of per-line margins = 0.1901529008958304   ← does NOT match stored
```

The two candidates differ in the fourth decimal, so this is a real discriminator, not a
tautology: the platform aggregates correctly and does **not** take the mean. `current_margin`
(0.17351874) is the same formula over the recent window, and its `current_effective_cost`
of ₹341.75 is the quantity-weighted blend `(30×320 + 90×349) / 120` — verified. All
figures are ratios, not percentages. `margin_change_pp` stores the ratio difference
(−0.04978) and the frontend `pp()` helper renders it `(v*100).toFixed(1)` → **−5.0 pp**,
which is the correct percentage-point reading.

**(3) Commercial weather — "Margin 7.8%, below the 15% review floor" — DISAGREES**
This one does not survive. See Finding 1. The reported value 0.07776709890822876 is
`Σ gross_profit_12m ÷ Σ revenue_12m` over **all** relationships, including two whose
gross profit is `NULL` because they have no cost data at all — they contribute ₹199,331
of revenue to the denominator and nothing to the numerator.

```
all 4 relationships     : 25,650 / 329,831 = 0.0777670989082288   ← what the screen shows
the 2 with cost data    : 25,650 / 130,500 = 0.1965517241379310   ← the honest figure
```

### `thresholds_version`

**Persisted computed rows: 100% stamped.** Every table that carries the column has no
blanks and one consistent value:

| Table | Rows | Missing | Version |
|---|---|---|---|
| `approval_requests` | 4 | 0 | `ci_f7de764806` |
| `quote_decisions` | 2 | 0 | `ci_f7de764806` |
| `customer_item_metrics` | 4 | 0 | `ci_f7de764806` |
| `signals` | 12 | 0 | `th_4bca9e0a59` (as `threshold_config_version`) |

Two version namespaces coexist — `ci_` for the commercial/quote thresholds and `th_` for
the detector thresholds. That reads as deliberate (two threshold objects), not as drift.
The `decisions` table carries no version column of its own, but every decision links
`signal_ids`, so its provenance is reachable in one hop.

**Read API: 8 of 26 manager-facing computed payloads carry a version** — see Finding 11.
Notably `POST /insight/simulate` and `POST /insight/negotiate` both carry
`thresholds_version`, while `GET /insight/weather`, which explicitly bands margin against
`th.margin_floor`, does not.

---

## 5. Findings

### 1 · MAJOR · Weather reports the book's margin as 7.8% POOR by dividing costed profit by uncosted revenue — **CONFIRMED**

**Where:** `#/weather` · `GET /api/v1/insight/weather` · `backend/app/routers/insight.py:236-239`
**Evidence:** `/tmp/shots/52-weather.png`

The screen shows, three lines apart:

> **Margin · POOR** — 7.8%, below the 15% review floor
> *Aggregated as total gross profit over total revenue, not as an average of per-line margins.*
>
> **Evidence quality · FAIR** — 50% of relationships have usable cost data
> *Margin is not asserted for a relationship without enough cost coverage, so this bounds everything else on this page.*

The second statement is false of the first. The computation is:

```python
revenue = sum(float(r.revenue_12m or 0) for r in rows)      # ALL relationships
profit  = sum(float(r.gross_profit_12m or 0) for r in rows) # NULL profit → 0
margin_now = (profit / revenue) if revenue else None
```

Two of the four relationships have `gross_profit_12m = NULL` (`cost_missing_txns` 6 and 8,
`cost_covered_txns` 0). They are coerced to zero profit while their full ₹199,331 of
revenue stays in the denominator.

**Expected:** the same treatment the rest of the codebase already uses. `POST /insight/simulate`
computes its baseline over the costed relationships only — `revenue 75,420`, `gross_profit 12,090`,
`margin 0.1603` — where 75,420 is exactly the recent revenue of the two costed relationships.
Applied to the 12-month figures the honest answer is **19.7%**.

**Why it matters:** 19.7% is *above* the 15% review floor. The band flips **POOR → GOOD**.
A manager reading this page concludes the book is priced below its own review floor when
the evidence says the opposite, and the same manager has two other manager screens
(`/simulate` at 16.0%, the customer-item screen at 17.3%) quietly disagreeing with it.
This is precisely the failure mode `docs/architecture.md` says the platform exists to
prevent — *"a detector that would depend on a flagged fact withholds its signal rather
than asserting on bad data."* Withholding (the `UNKNOWN` band the builder already
supports when `margin_now is None`) would be the correct outcome here, or restricting
both sums to rows with cost.

---

### 2 · MAJOR · "Approvals waiting" is counted three different ways on one role's screens — **CONFIRMED**

**Where:** `#/` tile vs nav badge vs `#/approvals` · `backend/app/routers/insight.py:2406`
**Evidence:** `/tmp/shots/50-storyboard-tiles.png`, `/tmp/shots/31-approvals.png`

With four requests in the org (three PENDING), the manager is shown:

| Surface | Says | Source |
|---|---|---|
| Storyboard tile "Approvals waiting" | **3** | org-wide `count(*) where status='PENDING'` — no role scoping at all |
| Nav badge on "Approvals" | **2** | `pending_for_me` |
| `#/approvals` — "2 waiting on you" | **2** | `pending_for_me` |
| Actually decidable by this manager | **1** | the salesperson's thin-price request |

The tile counts the OWNER-authority below-cost request that `approvals.inbox` deliberately
keeps out of a manager's queue — so the tile's "Work through these →" lands on a screen
where the third item does not exist and cannot be made to appear. The remaining gap
between 2 and 1 is Finding 6: `pending_for_me` counts the manager's own request, which
they can never decide.

**Expected:** one number, role-scoped, matching what the queue will actually show. The
storyboard tile should read `approvals.pending_count(session, principal)` rather than an
unscoped `count(*)`.

---

### 3 · MAJOR · The forced first-sign-in password change does not exist — **CONFIRMED**

**Where:** `POST /api/v1/auth/login` · `backend/app/routers/platform_auth.py:88-94` ·
`frontend/src/platform/PlatformApp.tsx`
**Evidence:** `/tmp/shots/02-after-signin.png`

Login returns `must_change_password: true` **and a fully working token in the same
response**. I used that token before changing anything:

```
POST /api/v1/auth/login {"email":"m.rao@pie.example","password":"change-me-now"}
  → 200  must_change_password: true
GET  /api/v1/decisions  (that token)
  → 200  [full decision list]
```

There is no server-side gate: `authz.load_principal` never reads the flag. There is no
client-side flow either — signing in with `change-me-now` in the browser went straight to
the Team focus storyboard with no interstitial, and `grep -rn "must_change_password" src/`
finds it in exactly three places, none of which is a route guard:

```
src/platform/api.ts:93           type declaration
src/platform/types.ts:562        type declaration
src/platform/AdminScreens.tsx:944  renders the string "must change" in the users grid
```

The flag is not even persisted into the browser session — `pie_platform_session` in
`localStorage` has no such key. The only way to change your own password is to navigate to
`#/settings` and find the form, which nothing prompts you to do. All three seeded accounts
still showed status **"must change"** on the Settings users grid.

**Expected:** either the server refuses non-`/auth` and non-`me/password` calls while the
flag is set, or the client blocks on a change-password screen. Today "temporary password"
means "permanent password with a badge next to it", including for every account an owner
creates via `reset-password`.

**Not rated BLOCKER because:** it grants no privilege the account does not already have,
and every authority boundary in §3 held. It is an account-hygiene failure, not an
escalation path.

---

### 4 · MAJOR · An unparsed RFQ quantity silently becomes 1, and the quotation total follows it — **CONFIRMED**

**Where:** Quote Builder `#/quotes` · `POST /api/v1/quotes/{id}/intake` · `backend/app/store.py:59-82`
**Evidence:** `/tmp/shots/42-quote-lines.png`

`_split_rfq` matches a trailing bare number. A number followed by a unit word, or a leading
quantity, falls through to the default of 1 with no flag and no note. One intake, five lines:

| RFQ line | `reqQty` |
|---|---|
| `CNMG 120408 TN2000, 100` | **100** ✓ |
| `CNMG 120408 TN2000 x100` | **100** ✓ |
| `CNMG 120408 TN2000 - 100 nos` | **1** ✗ |
| `CNMG 120408-MP insert 100 nos` | **1** ✗ |
| `100 nos CNMG 120408 TN2000` | **1** ✗ |

Priced end to end, the consequence is a quotation out by the quantity factor:

```
RFQ:  "CNMG 120408 TN2000 - 100 nos"   → resolved READY, priced at ₹414
summary: {"subtotal": 414.0, "tax": 74.52, "grand": 488.52}      ← one unit, not a hundred
```

It also feeds the approval a manager signs: assessing the same line at qty 1 versus qty 100
selects band `1` versus band `51–200`, and the quantity band is what decides which floor
applies.

**Scope qualification:** intake reported `{"read_by":"pattern","detail":"no AI provider
configured"}` — `AI_PROVIDER=mock`, so the regex fallback is what ran. This is the path a
fresh clone and this review both run on; a configured Anthropic reader may well handle
"100 nos". The finding is against the offline path, and the silent default (rather than a
flag on the line) is the part that is wrong regardless of which reader ran.

---

### 5 · MAJOR · There is no team surface, and account attribution is never rendered — **CONFIRMED (gap, not a bug)**

**Where:** every screen · `frontend/src/platform/types.ts:23,143,203`
**Evidence:** `/tmp/shots/16-customers.png`, `/tmp/shots/02-after-signin.png`

`assigned_user_id` is on the wire on accounts, decisions and approvals — `GET /api/v1/accounts`
returns it for all five customers — and is typed in the frontend in three places. It is
rendered **nowhere**: `grep -rn "assigned_user_id" src/` returns only the three type
declarations, and the Customers grid columns are Customer / Last order / Orders (12m) /
Value (12m) / Needs you. There is no per-salesperson view, no per-rep roll-up, and no
"assigned to" column on any list. The three manager-routed decisions carry
`assigned_user_id: null` with `assigned_role: SALES_MANAGER`, so they are routed to a role
and belong to no one in particular.

The manager's landing page is titled **"Team focus — Where the team's attention is worth
spending"**, and contains no view of the team.

**Expected for this role:** at minimum an owner column on Customers and an assignee on the
decision card; realistically, one surface that answers "how is each of my people doing".
The server already holds everything needed.

**Note on what I could not test:** all five customers are assigned to `usr_sales`, so
the salesperson's account list is identical to the manager's. This dataset cannot
distinguish "manager sees the whole org" from "there is only one book" on the accounts
endpoint. Decision scoping *was* separable and was verified (5 vs 2, §3).

---

### 6 · MINOR · `can_decide` ignores the self-approval rule, so the UI offers a button that always fails — **CONFIRMED**

**Where:** `backend/app/approvals.py:400` (`to_dict`) and `:369` (`pending_count`)
**Evidence:** `/tmp/shots/32-self-approve-attempt.png`

`to_dict` computes `can_decide` from role vs `required_authority` only; `_assert_can_decide`
additionally refuses self-approval. So a manager's own request comes back with
`can_decide: true`, the card renders an enabled **Approve**, and clicking it 403s. The same
mismatch inflates `pending_count`, which is what the nav badge and "2 waiting on you" read.

To the app's credit the failure is handled honestly — the server message *"You cannot
approve your own request"* is displayed rather than swallowed. The defect is offering the
action at all.

**Expected:** `can_decide` should reflect the rule the decide path enforces, and
`pending_count` should exclude requests the caller cannot decide.

---

### 7 · MINOR · Product mix's two empty reasons are attached to the wrong conditions — **CONFIRMED**

**Where:** `#/mix` · `backend/app/commercial/insight/mix.py:157-166`
**Evidence:** `/tmp/shots/13-mix.png`, `/tmp/shots/24-item-lines.png`

The screen says **"Nothing has been traded yet."** over a book with 26 sales lines and
₹3,29,831 of revenue. The ternary picks the message from `not order` — whether any *columns*
(lines of business) exist — rather than from whether any rows matched:

```python
"No trade could be placed against a line of the business yet… Item categories come
 from Zoho, from the HSN ranges, or from an override in Settings."
   if not order else
"Nothing has been traded yet."
```

The first message is the accurate description of the state I am actually in, and it is on
the branch that fires when *no lines of business are configured*. The Item lines screen
states the real cause plainly one click away: *"0% of 4 items are placed in a line · 4 are
not · they carry 100.0% of revenue (₹3,29,831)."*

**Expected:** the two strings swapped, so the screen names the cause a manager can act on.

---

### 8 · MINOR · Supply, Cash and How-we-pay blame a missing sales sync that is not missing — **CONFIRMED**

**Where:** `#/supply` `#/payments` `#/payables` · `backend/app/routers/insight.py:161-165`
**Evidence:** `/tmp/shots/21-supply.png`, `22-payments.png`, `23-payables.png`

`_no_data()` emits one template for every empty insight screen:

> "No sales history has been synced yet, so {what} cannot be computed. Connect a Zoho
> company and run a sync."

There *are* 26 sales rows. What is missing is the supplier/cash side — `vendors 0`,
`bills 0`, `purchase_orders 0`, `payment_receipts 0`, `vendor_payments 0`, `invoices 0`.
The screens are correctly empty (Finding: see §8) and the remedy offered is even correct;
the stated cause is not. `/stock` gets this right with a specific message
("Stock has not been folded into business state yet"), which is the pattern to follow.

---

### 9 · MINOR · The same margin is printed 17.3% and 17.4% on one screen — **CONFIRMED**

**Where:** `#/account/cst_brakes/item/prd_cnmg` · `GET /api/v1/commercial/customers/{c}/items/{p}`
**Evidence:** `/tmp/shots/18-account-cst_brakes-item-prd_cnmg.png`

The KPI tile and the chart footer both read **CURRENT MARGIN 17.3%**; the narrative
directly beneath reads *"Margin declined from 22.3% to **17.4%** over 4 months."*

Cause: the payload rounds `headline.current_margin` to 4dp (`0.1735`) and the frontend
renders `(0.1735*100).toFixed(1)` → `17.3`, while the `diagnosis` sentence is formatted
server-side from the unrounded `0.17351874…` with Python's `:.1%` → `17.4%`. Two formatters
straddling the same rounding boundary.

**Expected:** one rounding, applied once. A manager quoting a figure from this screen can
pick either.

---

### 10 · MINOR · Opportunities tells the manager to operate a control they do not have — **CONFIRMED**

**Where:** `#/opportunities` · **Evidence:** `/tmp/shots/07-opportunities.png`

> "2 of 4 relationships have a real gap, but every one is below your 10,000 materiality
> floor — the largest is 3,352. **Lower the floor in Settings** to see them, or leave it."

The materiality floor is part of the margin policy, which is `require_owner`. For this role
the Settings field is `disabled` (verified in §3). The empty state is otherwise excellent —
it names the count, the floor and the largest gap, and argues for leaving it alone — but its
call to action is addressed to the wrong role.

**Expected:** for a manager, "ask an owner to lower the floor", matching the phrasing the
Data screen already uses (*"No Zoho company is connected. Ask an owner to add one."*).

---

### 11 · MINOR · `thresholds_version` is absent from most manager read payloads — **CONFIRMED**

Persisted rows are stamped without exception (§4). The read API is inconsistent: of 26
manager-facing computed payloads, **8 carry a version and 18 do not**.

| Carries it | Does not |
|---|---|
| `landscape` `opportunities` `bonds` `mix` `dependency` `daily` `quote-intelligence/thresholds` `admin/policy` (+ `POST simulate`, `POST negotiate`) | `weather` `storyboard` `revenue-flow` `lost-revenue` `composition` `cadence` `targets` `stock` `supply` `payments` `payables` `journey` `simulate/scenarios` `catalogue` `cashflow` `migration` `commercial/customers/{c}/items/{p}` `commercial/customers/{c}/portfolio` |

Two omissions matter more than the rest. `weather` bands margin against `th.margin_floor`
and does not say which version of that floor it used. `commercial/customers/{c}/items/{p}` is
the full-economics screen a manager argues a price from, and its floor references
(`TARGET_MARGIN_PRICE`, `MARGIN_FLOOR_PRICE`, `MIN_MARGIN_PRICE`) are threshold-derived.

`CLAUDE.md`'s rule — *"never let a computed row be written without one"* — is satisfied.
This is the adjacent gap: a number on screen cannot be traced to the policy version that
produced it without going back to the database.

---

### 12 · POLISH · The Quote Builder's decision-support panel sends a UI placeholder as a product identifier — **CONFIRMED**

**Where:** `frontend/src/components/DecisionSupport.tsx:59-60` · `backend/app/pie_service.py:316`
**Evidence:** `/tmp/shots/43-supply-options.png`

On an AMBIGUOUS line, `pie_service` sets the resolution's `desc` to the human sentence
`"Not resolved — choose the intended product"`. `productRef(line)` reads
`line.supplyDesc || line.reqDesc || …` and posts it as a product:

```json
POST /api/v1/quote-support
{"customer":"Brakes India","products":["Not resolved — choose the intended product"],"proposed_price":null}
```

The manager's panel then prints the sentence back as if it were a SKU:

> *"'Not resolved — choose the intended product' not found in sales history — no prior
> price/margin context for this item."*

The request also fires twice for one drawer open. `"No PIE match"` is the same class of
placeholder on the UNRESOLVED branch.

**Expected:** no decision-support call for a line with no resolved product; the drawer
should show the candidate picker alone until one is chosen.

---

### 13 · POLISH · `docs/ui-standards.md`'s raw-`<table>` enumeration is stale — **CONFIRMED**

The document invites this check explicitly (*"the next reader can check it in a minute"*),
so here is the result. `rg -n '<table' frontend/src --glob '*.tsx'` finds three classes not
in the table at the end of §"Where this codebase stands":

| Raw `<table>` | Where | Verdict |
|---|---|---|
| `dp-table ci-table` | `CommercialScreens.tsx:557` — "Volume against margin" | **Correct.** Six comparison periods; the row count is the window, not the business. |
| `facttable trace-table` | `PlatformApp.tsx:1117` | **Correct.** Fact panel. |
| `grid` | `viz/Mix.tsx:394`, `viz/BookFlow.tsx:211` | **Correct.** Both carry a comment arguing the fixed-shape case; Mix is an n×n of the business's own lines, BookFlow is three stages. |

Nothing found is a violation — but three legitimate tables sitting outside the document's
list is how the next real one hides. The two entries the document marks "Arguable"
(`dp-table` in DataScreen, `id-table` in IdentityScreen) both still stand and both still
look arguable rather than wrong.

---

## 6. What works well

- **The approval gate is the real thing, not a flag.** `_covers()` compares the price now on
  the line against the price that was approved, so an approval granted at ₹380 refuses a
  ₹250 send with a message that names the line and the reason. Most systems that claim an
  approval workflow do not close this.
- **Authority is split where the money changes character.** A thin margin is a manager's
  call; below cost is the owner's. The split is enforced at the decide path *and* reflected
  in queue composition — the owner-authority request never enters a manager's actionable
  list, so the queue stays a list of things you can actually do.
- **Redaction is absence.** The salesperson's copy of the very request they raised has no
  `subject` key at all. There is nothing in the network tab to read.
- **Aggregation is done right, and provably.** `Σgp ÷ Σrev` differs from the mean-of-lines
  by 0.00008 in this dataset and the platform lands on the correct one exactly.
- **The customer-item screen is the best thing in the product.** Seven KPIs, a
  price-vs-cost chart, a period table, and then *"Every figure above is an aggregate of
  exactly these lines"* over the six actual invoice lines with their per-line cost, gross
  profit and margin. A manager can win an argument from this screen.
- **Empty states argue rather than shrug.** Opportunities does not say "no data" — it says
  two relationships have a real gap, the largest is ₹3,352, all are below your ₹10,000 floor,
  and here is why leaving it there is reasonable. The Simulator refuses to predict volume
  response and says so in the payload.
- **The Quote Builder's line table is now `DataGrid`.** The offender named in `CLAUDE.md`
  and in `ui-standards.md` has been fixed: `LineGrid.tsx` imports `DataGrid` and the
  rendered screen has one `.ag-root-wrapper` and zero raw tables. The keyboard contract
  (↑↓ navigate · Enter supply options · Space select · F2 edit the rate · / search · Esc)
  is shown under the grid.
- **The decision audit trail is complete.** `POST /decisions/{id}/action` recorded
  `{"action":"ACT","actor_user_id":"usr_manager","acted_at":"…","note":"…"}` and moved the
  status to ACTIONED; `/trace` returned an honest *"this decision was raised from a signal…
  so it has no state to drill into"* rather than an empty panel.
- **No 500s, no console errors, no failed API calls across 29 screens.**

---

## 7. UI-standards observations

Measured against `docs/ui-standards.md` on the screens this role uses.

**Holding.** MUI throughout — the shell is `AppBar` + `Drawer`, every button is MUI's, the
login page is a `Paper` card with MUI `TextField`s. Status is `Chip` everywhere I looked:
POOR / FAIR / GOOD on Weather, PENDING on approval cards, HIGH / MEDIUM / LOW priority
bands, `must change` on the users grid, `unresolved` / `manual review` / `AMBIGUOUS` in the
quote grid — a word and a shape, never colour alone. Dashboard surfaces are `Paper` (`Bp`),
and `Card` is reserved as §2 requires. No hand-rolled shimmer appeared on any screen; the
theme carries the palette.

**Grids.** Where the row count is the size of the business, it is AG Grid through
`platform/DataGrid.tsx`: the decisions list, the Customers list, the Settings users grid,
the quote line grid, the peer table and the transactions table on the customer-item screen.
Counted per screen during the walk — 1–2 `.ag-root-wrapper` on each. The known offender is
fixed (§6). The raw tables that remain are fact panels, chart fallbacks (`viz-table`) and
fixed-shape matrices — all defensible; the documentation drift is Finding 13.

**Worth arguing about.**

- `CommercialScreens.tsx:41` defines a private `pp()` string helper
  (`` `${sign}${(v*100).toFixed(1)} pp` ``) and uses it in three places, while
  `platform/kit.tsx` already owns `PercentageValue` and `VarianceIndicator` for exactly this.
  §10 says a pattern appearing more than once becomes a component; this is the third use of a
  local one. It also renders the movement as text with a `tone="warn"` prop rather than through
  `VarianceIndicator`, which §6 exists to make consistent (the arrow-*and*-word rule).
- The Settings screen mixes MUI (`Button`, the `DataGrid` users table) with raw
  `<input className="input">` for both password fields (`AdminScreens.tsx:1119-1128`),
  inside a `<form className="st-pw">`. §8 names `TextField`. It is the one form on this
  role's screens that is not MUI, and it is the one a first-time signer-in has to use.
- Finding 9's double rounding is a §4-adjacent problem: the same value formatted by two
  different owners.

---

## 8. Empty-but-correct

Checked against row counts in `backend/data/platform.db` before judging any screen.

| Screen | State | Verdict |
|---|---|---|
| `/stock` | `stock_snapshots 0`, `business_states 0` | **Correct, well explained.** "Stock has not been folded into business state yet. It is built at the end of every sync." |
| `/supply` | `vendors 0`, `bills 0`, `purchase_orders 0` | **Correctly empty, wrongly explained** — Finding 8. |
| `/payments` | `payment_receipts 0`, `invoices 0` | **Correctly empty, wrongly explained** — Finding 8. |
| `/payables` | `vendor_payments 0`, `bills 0` | **Correctly empty, wrongly explained** — Finding 8. |
| `/mix` | 26 sales rows, 0 items placed in a line | **Correctly empty, wrongly explained** — Finding 7. |
| `/targets` | `vendor_targets 0` | **Correct.** "Nothing in Zoho holds a principal's target, so they are typed in once and kept," with an Add action. Exemplary. |
| `/identity` | `customer_identities 0`, `item_identities 0`, `identity_suggestions 0` | **Correct.** Nothing has been ingested from two connectors, so there is nothing to link. |
| `/opportunities` | 2 real gaps, both under the ₹10,000 floor | **Correct and the best empty state in the product** — it shows its working. Only the call to action is misaddressed (Finding 10). |
| `/item-lines` | 4 items, 0 placed | **Correct**, and it quantifies the consequence: those 4 carry 100% of revenue. |
| `/landscape` | Was empty before recompute (`customer_item_metrics 0`); 4 points after | **Was correct then, correct now.** See note below. |
| `/dependency` | "0% of revenue could be traced to a principal (₹3,29,831 could not)" | **Correct.** Attribution runs through bills; there are none. It says exactly that. |
| Storyboard banner | "This is not today's picture — nothing has been synced yet" | **Correct.** `sync_runs 0`, `zoho_connections 0`. Demo history is anchored at 2026-07-22, so the ~2.5-week-stale windows are the seed, not a defect. |

**One state change I made, disclosed.** `/landscape` and `/weather` were initially empty
because `customer_item_metrics` had zero rows — the demo seed writes sales and cost lines
but never runs the commercial recompute. I ran `POST /api/v1/commercial/recompute` **as the
manager**, which is a supported action for this role and computes derived metrics from
existing data. It produced 4 relationship rows (2 with cost, 2 without) and the margin
screens populated. No threshold was moved, no band widened and no seed row edited. Both
"before" and "after" are reported: before the recompute, `/landscape`'s empty reason
("No relationship has trailing revenue yet. Run a sync, then recompute metrics.") named the
right remedy — but on a fresh clone, every margin screen is blank until someone knows to
press a button that is not on any screen.

---

## 9. Not covered, and why

- **Zoho ingestion.** No connector is configured and `POST /api/v1/data/connection` is
  owner-only, so the whole supply/cash/stock half of the book is structurally untestable
  from this role. Everything downstream of a sync — supplier concentration, payables ageing,
  stock carrying cost, principal attribution — was reviewed as an empty state only.
- **The owner's half of the approval loop.** I raised a below-cost request and confirmed the
  manager is refused, but I did not sign in as the owner to complete it, since the brief
  scopes this review to one role. What an owner sees on that request is unverified.
- **Salesperson scope on `/api/v1/accounts`.** All five customers are assigned to the single
  salesperson, so manager and salesperson account lists are identical and the seed cannot
  separate "whole-org scope" from "one book". Decision scoping was separable and was verified.
- **`/negotiate` and `/simulate` driven through their forms.** Both were exercised through
  their POST endpoints and both screens rendered with correct input guards, but the
  Autocomplete on the Negotiation desk did not accept a scripted selection, so the
  form → result path was not walked by hand.
- **Multi-manager behaviour.** One manager account exists. Whether two managers see each
  other's requests, and whether "cannot approve your own" holds across a pair of colluding
  managers, is untested — `allow_self_approval` is off, which is the only lever I could see.
- **AI failure modes.** `AI_PROVIDER=mock`, so DEGRADED / FAILED / SUPPRESSED were read on
  the `/states` documentation screen rather than provoked. The `ai_call_logs` table holds 9
  rows but `/internal/ai-metrics` is owner-only.
- **Responsiveness below desktop.** Every screen was driven at 1600×1100. Tablet and laptop
  widths (§12) were not exercised.
- **Accessibility beyond the structural.** Chart fallbacks and ARIA labels were observed in
  passing; no screen reader or keyboard-only pass was run.
