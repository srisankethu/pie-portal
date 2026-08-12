# Role review — Business Owner

Reviewer: automated end-to-end review, signed in only as `s.menon@pie.example`
(role `OWNER`, `usr_owner`, org `org_pie`). Review date **2026-08-08**;
demo history is anchored at 2026-07-22, so "recent" windows are ~2.5 weeks
stale by design and that is not reported as a defect anywhere below.

Manager (`m.rao@pie.example`) and salesperson (`r.nair@pie.example`) tokens were
minted **only as setup** — to raise a below-cost request for the owner to sign,
and to prove that a policy the owner changed is visible to the roles it
governs. Neither role is the subject of this review.

Password changed through the UI during the review, as the brief asked:
**`s.menon@pie.example`** — password changed from the seed default during this
review; the value is not recorded here and has since been rotated again.


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
> | F1 · nothing forces the seeded password to change | **Fixed** |
> | F2 · a human action overwrites the previous one | **Fixed** — the trail appends, and legacy rows are backfilled |
> | F3 · owner-only trust and AI-ops surfaces unreachable | **Partly, by decision** — the trust half is built ("Your data"); `ai-metrics` is deliberately not, because with `AI_PROVIDER=mock` it is a screen of zeros |
> | F4 · raw enums against raw composite keys | **Fixed** — one label map, and the pair subject resolves to two names |
> | F5 · erosion threshold does not govern the queue | **Fixed** — the owner's value reaches the Signal Engine |
> | F6 · signing a below-cost line captures no rationale | **Fixed** — refused without one |
> | F7 · signals do not carry `CommercialThresholds.version` | **By decision** — left as is, and `CLAUDE.md` now states that a signal stamps `th_…` and why that is not `ci_…` |
> | F8 · metric rows restamped, so no version history | **By decision** — the upsert is kept and the doc narrowed to what versioning actually buys |
> | F9 · `/trust/disclosure` names a model never called | **Worked around, not fixed** — the trust screen states the provider and says "No model is called" on the default configuration; the payload still reports `AI_MODEL` |
> | F10 · a salesperson clicking Settings gets a raw API error | **Fixed** |
> | F11 · a sync with nothing connected reports success | **Fixed** — the result says which source it read |
> | F12 · AG Grid theme hardcoded | **Fixed** — derived from the MUI theme, with a test that no literal returns |
> | F13 · §10 documents components that do not exist | **Fixed** — two rows were stale, not four; corrected, and `kit.contract.test.ts` now asserts the table against `kit.tsx` |
> | F14 · the raw-`<table>` enumeration is already incomplete | **Fixed** — replaced by a rule |
> | F15 · `CLAUDE.md` calls the Quote Builder table hand-written | **Fixed** |
> | F16 · a customer with no GSTIN can never be linked | **Fixed** — a name-based suggestion where no identifier exists, never auto-linked |
> | F17 · exact product names resolve as AMBIGUOUS | **Not a defect** — measured, and the review's own qualification holds. `CNMG 120408-MP insert` is a description, and the catalogue holds `CNMG 120408-49 - TN2000` and two siblings at score 1.0, so the engine abstains and offers six ranked options — the documented answer. A real code (`2001174`) resolves `AUTO_MATCH`/`EXACT`. Making a description pick one would be guessing, which pie-parser §1 forbids |
> | F18 · the Settings password form is not MUI | **Fixed** — `TextField`, plus the `username` field a password manager needs |
> | F19 · the HUMAN LOG does not say who acted | **Fixed** — `actor_name`, resolved server-side |
> | F20 · dead CSS | **Fixed** |
> | F21 · `thresholds_version` omitted from the approval payload | **Fixed** |

---

## 1. Verdict

Yes, with reservations that are about reach rather than about truthfulness. The
things an owner would be most afraid of in a system like this — a number the AI
invented, a margin policy that changes the past, a manager quietly signing off a
loss — are genuinely closed, and I could not break any of them. Threshold
versioning works exactly as designed and I proved it row by row; below-cost
authority is enforced server-side with a clear refusal; no AI-authored text
anywhere in the product contains a digit; every empty screen says *why* it is
empty, usually naming the specific threshold and figure responsible.

The reservations are that several of the capabilities that make this platform
worth owning are not in the product the owner actually uses. AI cost and health
metrics and the entire trust surface — disclosure, break-glass log, export,
erasure — are implemented, owner-scoped and working, and unreachable from the
UI. The audit trail on a decision keeps only the *last* human action, so an
owner's recorded reasoning is destroyed by pressing Undo. The owner's "Erosion
threshold" setting silently does not govern the margin-deterioration decisions
in their queue. And nothing forces the seeded default password to be changed.

An owner could run this today and trust the numbers on it. They could not yet
answer an auditor's "who approved this loss and why", and they cannot see what
their AI is costing them without a database client.

---

## 2. What was tested

