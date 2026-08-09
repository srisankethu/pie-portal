# 05 — Marketing science

What the analytics layer genuinely does today, whether share of wallet can be
estimated honestly here, which of the adjacent concepts earn their place, and
what sounds advanced but is wrong for this book.

Read against `CLAUDE.md` §1. The constraint that shapes every answer below is the
last one in that section: **absence of evidence is not a pass.** Most of the work
in this category turns out to be finding the places where a missing record would
have made a number look *better*, and refusing there.

**Honest summary of the category's worth.** Ranked twelfth of fifteen, and that
is right. The analytics base here is already stronger than most distributors
have, so almost everything on offer is refinement of things that work. Two items
change behaviour rather than screens — taking a self-reported number out of a pay
formula, and recording *why* quotes are lost — and the second is barely marketing
science at all. It is data capture that happens to be the only wallet evidence
this platform can observe.

---

## 1. What the layer does today

Verified by reading the code, not the docstrings.

| Module | What it does | The judgement worth noting |
|---|---|---|
| `insight/mix.py` | Three-state grid (`BUYS`/`LAPSED`/`NEVER`) over two interchangeable pivots — lines of the business, or principals | The third state is the whole point. A two-state grid folds "you had the line and lost it" into "no", which is a different phone call and usually a shorter one. |
| `insight/cohorts.py` | Revenue bands, migration between them, dormancy, lost-revenue causes, a per-customer health timeline | `journey` reuses `flow.classify` rather than re-deriving growth, so the two views cannot disagree. `migration` computes one quantile edge-set across the union of both periods — banding separately would let a customer "move up" while spending less. |
| `insight/radar.py` | Ranked opportunities on money at stake **and** evidence sufficiency | Sorts confidence-first, then money. `below_floor()` lets an empty radar explain itself, so the fix for an empty screen is a better empty state and never a lower floor. |
| `insight/flow.py` | Revenue movement decomposed into five mutually exclusive causes | `reconciles()` is an actual assertion, not a comment. `RECOVERED` is carved out of `NEW` using history older than the comparison window. |
| `insight/bonds.py` | Counterparty tie strength as five published facets | Weights live in `CommercialThresholds`, inside the version hash. Missing facets renormalise rather than scoring zero — unknown is not bad. Below `min_transactions` there is no score at all, with the reason attached. |
| `commercial/benchmark.py` | Same-item peer position | Median not mean; the subject excluded from its own benchmark; `_median_decimal` so money never leaves `Decimal`. |
| `commercial/categories.py` | Which line of the business an item is | Four ranked sources with provenance carried beside the answer. `UNCATEGORISED` is never guessed and is excluded from both sides of a coverage ratio. |

Two things found while reading:

- **`_lift` in `mix.py` returned P(B│A) — confidence, not lift.** Nothing computed
  the wrong number; the name did. Addressed below.
- **`insight/dependency.py` already refuses share of wallet by name**, in its
  docstring: *"the difference between 'they are 8% of our revenue' (a fact) and
  'we are 8% of their purchasing' (a guess this platform is not entitled to
  make)."* Any wallet work either honours that or supersedes it with a named
  basis. It must not quietly contradict a module docstring.

---

## 2. Share of wallet

### 2.1 The finding that matters most

`share_of_wallet_est` already existed, and it was inside a pay formula.

- `incentive_engine/models.py` — a bare `Decimal` on `CustomerAttributes`.
- `incentive_engine/rsi.py` — a component of the Relationship Strength Index.
- `config/parameters.yaml` — weighted **15 of 100**.
- RSI sets the band; the band sets `w_base` and `w_inc`; `w_inc` is what a
  salesperson is paid on.
- **Nothing in the application produced it.** The only constructions of
  `CustomerAttributes` were in tests.
- `docs/incentive/DESIGN_DECISIONS.md` said so plainly: *"share-of-wallet is the
  weakest because it is an estimate the salesperson supplies."*

A self-report, from the person whose compensation it moves, weighted 15%, with no
producer. Q5 of that document anticipated it failing on weak correlation and
proposed trimming the weight to 10 — which is the same defect, smaller, and needs
no backtest to settle.

**Done:** weight → 0, its 15 points to regularity (20 → 35), weights still summing
to 100 so no band boundary moves and no historical RSI shifts. The field became
`share_of_wallet_declared: Optional[WalletDeclaration]` carrying who said it and
when; `None` — nobody has declared one — is the true state for every customer
today and is not a declared zero. `RSIResult.unmeasured` names it, after
`bonds.py`'s `missing_facets`, and the score is **not** renormalised: an RSI that
moved because somebody typed into a form would make last quarter's payout
unexplainable.

### 2.2 Can it be estimated honestly?

Not as one number for all customers. It can be estimated as a **ladder of bases**,
where the answer carries its rung and most of the book lands on the bottom one.

| Rung | Denominator | What it actually claims |
|---|---|---|
| `MEASURED_TENDER` | Σ tendered, from published documents | A measurement — of *tendered* buying only |
| `BOUNDED_ASKS` | our revenue + quotes lost to competitors | An upper bound, nothing more |
| `DECLARED` | somebody's stated view | A band, attributed and expiring |
| `UNKNOWN` | — | A refusal naming the missing thing |

