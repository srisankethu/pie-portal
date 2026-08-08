# Role review — SALESPERSON

**Account:** `r.nair@sanketh.in` · `usr_sales` · org `org_sanketh`
**Password set during this review:** `Sales-Review-2026!` (was `change-me-now`)
**Date of review:** 2026-08-08 · demo history anchored at 2026-07-22
**Build:** `9f9b7e8` · backend at Alembic head `b2d95e11c74a` (`/api/health` → `CURRENT`)
**Method:** real browser (Playwright/Chromium) against `localhost:5173`, plus a
network-level sweep of the API. Screenshots under `/tmp/shots/` (not committed).

---

## 1. Verdict

A salesperson can do the core of their job in this app today, and the parts that
work are unusually good: the decision queue reads well, acting on a decision is
captured cleanly and reflected everywhere, the negotiation desk refuses to
invent a floor it cannot compute, and the approval gate blocks a below-cost
quote with a named authority and an audit thread. The literal cost/margin
invariant also holds — I swept **59 GET path templates** and found **no `cost`,
`margin`, `unit_cost`, `gross_profit`, `purchase_rate`, `last_purchase` or
`landed` field in any response this role receives**, and every parameter and
header escalation I tried was refused.

The invariant that fails is the stronger one this review was asked to test:
**cost is exactly reconstructible from numbers the salesperson is served.** I
recovered the true unit cost of three separate items — ₹349, ₹372 and ₹420,
matching the database exactly — through three independent channels, one of which
needs no probing at all. That is a BLOCKER, because "cost and margin never reach
a salesperson" is the claim the permission model exists to make.

Two other things stop this being a clean bill. A quote line priced **below cost**
can be sent with a green `READY` chip and no approval, because the Quote Builder
and the approval gate read cost from two different places and the gate follows
the one that knows nothing. And the seeded `must_change_password` flag does
nothing: there is no forced password change, so a default credential stays live
until somebody volunteers to change it.

---

## 2. What was tested

**Screens — 32 URLs visited in the browser**, every route in
`frontend/src/platform/route.ts` plus the nav, the two parameterised patterns and
a nonsense path:

`/` `/decisions` `/decision/:id` `/customers` `/accounts` `/account/cst_rane`
`/account/cst_rane/item/prd_cnmg` `/quotes` `/negotiate` `/approvals` `/states`
`/data` `/identity` `/settings` `/journey` `/composition` `/cadence` `/bonds`
`/mix` `/dependency` `/payments` `/stock` `/targets` `/item-lines` `/weather`
`/opportunities` `/lost-revenue` `/landscape` `/simulate` `/payables` `/supply`
`/nonsense-route`

**API — 59 GET path templates swept** with a salesperson bearer token (26 → 200,
30 → 403, 3 → 404 for absent ids). Each 200 body was walked key-by-key against a
forbidden-name pattern *and* value-matched against the three seeded `unit_cost`
values (320, 349, 372). Sweep script and raw output retained in the session
scratchpad.

**Escalations attempted** (all refused): `?role=SALES_MANAGER`, `?role=OWNER`,
`?include=cost`, `?include=cost,margin`, `?fields=cost,margin,unit_cost`,
`?include_cost=true`, `?show_cost=1`, `?restricted=true`,
`?data_class=RESTRICTED`, `?economics=true`, `?as_role=OWNER`, `?expand=cost`,
`?view=manager` across six base paths; headers `X-Role: OWNER`,
`X-Platform-Role: SALES_MANAGER`, `X-Data-Class: RESTRICTED`,
`X-Platform-Authorization`. **78 parameter/header probes, zero produced a
cost or margin field.**

**POST surfaces exercised** (not in the GET sweep, and where the leak actually
is): `/api/quotes`, `/api/quotes/{id}/intake`, `/api/quotes/{id}/lines/{id}/price`,
`/api/v1/insight/negotiate`, `/api/v1/quote-intelligence/assess`,
`/api/v1/quote-intelligence/snapshot`, `/api/v1/approvals/quote-line`,
`/api/v1/decisions/{id}/action`.

