# User flow audit — do the flows actually work?

`user-flows.md` maps what each flow is *meant* to do. This is the companion
that asks whether it *does*, by executing the flows rather than reading them.

**Method.** A throwaway SQLite database bootstrapped from `alembic upgrade head`
plus the seeded org (owner / manager / salesperson, and a demo book with a
recorded purchase cost of ₹349 on `prd_cnmg`), driven through
`app.main.app` — the whole app, so router mounts, plan gates, role guards and
lifespan startup are all in play, with the **real** pie-parser engine loaded
(6,717 products from the corpus). Every check below is a documented
expectation from `user-flows.md`; a deviation is a finding. The pie-parser CLI
flows were run directly. The three findings whose subject is UI feedback were
additionally confirmed in Chromium against a running dev server — §2 F2–F4
records what was clicked and what appeared.

**Result.** Over 100 behavioural checks across both repositories. **Four
defects, all now fixed with regression tests**: one genuine functional defect
(RFQ quantity misreading, contained by the send gate) and three messages the
interface set and never rendered. Every invariant the business depends on —
cost containment, approval authority, the identity gate, the send gate —
**holds under direct attack.**

---

## 1. What holds

### Cost and margin never reach a salesperson — verified, not assumed

The invariant was attacked from four directions at once, on a line priced
₹200 against a recorded cost of ₹349:

| Probe | Salesperson sees | Manager sees |
|---|---|---|
| Assessment exception codes | `APPROVAL_REQUIRED`, `NEW_RELATIONSHIP` | `NEGATIVE_MARGIN`, `NEW_RELATIONSHIP` |
| Quote payload | no cost / margin / floor / MFLOOR key | economics present |
| Approval request (own) | `required_authority: APPROVAL_REQUIRED`, `requires_rationale: false` | `required_authority: OWNER`, `can_decide: false` |
| Approval queue | no economics, no rule name | full economics |

The collapse is real: the salesperson is told *that* the line needs approval
and never *why*, while the control still reaches them (a ₹200 line and a
₹5,000 line produce different answers). A **cross-request price walk** —
₹100 → ₹5,000 in six separate requests, which the per-request guard cannot
see — returned an identical answer at every price. The four anti-walk guards
all fire: 5 distinct prices for one product → 400; 4 → allowed (a real
quantity-break quote still works); a 201-line batch → 400; an `as_of` outside
±90 days → refused.

### Below cost is the owner's signature — enforced on the server

Driven directly against the API, bypassing any UI:

- Manager POSTs the approval → **403**.
- Owner approves with no note → **400**, a rationale is required.
- Owner approves with a rationale → **200**.
- Anyone re-decides it → **409**.
- Manager tries to approve their **own** request → **403** (self-approval off).
- The manager's queue contains only the MANAGER-authority request;
  `pending_for_me` is 0 for them and 3 for the owner — the badge never counts
  work the viewer cannot do.
- The signature lands in the hash-chained audit log (`APPROVAL_DECIDED`) and
  the chain **verifies unbroken**.

### The forced password gate exists and is enforced

`docs/reviews/role-review-sales-manager.md` records this as *"MAJOR · The
forced first-sign-in password change does not exist — CONFIRMED"*. It exists
now: a seeded account signs in with `must_change_password`, every other path
403s, `/auth/me` stays open so the client can draw the gate, a wrong current
password is 403, a short new one 400, and the successful change returns a
fresh token so it does not sign its own user out. **7/7.**

### The engine resolves, and abstains, correctly

| Input | Result |
|---|---|
| `2001174` (catalogue material number) | **EXACT** — "Exact manufacturer identity" |
| `CNMG 120408-49 - TN2000` (a description) | **AMBIGUOUS**, candidate offered, nothing selected |
| `WHOLLY UNPARSEABLE GIBBERISH XQZ` | no product, never guessed |
| `CUST-XYZ-999` scoped to a customer | **UNRESOLVED**, explicitly *not* cross-matched against the manufacturer catalogue |

Unknown really does stay unknown. The send gate then refuses the quote and
names the offending lines.

### The identity gate refuses everything but the engine's own proposal

Over the public API: confirming a different record → `recorded: false`;
omitting the customer → 422; even confirming the *exact* hit → `recorded:
false`, because an EXACT resolution is not a `NEEDS_REVIEW` proposal and there
is nothing to confirm. Every refusal is a 200 with one uniform reason — no
status code tells a caller which half of the guess was right.

### Role gates, the machine surface, and health

- 16/16 role-gate probes: eight manager-only surfaces refuse a salesperson;
  eight owner-only surfaces refuse a manager. A manager cannot edit policy or
  create users; not even the owner can change their own role.
- API keys: only the owner mints; the secret is returned once and never again;
  the key defaults to the **narrowest** role, not the creator's; revocation is
  immediate and idempotent; an unknown id is a uniform 404.