**`MEASURED_TENDER`.** A government or PSU tender publishes the quantity and value
being bought, and the award says who supplied it. `Σ won ÷ Σ tendered` is
arithmetic over two documents — a ratio of sums, never a mean of per-tender
shares, because a ₹2L tender won outright and an ₹80L tender lost do not average
to 50%.
*Weakness, and it is a scope weakness rather than a precision one:* it says
nothing about the same customer's off-tender buying. A customer at 60% of their
tendered tooling may be at 5% of their total. The field is therefore named for
what it measures, and the caveat ships with every answer.

**`BOUNDED_ASKS`.** Every quote recorded as lost to a competitor is spend this
book watched go elsewhere. Their spend is *at least* our revenue plus those
losses, so our share is *at most* the ratio — an upper bound, published as one,
with `share_low` fixed at zero because nothing in the evidence establishes a
floor.
*Three weaknesses, and the third is the dangerous one:*
1. Enquiries that never reached us are invisible, so the bound is loose.
2. A loss whose reason does not say where the money went cannot be counted
   either way, and is dropped from both sides rather than folded into "nobody
   bought it" — folding it would shrink the competitor's side and overstate our
   share.
3. **The bound tightens with quoting discipline, and the failure flatters us.** A
   customer whose orders arrive without recorded quotes has few recorded losses,
   so the ceiling sits near 100% and they read as an account we own. This is the
   direction that matters: a missing record makes the answer *better*, which is
   exactly the §1 trap. Quote coverage is therefore computed, returned beside
   every answer, and gates the bound entirely — below the floor the module
   refuses rather than reporting a ceiling it knows is loose.

**`DECLARED`.** A stated view, widened to a band (±0.10) and expiring at twelve
months, carrying who said it. Somebody who says "about a third" is not claiming
33.3%, and recording it as though they were is how an offhand answer becomes a
figure in a review.
*Weakness:* it is the number just removed from the RSI. Reachable in the ladder,
**deliberately unwired from the endpoint** until there is somewhere to record a
declaration that is not inside a pay formula.

**`UNKNOWN`.** The common answer. The refusal names the specific missing thing —
*record the next declined enquiry with who won it* / *fill in the award* / *ask
again on the next visit* — because "not enough data" sends nobody to do anything.

### 2.3 No midpoint, anywhere

There is no `share` field and no midpoint on `WalletEstimate` or in the dict it
emits, and a test pins that across all four rungs. The band's width is the
estimate's honesty; a `(low + high) / 2` would be consumed as a measurement
within a week by a chart axis, a sort or an export. A caller that wants one number
has to write the arithmetic, which is the point at which somebody asks whether it
is wise.

---

## 3. The other concepts

### 3.1 Lift rather than raw co-occurrence — `insight/mix.py` *(done)*

`_lift` returned P(B│A). That is confidence, and on its own it misleads in the
direction that wastes a sales visit: if nearly every customer takes cutting
tools, then *"90% of your coolant customers also take cutting tools"* is 90% and
carries no information, because 90% of everybody takes them — and it sat at the
top of every gap list, the same sentence under every customer.

Now three numbers travel together: `confidence` (P(B│A), the one displayed),
`base_rate` (P(B)) and `lift` (their ratio). Gaps are **ordered by lift, shown
with confidence**. Two independent floors, because the comparison has two sides:
`MIN_PEERS` guards the anchor a share is computed over, `MIN_BASE` guards the
target it is compared against — dividing by a base rate drawn from three
customers manufactures a large ratio from noise. Clearing the first but not the
second reports confidence with a null lift rather than nothing: the co-occurrence
is still a fact, and only the comparison is unsupported.

**Where this matters, honestly:** far more on the principal pivot than the
category one. There are five lines of the business and nearly everyone takes
cutting tools, so on `BY_CATEGORY` lift can mostly only say "no information
here". Across principals the base rates are uneven and lift genuinely reorders
the list.

### 3.2 Behavioural segmentation — a published rule, not a new score

`bonds.py` argues at length against unauditable composites; a new segmentation
score walks straight into that. The right shape is a **cell** built from axes
already computed and already floored elsewhere: breadth (`bonds.breadth_of`),
cadence regularity (`cadence.py`), size band (`cohorts.band_of`). No new weights,
nothing new inside the version hash, and each segment names an action — "broad +
regular + small" is a stocking-agreement candidate; "narrow + irregular + large"
is single-threaded and fragile.

- **Price sensitivity** is honest only from `QuoteDecision` × `QuoteOutcome`: win
  rate as a function of the quoted price's distance from the reference. Real
  observed elasticity, but it needs quote volume that will not exist for a year,
  and it reads reference data, so it is RESTRICTED and cannot reach a
  salesperson's screen. Do not fake it from discount depth.
- **Service intensity** has observable proxies — lines per document, documents
  per month, share of below-median-value orders. Label them proxies. Do **not**
  convert to a rupee cost-to-serve: there is no activity cost basis in this data,
  and a fabricated one is a cost number on a screen with nothing behind it.