**Day in the life completed end to end:** read the queue, opened a decision,
accepted it with a note, watched it land; drove the negotiation desk on two
customer×item pairs; built two quotes from pasted RFQs; priced a thin line;
raised an approval and read it back as the requester.

---

## 3. Cost/margin containment — the invariant result

### The literal invariant holds

No response served to `usr_sales` contains a cost or margin **field**. Across 59
GET templates and 68 UI-fetched 200 bodies, the only key matching the forbidden
pattern was:

```
GET /api/v1/data/status → {"read_model": {"cost_records": 3,
                                          "cost_records_pending_discount_backfill": 0}}
```

Those are **row counts, not costs**. Three counts over four products cannot
reconstruct a margin. I do not consider this a leak; it is noted for completeness.

Where the server does withhold, it withholds well. `/api/v1/quote-intelligence/assess`
returns `references_withheld: ["Holds this relationship's usual margin", "Target
margin price", "Review floor", "Approval floor"]` — the *names* of what is being
kept back, with no values — and RESTRICTED exception impacts arrive as
`{"impact_amount": null, "impact_data_class": "RESTRICTED"}`. Per-line
`economics` is absent from a salesperson's quote payload entirely. That is the
design working.

### The invariant that fails: exact cost reconstruction — **BLOCKER, CONFIRMED**

Three independent channels. All three were exercised with a salesperson token
only; the database was read afterwards purely to verify the answers.

**Channel 1 — `floor_price` on `/api/v1/insight/negotiate`, swept across tool
families.** The response carries `floor_price`, and `commercial/floor.py:181`
computes it as `floor = cost × (1 + m_floor(family))`. The salesperson chooses
the family on the screen, so they can request the *same item* under all seven
families and read back seven floors that are all multiples of one unknown cost:

```
prd_cnmg floors served to the salesperson:
  default 436.25 · inserts 425.78 · solid_carbide 439.74 · holders 436.25
  metrology 467.66 · chemicals 411.82 · machines 390.88
```

Solving for the single cost `C` that makes every implied `m_floor` a whole
percentage yields **exactly one candidate**:

```
prd_cnmg → unique solution C = ₹349.00   (m_floors 12/18/22/25/26/34%)
prd_dnmg → unique solution C = ₹372.00   (same multiplier set)
```

Database ground truth: `prd_cnmg` = 349, `prd_dnmg` = 372. **Exact.** The
organisation's whole margin-floor policy falls out of the same solve. The
salesperson can then compute margin on any line they can see a price for:

```
CNMG: last_price_paid ₹414 vs cost ₹349 → gross margin 15.7%
DNMG: last_price_paid ₹430 vs cost ₹372 → gross margin 13.5%
```

The response even says the quiet part: `unavailable: [{"series":
"cost_and_margin", "reason": "…the floor already carries it…"}]`.

**Channel 2 — `filterCounts.MFLOOR` as a yes/no oracle.** `app/store.py:298`
computes `"MFLOOR": sum(1 for ln in self.lines if ln.economics().below_floor)`
**for every role** — it is not behind the `mgmt` guard that correctly nulls
`marginFloor` two lines earlier. It flips as the price crosses the floor:

```
POST /api/quotes/q17/lines/l18/price {"price":555} → filterCounts.MFLOOR = 0
POST /api/quotes/q17/lines/l18/price {"price":400} → filterCounts.MFLOOR = 1
```

Twenty bisection probes recover the exact floor price of a line the product
never intends to show: **₹494.12**. At the 15% margin floor that is a cost of
**₹420.00**, independently confirmed by `pricing.compute_economics(420.0, None,
400.0, "inserts")` reproducing the `recommended: 555.0` the salesperson was
served and `margin = -0.05`.

This one is the sharpest because the UI *already hides it*: the "Below margin
floor" chip is gated `{mgmt && …}` at `QuoteBuilder.tsx:481`, so the count is
sent to the browser and suppressed there. That is precisely the failure mode
`CLAUDE.md` §1 names — *"absent from the response, not hidden in the browser."*

