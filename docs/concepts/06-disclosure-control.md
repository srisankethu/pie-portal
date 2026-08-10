# Adversarial inference and statistical disclosure control

**Subject:** what the response surface discloses to a role **across many
requests**, rather than in one.
**Written:** 2026-08-09, against `main` at `46ada72`.
**Method:** read of the response builders — `commercial/quote_service.project`
and `.summarize`, `.snapshot_to_dict`, `commercial/quote_exceptions`,
`commercial/references`, `commercial/benchmark`, `commercial/floor`,
`context/assembler`, `context/quote_bundle`, `decisions/quote_support`,
`routers/quote_intelligence`, `routers/quote`, `routers/insight`,
`routers/commercial`, `trust/` — then confirmed against the running API.

> **Status.** The findings marked CLOSED were fixed in the same change that
> added this file, and the numbers below were measured against the fix. The
> findings marked ACCEPTED are deliberate and are not defects. The findings
> marked OPEN are real and not yet addressed; each says why.

---

## 1. Why this document exists

CLAUDE.md §1 already forbids cost and margin reaching a salesperson, and the
codebase enforces it carefully: the server omits the fields rather than hiding
them, `ResolvedFloor` has no cost attribute so a projection that forgets to
strip it cannot leak what the object never carried, and `store.py` omits
`MFLOOR` rather than zeroing it because a zero still answers the question.

All of that is per-request reasoning, and every one of those guards is correct.
The `filterCounts.MFLOOR` incident was not caught by any of them, because it was
not a field escaping. It was a **count** — a value that answers a margin
question without being a margin — and the attack was twenty requests, not one.

This document is about the class, not the instance. The question it asks of
every channel is not *what does this response contain* but **what does a
sequence of these responses reveal**.

### The property to hold

> A role that may not see a value must not receive **any function of it** they
> can invert — including a boolean, a count, a severity, or the mere fact that a
> named rule fired.

A guard that satisfies this for one request and not for a hundred has not
satisfied it. The test suite is where this has to be enforced, because the
reviewer reading a diff sees a correct-looking redaction and moves on; that is
exactly what happened with MFLOOR, and exactly what happened again below.

---

## 2. The enumeration

Salesperson role, `org_sanketh`. Each row: what one request gives, what
repetition turns it into, and what it cost the attacker before the fix.

| # | Channel | One request | Under repetition | Cost | Status |
|---|---|---|---|---|---|
| 1 | `assess` → `exceptions[].code`: `NEGATIVE_MARGIN`, `BELOW_MIN_MARGIN`, `BELOW_MARGIN_FLOOR` | "This line needs approval." Cost, margin and `impact_amount` all absent | **Exact unit cost** of any catalogue item, then `min_margin` and `margin_floor` by solving the two ratios | 2 requests | **CLOSED** |
| 2 | `assess` → `as_of`, unvalidated | Assessment as of a chosen date — legitimate, for back-dating | Row 1 repeated per date: the item's **whole cost history curve**, i.e. every supplier price move and when it landed | 2 × dates | **CLOSED** |
| 3 | `assess` → `BELOW_PEER_MEDIAN` / `ABOVE_PEER_MEDIAN` | "Other customers pay more." `PEER_MEDIAN_PRICE` itself is RESTRICTED and stripped | A two-sided bracket on the withheld median; `quote_price_tolerance_pct` *is* disclosed to sales, so it inverts exactly | 1–2 requests | **CLOSED** |
| 4 | `assess` → `summary.critical`, `.requires_approval` | Two integers, returned to every role | Monotone in the number of probe lines below each boundary — MFLOOR at quote scale, surviving even if every per-line field were stripped | 1 request | **CLOSED** |
| 5 | `insight/negotiate` → `floor_price`, with caller-supplied `family` | The published floor `F`. Disclosable by design | `family` unvalidated, unknown values fall back to `default`, so sweeping it on one product yields the **relative m_floor table**; composed with row 1, the absolute one | 1 per family | **CLOSED** |
| 6 | `quote_support` → `unknowns[]`, `cost_quality:<product>` | "recorded unit cost is at or above the last selling price" — `unknowns` is not role-filtered | Nothing further needed: a hard lower bound on a RESTRICTED value, stated against a price the salesperson is shown | 1 request | **CLOSED** |
| 7 | `assess` → customer scope | Full price history — last paid, band, recent average — for **any** customer name in the org | Enumerates the book. Not a cost leak, but the operational half of every composition above | 1 request | **CLOSED** |
| 8 | `context/assembler._is_restricted` | Correct today for every field a detector currently emits | Not a live leak — a live *mechanism*. Redaction is by label spelling | next feature | **OPEN** |
| 9 | `project` → `references_withheld` | Labels of the cost-derived references not shown | A presence oracle on "is cost known" — which `NO_COST_BASIS` already states in plain words to the same reader | — | **ACCEPTED** |
| 10 | `quote_intelligence/quotes/{id}` audit read | Every snapshot for a quote id, scoped by org only | A salesperson who guesses a quote id reads another salesperson's decisions | 1 request | **OPEN** |
| 11 | `insight`: `/revenue-flow`, `/journey`, `/migration` | Org-wide revenue per customer, no role guard | Scope, not cost. Adjacent endpoints (`/weather`, `/opportunities`, `/lost-revenue`) are manager-gated; these three are not | 1 request | **OPEN** |

