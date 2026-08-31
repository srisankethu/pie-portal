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

**Result.** Over 100 behavioural checks across both repositories. **Six
defects, all now fixed with regression tests**: one genuine functional defect
(RFQ quantity misreading, contained by the send gate), a second in the flag that
was supposed to contain it, a third reading a dimension as an order quantity,
and three messages the interface set and never rendered. Every invariant the business depends on —
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

`make verify` green: **476 tests, 6,717 corpus rows, 0 quarantined, no family
below 100%, byte-identical reruns.** `eval_identity`: 13/13 cases, **0
false-positive identity** (the cardinal error). CLI exit codes as documented
(2 on bad arguments / missing source / unknown record id; 0 on a clean lint).
pie-portal's own suite: **3,544 passed, 141 skipped, 0 failed**, and the full
gate (`make verify`) green end to end — frontend build, migrations from nothing
on SQLite *and* PostgreSQL, row-level security, the queue's concurrent
claim, and the `pg_dump` → restore drill. The engine-backed (`requires_pie`)
tests are most of that skip count and are not covered by a bare `make verify`,
which is what the gate's own "but narrowed" note says; run separately against a
pie-parser checkout they are **100 passed**.

Both figures are from a re-run after this branch merged its base in, which is
why the parser count is 476 rather than the 406 of the branch alone — master's
PR #20 brought its own tests. A stale engine checkout is not a neutral
condition here: run against the pre-#20 parser, twelve pie-portal tests fail.

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
| `2001174 nos` | code `s`, qty 2,001,174 | code `2001174 nos`, **CONFIRM READING** |
| `CNMG 120408 nos` | code `CNMG`, qty 120,408 | code intact, **CONFIRM READING** |
| `2001174 - 250000 nos` | qty 250,000 | qty 250,000 — unchanged |
| `100 nos 2001174` | qty 100 | qty 100 — unchanged |
| `CNMG 120408 10000 nos` | qty 10,000 | qty 1, **CONFIRM READING** — narrowed |

`tests/test_rfq_splitting.py` gained three tests (11 assertions) and one
assertion on an existing one. All seven new parametrisations fail against the
old patterns and pass against the new ones, so they are regression tests
rather than descriptions.

**The last row is the cost of the fix, and it is a real one.** Space-separated
is now the *only* shape a unit word cannot rescue, so a genuinely stated
five-digit order written `CNMG 120408 10000 nos` stops being read — four digits
is the ceiling for that shape, and `CNMG 120408 1000 nos` still reads 1,000.
That is the trade this defect forces: the same shape carries both `DNMG 150608
nos` (a code, no quantity) and `CNMG 120408 10000 nos` (a code and a quantity),
and nothing after the number says which. The line travels `proposed` rather
than defaulted, so the estimate is blocked until a person confirms it — a
five-digit order becomes one extra confirmation, where the alternative was a
quotation for a hundred and fifty thousand pieces nobody ordered. A tab counts
as an explicit separator, not as this shape, so `CNMG 120408<TAB>12345 nos`
still reads 12,345.

Deliberately **not** changed: the fallback still leaves the unit word in the
code (`CNMG 120408 nos` keeps its `nos`). Stripping it there would make the
line resolvable as well as flagged, but `_UNIT_WORDS` includes `ea` and
`each`, so a description legitimately ending in one of those would be
truncated — a wider change than this defect justifies, and the line is blocked
either way. Two consequences that follow from it, stated because they are not
obvious: that string is what reaches the resolver (`build_lines` passes
`row["code"]` to `pie_service.resolve`) and what shows as the requested code on
an AMBIGUOUS line; and `confirm_reading` clears `proposed` and nothing else, so
confirming the reading does not clean the code.

#### F1b · The flag itself was wrong in both directions — **FIXED**

The rule deciding whether a line travels `proposed` was
`re.search(r"\b{_UNIT_WORDS}\b", code)`, and it erred both ways.

**It under-flagged.** A `\b` needs a boundary before the word and there is none
between a digit and a letter, so `2001174nos` came back qty 1 and *unflagged* —
better than the 2,001,174 it used to return, and still a silent default of
exactly the kind §1 says is never a pass.

**It over-flagged, on real rows.** Searching anywhere in the line matched
mid-string: `WMT PC 805M MOULDED INSERTS` read the grade token `PC` as `pcs`,
and `DOV-LOK PCD MINI TIP INSERT NO WIPER` read the English `NO` as a count.
**Ten of the 6,717 catalogue rows** were blocked for a unit word that was not
one — and a flag that fires on ordinary descriptions is a flag people learn to
click through, which costs the cases it exists for.

Both halves have one cause: the test asked *whether* a unit word appears, when
what matters is whether it is **doing the work of a unit** — and the test for
that is adjacency to a number.

```python
re.search(rf"(?:\b{_QTY_KEYWORD}\b"
          rf"|\d[\s.-]*{_UNIT_WORDS}\b"
          rf"|\b{_UNIT_STRONG}[\s.-]*\d"
          rf"|\b{_UNIT_WORDS}\W*$)", code, re.IGNORECASE)
```

Four arms, each earning its place:

- **The explicit keyword, anywhere.** `qty` and `quantity` never occur in
  product prose — 0 of the 6,717 corpus rows contain either — so
  `CNMG 120408-MP - qty to be confirmed` is the customer saying the number is
  not settled, and there is no digit for an adjacency test to find.
- **A unit after a number.** `- 100 nos urgent`, `(100 nos)`, `100 nos TN2000`,
  `2001174nos` with no space, and `100-nos` with a hyphen.
- **A unit before a number**, long forms only. `- nos 100 required` is ordinary
  phrasing, but that position is exactly where `WMT PC 805M` sits, so bare `pc`,
  `no` and `ea` are excluded from this arm and only this one.
- **A unit at the end with nothing to attach to.** `insert, nos` — a marker with
  no number is the case most in need of a human. The trailing run is `\W*`
  rather than an enumerated punctuation class, because the enumeration kept
  being wrong by one character: `(nos)`, `nos?`, `nos —` and WhatsApp's `*nos*`
  each defeated a list that did not name them. `\W*` cannot swallow a digit, so
  it stays specific.

A grade token in front of a dimension matches none of the four.

**The first attempt at this was wrong, and how it was caught is the point.** It
anchored the unit word to the end of the line. That cleared all ten false flags
and passed every test in the file — and silently lost the flag on `CNMG 120408
TN2000 - 100 nos urgent`, `CNMG 120408 (100 nos)`, `- 250 nos, need by friday`
and eight more: a stated quantity, unread, defaulted to 1, with nothing on
screen to say so. One trailing courtesy word was enough. The measurement that
justified the anchor had swept the 6,717-row **catalogue** — clean product
descriptions — when the input this function parses is **messy buyer prose**.
Right method, wrong corpus, and the corpus was the half that flattered the
change.

An adversarial pass over realistic inbound text found the class, and kept
finding narrower versions of it — a closing bracket, a question mark, an em
dash, a WhatsApp asterisk, a hyphen between number and unit, a unit written
before its number. Each was one character away from the last, which is the
argument against enumerating punctuation at all.

The rule that replaced it flags **0** of the 6,717 rows in three shapes where
the bare search flagged 10, holds every quantity-bearing shape four adversarial
lenses could construct, and leaves nothing unflagged-but-stated across the
repository's own 14-case inbound seed set. `test_rfq_splitting.py` goes from 28
collected cases to 55; against the original rule 6 of them fail, and against the
anchored attempt 21 do.

#### F1c · A dimension read as an order quantity — **FIXED**

Found by replicating the adversarial pass's own sweep — 300 catalogue rows
across 10 realistic RFQ templates — against the fixed flag. Three of 3,000 lines
still came back as one piece in silence, and all three were the same catalogue
row: `ENDMILL 57N8 10x10x22-30x76 Rad 1,0`.

The cause is not the flag at all. It is `_QTY_PATTERNS` reading a **European
decimal comma** as a quantity separator:

| Catalogue description | Was read as |
|---|---|
| `ENDMILL HARL 5FL 8x8x40x87 R0,5` | code `…R0`, qty **5** |
| `ENDMILL 5777 12x12x26x83 RAD 0,75` | code `…RAD 0`, qty **75** |
| `KSSM 8+ MILL. INSERT IC=10 x 4,45` | code `…x 4`, qty **45** |
| `END MILL W4N1 12x12x26x83 R2,0` | code `…R2`, qty 0 → clamped to 1 |

Both halves of the line wrong from one character. `R0,5` is a 0.5 mm corner
radius; `R0` is a different product, and one that **collides with a genuine
`R0`** — so this is the F1 defect exactly, through a third door: the code
mangled and the quantity invented. **790 of the 6,717 rows carry a decimal
comma**, and the rule truncated every one that ended in it.

It also swallowed real quantities. Because a pattern had *matched*, the earlier
`250000 nos` in `250000 nos ENDMILL … Rad 1,0` never reached the
leading-quantity rule or the flag, and a quarter-million-piece line travelled as
one.

Two guards, both narrow:

- **A comma separates a quantity when it is followed by whitespace**
  (`2001174, 20`) **or when what precedes it is not a digit**
  (`CNMG 120408-MP, 10`). `digit,digit` with nothing between is a decimal.
- **Zero is never a quantity.** A matched zero now falls through to the next
  rule instead of being clamped to one, which is what lets the line above find
  its real quantity.

Catalogue rows whose code the splitter truncates: **103 → 34**, and the 3,000-line
sweep goes to **0**. The 34 that remain are `_BARE_QTY_DIGITS` behaving as
documented — a trailing number of four digits or fewer *is* read as a quantity,
which is what makes `CNMG 120408 TN2000  100` work. Pasting a raw description
that happens to end in a number is genuinely ambiguous, and is left alone rather
than guessed at. Eight parametrised cases pin both guards; all eight fail
against the previous rule.

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
sit inside it, and an updater is a place React is entitled to call twice — it
does so in development under `StrictMode`, which `main.tsx` enables — so a
toast fired from there was liable to appear twice. Development-only as a
*symptom*; the rule that a state updater must be free of side effects is not.
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