**Setup.** The verified recipe was followed exactly, with one correction: the
repository was not present in this container at all, so `srisankethu/pie-portal`
and `srisankethu/pie-parser` were cloned fresh to `/home/user/`. Catalogue built
to 6717 products; `python3 -m app.bootstrap` migrated and seeded;
`/api/health` reported `migration.state == CURRENT` at revision `b2d95e11c74a`;
`/openapi.json` reports **99 paths**.

**Screens — all 28 routes in `frontend/src/platform/route.ts`, driven in
Chromium**, plus `/decision/:id` and `/account/:id`:
`/`, `/decisions`, `/customers`, `/account/cst_rane`, `/quotes`, `/states`,
`/data`, `/approvals`, `/settings`, `/identity`, `/weather`, `/opportunities`,
`/lost-revenue`, `/journey`, `/simulate`, `/landscape`, `/composition`,
`/cadence`, `/payments`, `/payables`, `/stock`, `/supply`, `/bonds`, `/mix`,
`/dependency`, `/targets`, `/item-lines`, `/negotiate`.

**Every one rendered. Zero screens returned 403. Zero failing API calls
(no response ≥ 400) and zero uncaught console errors across the whole walk**
(`/tmp/shots/walk.json`, `/tmp/shots/walk-*.png`, 57 screenshots in
`/tmp/shots/`).

**Flows driven through the real UI:** sign-in; password change (including a
rejected too-short password); the Settings super-admin surface; margin-policy
edit and save; the approvals queue and a below-cost signature; a decision
opened, acted on with a note, and undone; the Quote Builder from customer
selection through RFQ paste to a resolved three-line grid.

**Endpoints exercised directly** (≈30 distinct, ~70 calls): `/api/health`,
`/api/v1/auth/login`, `/api/v1/admin/{policy,margin-policy,users,me/password}`,
`/api/v1/commercial/recompute`, `/api/v1/approvals` (+ `/quote-line`,
`/{id}/decide`, `/{id}`), `/api/v1/decisions` (+ `/{id}/detail`, `/{id}/trace`,
`/{id}/action`), `/api/v1/internal/ai-metrics`,
`/api/v1/trust/{disclosure,payloads,access,export,erasure}`,
`/api/v1/data/{status,sync}`, `/api/v1/connections`, `/api/v1/identity/customers`.

**Read model inspected directly** at `backend/data/platform.db` throughout.

**One thing I changed that a later reader should know about:** to test (f) I
pressed Sync with no Zoho company connected. That ran the *fixture* source and
wrote demo records into the read model — customers 5→8, products 4→6, sales
txns 26→29, cost records 3→4, decisions 5→10. Every observation before that
point is against the documented baseline; every observation after it is against
the enlarged set, and I say which where it matters.

---

## 3. Threshold versioning — the property holds, and I proved it

**Result: CONFIRMED working, exactly as `CLAUDE.md` describes.** This is the
strongest thing in the platform.

**Baseline.** `CommercialThresholds.version` = `ci_f7de764806`. I ran a full
recompute *before* touching any policy, producing 4 metric rows:

```
cst_ace     prd_holder  ci_f7de764806  2026-08-08 16:28:13.465221
cst_brakes  prd_cnmg    ci_f7de764806  2026-08-08 16:28:13.466961
cst_pitti   prd_dnmg    ci_f7de764806  2026-08-08 16:28:13.468309
cst_rane    prd_ream    ci_f7de764806  2026-08-08 16:28:13.468961
```

**The change.** Through the Settings UI (`/#/settings` → Margin policy →
Approval floor), `min_margin` 12% → 14%, then "Save margin policy"
(`/tmp/shots/07-policy-edited-unsaved.png`, `08-policy-saved.png`). The screen
validated the ladder live before saving: *"Ladder holds: approval floor 14.0% ≤
review floor 15.0% ≤ target 24.0%."*

**A new version appeared.** `GET /api/v1/admin/policy` afterwards:

```
version:         ci_033195fae1
default_version: ci_f7de764806
OVERRIDDEN: min_margin = 0.14 (default 0.12)
```

**Nothing was silently restamped.** Immediately after the save, with no
recompute, all four rows were untouched — same version *and* same
`computed_at` to the microsecond:

```
cst_ace  prd_holder  ci_f7de764806  2026-08-08 16:28:13.465221   ← unchanged
cst_rane prd_ream    ci_f7de764806  2026-08-08 16:28:13.468961   ← unchanged
```

**Newly computed rows carry the new version, and only those rows.** I then
recomputed **one customer** (`POST /api/v1/commercial/recompute
{"customer_id":"cst_rane"}`). This is the decisive observation:

```
cst_ace     prd_holder  ci_f7de764806  16:28:13.465221   ← old policy, untouched
cst_brakes  prd_cnmg    ci_f7de764806  16:28:13.466961   ← old policy, untouched
cst_pitti   prd_dnmg    ci_f7de764806  16:28:13.468309   ← old policy, untouched
cst_rane    prd_ream    ci_033195fae1  16:29:47.716257   ← recomputed, new policy
```