### Row 1, in detail

This is the one that matters, and it is `filterCounts.MFLOOR` in a second
costume on an endpoint written *after* the postmortem.

`POST /api/v1/quote-intelligence/assess` takes `proposed_price` from the request
body and returns which exception rules fired. Three of those rules are boundary
predicates on cost:

```
NEGATIVE_MARGIN      fires at   price <= unit_cost
BELOW_MIN_MARGIN     fires at   price <  cost / (1 - min_margin)
BELOW_MARGIN_FLOOR   fires at   price <  cost / (1 - margin_floor)
```

Walking `proposed_price` locates each boundary. The first has **no policy
multiplier in the comparison at all**, so the price at which it switches on *is*
the purchase price — recoverable without knowing any threshold. Solving the
other two against it then yields `min_margin` and `margin_floor`, which
`/quote-intelligence/thresholds` deliberately withholds from a salesperson and
which a test asserts are withheld.

`_MAX_LINES` was 200, so the probes ran in parallel: one request brackets, a
second resolves to the paisa. The endpoint writes nothing, needs no resolvable
customer (an unknown customer still returns a full per-line assessment, and a
test pins that behaviour), and there is no rate limiting anywhere in the app.

**This is not the derivability CLAUDE.md §1 accepts.** That paragraph accepts
that `F` and `recommended` are both cost × a policy multiplier and that anyone
willing to do the algebra recovers cost — an acceptance that assumes the
multiplier is unknown and the number is one the desk needs. Neither holds here.
`NEGATIVE_MARGIN` needs no multiplier, and the boundary it exposes is not a
number anyone was meant to read. This is the *second* corollary of that
paragraph, not the first.

### Row 5, in detail

`floor.py`'s module docstring states the defence explicitly: `m_floor` is
owner-zone and varies by family, "so a salesperson holding one observed line
cannot invert F to cost — and holding two lines in different families does not
help either, because the two multipliers differ and they know neither."

That argument holds only while the family is a property of the **item**. It is
supplied on the `/negotiate` request body, and `m_floor_for_family` fell back to
`default` for any name not in the table. So pricing one item under two families
returned two floors whose ratio is the ratio of their multipliers, and sweeping
the name enumerated the table by observing which names moved the floor.

### The pattern

Every guard in this codebase is correct per request. `store.py`'s own MFLOOR
postmortem says so, and the test at
`test_quote_intelligence_api.py::test_a_salesperson_still_learns_that_approval_is_needed`
encoded the same belief in its docstring: *"they can see the line is under the
floor; they cannot see how far under."* True of one response, false of two.

The defect class is not *a field escaped*. It is **a suppressed cell whose row
total is still published** — textbook missing complementary suppression.
Stripping `manager_detail` and a RESTRICTED `impact_amount` leaves the fact that
a *named* rule fired, and a predicate a caller can walk is the number it tests
against.

---

## 3. What was closed, and how

### The mechanism: `boundary_refs`

`QuoteException` gained `boundary_refs: frozenset[str]` — **the values that
place a rule's boundary, not every value its condition reads.** That distinction
carries the design:

- `NEGATIVE_MARGIN` → `{COST_BASIS}`
- `BELOW_MIN_MARGIN` → `{COST_BASIS, MIN_MARGIN_PRICE}`
- `BELOW_MARGIN_FLOOR` → `{COST_BASIS, MARGIN_FLOOR_PRICE}`
- `BELOW_PEER_MEDIAN`, `ABOVE_PEER_MEDIAN` → `{PEER_MEDIAN_PRICE}`
- `BELOW_LAST_PRICE` → `{LAST_PRICE_PAID}` · `BELOW_BAND_PRICE` → `{BAND_PRICE}`
- `COST_INCREASE_NOT_PASSED` → `{LAST_PRICE_PAID}` — **not** `COST_BASIS`
- `MARGIN_EROSION`, and the data-quality rules → `frozenset()`