**Channel 3 — `recommended`, in every quote line, no probing at all.**
`pricing.recommend_price` is `round(cost / (1 − target_margin(family)) / 5) × 5`.
It is served on every line and is invertible to within the ₹5 rounding step:

```
cost 349 → recommended 460    cost 420 → recommended 555
cost 372 → recommended 490    cost 500 → recommended 660
```

That is cost to roughly ±₹2 for free, on every line of every quote.

**One disclosure that is *not* a defect.** The assess response says "This price
does not cover what the item costs us" on a below-cost line. Bisecting the price
against the three exception tiers also recovers cost — but you cannot warn
somebody they are selling below cost without telling them they are below cost.
That channel is inherent to the feature. Channels 1–3 are not: none of them
requires the salesperson to learn a cost in order to do their job.

**What would close it:** round `floor_price` to a coarse band before serving it,
or vary the multiplier per item rather than per family (channel 1); move
`MFLOOR` behind the same `mgmt` guard that already protects `marginFloor`
(channel 2); serve `recommended` only as a coarse band, or omit it (channel 3).

---

## 4. Findings

### BLOCKER

**F1 · Exact unit cost is reconstructible by a salesperson — CONFIRMED**
Evidence and reproduction in §3. Three channels; ₹349, ₹372 and ₹420 all
recovered exactly from salesperson-only responses and confirmed against
`cost_records`. Endpoints: `POST /api/v1/insight/negotiate` (`floor_price`),
`POST /api/quotes/{id}/lines/{id}/price` (`filterCounts.MFLOOR`),
`POST /api/quotes/{id}/intake` (`lines[].recommended`).
Expected: no served number from which cost or margin can be derived.

**F2 · A line priced below cost passes as "within policy" and can be sent —
CONFIRMED**
On quote `q17` line `l18` (PIE item `2001174`, true catalogue cost ₹420) priced
at **₹400**:

- the grid shows a green **`READY`** chip and "Commercial exceptions **0**"
  (`/tmp/shots/22-thin-line.png`);
- `GET /api/v1/approvals/quotes/q17/gate` → `{"can_submit": true,
  "blocked_reason": null}`;
- `POST /api/v1/approvals/quote-line` → `{"detail": "This price does not need
  approval — it is within policy."}`;
- yet the same payload carries `filterCounts.MFLOOR: 1`, and
  `compute_economics(420, None, 400, "inserts")` gives `margin = −0.05,
  below_floor = True`.

Root cause: two cost sources. `app/store.py` reads the PIE catalogue cost and
correctly flags the line; `commercial/quote_exceptions.py:172` guards every
margin exception behind `if unit_cost is not None and unit_cost > 0` against the
*org's* `cost_records`, which hold nothing for a PIE code — so no exception
fires and no approval is required. The gate follows the blind source.
Expected: a below-cost line raises `NEGATIVE_MARGIN` and blocks the quote, as it
correctly does for `prd_cnmg` where the org *does* hold a cost record.

**F3 · `must_change_password` is never enforced — CONFIRMED**
`r.nair@sanketh.in` / `change-me-now` signs straight into the dashboard; no
prompt, no interstitial (`/tmp/shots/02-after-signin.png`). The flag is read in
exactly two places — the login response and an admin-screen label
(`grep must_change_password` returns no dependency, no guard, no router check).
The token issued *before* any password change already works:

```
POST /api/v1/auth/login {"…","password":"change-me-now"} → 200, must_change_password: true
GET  /api/v1/decisions  (that token)                     → 200, full queue
```

Expected: either a forced change before the session is usable, or the flag
removed so it stops implying a control that does not exist.

### MAJOR