Two policies coexisting in one table, each row saying which floor it was judged
against. That is the whole promise, demonstrated.

**Signals, being append-only, preserve it over time.** The signals table
currently holds three versions side by side: `th_4bca9e0a59` (10),
`ci_f7de764806` (5), `ci_e73ef9d815` (1).

**Approval requests are stamped too** — every request I raised carries the
version in force when it was raised (`ci_033195fae1`), so a past signature can
be read against the policy that made it necessary.

**The hash is genuinely content-addressed.** I later set the erosion threshold
to 0.20 (version → `ci_e73ef9d815`), then cleared the override; the version
returned to **exactly `ci_033195fae1`**. Same content, same hash — it is a
content hash, not a counter.

**The change is visible to the roles it governs — and well.** A manager's
Settings screen (`/tmp/shots/24-settings-manager.png`) shows, read-only:

> Version in force **ci_033195fae1** · differs from the environment default
> **ci_f7de764806** · last edited 8 Aug 2026, 9:59 pm

and the whole margin policy with `overridden` marked against the approval
floor, fields rendered `disabled` (typing into them is refused), and no Save
button. `PATCH /margin-policy` as a manager returns `403 {"detail":"Owner role
required"}`. This is the best-executed screen in the product.

**And it changed what the platform actually refuses.** A salesperson raised
CNMG at ₹401 against a ₹349 cost — a 12.97% margin, which was *above* the old
12% floor and is *below* the new 14% one:

```
HTTP 201  authority: MANAGER  title: "Below the approval floor for this item"
```

Under the old policy that line would have gone out unsigned. The owner's edit,
made in a browser, is enforced for a salesperson one call later.

**Two honest caveats** (see findings 7 and 8): signals from the Signal Engine
carry `SignalThresholds.version` (`th_…`), *not* `CommercialThresholds.version`,
so `CLAUDE.md`'s "stamped on every … signal" is not literally true; and metric
rows are upserted in place, so a *full* recompute restamps every row and the
prior version is gone — the stamp explains the current number, it does not give
you a history of past ones. All 7 metric rows now read `ci_e73ef9d815`.

---

## 4. Authority and audit

### Below-cost is the owner's signature, and it holds

A salesperson raised CNMG 120408-MP at ₹300 against a ₹349 effective cost for
Brakes India. The server re-derived the economics rather than trusting the body,
and returned `required_authority: OWNER`, title *"This price does not cover what
the item costs us"*.

**The manager was refused, clearly:**

```
POST /api/v1/approvals/{id}/decide  {"status":"APPROVED"}   (manager token)
HTTP 403 {"detail":"Selling below what the item cost us is the owner's decision"}
```

**Cost and margin are absent for the salesperson, not hidden.** The same request
fetched with the salesperson's token has no `subject` key at all; the strings
`unit_cost`, `margin`, `gross_profit`, `manager_detail` and `impact_amount` do
not appear anywhere in the response.

**Owners can approve their own requests**, as `CLAUDE.md` says they must — and
this is genuinely policy-driven, not an accident. With
`allow_self_approval = false`:

* owner raises and approves own request → `HTTP 200`, `APPROVED`
* manager raises and approves own request → `HTTP 403 "You cannot approve your
  own request"`

### What gets captured — good, with one hole

The signed below-cost record (`approval_requests`) carries a full economics
snapshot frozen at request time (quoted ₹300, unit cost ₹349, margin −16.33%,
gross profit −₹2,450, the `NEGATIVE_MARGIN` exception with its
`MIN_MARGIN_PRICE` reference code and ₹5,290.70 impact), the requester, their
`reason_code` and free-text reason, the decider, both timestamps, an
append-only `thread`, and `thresholds_version: ci_033195fae1`.

**The hole: `decision_note = None`.** Approving a below-cost line in the UI is a
single click — no dialog, no note field (`/tmp/shots/09-approvals-owner.png`,
`12-approved.png`). The field exists and works when supplied via the API. So the
one action in the product that means *"I am choosing to lose money"* records
who and when but never why — while "Do something different" on a decision
*does* open a dialog and demand a note. See finding 6.

### Human accept/modify/reject on a decision — not auditable later

I actioned a MARGIN_DETERIORATION decision through the UI with a note, and it
was stored properly:

```json
{"action":"ACT","actor_user_id":"usr_owner","acted_at":"2026-08-08T16:39:06.026366+00:00",
 "note":"Owner: holding price for this account; renegotiating the supply cost with the principal instead."}
```

Then I pressed the equivalent of Undo (`POST /{id}/action {"action":"REOPEN"}`,
exactly what `papi.reopen` sends). The record became:

```json
{"action":"REOPEN","actor_user_id":"usr_owner","acted_at":"2026-08-08T16:39:51.609718+00:00",
 "note":"Undone by the user"}
```

