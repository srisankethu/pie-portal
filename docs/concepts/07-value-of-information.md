# 07 · Value of information

*What the platform does not know, what each missing fact would be worth, and
what it would cost to start knowing it.*

The platform is unusually disciplined about absence. It suppresses rather than
guesses, it records the suppression, and it names what is missing. That
discipline produces a by-product nobody has used yet: **a list of the things
worth knowing, written by the system itself, every time it declines to answer.**

This document reads that list, prices the entries, and says which to act on.

Two conventions throughout. Value is expressed against `R` — annual won
revenue — with a worked figure at `R = ₹5 Cr` so the shape is visible;
substitute the real number. Policy anchors are the defaults in
`commercial/config.py`: target margin 24%, soft floor 15%, hard floor 12%,
sales discretion band ±3pp, materiality floor ₹10,000.

---

## 1. What the platform already knows, and where it refuses

### The suppression machinery

- `signals/base.py` `Sufficiency` carries `history_months`, `txn_count`,
  `missing_fields`, `anomalies`, `level` and `reasons`. Every detector attaches
  it, so **every suppression already names what is missing**.
- `ai/interpret.py` withholds up front on `INSUFFICIENT` without spending a
  provider call. `decisions/preflight.py` counts `would_suppress_up_front`
  before anything is sent. `AiCallLog` writes one row per decision point
  including that path.
- `insight/radar.py` sorts `(confidence, impact)` descending — confidence
  first, so a ₹4L figure resting on two invoices does not outrank a ₹90k figure
  resting on sixty.
- `insight/stock.py` reports `no_reorder_point` as a finding about master data
  rather than substituting a computed reorder level.

### The refusal list, now typed

Nine modules emit machine-readable refusals. Until this change they rendered
identically, so a limit and a job read the same. Each now carries a `kind`
(`commercial/insight/absence.py`):

| Kind | Meaning | Entries |
|---|---|---|
| `PERMANENT` | No data would fix it | `weeks_of_cover`, `recovery_probability`, `expected_recovery_value`, `supplier_and_brand`, both `dependency` claims, `mix` gap-is-not-an-opportunity |
| `COLLECTABLE` | **Somebody must record something — this is the worklist** | `reorder_point`, `delivery_against_promise` (×2), `payment_behaviour`, `SUPPLIER_DELAY_AGAINST_PROMISE`, both `bonds` reliability entries |
| `BUILDABLE` | Data exists or can be bought; engineering work | `branch` (Zoho warehouse endpoints), `SUPPLIER_DELAY_BY_ITEM` (one API call per order), `bonds` pay-on-time |
| `TRANSIENT` | Resolves itself as the period runs | `schemes` `TOO_EARLY`, `TOO_FEW_DOCUMENTS` |
| `WITHHELD` | Computed and correct; hidden from this reader by role | `margin`, `inventory_value_and_carrying_rate`, `cost_and_margin`, `monthly_cash_drain` |

The distinction that pays for the field is `PERMANENT` vs `COLLECTABLE`.
`WITHHELD` exists because filing a working permission rule under "not
answerable" invites somebody to "fix" it. `TRANSIENT` exists because putting
"wait a fortnight" on a worklist is noise.

**Correctly permanent, and worth saying so.** A demand forecast this data
cannot support (`weeks_of_cover`, `recovery_probability`,
`expected_recovery_value`); what a customer buys elsewhere; whether a second
source exists; and the grain mismatch in attributing a stock *level* to a
supplier — an item bought from two suppliers has no single one, and perfect
data does not change that. These are answers, not gaps. They should never
appear on a worklist.

### One refusal that is probably stale

`bonds.unavailable()` says bills carry no payment date. That is true of the
`bills` table, which is what the module reads — but `BillPaymentApplication`
carries `bill_date`, `bill_due_date` and `paid_on` at exactly the grain a past
month needs, and it is ingested. This is the same failure
`stock.supplier_and_brand` was narrowed for once already, and CLAUDE.md names
the cost: *a screen that refuses on a reason the platform has since fixed
teaches people to ignore its refusals.* Tagged `BUILDABLE` with the table
named; not narrowed here, because that changes what the reliability facet
scores.

### The outcome path

- `DRAFT` written on snapshot (`routers/quote_intelligence.py`).
- `SENT` written when the Zoho estimate is created (`routers/quote.py`).
- `WON`/`LOST` reachable only via `POST /api/v1/quote-intelligence/outcome`.
  On `main` the client function exists (`frontend/src/intelligence.ts`) with
  **zero callers** — the terminal states are unreachable from the UI.