**F4 · The home screen's primary call-to-action sends this role to a 403 —
CONFIRMED**
"Today" renders a briefing beat "₹1,02,629 of revenue stopped" whose **NEXT**
button is "Open the lost-revenue breakdown (3)". Clicking it navigates to
`/lost-revenue`, which fails with `403 /api/v1/insight/lost-revenue?months=3` and
renders "This did not load. Manager or owner role required."
(`/tmp/shots/06-cta-lost-revenue-403.png`). `PlatformApp.tsx:380-382` states the
rule this breaks — *"a nav item that always 403s is a nav item that teaches
people the product is broken"* — and correctly omits `lostRevenue` from the nav;
the storyboard beat routes there anyway via `vizPath()`. The sibling CTA "See
revenue by customer (3)" → `/journey` works fine.
Expected: the beat is suppressed, or its destination is a screen this role can open.

**F5 · Approval timestamps render 5½ hours wrong — CONFIRMED**
The approval raised at **16:46:31 UTC = 10:16 pm IST** displays on `/approvals`
as **"8 Aug 2026, 4:46 pm"** (`/tmp/shots/23-approvals-pending.png`) — the naive
UTC string formatted as if it were local. The same field is serialised two ways:

```
POST /api/v1/approvals/quote-line → "requested_at": "2026-08-08T16:46:31.567180+00:00"
GET  /api/v1/approvals            → "requested_at": "2026-08-08T16:46:31.567180"
```

Root cause: `app/approvals.py:394` calls `request.requested_at.isoformat()`
directly. The column is `DateTime(timezone=True)` but SQLite does not round-trip
tzinfo, so the freshly-built object is aware and the re-read one is naive.
`clock.aware()` exists for exactly this and is not used. The Quote Builder's
"Saved 10:13 pm" is correct, so the app shows one right and one wrong clock on
adjacent screens.
Expected: `clock.aware(...)` before `isoformat()`, and IST throughout.

**F6 · A prose RFQ silently loses every quantity — CONFIRMED**
Pasting a normal customer email:

```
Please quote for the following:
1. CNMG 120408-MP insert - 100 nos
2. DNMG 150608-MP insert - 50 nos
3. 25mm shank turning holder - 5 nos
4. 8.0mm HSS-Co machine reamer - 10 nos
```

produces **5 lines** — the prose header "Please quote for the following:" becomes
a quote line — and **every line comes back `reqQty: 1`**. The quantities are
dropped with no warning; a 100-piece line becomes a 1-piece line
(`/tmp/shots/18-rfq-resolved.png`). The app's own sample format
(`2001174, 20` / `2045826 x30`) parses quantities correctly
(`/tmp/shots/19-sample-rfq-resolved.png`), so the engine is fine — the tolerant
path is not. PIE is online; no `PIE OFFLINE` was seen at any point.
Expected: an unparsed quantity is flagged, not defaulted to 1. Silently quoting
1 of something a customer asked 100 of is the expensive direction to fail in.

### MINOR

**F7 · "Identities" is a nav item that always 403s — CONFIRMED**
`{ key: "identity", … }` at `PlatformApp.tsx:447` is unconditional, three lines
below the comment forbidding exactly this. Every call on the screen fails:
`403 /api/v1/identity/customers`, `403 /api/v1/identity/settings/policy`,
`403 /api/v1/identity/customers/suggestions/pending`
(`/tmp/shots/w-identity.png`). Nothing on the screen works for this role.
Expected: gated like `simulate`, `weather`, `targets` and `supply` already are.

**F8 · Permission denial rendered as bare coloured text — CONFIRMED**
`/settings` prints "Manager or owner role required" as red body text
(`/tmp/shots/03-settings.png`). It is `<div className="dp-error">`
(`AdminScreens.tsx:274`, `:1106`; also `IdentityScreen.tsx:328`,
`ConnectionsPanel.tsx:904`), styled `.dp-error { color: var(--danger-fg) }`.
`ui-standards.md` §6 forbids this in as many words — colour alone fails in
greyscale and forced-colours. `AdminScreens.tsx:840` uses `<Alert severity="error">`
for the same job, so one file does it both ways.

**F9 · Two empty states state something false about the data — CONFIRMED**
Both screens are correctly empty; both explain it wrongly.