### 3.3 Next-best-offer — `mix.py` gaps → `decisions/`

The gaps and their strongest anchor already exist; NBO is the cross-customer
ranking plus the phrasing, and the phrasing belongs in `decisions/`, the
designated seam. Two constraints:

- **No rupee value on a suggestion.** `mix.py` explicitly refuses to say a gap is
  money; an NBO carrying a value reintroduces that claim through a different
  screen. Rank on evidence — now lift × support × the customer's spend in the
  anchor line — and render the evidence sentence.
- **Dismissals must be durable.** "They have no lathes" is real negative
  knowledge from a human. Store it against (customer, column), suppress the
  suggestion, and surface accumulated reasons as catalogue feedback. Without
  this the list re-suggests the same wrong thing every month and gets ignored,
  which is how these features actually die.

### 3.4 Customer lifetime value — a fact and an arithmetic projection, separately

Realised contribution to date is Σ gross_profit over the relationship, aggregated
per §1 and RESTRICTED. Forward: current run-rate held flat for N months,
discounted at a rate in `CommercialThresholds` inside the version hash, with the
flat-run-rate assumption named on the row. No churn probability. Cost coverage
must travel with it exactly as `health_timeline` already does — a contribution
figure computed over a third of the lines is the §1 trap wearing a rupee sign.

---

## 4. What to do, in order

1. ~~Take the self-reported wallet share out of the RSI.~~ **Done.**
2. ~~Record why quotes are lost.~~ **Done** — enum, columns, migration, a
   `set_outcome` that refuses a `LOST` with no reason, and the screen that asks.
3. ~~The wallet ladder and the tender store.~~ **Done**, with `DECLARED` wired
   only as far as honesty allows.
4. ~~Lift alongside confidence in `mix.py`.~~ **Done.**
5. **Next-best-offer with durable dismissals.** Not done. The largest remaining
   item and the one that turns `mix.py`'s existing output into sales activity.
6. **Segmentation cell and CLV.** Not done. Good screens; they do not change what
   anyone does on Monday.

---

## 5. What sounds advanced and is wrong here

- **Peer-basket-ratio wallet imputation.** *"Customers like this one spend 0.3× their
  insert spend on coolant, so their coolant wallet is X."* The ratio is fitted over
  customers who buy both **from us** — the high-share ones — so it estimates our
  expected revenue at the peer attach rate, not their total spend. It is a
  headroom estimate wearing a wallet label. If you want the number, call it
  "revenue if they attached like their peers" and keep it out of any share.
- **Coolant volume as a machine-hours denominator.** The most tempting
  cutting-tool-specific idea available, and it fails three ways: coolant purchase
  is lumpy (one drum spans months, so a 12-month window holds 0 or 3 purchases
  for identical consumption); coolant is routinely bought from the machine dealer,
  so our volume is itself an unknown share; and tooling per machine-hour varies by
  an order of magnitude between aluminium and Inconel. Three unknown multipliers
  stacked produce a decimal point and no information.
- **BG/NBD + Gamma-Gamma CLV, or any non-contractual churn model.** They assume a
  large, homogeneous, high-frequency population. This book is a few hundred
  counterparties with enormous size dispersion and tender-driven lumpiness. The
  model will emit a per-customer "probability alive" and it will be a fitted
  artefact. `cohorts.dormancy` already makes the right call: *"not a churn
  prediction — an observation with a date attached."*
- **Any propensity or uplift model producing a per-customer conversion
  probability.** `radar.py` already names why: confidence here is evidence, not
  probability. A propensity score is precisely the output §1 forbids.
- **RFM as a new score.** It is `bonds.py` with worse weights and unpublished
  facets. Bonds already measures recency against each counterparty's *own* rhythm,
  which is strictly better than fixed quantiles.
- **Firmographic wallet sizing** (MCA-filed turnover × an industry tooling-spend
  percentage). The percentage is the whole answer and it is a guess; a job shop and
  an assembly plant at identical turnover differ ~20× in tooling spend.
- **National market size ÷ plant count**, or HSN-level trade data, as a
  customer-level denominator. Not a customer-level fact.
- **A band rendered as its midpoint.** Even where a band is honest, emitting the
  midpoint converts a refusal into a measurement.

---

## 6. Open, and worth saying

- **`wallet_min_quote_coverage = 0.30` is a guess.** The config comment says so.
  It is set low deliberately — a floor high enough to be safe would refuse every
  customer, and a screen that always refuses teaches nobody anything. Revisit it
  against real coverage rather than leaving it because it shipped.
- **The `DECLARED` rung is reachable and unwired.** Deliberate. See §2.2.
- **No UI for the wallet estimate or the tender store.** Both are API-only. The
  tender form is a small screen; the wallet band probably belongs on the customer
  page beside `/dependency`, which measures the opposite direction.
- **`mix.py`'s `Cell.orders` counts lifetime while `Cell.revenue` counts the
  window.** A cosmetic inconsistency found while reading, deliberately left alone
  — out of scope for this change, and noted here so the next reader does not have
  to re-find it.