- Resolution API: RESOLVED / ABSTAINED / 401 / 422 all as documented, rate-limit
  headers on every answer, OpenAPI served without a credential.
- `/api/health`: `CURRENT` → 200; an empty database → **503 EMPTY**; one
  revision down → **503 BEHIND**, naming the pending revision and the exact
  command, with "No data is lost."

### pie-parser

`make verify` green: **406 tests, 6,717 corpus rows, 0 quarantined, no family
below 100%, byte-identical reruns.** `eval_identity`: 13/13 cases, **0
false-positive identity** (the cardinal error). CLI exit codes as documented
(2 on bad arguments / missing source / unknown record id; 0 on a clean lint).
pie-portal's own suite: **3,553 passed, 0 failed**, and the full gate
(`make verify`) green end to end — frontend build, migrations from nothing
on SQLite *and* PostgreSQL, row-level security, the queue's concurrent
claim, and the `pg_dump` → restore drill.

---

## 2. Findings

### F1 · MODERATE · An RFQ line's part number can be misread as its quantity — **FIXED**

> Fixed in the commit that follows this audit; the reproductions below are kept
> in the past tense because they are the reason the guards exist, and
> `tests/test_rfq_splitting.py` now pins all of them. The fix is described at
> the end of this finding.

`backend/app/store.py` `_split_rfq`. Two related over-reads, both reproducible:

| Pasted line | Read as | Should be |
|---|---|---|
| `2001174 nos` | code `s`, qty **2,001,174** | code `2001174`, qty unstated |
| `CNMG 120408 nos` | code `CNMG`, qty **120,408** | code `CNMG 120408`, qty unstated |
| `DNMG 150608 nos` | code `DNMG`, qty **150,608** | code `DNMG 150608`, qty unstated |

Cause. `_UNIT_WORDS` matches a *prefix* of the following token, so in
`2001174 nos` the engine reads `no` as the unit and leaves `s` as the product
code. Separately, the `_BARE_QTY_DIGITS = 4` sanity cap that correctly stops
`DNMG 150608` being read as 150,608 units is **not applied** when a unit word
is present — so the code's own digits become the quantity.

The comment at `store.py:129` states the opposite: *"A unit word has to be
present — that is what makes this safe on a code whose own tail is numeric."*
The unit word is precisely what makes it unsafe, because it bypasses the cap.

Impact, stated honestly. **Contained.** The mangled code (`s`, `CNMG`) does not
resolve, so the line lands AMBIGUOUS and the send gate refuses the quote by
name — verified end to end. No wrong quote can reach a customer through this
path. What it costs is a nonsense line the salesperson must work out, on
input that is ordinary Indian-market phrasing (`nos` is the common unit word),
and it contradicts the module's own promise that a quantity is "flagged, never
defaulted" — here one is *invented* from the product code.

**The fix.** Two changes in `_QTY_PATTERNS`, each aimed at one of the causes:

1. A `(?![A-Za-z])` guard after `_UNIT_WORDS` in the leading rule, so a unit
   word can no longer match a *prefix* of the next token. `nos` must be `nos`,
   not `no` with the `s` left behind as a product code.
2. The trailing "`<code> 100 nos`" rule split in two: an explicit separator
   (`,` `:` `-` `–` `—`) or a `qty` keyword keeps the bound lifted, while a
   whitespace-only separator keeps `_BARE_QTY_DIGITS`. The unit word no longer
   lifts the bound on its own, because it sits *after* the number and says
   nothing about which digits were meant.

After the fix these lines carry no quantity and travel `proposed` — status
`CONFIRM READING`, a technical blocker a person clears one line at a time —
which is the documented handling for a unit word that was stated and could not
be attributed. Verified end to end through the live app:

| Pasted | Before | After |
|---|---|---|
| `2001174 nos` | code `s`, qty 2,001,174 | code `2001174`, **CONFIRM READING** |
| `CNMG 120408 nos` | code `CNMG`, qty 120,408 | code intact, **CONFIRM READING** |
| `2001174 - 250000 nos` | qty 250,000 | qty 250,000 — unchanged |
| `100 nos 2001174` | qty 100 | qty 100 — unchanged |

`tests/test_rfq_splitting.py` gained three cases (11 assertions). All seven
new parametrisations fail against the old patterns and pass against the new
ones, so they are regression tests rather than descriptions.

Deliberately **not** changed: the fallback still leaves the unit word in the
code (`CNMG 120408 nos` keeps its `nos`). Stripping it there would make the
line resolvable as well as flagged, but `_UNIT_WORDS` includes `ea` and
`each`, so a description legitimately ending in one of those would be
truncated — a wider change than this defect justifies, and the line is blocked
either way.

### F2 · MINOR · A failed workspace switch told the user nothing — **FIXED**