See §6: this is already built on an unmerged branch.

### A structural gap that bounds everything below

Quotes live in memory (`store.py`, ids prefixed per process precisely because
they restart at `q1`). A quote neither snapshotted nor sent leaves no row.
**RFQs that were priced and abandoned are structurally invisible** — "we
couldn't meet the lead time so we didn't bid" is a real category of loss that
no analysis built on `QuoteOutcome` will ever see.

---

## 2. The measurement that reframes the whole category

A read-only probe of the live Zoho books (SLS ENGINEERS, org `60063559751`),
counting estimates by status:

| Status | Count | How it gets set |
|---|---|---|
| `invoiced` | **50–99** | automatic — converting an estimate to an invoice *is* the bookkeeping workflow |
| `accepted` | 1–19 | manual |
| `sent` | **exactly 30** | default; undetermined |
| `declined` | **1–6** | manual |
| `expired` | ≥1 | automatic, on the expiry date |
| `draft` | <60 | default |

Estimates carry `accepted_date`, `declined_date`, `expiry_date`,
`salesperson_name`, `customer_id` and `total`. 4U Precision (org
`60036630626`) uses estimates on its own numbering.

**Wins are recorded 60–120 times. Losses are recorded 3–6 times.** Not through
carelessness: a win records itself as a side effect of invoicing, and a loss
requires somebody to go back and click Declined.

Three consequences:

1. A win rate read off Zoho status would be ~95% and meaningless. This is the
   §1 failure mode exactly — **no declined record reads as "we won"**.
2. Syncing estimate status is therefore not a cheap standalone win. It is a
   free, retrospective, dated, salesperson-attributed record of the
   **numerator**, waiting on a denominator.
3. Zoho already distinguishes `expired` from `declined`. A quote that timed out
   is not a quote that was lost, and the vocabulary should preserve that.

---

## 3. Ranked candidate fields

Ranked by **decision-quality improvement per unit of collection cost**. Ceiling
is stated separately, because the two do not order the same way.

### 1 · Vendor MSME status — highest certainty, lowest analytical interest

**Value.** s.43B(h) disallows the deduction for a payment to a registered
micro/small enterprise beyond 45 days (15 without a written agreement), in the
year of accrual. Every other input already exists: `Bill.due_date`,
`Vendor.payment_terms_days`, the `vendor_payment_terms` human-override table,
and a `CASH_PAYABLE_OVERDUE` decision type. **One flag per vendor converts an
existing overdue signal into a statutory one.**

If ₹40L of annual purchases are from MSME vendors and 15% slips past 45 days:
₹6L disallowed → ~₹1.7L of tax pulled forward, plus MSMED s.16 interest at
three times the RBI bank rate, compounded monthly and non-deductible. Call it
**₹1.5–2L a year of cash timing plus a live audit exposure**, at near-certainty.

**Collection cost.** Effectively zero. Not a desk field — most MSME vendors
print their Udyam number on the invoice. One backfill pass over the top vendors
by spend, following the `vendor_payment_terms` pattern exactly: human-set
overlay, Zoho's value never overwritten, survives a complete re-sync.

**Unlocks:** a payables queue ordered by statutory deadline rather than age.
**Does not unlock:** anything commercial. Zero bearing on pricing or customers.

**Caveats.** MSME status is self-declared and changes when a vendor crosses a
turnover threshold, so the field needs a `declared_on` date and goes stale —
model it as evidence with an age, not a fact. And the 45/15-day split and its
acceptance-date trigger should be confirmed with the firm's CA before the
platform asserts a deadline; the thresholds belong in config so that answer is
a setting rather than a code change.

### 2 · Loss reason on a quote — the item the category ranks on

**Value.** Without it every loss is reported as price, and it usually is not.
The cost is not the misreporting, it is the response: a business that believes
it loses on price discounts. If mis-attribution drives systematic use of the
bottom of the ±3pp discretion band on the ~25% of quotes where price was never
the issue:

`0.03 × 0.25 × R` = **₹3.75L a year at R = ₹5 Cr**, recurring, and compounding
into the customer's price expectation.

Treat that as an order of magnitude, not a forecast. The field does not recover
losses; it stops one specific wrong reflex.

**Collection cost.** One tap. The real cost is not seconds but **adoption
risk** — this platform's characteristic failure mode is friction at the desk,
and a loss-reason field is the classic thing that gets filled with the first
chip in the list forever. Design against that or do not ship it (§4).