The owner's reasoning is **gone**. `repositories.record_human_action` assigns a
single object rather than appending, so only the last action survives — while
`frontend/src/platform/api.ts:121` documents the opposite in as many words:
*"The reopen is itself recorded, so the audit trail keeps both the action and
its reversal."* The platform already has the right pattern next door: approval
requests keep an append-only `thread`. See finding 2.

### Break-glass is logged, not silent — CONFIRMED

Exercised through the platform's own `app.trust.access` code path:

* reach with no grant → `AccessDenied` raised, nothing logged, nothing served
* grant with a 3-character justification → refused (`ValueError`)
* a real grant, two uses, then a revoke → **four separate events**

Read back through the customer-facing `GET /api/v1/trust/access` as the owner:

```
GRANTED   TICKET-4471: owner reports margin screen blank; inspecting customer_item_metrics…
ACCESSED  customer_item_metrics
ACCESSED  approval_requests
REVOKED   Revoked by usr_owner
```

Per-*use* logging, not just per-grant; a 4-hour default TTL; and the endpoint
carries the promise back to the customer: *"Every entry above is recorded at the
moment it happens and cannot be edited or removed."* This is exemplary — and
the owner has no screen on which to see any of it (finding 3).

---

## 5. AI honesty

**No number in this product could have come from the model. I looked for one
and did not find it.**

* **Zero digits in any user-facing AI text.** Across all 5 seeded decisions I
  concatenated `recommendation + explanation + caveat` and regex-searched for
  digits: **none**, in any of them. The model phrases; it does not quantify.
* **The separation is visible on screen.** The decision detail
  (`/tmp/shots/13-decision-detail.png`) puts *"FACTS · WHAT THE DATA SHOWS"*
  first — each fact with its source (`invoice`, `bill, invoice`) — then
  *"EVIDENCE USED · zoho · invoice · 3 records"*, and only then *"AI
  RECOMMENDATION"*, closing with *"Interpretation only; figures come from the
  signal."*
* **The AI's bounded influence is disclosed, numerically.** The detail shows
  `priority 57/100 · base 52 · ai +5`. The owner can see exactly how much the
  model moved the ranking. It was `+5` on all five decisions — with the offline
  mock that is honest but inert; it adds no discriminating signal.
* **Grounding facts are shown with their data class.** Margin-deterioration
  facts are correctly flagged `restricted` (`baseline_margin_pct`,
  `prior_unit_cost`, `recent_unit_cost`, …) and served to the owner.
* **The model does not see customer names.** Stored interpretation titles carry
  pseudonyms — *"Customer Decline: Customer C-HKRSDB"*, *"Margin Deterioration:
  Item P-PMPRCF"* — while the screen shows "ACE Designers". The name vault is
  doing its job, and the pseudonymous title is not rendered.
* **Telemetry is real and matches the brief exactly.** `ai_call_logs` seeded
  with **5 rows**, every one `provider = mock`, `model = mock-1`,
  `ai_status = OK`, `attempts = 1`, `cache_hit = 0`, with token counts and a
  derived cost. No prompt or response content is stored, as documented.
* **The layer invariants are intact.** Both `CLAUDE.md` §1 greps print nothing,
  and `tests/decision_platform/test_layer_boundaries.py` passes (6 passed).
* **The health band refuses to infer.** `GET /api/v1/internal/ai-metrics`
  reports `band: INSUFFICIENT_DATA` with *"Fewer than 20 calls in window; no
  health inference drawn."* rather than reporting a flattering 0% degraded rate
  as if it meant something. Manager gets `403`.

Two blemishes, neither of which is a fabricated number: the metrics have no UI
at all (finding 3), and `/trust/disclosure` names a model that was never called
(finding 9).

---

## 6. Findings

### MAJOR

**F1 — Nothing forces the seeded default password to be changed. CONFIRMED.**
`POST /api/v1/auth/login` returns `must_change_password: true` for
`s.menon@pie.example`, and the app signs straight through to the dashboard
(`/tmp/shots/02-after-initial-signin.png`). There is no interstitial:
`PlatformApp.tsx:340` is `if (!session) return <SignIn …>` and nothing else
consults the flag. The only frontend readers are `AdminScreens.tsx:944`, which
renders it as the word "must change" in the user grid, and the voluntary
password form. All three seeded accounts therefore keep the published password
`change-me-now` indefinitely — I signed in as manager and salesperson with it
after the review had been running for an hour. *Expected:* first sign-in blocks
on a password change, as the account lifecycle and the flag's name both imply.

**F2 — A human action on a decision overwrites the previous one, destroying the
recorded reason. CONFIRMED.** `repositories.record_human_action` (line 890)
assigns `decision.human_action = {…}` rather than appending. Evidence in §4: an
owner's ACT note was permanently replaced by the REOPEN entry. `api.ts:121`
promises the opposite. *Expected:* an append-only trail, as
`approval_requests.thread` already does.