`COST_INCREASE_NOT_PASSED` reads a past cost movement to decide whether to fire
at all, but it fires at *this customer's last price* — a number the salesperson
is shown. Listing every input read would withhold the one warning that tells a
salesperson our buying price moved and theirs did not, protecting nothing. That
is the first corollary of §1 — do not fix a leak by degrading the desk — and it
is why the field is named for boundaries rather than for inputs.

A rule with no price in its condition cannot be walked and carries the empty set
naturally; no second flag is needed.

`quote_service._project_exceptions` withholds any rule whose `boundary_refs`
intersect what the recipient may not see, and substitutes one fixed
`APPROVAL_REQUIRED` (or `REVIEW_EXPECTED`). The withheld set is derived from the
line's own references — `{code for r in references if r.data_class ==
RESTRICTED} | {COST_BASIS}` — rather than from a list kept in the projection, so
a reference whose classification changes moves the projection with it.

The same function serves `project` and `snapshot_to_dict`, so the quote screen
and the audit screen cannot drift apart about what a salesperson may read.
`worst_severity` and `blocking` are now read off the *projected* list: taken
from the assessment they were a second, finer copy of the same bit, since
CRITICAL-vs-WARNING separates the approval floor from the review floor.

### The amplifiers

None of these made the attack possible; all of them made it cheap.

- **`can_view_customer` on `/assess`, `/snapshot`, `/outcome`.** The rule
  `/accounts`, the insight timeline and `/negotiate` all apply; these three
  skipped it. Degrades to *unresolved* rather than 403, so an out-of-scope
  account is indistinguishable from one we have never seen — the same property
  the 404-not-403 rule protects elsewhere.
- **`as_of` bounded to ±90 days** on both request bodies.
- **At most 4 distinct prices per product per request.** Quoting quantity-break
  tiers is real work; 200 is a bisection. Refused, not truncated — a silently
  shortened assessment is a screen quietly telling somebody their line is fine.
- **`m_floor_for_family(..., strict=True)` from a request**, raising
  `UnknownFamily` → 400. `report.py` stays lenient: there the family comes off a
  recorded line, and "no family set" honestly means the default multiplier.
- **`summarize` honours the `role` it already took and ignored.** Omitted, not
  zeroed.
- **The `cost_quality` unknown is reworded for a salesperson.** `unknowns` is
  not a fact list, so nothing about it passed through the `data_class` gate —
  but it is free text this codebase writes, and it stated a comparison between a
  restricted value and one the reader is shown. It reached both the client and
  the model.

### Measured

Sweeping `proposed_price` from ₹118 to ₹150 against the seeded fixture (unit
cost ₹124, `min_margin` 0.12, `margin_floor` 0.15) and recording every price at
which the response changes:

```
MGMT  boundaries: [125, 132, 135, 141, 142, 146]
SALES boundaries: [     132, 135, 141, 142, 146]
```

The sets differ by exactly one element — **125, the step at unit cost**.
Everything else is shared and operational. The salesperson keeps
`BELOW_LAST_PRICE`, `BELOW_BAND_PRICE`, `COST_INCREASE_NOT_PASSED` and
`MARGIN_EROSION`; they lose only the three cost rules and the peer-median pair.
The desk is not blunted.

`test_a_salesperson_cannot_walk_the_price_to_recover_cost` performs this sweep
and asserts nothing changes in ₹123–126, with
`test_the_manager_view_still_moves_at_cost` as the counterpart so it cannot pass
by flattening the engine.

### The residual, stated rather than left to be found

A substitute is itself a boundary. Anything that tells somebody "this needs
approval" has to move somewhere, and hiding it would be a worse defect than the
one being closed — they would send the line.

What changes is how many and which. `NEGATIVE_MARGIN` and `BELOW_MIN_MARGIN`
collapse into one, and that collapse is the point: cost is always at or below
the approval floor, so their union fires at the approval floor alone and the
parameter-free edge stops existing. What remains is two boundaries —
`cost/(1 - min_margin)` and `cost/(1 - margin_floor)` — in three unknowns.
Underdetermined; cost does not come out.

> **The standing budget.** The projection may disclose **one boundary per
> distinct action the recipient can take**, because that is the information the
> control exists to convey. Anything beyond that is a leak.

---

## 4. Concepts assessed, ranked by leak probability × cost

1. **Complementary suppression — no predicate to a role denied its input.**
   Prevents. The rule above covers MFLOOR, rows 1, 3, 4 and 5, and the next one
   nobody has written. **Done.**
2. **Information-flow typing / taint.** Prevents, and is the constructive form
   of (1). `boundary_refs` is the cheap 80%: a dynamic tag on the one object
   that crosses the role boundary, checked at the projection. **Done.** Full
   static taint analysis across `commercial/` is *not* worth it — §7's size
   floor argues against the machinery, and the dynamic tag catches the same
   class. Build it when a second boundary object exists.
3. **Query auditing over time.** Detects, does not prevent. Nothing records what
   a role has asked across requests. **Not built** — see §6.
4. **Nuisance-parameter closure.** Arithmetic rather than a concept: `as_of`,
   `family`, `customer` and the batch multiplied the query space for free.
   **Done.**
5. **Cell suppression proper.** Earns its place in exactly one spot, the peer
   benchmark, where `min_peer_customers` is already a k-anonymity threshold
   applied correctly — `is_reliable()` gates the reference and
   `peer_margin_gap()` refuses without it. It needed to gate the *exception*
   too, which (2) accomplishes.

### Separate `RESTRICTED`'s two meanings — recommended, not done

`data_class` currently conflates *"reveals our cost"* (`…_MARGIN_PRICE`) with
*"reveals another customer's price"* (`PEER_MEDIAN_PRICE`). They warrant
different rules: the first must never be inferable by a salesperson; the second
they can already read in prose, and treating it identically is why the peer rule
now looks like a bug rather than a decision. Left alone here because it changes
a classification the rest of the package reads, and that is a wider change than
this one.

---

## 5. What sounds advanced and is wrong here

**Differential privacy, with or without a budget ledger.** The threat model is
inverted. DP protects one row's contribution to an aggregate over many rows; the
sensitive value here is a *point* — one item's cost — read through a predicate.
Noise sufficient to hide it would have to exceed the paisa resolution the desk
trades in, which means noising `F` — and a floor you cannot subtract from your
agreed price to get your own contribution is the exact distortion `floor.py` was
written to remove. A privacy budget over a salesperson's quoting day would
exhaust on honest work and then block the one screen this role uses to decide
rather than to read. Forbidden by §1 by name, and the wrong instrument besides.

**Hiding the exception codes entirely.** The tempting fix and a regression. The
test at `:215` is right that withholding cost must not mean withholding the
control. Change what the boundary is computed against; never stop saying that
one fired.

**Removing the benchmark's exclusion of the subject customer.** A differencing
surface in principle and not an exploitable one in practice. A salesperson never
receives `PEER_MEDIAN_PRICE` to difference — the leak was the
`BELOW_`/`ABOVE_` pair, now closed. A manager who could difference it already
sees cost outright. The exclusion is analytically correct, `benchmark.py`
explains why, and removing it would understate every deviation on a
two-customer item to fix a leak that lives elsewhere.

**Coarsening or rounding `F`, `recommended`, or last price paid.** Explicitly
forbidden, and unnecessary — everything above was closed without touching a
number the desk reads.

---

## 6. Deliberately not built

- **The probe ledger.** A per-principal record of `(user, product, as_of,
  distinct prices, window)`, alerting above ~10 distinct prices per
  (product, date, user, day) — honest quoting revises a line three or four
  times. The shape to copy is `trust/access.py`, which logs *every use* rather
  than only the grant, on the stated ground that "opening the door once and
  opening it fifty times are different facts," and exposes it to the tenant with
  no way to suppress an entry. Same argument, different door. It detects rather
  than prevents, which is why it ranks below the two controls that landed — but
  it is the only one that keeps working against a leak nobody has enumerated.
- **Moving the salesperson's approval boundary to `F`.** This would make the
  residual in §3 zero-information, since `F` is already published to that person
  by `/negotiate`. It is not a security fix: `F = cost × (1 + m_floor)` and
  `MIN_MARGIN_PRICE = cost / (1 - min_margin)` are different numbers under
  different conventions — `floor.py` explains why the markup convention there is
  deliberate — and deciding which one governs approval is a commercial call
  about the desk. It should be taken on its merits.
- **Rows 8, 10 and 11 of the enumeration.** Row 8 is a mechanism rather than a
  live leak; rows 10 and 11 are scope rules on shared `routers/` surface and
  belong with whoever owns those screens.

---

## 7. A note on how row 1 was found, and nearly missed

The fixture in `test_quote_intelligence_api.py` assigned customers to
`u_sales`; the seeded salesperson is `usr_sales`. Harmless while `/assess`
ignored assignment — and the moment the scope check landed, **every salesperson
case in that file went green by resolving no customer at all**.
`test_a_salesperson_receives_no_cost_or_margin_anywhere_in_the_response` passed
because there was nothing left to leak.

It was caught by printing what a salesperson actually sees and reading it,
rather than trusting the green. That is the §1 tell — *absence of evidence is
not a pass* — arriving in a test fixture rather than in a detector, and it is
the reason the inference test above has a manager counterpart.

**Operational consequence of the scope fix:** a customer assigned to nobody now
shows a salesperson no history on the quote screen, silently. That is correct
behaviour and it will read as a bug the first time it happens.