**Unlocks:** loss composition by customer, family and salesperson; separating
price losses from spec, lead-time and no-decision losses. That separation is
the *precondition* for any price work, because a no-decision loss carries zero
information about price and including it biases the curve toward "we are too
expensive". It also makes lead-time and stock problems visible as commercial
problems, which is where a distributor's real losses usually sit.

**Does not unlock:** a win-rate curve. Not on its own, not for years.

### 3 · Won-vs-lost at scale — highest ceiling, longest fuse, mostly free

Three sources, in increasing order of friction:

- **Zoho estimate status — zero friction, retrospective.** `invoiced` and
  `accepted` give a substantial dated record of wins (§2). Ingestion does not
  read estimates at all today, and `QuoteOutcome` has no column to join one to,
  so this needs `estimate_external_id` plus a puller.
- **Invoice inference — zero friction, and at line grain.** A `QuoteDecision`
  line whose (customer, product) later appears in `SalesTxn` within a window is
  won. Strictly better than `QuoteOutcome` in one respect: **`QuoteOutcome` is
  per-quote by design**, and for a distributor partial awards are ordinary —
  "won 12 lines of 40" is a sentence the per-quote model cannot say. An
  inference is not testimony and must be recorded as `INFERRED`, never `WON`.
- **Two buttons.** The endpoint, the transitions and the client function
  already exist.

**Value.** Directly, it replaces `assumed_volume_change` in
`simulate.price_change` — today a number the user types. Note what that module
does right and must keep doing: it refuses to estimate elasticity and hands
back `break_even_volume_change` instead. Win/loss data does not replace that;
it says where the break-even sits relative to reality.

Second, it tests whether 24% is the right target. If the win rate is flat
between 24% and 27% on a family, 1pp on that family is `0.01 × R` = **₹5L a
year at R = ₹5 Cr**. That is the ceiling for the whole category.

**What it will not unlock, plainly.** Distinguishing a 45% win rate from a 55%
one at useful confidence needs on the order of 400 decided quotes *per arm*. At
30–60 quotes a month across three entities, split by family and segment,
**there will be no defensible price-response curve in year one and possibly not
in year three.** That is the argument for starting now, not against it: the
clock only starts when collection does, and the data is worthless until it is
old. Anyone promising a curve sooner will produce one the platform cannot
defend.

### 4 · Observed losing prices — real signal, and the trap runs backwards

**Value.** The only empirical market-price signal available, and the only thing
that could ever anchor the peer median to something other than our own win
history (§5).

**Collection cost.** Effectively zero marginal — it rides on the loss-reason
flow as one optional number revealed when `PRICE` is chosen.

**The trap.** *Absence of evidence is not a pass* has a mirror image this
codebase has not had to face yet: **presence of asserted evidence is not
evidence either.** A customer volunteers a losing price when it serves them, so
the number is systematically low, sometimes invented, always unverifiable.
Store it with `provenance: CUSTOMER_ASSERTED` and a confidence that can never
reach `SUFFICIENT`. It may inform a human in the drawer. It must **never** enter
`benchmark.py`, `references.py` or any computed median — the moment it does, a
made-up number carries a `thresholds_version` and looks exactly like a measured
one.

**Unlocks:** a salesperson's argument, and a slowly accumulating picture of
where competitors price. **Does not unlock:** a defensible market price, ever.

### 5 · Parts-per-edge — the best sales tool here and the worst platform field

**Value.** Cost-per-component is the only frame in which a premium insert beats
a cheaper one, and it is the most useful thing a cutting-tool distributor can
say in the room.

**Collection cost.** High and recurring. It comes from a shop-floor trial, not
a conversation, and it varies by machine, material, coolant and operator — and
decays.

**Why it ranks last anyway.** Cost-per-component is
`price ÷ (edges × parts_per_edge)`. That is a computed number, so under §1 it
belongs in `commercial/`, deterministic, carried on a persisted row with a
`thresholds_version` — and its input is one customer's unverified recollection
of one job. That is a `thresholds_version` on an anecdote, and everything on
the screen would look equally measured.

**The right shape is not a field.** It is a *reference* on the customer × item
drawer — `references.py` already has the pattern, with a label, a value, a data
class and provenance — showing what this customer reported, when, and on what
job. Unbanded, uncomputed, uncarried. If it later earns a computation, it earns
it once there are enough of them to say something.