**F3 — Every owner-only trust and AI-ops surface is unreachable from the UI.
CONFIRMED.** `grep -rn "ai-metrics\|trust/" frontend/src/` returns **nothing**.
Seven working, owner-scoped endpoints have no screen: `/internal/ai-metrics`,
and `/trust/{disclosure,payloads,access,export,erasure}`. The only `internal`
call the frontend makes is `demo-seed`. The `/states` screen is a showcase of
*designed states*, not the ops metrics. *Expected:* the owner can see what the
AI costs, who from the vendor opened their data, and how to export or erase it,
without a database client.

**F4 — Commercial decisions render as raw enum names against raw composite keys
in the owner's main queue. CONFIRMED.** After a sync, `/decisions` lists 10
rows, 4 of which read e.g. `CI_MARGIN_DECLINE_NO_VOLUME` /
`cst_pitti::prd_dnmg` (`/tmp/shots/17-decisions-after-sync.png`). This is a
backend defect as much as a frontend one — the detail endpoint returns
`subject_label: "cst_pitti::prd_dnmg"` and
`interpretation.title: "Ci Margin Erosion: Entity C-QQTCZH"`. The type name is
the §2 "responsibility duplication" failure exactly: `format.ts:6 TYPE_LABEL`
(used by the queue, via `ui.tsx:172 TYPE_LABEL[t] || t`) has no `CI_*` entries,
while a *second* map `CommercialScreens.tsx:61 SIGNAL_LABEL` has them all
("Margin eroding", "Cost not passed on"). One concept, two owners, and the
queue got the incomplete one. *Expected:* "Margin eroding · Pitti Engineering
Ltd · DNMG 150608-MP insert".

**F5 — The owner's "Erosion threshold" does not govern the margin-deterioration
decisions they see. CONFIRMED.** Two independent thresholds answer the same
business question with different defaults:

| | value | editable by owner? | governs |
|---|---|---|---|
| `CommercialThresholds.min_margin_deterioration_pp` | 3 pp | **yes**, Settings | commercial screens (`commercial/detectors.py:123,152`) |
| `SignalThresholds.margin_drop_points` | 5 pp | no — env only | the `MARGIN_DETERIORATION` decisions in the queue (`signals/margin.py:63`) |

Proof: I set the Settings field 3 pp → 20 pp; the commercial detectors went
silent (recompute dropped from four `CI_*` types to `{'CI_COST_NOT_PASSED': 1}`)
while `SignalThresholds.version` stayed `th_4bca9e0a59` with
`margin_drop_points = 0.05`, untouched. An owner who raises the erosion
threshold to stop the noise will watch the commercial screens quieten and the
decision queue carry on. *Expected:* one threshold, or two that say plainly
which is which. (Restored to default afterwards.)

**F6 — Signing a below-cost line captures no rationale, because the UI never
asks. CONFIRMED.** Clicking "Approve" on the below-cost request decided it
immediately — no dialog, no note (`/tmp/shots/10-approve-dialog.png` shows the
queue already empty). Stored `decision_note = None`. The same screen's "Ask for
a different price" and the decision screen's "Do something different" both open
a dialog and require text (`/tmp/shots/14-modify-dialog.png`, dialog buttons
`['Cancel','Log decision']`). The API accepts a note and stores it correctly
when one is supplied. *Expected:* the one irreversible commercial concession in
the product should ask why at least as insistently as a routine modify does.

### MINOR

**F7 — Signals do not carry `CommercialThresholds.version`. CONFIRMED.**
`CLAUDE.md` says it is "stamped on every metric row, signal and quote
snapshot". Signal Engine signals carry `signals/config.py`'s independent
`th_`-prefixed hash (`th_4bca9e0a59`); only commercial-detector signals carry
`ci_`. So a `MARGIN_DETERIORATION` decision cannot be traced to the commercial
policy in force when it fired. *Expected:* either both versions on the row, or
the document narrowed to what is true.

**F8 — Metric rows are restamped in place, so there is no version history.
CONFIRMED.** `compute._upsert` upserts on `(org, customer, product)` and sets
`row.thresholds_version = th.version`. After my final full recompute all 7 rows
read `ci_e73ef9d815` and the earlier `ci_f7de764806` numbers no longer exist
anywhere. The stamp explains the *current* number honestly; it does not let you
reconstruct last month's. Signals are append-only and do not have this problem.
Worth stating in the docs, since "past numbers stay explainable" reads as a
stronger promise than the read model delivers.

**F9 — `/trust/disclosure` names a model that was never called. CONFIRMED.**
Returns `"provider":"mock"` alongside `"model":"claude-haiku-4-5-20251001"`,
because `disclosure.statement()` reports `settings.AI_MODEL` (default
`claude-haiku-4-5-20251001`) regardless of provider, while every one of the 15
`ai_call_logs` rows records `mock-1`. On the one screen whose entire job is to
tell the owner truthfully where their data goes, the model field is wrong.
*Expected:* the model actually in use, or no model when the provider is offline.