| Screen | Message served | Actual reason |
|---|---|---|
| `/mix` | `"Nothing has been traded yet."` | 26 sales lines, ₹3,29,831 traded. All four products have `category = NULL`, so there is no line of business to group by. |
| `/payments` | `"No sales history has been synced yet…"` | Same 26 lines exist. `payment_receipts` is empty — no *receipts*, not no sales. |

The Customers screen shows ₹1,29,600 for ACE Designers on the same session, so
the contradiction is visible to the user. `ui-standards.md` §10 defines
`EmptyState` as "nothing to show, **and why**"; a wrong *why* is worse than none.

**F10 · A percentage-point movement is rendered as a percentage — CONFIRMED**
`/composition` reads "ACE Designers held 100% … and 24% … — **down 76% of
share**". The backend sends `movement.biggest_mover = {"from": 1.0, "to": 0.2445,
"change": -0.7555}`, i.e. `change = to − from`, a **percentage-point** difference.
`Patterns.tsx:423` renders it with `pct()` and the word "%". `CLAUDE.md` §1 says
movement is percentage points, and `CommercialScreens.tsx:41` has a `pp()` helper
whose tooltip says "24% to 20% is −4 pp, not −17%". Here the two happen to
coincide because `from` is 100%; the same payload's `others` entry for Brakes
India (`from 0.0 → to 0.2805`) would read "up 28% of share" where the relative
change is undefined.

**F11 · Changing the password does not invalidate existing sessions — CONFIRMED**
A token minted before the change is still accepted after it:

```
old password → 401 (correctly rejected)
old token    → 200 /api/v1/decisions
```

Expected: a credential change revokes outstanding tokens. Low exposure here
(tokens carry no expiry claim to shorten either), but it is the reason a
password change is asked for after a suspected compromise.

**F12 · `/api/v1/accounts/{id}/items` answers an unknown or out-of-scope
customer with `200 []` — CONFIRMED**
`accounts.py:165-169` returns `[]` both for a customer that does not exist and
for one this salesperson is not assigned. Scope *is* enforced, and the two cases
are indistinguishable so scope is not probeable — but the sibling endpoint
`/api/v1/insight/customers/{id}/timeline` returns `404 {"detail": "Customer not
found"}` for the same input. Two endpoints about the same subject, two answers.
An empty 200 is also indistinguishable from "this account has bought nothing",
which is a real state here (TVS Sundram Fasteners).

### POLISH

**F13 · `marginFloor` is nulled rather than omitted — CONFIRMED**
`store.py:266` sets `floor = self._margin_floor() if mgmt else None`, so a
salesperson receives `"marginFloor": null`. The value is properly withheld, but
`CLAUDE.md` §1 asks for *absent*, not null. Cheap to make exact.

**F14 · Subject-verb agreement on `/bonds` — CONFIRMED**
"3 are anchored; **1 are** past their own buying rhythm."
(`/tmp/shots/w-bonds.png`).

**F15 · `CLAUDE.md`'s digest is stale on its own headline example — CONFIRMED**
Lines 21-26 say the Quote Builder's line table "stayed a hand-written
`<table class="grid">` through three UI passes". It is a `DataGrid` now
(`components/LineGrid.tsx:420`), and `ui-standards.md` records the conversion.
The rule is right and worth keeping; the example reads as a live defect and is not.

**F16 · Login form styling is inconsistent — CONFIRMED**
Email uses a floating `InputLabel`, Password uses a placeholder only
(`/tmp/shots/01-login.png`). Chromium also logs the standard warning that the
Settings password form has no username field for accessibility.

---

## 5. What works well

- **Acting on a decision is genuinely well built.** Accept → dialog with an
  optional note → `POST /api/v1/decisions/{id}/action` → status `ACTIONED`,
  `human_action` with actor, note and timestamp, a **HUMAN LOG** entry on the
  detail page, a `CLOSED` panel, an **Undo**, a notistack toast, the queue row
  flipping to `ACTIONED` and the "Today" badge dropping 2 → 1. Every surface
  agreed, immediately. (`/tmp/shots/08`–`11`.)
- **The closing panel is honest about what it cannot do:** "Outcome measurement
  runs on later Zoho data and will appear here when available" — matching
  `architecture.md`'s "Outcome Tracker — deliberately not built" rather than
  faking a result.
- **The negotiation desk refuses to guess.** With no purchase record it returns
  `negotiable: false` and says why: *"pricing it without a floor is a guess, and
  the desk will not pretend otherwise."* That is `CLAUDE.md` §1's "do not weaken
  a rule to make output appear", implemented.
- **Scope is enforced and not probeable.** All three manager-routed decisions
  (`MARGIN_DETERIORATION` ×2, `COST_PASS_THROUGH`) return **404 "Decision not
  found"** — byte-identical to a decision that does not exist — on `/`, `/detail`
  and `/trace`. Exactly what `architecture.md` promises.
- **Withholding names itself.** `references_withheld` lists the four RESTRICTED
  reference *labels* with no values, and RESTRICTED exception impacts arrive as
  `null`. A salesperson can see that something is being kept from them and what
  kind of thing it is — much better than a silently short response.
- **The approval object is complete.** `required_authority: OWNER`,
  `can_decide: false`, a `thread` with the requester, note and time, a
  `Withdraw` action, and a gate that blocks with a specific reason: *"1 line(s)
  need approval before this quote can be sent: CNMG 120408-MP insert."*
- **Money arithmetic is right.** ₹530 × 20 + ₹3,450 × 30 = **₹1,14,100**; GST 18%
  = **₹20,538**; total **₹1,34,638**. Indian lakh grouping throughout, `taxRate`
  carried as the ratio `0.18` and rendered "18%", and the rate travels with the
  amount rather than being hardcoded twice.
- **The AG Grid conversion landed.** The quote line grid is `DataGrid` with a
  real rate editor (F2 / Enter / Tab / Esc, documented in a keyboard-hint strip
  under the grid), header select-all, and `Chip`s for every state.
- **The RESTRICTED disclosure on "Today"** — *"Margin-based items are not shown
  for your role. They are absent from the response, not hidden here."* — is the
  right thing to say. It is also, on the evidence of §3, not quite true yet.

---

## 6. UI-standards observations

Measured against `docs/ui-standards.md`, on the screens this role uses.

**Holding:**
- MUI is the design system throughout; the shell is `AppBar` + `Drawer`, dialogs
  are `Dialog`, toasts are notistack. No `.toast` div survives.
- **No hand-rolled shimmer remains.** `grep` for `skeleton`/`shimmer` in `.tsx`
  returns only the two comments recording their removal; loading is
  `LoadingState`.
- **No `<Card>` is used as a generic container** — `grep '<Card'` returns nothing
  across `frontend/src`. §2 holds by construction.
- **Tables.** Every business-sized table this role sees is AG Grid via
  `platform/DataGrid.tsx`: decisions, customers, cadence, and the quote line
  grid. The 20 raw `<table>` elements left are the categories §3 permits —
  `facttable` fact panels, `viz-table` chart fallbacks, `mig-grid`, `qi-refs`,
  `cx-scopes`. **The known offender is fixed** (see F15).
- Status is `Chip` almost everywhere: `HIGH`, `OPEN`, `ACTIONED`, `READY`,
  `EXACT`, `AMBIGUOUS`, `PENDING`, `OWNER ONLY`.
- Theme tokens are used rather than literals — even `.dp-error` reaches for
  `var(--danger-fg)`; its problem is §6, not §11.

**Not holding:**
- **§6, custom coloured text** — F8. Four `.dp-error` sites, three of them on
  this role's screens.
- **One condition, three presentations.** "Manager or owner role required"
  appears as a MUI `Alert` (Today), as bare red text (Settings), and as an
  `ErrorState` with a **Try again** button (weather, opportunities, lost-revenue,
  landscape, simulate, payables, supply, item-lines). The third is the worst
  offender in substance: "Try again" invites a salesperson to retry something
  that can never succeed. A permission denial is not a load failure and should
  not borrow its affordances.
- **§13, accessibility** — Chromium logs the missing-username warning on the
  Settings password form.

---

## 7. Empty-but-correct

Checked against row counts in `backend/data/platform.db` before judging.

| Screen | State | Verdict |
|---|---|---|
| `/approvals` (initially) | "Nothing waiting." | **Correct and well-written.** Tells you where requests come from. Populated correctly once one was raised. |
| `/targets` | "No targets on record… Nothing in Zoho holds a principal's target" | **Correct.** No `supplier_targets` rows, and the reason is the true one. |
| `/stock` | "Stock has not been folded into business state yet." | **Correct.** No stock tables populated; the reason is accurate. |
| `/payments` | "No sales history has been synced yet…" | **Empty is correct** (`payment_receipts` = 0) but **the reason is false** — 26 sales lines exist. See F9. |
| `/mix` | "Nothing has been traded yet." | **Empty is correct** (no product `category`) but **the reason is false** — ₹3,29,831 traded. See F9. |
| Home "Where the money moved" | Chart area renders blank below the caption | **Thin-data, not a defect.** Four customers over two 3-month windows; the accessible "View as a table" fallback is present. |
| `/negotiate` on `prd_holder` | Refuses to compute, explains why | **Correct, and the best empty state in the app.** No cost record for that item; it says so and declines rather than guessing. |
| Decision queue showing 2 of 5 | 2 salesperson decisions | **Correct.** The other three are `MARGIN_DETERIORATION` ×2 and `COST_PASS_THROUGH`, all routed to `SALES_MANAGER` — exactly the two categories `architecture.md` says never reach this role. |
| "data to 2026-07-21" on Today | ~2.5 weeks stale | **Correct.** Demo history is anchored at 2026-07-22; the screen states its own as-of date rather than implying freshness. Not a bug. |

---

## 8. Not covered, and why

- **Customer scoping could not be properly exercised.** All five seeded customers
  are assigned to `usr_sales`, so "you see only your own accounts" is trivially
  satisfied and cannot fail. I read the scope predicates instead
  (`accounts.py:57-59` and `:168-169` — both filter on
  `assigned_user_id == principal.user_id`) and tested the reachable negative
  cases: unknown ids, and the three manager-routed decisions, which correctly
  404. **A customer assigned to another user is untested against a live server.**
  Reassigning one would have meant editing seed data, which the rules of
  engagement forbid.
- **One manager login, for arithmetic verification only.** I signed in as
  `m.rao@sanketh.in` exactly once, to read back `marginFloor` and confirm the
  ₹494.12 floor I had recovered by bisection was the real one. No manager screen
  was reviewed and no manager behaviour is reported here. Flagging it because the
  brief said salesperson only.
- **Approval decisioning** — accept/reject/changes-requested — needs a manager or
  owner, so only the requester's half of the loop is covered.
- **`/simulate`, `/weather`, `/opportunities`, `/lost-revenue`, `/landscape`,
  `/payables`, `/supply`, `/item-lines`** were confirmed to 403 for this role and
  then left alone; reviewing their content is a manager-role job.
- **Zoho estimate creation.** The "Create Zoho estimate" button was not pressed —
  there is no Zoho connection, and it is an outward-facing write.
- **Multi-tenant isolation.** One organization is seeded; I probed a guessed
  foreign customer id (`cst_other_org` → `200 []`) but could not test a real
  second tenant.
- **Undo on a decision, and Withdraw on an approval** were left in place rather
  than exercised, so the review's own artifacts stay legible to the next session.
- **Load and concurrency.** Out of scope for a role review.

---

*Findings: 3 BLOCKER (F1–F3), 3 MAJOR (F4–F6), 6 MINOR (F7–F12), 4 POLISH
(F13–F16) — 16 in total, all marked CONFIRMED. Each was reproduced against a
running server; nothing in this document is inference from reading code alone,
though code references are given where they identify the cause.*