### A sixth candidate, outside the original five, which I would rank third

`expected_delivery_date` on purchase orders. `simulate.py` blocks an entire
scenario on it — blank on effectively every order in this book — and
`supply.py` refuses a measure for the same reason. It is not a limit; it is one
keystroke at PO entry. For a distributor whose real losses are lead-time
losses, it also feeds the loss-reason taxonomy directly. It is the clearest
`COLLECTABLE` in the package.

---

## 4. Capturing any of this without desk friction

**The governing rule: never add a screen. Add a control to a screen somebody is
already on for another reason.**

**Loss reason.** Six chips plus one, in the quote row already showing "Sent 11
days ago".

- **No default, ever.** A pre-selected "Price" is the benign-default failure of
  §1 with a UI on it.
- **"Don't know" is a chip, not a blank.** The most important detail here. A
  blank cannot be told apart from "nobody asked"; "Don't know" is a recorded
  fact about the sales process, and if it is 60% of losses that finding is
  worth more than the taxonomy.
- **Never mandatory.** Blocking quote closure on a reason code is how every
  field on this list becomes "Price". See §6 — the existing implementation
  makes the opposite choice, and it is the one thing about it I would change.
- `QuoteOutcome.note` already exists and is the trap: free text feels
  lower-friction, gets filled with "customer said too costly", and cannot be
  aggregated. Chips are the field; the note is the optional second line.
- **`NO_DECISION` must not be coded `LOST`.** A deferred order is not a lost
  one, and folding them together makes the win-rate denominator meaningless.
  Zoho already separates `expired` from `declined` (§2).

**Won/lost.** Do not ask. Sync estimate status; infer from invoices. Reserve
the tap for the residue neither resolves.

**Losing price.** One optional number, revealed only when `PRICE` is tapped.
Never blocks.

**MSME.** Not at the desk at all. A vendor-master overlay, backfilled once.

**Parts-per-edge.** A note on the customer × item drawer, entered when somebody
happens to learn it. No prompt, no reminder, no completeness metric.

---

## 5. The selection-bias problem, designed for rather than discovered

Outcomes are observed only for quotes that were sent, at prices chosen by a
rule. **This bias is already in the platform's numbers, before any new field
is added:** `SalesTxn` is invoice-line grain, so every price the platform
benchmarks against — the peer median in `benchmark.py`, `BELOW_LAST_PRICE`,
`BELOW_PEER_MEDIAN`, `BELOW_BAND_PRICE` — is drawn from prices that *won*. The
peer median is not the market price; it is the market price **conditional on
our having won at it**. Nothing in the codebase says so, and a sentence in
`benchmark.py` would be worth adding regardless of what else gets built.

Three consequences and the design response to each.

**1 · Price is nearly a deterministic function of cost.** Policy sets target
margin by family; price falls out. Absolute price therefore carries almost no
independent variation, and a curve fitted on it is fitting the cost
distribution.

> **Response:** the regressor is `quoted_margin − target_margin_for_family`, in
> percentage points — deviation from policy, not price. That conditions out
> cost and family in one move, and it is already recoverable: `QuoteDecision`
> stores `quoted_unit_price`, `unit_cost`, `margin` and `thresholds_version`.
> Nothing new needs storing. Write it down before anyone fits anything, because
> the first person to reach for this will reach for price.

**2 · The residual variation is not random.** A salesperson discounts harder
when they expect to lose, which biases the curve in the worst direction: it
makes discounting look ineffective.

> **Response:** you cannot remove the selection; you can **record the selection
> rule**, which makes it conditionable. `QuoteDecision.override_reason_code`
> already exists and is populated only on below-floor overrides. Widen it to
> *why this price* on any line priced off-recommended — "customer pushed back",
> "matching a known competitor", "volume commitment", "standard for this
> account". That is the propensity variable, and without it the curve is not
> estimable at any sample size.
>
> Randomising within the discretion band would genuinely break the bias. At
> 30–60 quotes a month it would cost more in lost orders than the information
> is worth. **Don't.**

**3 · Two populations are missing entirely.** Never-quoted RFQs (invisible
until quotes persist) and quotes at prices nobody would have chosen. Any curve
is conditional on the policy that generated the prices and **cannot extrapolate
outside the observed band**: fitted on 21–27% margin, it says nothing about 30%.

> **Response:** state the support. If a screen ever shows a win-rate curve, it
> shows the margin range the data covers and refuses outside it — exactly the
> way `stock.py` refuses `weeks_of_cover`.