**F10 — A salesperson clicking "Settings" gets a raw API error. CONFIRMED.**
Settings is offered in the salesperson's nav; opening it renders the banner
**"Manager or owner role required"** above their account panel
(`/tmp/shots/24-settings-salesperson.png`) — the `403` detail string from
`/admin/policy` surfaced as an error. The screen is otherwise correctly scoped.
*Expected:* don't fetch what this role cannot have, or say "Organization
settings are owner-only."

**F11 — A sync with nothing connected reports success without labelling the
source. CONFIRMED.** With zero connections, `POST /api/v1/data/sync` returns
`202` and completes: **"COMPLETED · Finished cleanly · Customers 3 · Items 2 ·
Sales lines 75 · Cost records 25 · Signals 5 · Decisions 5"**
(`/tmp/shots/17-data-after-sync.png`), writing fixture records into the read
model. The API response does carry `source: "fixture"`, and the top of the same
page does say *"None connected yet · running against the offline sample
source"* — so this is not a lie, but the result block itself reads exactly like
a successful pull from the owner's books. *Expected:* the sync result names its
source, e.g. "Succeeded — sample source".

**F12 — The AG Grid theme is hardcoded, so a palette change will not reach any
grid. CONFIRMED.** `platform/DataGridImpl.tsx:26-40` sets seven literal colours
(`#5980a6`, `#ffffff`, `#1d1f20`, `#6b6f76`, `#eef6ff`, …) plus
`browserColorScheme: "light"`, with the comment *"read off styles.css
deliberately"*. `ui-standards.md` §11 says a literal in a component "is a value
that will not follow", and §"Aligned" lists the grid wrapper as done. Since §3
routes every table in the product through this wrapper, this is the single
literal with the widest blast radius.

**F13 — `ui-standards.md` §10 documents four components that do not exist.
CONFIRMED.** It states *"`RiskCard`, `InsightCard`, `ActionCard` and `TrendCard`
are `Card`"*. `grep -rn "RiskCard\|InsightCard\|ActionCard\|TrendCard"` returns
nothing, and `grep -rn "<Card\b"` across the whole frontend returns **nothing** —
there is not one `Card` in the application.

**F14 — The raw-`<table>` enumeration is already incomplete. CONFIRMED.** The
document invites the reader to check it with `rg -n '<table' frontend/src`.
Running it finds tables the table does not list: `CommercialScreens.tsx:557`
(`dp-table ci-table`, the volume-against-margin periods),
`PlatformApp.tsx:1117` and `:1247` (`facttable`), `viz/BookFlow.tsx:211` and
`viz/Mix.tsx:394` (both `class="grid"`). **Each one is individually correct**
under §3/§13 — fact panels, fixed-shape chart twins, a handful of comparison
periods. The defect is only that the document promises an enumeration and the
enumeration has already drifted, which is precisely the failure mode it was
written to prevent.

**F15 — `CLAUDE.md` calls the Quote Builder line table a hand-written
`<table>`; it is not. CONFIRMED.** The digest says it "stayed a hand-written
`<table class="grid">` through three UI passes", and uses that as the worked
example for the grid rule. `frontend/src/components/LineGrid.tsx:420` renders
`<DataGrid<Row>`; the page carries **0** raw `<table>` elements and 3 AG Grid
rows. `ui-standards.md` is the accurate one ("Two tables have been converted").
The stale digest is the paragraph people actually read.