`switchOrganization` caught the refusal and called `setNotice(...)`, but
`notice` has exactly one renderer — the sign-in card, inside `if (!session)`.
A refused switch leaves the session intact, so the shell stayed mounted and
that renderer was never reached: the switch failed in silence. The comment
above the call said "quiet beyond the toast", and there was no toast. Worse,
the sentence persisted in state and could surface at the *next* sign-out,
describing something that had happened long before.

Now `flash(...)`, the notistack helper the rest of the shell already uses.

### F3 · MINOR · "Signed in as … in another tab" was never shown — **FIXED**

Same cause. The tab adopted the new account correctly and said nothing, so the
name and role changed under the reader with no explanation. (The sibling case —
signed *out* elsewhere — did display, because there the session becomes null
and the sign-in card renders.)

Now a toast, and raised *outside* the `setSession` updater. The message used to
sit inside it, and React may call an updater twice under `StrictMode` — which
`main.tsx` enables — so a toast fired from there would have been shown twice.
The handler reads the current session through a ref instead, which also removes
the reason the updater was reached for: the listener is registered once and
would otherwise compare against the session that existed when it was registered.

### F4 · MINOR · The Data screen's error offered no retry to a salesperson — **FIXED**

`ErrorState` was rendered without `onRetry`, so the kit's "Try again" button
never appeared, and the only re-fetch — "Refresh status" — sits inside the
manager-only branch. For a salesperson the honest error was the end of the
road. It now passes `onRetry={load} busy={checking}`, the state the loader
already maintains.

**Evidence class.** F2–F4 were found by reading rather than by executing the
UI, and each is now pinned twice.

In the suite, by `platform/PlatformApp.messages.contract.test.ts` — a
source-level contract test in the idiom `kit.contract.test.ts` already uses,
rather than a mount of the whole application to read a toast. Its four
behavioural assertions fail against the unfixed source; two further assertions
guard against a vacuous pass (that `notice` still has exactly one renderer
behind the signed-out gate, and that it is still used for the two messages that
*do* end signed out).

And in a browser, because the one thing a source-level test cannot answer is
whether the message reaches the reader. Chromium driven against `npm run dev`
in front of the live backend, seven checks, all passing:

| Checked | Result |
|---|---|
| F3 · a sign-in in another tab | Toast "Signed in as D. Other in another tab." visible — and raised **once**, so the `StrictMode` double-fire the ref was introduced to prevent does not happen. |
| F2 · a workspace switch refused, the membership revoked server-side mid-session | Toast "That workspace could not be opened. It may no longer be yours." visible; the reader stays on the screen they were on. |
| F4 · the Data screen's error, **as a salesperson** | "Try again" rendered, the manager-only "Refresh status" absent, and pressing it re-fetches — status requests 2 → 3. |

Legibility was measured rather than judged from a whole-page screenshot: both
toasts render fully inside the 900 px viewport (F2 at y=838, F3 at y=857, each
36 px tall), and the close-ups show the full sentence. F4 was re-run
specifically as the salesperson rather than the manager, because the role is
the finding — the manager already had a re-fetch and would have passed a check
that proved nothing.

---

## 3. What looked broken and was not

Recorded because a reader who re-runs this should not re-raise them:

- **A descriptive line abstaining.** `CNMG 120408` returning AMBIGUOUS is the
  documented correct answer — it is an ISO designation, not a catalogue
  material number. Feeding the engine a real MM# resolves EXACT.
- **`NO_COST_BASIS` shown to a salesperson.** Its text is "No purchase cost on
  record" — an absence statement carrying no value, not a leak.
- **`ModuleNotFoundError: identity` on confirmed mappings.** An artifact of a
  harness that never ran app startup; on the real startup path the mapping
  store loads. The degradation it triggers (log, fall back to defaults, never
  fail the request) is the documented behaviour.
- **405 on `GET /admin/users/{id}`.** No such route exists — only PATCH.
- **The manager's approval queue omitting a request.** Owner-authority requests
  are deliberately excluded from a manager's inbox.
- **A price-sweep that was not refused.** The request had used `price` where
  the model expects `proposed_price`, so no line carried a price at all. With
  the correct shape the guard fires.

---

## 4. Coverage and limits

Exercised: entry/session/CSRF, the quoting loop through resolution, pricing,
assessment and the send gate, approvals end to end, role gates across 16
surfaces, the machine API, health degradation, and the parser CLI journeys.

The browser was driven only for F2–F4 above, which are the three findings whose
whole subject is UI feedback. Seven checks are a confirmation of three fixes,
not a sweep of the front end.

Not exercised: the rest of the browser UI, live Zoho/ERP connectors and the
OAuth redirect, the scheduled sync and queue worker under real concurrency, and
the trust surface's destructive half — erasure was read but deliberately never
executed.