---

## 6. Existing work — and a collision between two open PRs

**Loss-reason capture is not unbuilt. It has been built twice, incompatibly,
and neither has merged.** Nothing in this section is proposed as new work.

### The collision, first, because it blocks both

| | PR #42 `claude/quote-win-loss` | PR #66 `claude/concepts-05-marketing-science` |
|---|---|---|
| Migration | `c7e41b90d3aa_quote_loss_reason.py` | `b7c41e0a9d38_quote_loss_reason.py` |
| `down_revision` | `0e8d9299b0c7` | `0e8d9299b0c7` |
| Column | `quote_outcomes.loss_reason` `String(32)` | `quote_outcomes.loss_reason` `String(24)`, plus `lost_to` `String(255)` |
| Vocabulary | `PRICE`, `DELIVERY`, `COMPETITOR`, `CUSTOMER_CANCELLED`, `NO_DECISION` | `LOST_ON_PRICE`, `LOST_ON_DELIVERY`, `LOST_ON_APPROVAL`, `NOT_BOUGHT`, `NO_DECISION`, `UNKNOWN` |

Both define `QuoteLossReason` in `domain/enums.py`. Both branch from the same
revision. **Whichever merges second produces two alembic heads and a duplicate
`loss_reason` column**, so `verify.sh` step 5 fails on an empty database rather
than in production — the system working, but only after two sessions have both
finished.

This needs one human decision before either merges, and it is not a conflict
anybody can resolve mechanically: the two vocabularies mean different things.

**They are complementary rather than redundant.** #66 is the capture side, #42
the analysis side; they collide only on the enum and the migration.

- **#66 has the better vocabulary.** `LOST_ON_APPROVAL` names a loss caused by
  our own approval latency — an internal-process failure neither #42 nor this
  document had thought of, and the only one on either list the business can fix
  without touching price. `lost_to` records who won it, which is the nearest
  safe thing to the competitor signal ranked 4th above: a name invites no
  fabricated number the way a price does.
- **#42 has the better analysis.** `insight/outcomes.py` (534 lines) carries the
  evidence floors, the per-quote counting, the product-and-band price comparison
  and the `NOT_RECORDED` denominator discipline. #66 has no equivalent.

**Recommendation: #66's enum and migration, #42's `outcomes.py`.** Whichever
goes second drops its migration and adapts to the column already there.

One caution that applies to both, and more sharply to #66: both make the reason
**mandatory** on a loss, and #66 additionally excludes `UNKNOWN` from
`SELECTABLE_LOSS_REASONS`, so a person recording a loss must choose a
substantive reason and cannot say "don't know". That is the §4 failure exactly.
A forced choice among five substantive reasons does not produce truth, it
produces `LOST_ON_PRICE`, and a wrong reason is worse than a missing one because
nothing downstream can tell it from a real one. Make `UNKNOWN` selectable.

### On PR #42, assessed on its own terms

Verified by fetching and running the branch, not by reading its report.

One commit (`4afc788`), +2,129 lines. Measured against `main` at `46ada72`,
where it was a clean fast-forward with no rebase needed. `main` has since moved
to `6bf0a08`, so these numbers describe the branch as it stood and it needs a
rebase and a re-run before it lands:

- **1599 passed, 30 skipped** (the skips are `requires_pie`).
- Empty-database migration to a **single head** `c7e41b90d3aa`, **25/25**
  migration-integrity tests, no drift.

What it contains: `QuoteLossReason` (PRICE, DELIVERY, COMPETITOR,
CUSTOMER_CANCELLED, NO_DECISION); `QuoteOutcome.loss_reason` with a migration;
`InvalidLossReason` enforcement in `quote_service.set_outcome`; a 422 on the
router; `commercial/insight/outcomes.py` (534 lines) computing win rates by
facet, over time and by reason mix, plus a price comparison; 335 lines of new
router; a 572-line `QuoteOutcomes.tsx` screen; 488 lines of tests.

**It is good work.** It counts per quote, never per line, for the right reason.
It sets an evidence floor of 8 decided quotes and explains why 3 is too few. It
compares prices only within one product **and** one quantity band, with 3
observations each side, and accepts that most products will not qualify. It
treats losses recorded before the vocabulary existed as an explicit
`NOT_RECORDED` bucket rather than dropping them from the denominator — the
right instinct exactly. It made `benchmark.median_decimal` public rather than
growing a second rounding behaviour. It splits `build` from `pricing` so the
router can serve a cost-free win rate to a salesperson and keep the margin
comparison behind manager scope.