**F16 — A customer with no GSTIN can never be linked, and its revenue silently
splits. CONFIRMED.** Customer identity matches on GSTIN only
(`identity/matchers.py: by_gstin`; the screen says so: *"Matched on GSTIN — a
registration issued by the government, not by any ERP"*). After the sync there
are **two** `Pitti Engineering Ltd` customer rows — the seeded `cst_pitti` and
the synced `981b3d85…` / `cst-1001` — and `/composition` lists them as two
separate contributors, ₹56,160 and ₹15,660
(`/tmp/shots/25-composition-post-sync.png`). `/identity` raises no suggestion
and reports *"Nothing to review … None means no exact matches were found."*
Two distinct causes hide behind that one sentence: the seeded row has no
connector record at all, so it is not eligible for matching; and the synced
`Kirloskar` record has `gstin: None`, so it can never match anything either.
In a real Zoho book, unregistered and consumer contacts routinely have a blank
GST number, and for those the platform will keep creating parallel customers
and dividing one relationship's revenue between them, while the identity screen
says there is nothing to review. *Expected:* "no eligible key" reported
distinctly from "no match found", and a name-based *suggestion* (never an
automatic link) where GSTIN is absent. Half of this is an artifact of my own
fixture sync layered on seed data; the GSTIN-only eligibility gap is not.

**F17 — Exact product names from the read model resolve as AMBIGUOUS.
SUSPECTED.** Pasting `CNMG 120408-MP insert 50 nos`, `DNMG 150608-MP insert 25
nos`, `8.0mm HSS-Co machine reamer 10 nos` — the verbatim `products.name` values
— produced three rows all marked *"Not resolved — choose the intended product ·
unresolved · manual review · AMBIGUOUS"* (`/tmp/shots/23-quote-resolved.png`).
Marked SUSPECTED because PIE resolves against the 6717-product catalogue rather
than the 6 rows in this read model, so ambiguity is a defensible answer and
refusing to guess is the documented design. Still worth a look: if the owner's
own item master cannot resolve itself, every line is manual.

### POLISH

**F18 — The Settings password form is not MUI.** `AdminScreens.tsx:1119` uses
raw `<input className="input" type="password">` where §8 calls for `TextField`;
every other form on the screen is MUI. Chromium also logs *"Password forms
should have (optionally hidden) username fields for accessibility"*.

**F19 — The decision's HUMAN LOG does not say who acted.** It renders
`ACT · <note> · 8 Aug 2026`. `actor_user_id` and a full ISO timestamp are
stored; neither is shown. In a three-person business that is the first question.

**F20 — Dead CSS.** `styles.css:284` still carries
`@media (prefers-reduced-motion: reduce) { .skeleton { animation: none; } }`
guarding a `.skeleton` class the same comment says is gone.

**F21 — `thresholds_version` is stored on an approval request but omitted from
its API representation.** `approvals.to_dict` does not include it, so the UI
cannot show which policy a past signature was judged against even though the
column is populated.

---

## 7. What works well

* **The versioning property, end to end.** Section 3 is the evidence. Two policy
  versions coexisting in one table, a content hash that round-trips exactly when
  an override is cleared, and a manager screen that names the version in force,
  says it differs from the default, and timestamps the edit.
* **Refusals that explain themselves.** *"Selling below what the item cost us is
  the owner's decision"*, *"You cannot approve your own request"*, *"This is the
  only owner — promote someone else before changing it"*. Not one bare 403.
* **Absent, not masked.** The salesperson's view of a below-cost request has no
  `subject` key at all. There is nothing to read out of a network tab.
* **Empty states that name the number responsible.** `/opportunities`: *"2 of 4
  relationships have a real gap, but every one is below your 10,000 materiality
  floor — the largest is 3,352. Lower the floor in Settings to see them, or leave
  it: below this, a gap is real and not worth an afternoon."* That is a product
  arguing its own case with the owner's own policy.
* **Honest degradation, everywhere.** `/data` leads with *"No Zoho company is
  connected, so every screen is showing sample data or nothing at all."*
  `/dependency`: *"0% of revenue could be traced to a principal (₹3,29,831 could
  not)."* `/identity`: *"None means no exact matches were found — not that
  matching is switched off."* `/account/cst_rane`: *"Margin — no month has both
  revenue and a cost."* Not one confident zero anywhere in 28 screens.
* **The AI is fenced in and says so.** Zero digits in any AI text, a visible
  `base + ai adjustment` breakdown, pseudonymised subjects, a health band that
  declines to infer below 20 calls, and no prompt/response content stored.
* **Break-glass.** Refuses a thin justification, refuses an ungranted reach,
  logs every individual use, and exposes it on a customer-facing endpoint with
  no suppression mechanism.
* **The approval economics are re-derived server-side**, not taken from the
  request body — an approval cannot be for numbers the requester typed.
* **The Quote Builder grid.** AG Grid through the shared wrapper, states as
  `Chip`s, keyboard affordances spelled out on screen (*↑↓ navigate · Enter
  supply options · Space select · F2 edit the rate · / search · Esc close*), and
  nothing auto-priced.
* **Migration discipline.** A fresh clone migrated to head cleanly and
  `/api/health` reports state, revision and pending count rather than `ok: true`.

---

## 8. UI-standards observations

Measured against `docs/ui-standards.md`, not from memory.

**Holding well.** MUI is unambiguously the design system (`Button` ×29, `Box`
×17, `TextField` ×16, `Alert` ×8, `Dialog` family ×16). Loading is MUI
`Skeleton` through `kit.tsx:311`; no hand-rolled shimmer survives in TSX. Status
is `Chip` — the approvals queue shows `OWNER ONLY` and `PENDING` as chips, the
quote grid renders every line state as a chip carrying a word. The hand-rolled
`.dp-modal` CSS is now unreferenced. Tables are right: the user directory and
the quote line grid are both AG Grid through `platform/DataGrid.tsx` (F15), and
the `<table>`s that remain are fact panels and chart twins.

**Surfaces.** `Paper` (via `Bp`) is used consistently — 13 in `AdminScreens`, 11
in `CommercialScreens`, 9 each in `ConnectionsPanel` and `DataScreen`. But §2's
counterpart rule has no implementation at all: **zero `<Card>` in the codebase**,
and the four `Card` components §10 names do not exist (F13). Whether that is a
gap or a doc error, the document and the code disagree.

**Theme tokens.** One systemic breach: the AG Grid theme is seven literals
(F12), so the wrapper §3 makes mandatory is the one place a palette change will
not reach. Elsewhere the discipline holds — `viz.css` colour was audited case by
case and the surviving hues are chart encodings with legends, which §6 permits.

**Forms.** The password form is the outlier (F17); everything else on Settings
is `TextField`/`Select`.

