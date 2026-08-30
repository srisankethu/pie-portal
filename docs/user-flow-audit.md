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
flows were run directly.

**Result.** Over 100 behavioural checks across both repositories. **One genuine
defect** (RFQ quantity misreading, contained by the send gate), plus three
UI-feedback defects found by inspection. Every invariant the business depends
on — cost containment, approval authority, the identity gate, the send gate —
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
pie-portal's own suite: **3,479 passed, 0 failed.**

---

## 2. Findings

### F1 · MODERATE · An RFQ line's part number can be misread as its quantity

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

Fix, validated against both patterns:

```python
# leading "100 nos <code>" — a word boundary stops "nos" matching "no" + "s"
rf"^(?P<qty>\d+)\s*{_UNIT_WORDS}\b[\s.:]*(?:of\s+)?(?P<code>.+)$"

# trailing "<code> 100 nos" — keep the 4-digit cap unless an explicit
# , : - – — or qty marker introduces the number
rf"^(?P<code>.*?)(?:[,:–—-]\s*(?P<qty>\d+)|\s+(?P<qty2>\d{1,4}))"
rf"\s*{_UNIT_WORDS}\b\s*[.]?$"
```

Checked: kills all three misreads above; still reads `100 nos 2001174`,
`100 nos of CNMG 120408`, `50 pieces DNMG`, `CNMG 120408 - 100 nos`,
`CNMG 120408 100 nos` and `2001174, 5000 nos` correctly.

### F2 · MINOR · A failed workspace switch tells the user nothing

`PlatformApp.tsx:656-661` catches the refusal and calls `setNotice(...)`, but
`notice` is rendered only by the sign-in card inside `if (!session)`
(`:751-808`). During a workspace switch a session exists, so nothing renders
and no toast is raised. The user clicks, the switch fails, and the screen is
unchanged with no explanation. The stale notice can also surface later, out of
context, at the next sign-out.

### F3 · MINOR · "Signed in as … in another tab" is never shown

Same cause (`:500-517`). The tab adopts the new session correctly; the
message recorded for the user has no renderer in the signed-in shell. The
sibling case — signed *out* in another tab — does display, because there the
session becomes null and the sign-in card renders.

### F4 · MINOR · The Data screen's error state offers no retry to a salesperson

`DataScreen` renders `ErrorState` without an `onRetry`, so the kit's "Try
again" button never appears; the only re-fetch is "Refresh status", which sits
inside the manager-only branch. A salesperson who hits a status failure has
only a page reload.

F2–F4 were found by reading the code, not by executing the UI (the frontend
has no `node_modules` in this environment) — a lower evidence class than
everything in §1, and each should be confirmed in a browser before being
worked.

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

Not exercised: the browser UI itself (no frontend build in this environment),
live Zoho/ERP connectors and the OAuth redirect, the scheduled sync and queue
worker under real concurrency, and the trust surface's destructive half —
erasure was read but deliberately never executed.