**Three things to fix before it lands:**

1. **`frontend/node_modules` is committed as a symlink** (mode 120000). The
   `.gitignore` entry is `node_modules/` — with a trailing slash it matches
   directories, and git records this as a symlink, so it slipped past. It must
   be removed; on a fresh clone it points nowhere.
2. **The loss reason is mandatory on `LOST`** and refused elsewhere. The
   reasoning given is sound — a null is permanent because nobody backfills last
   quarter — but the failure mode is worse than the one it prevents. A required
   field with five options produces "PRICE" on everything, and a wrong reason is
   worse than a missing one because it cannot be told apart from a real one.
   Recommend: optional, with an explicit `NOT_RECORDED` chip the user can
   actively choose, so a deliberate "don't know" is distinguishable both from a
   blank and from a real reason. The existing `NOT_RECORDED` sentinel is
   already the right shape; it should be selectable, not only historical.
3. **No selection-bias treatment.** Nothing in the module mentions that
   outcomes are conditional on having been sent, or that the "price that wins"
   is conditioned on winning. The comparison it draws is descriptively fine;
   the risk is the reading it invites. §5 is the missing paragraph, and the
   cheapest fix is a `PERMANENT` entry in that screen's `unavailable` list
   saying which question the comparison cannot answer.

It does **not** contain: `estimate_external_id` (so no Zoho status join),
competitor-price capture, or line-grain win inference. Those remain open and
are ranked above in §3.

---

## 7. What to switch on first

1. **Type the refusals** `PERMANENT` / `COLLECTABLE` / `BUILDABLE` /
   `TRANSIENT` / `WITHHELD`. **Done in this change.** It converts a well-built
   refusal set into the worklist it was already shaped like, and makes the
   collectable entries visible as jobs rather than as apologies.
2. **Resolve the #42 / #66 collision** (§6) — one decision about one column,
   and it blocks both PRs. Recommended: #66's enum and migration, #42's
   `outcomes.py`, and make `UNKNOWN` selectable. This is the item the category
   ranks on, and no part of it needs writing from scratch.
3. **Fill in the collectable list**, which is now readable off the screens:
   reorder levels, promised delivery dates, the payment sync.
4. **MSME flag on the vendor master**, backfilled once against top vendors by
   spend. Independent of everything above; highest certainty on the list.
5. **`estimate_external_id` on `QuoteOutcome` plus an estimate puller**, once
   the loss side exists — not before, or the win rate reads 95%.
6. **Widen `override_reason_code`** to any off-recommended price. Cheap now;
   the thing that makes the outcome data analysable in three years rather than
   merely voluminous.

Then wait. The whole argument for starting is that the value is in accumulated
history, and nothing on this list except MSME pays out this quarter.

---

## 8. What sounds advanced but is wrong here

- **A Bayesian hierarchical win-rate model.** At 30–60 quotes a month across
  three entities, several families and five-plus reason codes, the posterior is
  the prior with extra steps. It would produce a number the platform cannot
  defend — the §1 failure mode with better mathematics.
- **Per-customer × item price elasticity.** Never enough data at that grain.
  The grain where it might work is family × segment, and that is years out.
- **Growing `QuoteOutcomeStatus` into a CRM pipeline.** The enum docstring says
  it is not one. Stages are how the desk gets abandoned.
- **Making the loss reason mandatory.** Produces a 100% completion rate and a
  field that says "Price". See §6.
- **Free text as the primary loss field.** `note` already exists and is the
  tempting wrong answer.
- **Coding `NO_DECISION` as `LOST`.** Poisons every denominator downstream.
- **Feeding customer-asserted competitor prices into any computed benchmark.**
  A fabrication carrying a `thresholds_version`.
- **Letting a model classify loss reason from an email thread unconfirmed.** A
  suggested chip a human taps is fine, and should record
  `AI_SUGGESTED_CONFIRMED` versus `HUMAN_ENTERED`. Storing the raw thread to do
  it is a new content store, and `architecture.md` rejected content logging for
  a reason that partly applies — that deserves an explicit decision rather than
  a drift.
- **Having the platform compute EVPI or EVSI.** The arithmetic in this document
  is a sizing exercise for a human deciding what to build. A feature that
  computes the value of information it does not have is computing on absent
  evidence, which is the exact thing §1 forbids. If it ever ships, it ships as
  a spreadsheet, not an endpoint.