**Accessibility.** `role="alert"` on the sign-in error, ARIA labels on the
family-margin inputs (`Target margin for solid_carbide_drill`), `View as a
table` fallbacks under the journey and cadence charts, and visible keyboard
hints on the quote grid. The margin-policy number inputs carry no `aria-label`,
though they sit under visible label text.

**Documentation drift is the real UI-standards finding.** Three of the
document's own claims are now false (F13, F14, F15). §"Where this codebase
stands" says it is "an enumeration rather than a promise, and the next reader
can check it in a minute" — I checked it in a minute and it had drifted, which
is the outcome that section was explicitly written to prevent.

---

## 9. Empty-but-correct

Row counts checked in `backend/data/platform.db` before calling anything empty.

| Screen | State | Verdict |
|---|---|---|
| `/stock` | "Stock has not been folded into business state yet. It is built at the end of every sync — run one, and this screen fills in." | **Correct.** `business_states` = 0 rows. Names the mechanism and the fix. |
| `/supply`, `/payables`, `/payments` | "No sales history has been synced yet, so … cannot be computed. Connect a Zoho company and run a sync." | **Correct.** No connector; suppliers/POs/payments genuinely absent. |
| `/targets` | "Nothing in Zoho holds a principal's target, so they are typed in once and kept." | **Correct**, and unusually good — explains *why* the source cannot supply it. |
| `/opportunities` | "2 of 4 relationships have a real gap, but every one is below your 10,000 materiality floor — the largest is 3,352." | **Correct and the best empty state in the product.** Data exists; policy suppresses it; both numbers shown. Exactly the behaviour `CLAUDE.md` demands — and I did not lower the floor to make it fill. |
| `/identity` → To review | "None means no exact matches were found — not that matching is switched off." | **Correct.** With one connector there are no cross-connector duplicates to suggest. |
| `/mix` (pre-sync) | "Nothing has been traded yet." | **Right emptiness, misleading wording.** 26 sales lines existed at the time and `/composition` rendered revenue by customer from them. Product mix was empty because no item was placed in a line (`/item-lines`: "0% of 4 items are placed in a line") — not because nothing had traded. Post-sync, once two items acquired a category from their HSN heading, the screen fills (2 customers, 5 lines — `/tmp/shots/25-mix-post-sync.png`), which confirms the diagnosis. The emptiness was correct; only the sentence was wrong. |
| `/trust/access` (pre-test) | `events: []` with the standing note | **Correct.** No staff had opened the tenant. |
| Company filter | Renders nowhere | **Correct by design.** `CompanyFilter.tsx`: "Nothing renders below two companies … a select with one option is a control that does nothing." One seeded organization, no connections, so per-row attribution honestly reads "Source not recorded". |
| AI health band | `INSUFFICIENT_DATA` | **Correct.** 5 calls against a 20-call minimum; refuses to infer either way. |

---

## 10. Not covered, and why

* **Cross-connector identity linking.** With one connector there is nothing to
  link across, so "linked, never merged" could not be demonstrated end to end.
  What I did confirm: the API returns an identity holding a *list* of `records`
  with `connector_count: 1` and each connector record preserved separately —
  the right shape — and `identity/service.py` offers link / suggest / unlink
  with no merge operation anywhere. What I did **not** observe is two connector
  records converging on one identity. F16 is what I found instead, and it is a
  narrower point: records with no GSTIN are never eligible for matching at all.
* **The three legal entities.** SLS Engineers, 4U Precision and UPS do not exist
  as data — one organization (`org_pie`) is seeded and `/connections` is
  empty. Multi-entity behaviour, and the company filter's real job, are untested.
* **A real AI provider.** Everything ran on the offline `mock`/`mock-1`. The
  grounding gate's rejection paths (`UNGROUNDED_NUMBER`, `SCALE_VIOLATION`) and
  the `DEGRADED`/`FAILED` statuses were never exercised, because the mock never
  produces a number to reject. `tests/live/` exists for this and needs
  credentials. My AI-honesty conclusion is therefore "nothing ungrounded was
  produced", not "the gate was proven to catch one".
* **Erasure.** `POST /api/v1/trust/erasure` is the only destructive route in the
  application and destroys the tenant data key. I read it (`GET` returns
  `{"erased": false, "key_destroyed": false}`) and deliberately did not fire it —
  it would have ended the review.
* **User deactivation and role change.** Verified server-side (`403` for a
  manager creating a user; the last-owner and self-role guards read in
  `admin.py`) but I did not click Deactivate or change a role in the UI, to
  avoid leaving the sibling sessions reviewing manager and salesperson roles
  locked out of accounts they need. This is a deliberate omission, not an
  oversight.
* **Outcome capture.** `Outcome` is documented as deliberately not built and the
  decision screen says so honestly: *"Outcome measurement runs on later Zoho data
  and will appear here when available."* Nothing to test.
* **`make verify`.** Not run — this is a behavioural review, and the gate is a
  contributor step.
