# PIE pricing model — the analysis, and the recommendation

Everything below is computed by `backend/app/monetization/`, not written by
hand. Re-run it with:

```bash
cd backend
python3 -m app.monetization                     # the tables in §2 and §9
python3 -m app.monetization --impact conservative
python3 -m app.monetization --json              # every figure, machine-readable
```

The interactive version is the **Pricing model** screen in the platform, behind
`PIE_OPERATOR_EMAILS`. It is not a tenant screen: a distributor who could read
it would be negotiating against PIE's own reservation price, which is a worse
disclosure than any of the cost-and-margin leaks CLAUDE.md §1 catalogues.

Figures in this document are the **base** impact set at assumptions version
`mon_fcb7f08100ec`. Where a number moves materially under the conservative or
aggressive set, the range is given.

---

## 1. The verdict on 0.1% of margin, first

**0.1% of gross margin is not a low price. It is not a price at all.** On every
reading of "margin", for every segment, it comes in below what it costs PIE to
serve the customer:

| Segment | 0.1% of PIE-touched margin | Cost to serve | Short by |
|---|---:|---:|---:|
| Small (₹5 Cr gross profit) | ₹0.4 L | ₹11.0 L | **29.8×** |
| Mid (₹35 Cr gross profit) | ₹2.6 L | ₹19.0 L | **7.3×** |
| Large (₹250 Cr gross profit) | ₹17.2 L | ₹58.5 L | **3.4×** |
| ₹100 Cr revenue | ₹1.7 L | ₹15.5 L | 9.1× |
| ₹1,000 Cr revenue | ₹14.5 L | ₹51.0 L | 3.5× |

And on the *incremental* margin reading — the one that sounds most defensible —
it is worse again by roughly a factor of eight, because incremental gross profit
is about an eighth of the gross profit flowing through the platform.

The hypothesis is not off by a rounding. **It is off by 20–50× against value and
by 3–27× against cost.** The right order of magnitude, expressed the same way, is
**2–3% of the gross margin PIE touches**, or equivalently about **0.5% of the GMV
it touches**.

Two things make 0.1% *sound* reasonable and both are worth naming, because they
are the reasons a number like this survives a meeting:

- **It is benchmarked against the wrong base.** Ten basis points is a payments
  number, and payments is a utility with near-zero marginal cost, no
  implementation and no cost to serve. PIE has an ERP connector build, a
  catalogue to embed and a customer-success load.
- **"Margin" is ambiguous by an order of magnitude.** 0.1% of the *whole book's*
  gross profit, of the *PIE-touched* gross profit and of the *incremental* gross
  profit are three different prices. The model reports all three, and the phrase
  on its own does not pick one.

---

## 2. What PIE should actually charge

| Segment | Economic value created | Cost floor | **Recommended** | Structure | Customer ROI |
|---|---:|---:|---:|---|---:|
| Small (₹5 Cr GP) | ₹0.74 Cr | ₹11.0 L | **₹11 L/yr** | ₹7.5 L + 0.156% of turnover | 5.7× |
| Mid (₹35 Cr GP) | ₹5.38 Cr | ₹19.0 L | **₹80.5 L/yr** | ₹53 L + 0.161% | 5.7× |
| Large (₹250 Cr GP) | ₹36.93 Cr | ₹58.5 L | **₹5.54 Cr/yr** | ₹3.66 Cr + 0.142% | 5.7× |
| ₹100 Cr revenue | ₹3.53 Cr | ₹15.5 L | **₹53 L/yr** | ₹35 L + 0.160% | 5.7× |
| ₹1,000 Cr revenue | ₹31.08 Cr | ₹51.0 L | **₹4.66 Cr/yr** | ₹3.08 Cr + 0.142% | 5.7× |

Under the **conservative** impact set those become ₹11 L (refused — see below),
₹33.5 L, ₹2.30 Cr, ₹22 L and ₹1.94 Cr. Under **aggressive**: ₹22.5 L, ₹1.65 Cr,
₹11.33 Cr, ₹1.09 Cr and ₹9.54 Cr. **The spread is 4–5×, and it is entirely
driven by how much PIE actually moves the funnel — which nobody has measured.**
That, not the choice of metric, is the largest uncertainty in this document.

The price is built by three constraints intersecting, in order:

1. **A floor from cost.** A fee that does not clear the cost to serve at 75%
   gross margin is a subsidy, not a price. No ROI argument makes it one.
2. **A ceiling from ROI.** The largest fee leaving the customer 5× net return.
3. **A target at 15% of value created**, clamped into that band.

### The small segment is a refusal under conservative assumptions

At ₹11 L the small distributor's cost floor is *above* its ROI ceiling: serving
it profitably and leaving it a 5× return are incompatible. The model reports an
**EMPTY BAND** rather than picking one, and the honest reading is that the
bottom of the market should not be sold to directly at all until onboarding and
support cost materially less. That is the single most actionable finding here
and it is not a pricing decision — it is a cost decision.

---

## 2a. What the base actually is — and it is not GMV

The word was wrong, and the word matters. **GMV is a marketplace term for
third-party volume an intermediary never owns.** A distributor buys and sells on
its own balance sheet; nothing "flows through" PIE. What the recommendation
bills on is the customer's **turnover** — and 0.15% of a marketplace's GMV and
0.15% of a company's turnover invite very different intuitions. Importing the
marketplace frame is the same error §01 convicts the 0.1% hypothesis of.

Said in the buyer's own terms, the total fee is:

| Segment | Turnover | Fee | % of turnover | % of gross profit |
|---|---:|---:|---:|---:|
| Small | ₹22 Cr | ₹11.0 L | 0.491% | **1.93%** |
| Mid | ₹171 Cr | ₹80.5 L | 0.470% | **2.01%** |
| Large | ₹1,330 Cr | ₹5.54 Cr | 0.416% | **1.95%** |
| ₹100 Cr revenue | ₹113 Cr | ₹53.0 L | 0.471% | **2.01%** |
| ₹1,000 Cr revenue | ₹1,120 Cr | ₹4.66 Cr | 0.416% | **1.95%** |

**About 2% of gross profit, near-flat across a 50× range of customer size.**
That is the number a CFO will compute, so it is the number to lead with.

### The definition, precisely enough for a contract

`strategies.BILLABLE_REVENUE` holds this, so the model and the agreement cannot
drift apart:

| What | In or out | Why |
|---|---|---|
| Invoice line items | **IN** | `SalesTxn.line_revenue`, one row per line |
| Line discounts | **Deducted** | Line revenue is post-discount — what was paid, not what was listed |
| GST | **OUT** | Line revenue is pre-tax. A tax-inclusive base moves PIE's fee with a rate change neither company controls |
| Freight, packing, invoice-level charges | **OUT** | They never become a `SalesTxn`: normalisation requires an item id, so an invoice-level charge has no line to write. Out by construction — stated so nobody adds them later |
| Credit notes and returns | **UNSETTLED** | Not netted, and *not nettable*: `CreditNoteDoc` is header-grain and its total is **tax-inclusive** while line revenue is pre-tax. Subtracting one from the other over-deducts by exactly the GST |
| Sales between the customer's own entities | **UNSETTLED** | Nothing marks a related party. Three connected companies that invoice each other have the same goods counted once per book — billed twice |
| Which connected companies count | **UNSETTLED** | Every connection contributes today. Whether a newly connected company is inside the fee is a commercial question the sum cannot answer |

The three unsettled rows are exactly why this is a contract definition and not a
query. `evidence.observe` now reports the credit-note total beside the revenue
with the tax-basis mismatch named, and raises a gap whenever more than one
connected company contributes — **reported, never silently netted**, because a
number corrected in the wrong direction is still the wrong number to invoice on.

---

## 2b. The constraint that picks the base: there is no quote conversion

**A quote is never converted into a sales order.** The estimate is sent from the
ERP; the order arrives later as a customer PO and is entered independently.
Nothing joins the two — `QuoteDoc` carries a free-text `reference` and no order
id, and there is no quote-to-order link table in the schema. The platform's own
note on the outcome capture is blunt about the consequence: roughly three
quarters of this book's estimates were raised in the ERP and ended with no
recorded outcome at all.

This is not a measurement weakness to be improved. It removes an entire class of
pricing metric, and it removes the base the first draft of this document
recommended billing on.

| What a fee could be billed on | Obtained how | Billable? |
|---|---|---|
| Whole connected-book invoiced revenue | Synced invoice lines | **Yes** — two independent records of one number |
| Whole-book gross margin | Synced sale + cost rows | Yes, if the customer will expose cost |
| Margin held, equivalents accepted | Recorded when the quote is priced | Yes, for quotes that went through PIE |
| Quote outcomes (won / lost) | Only where a person entered one | Partially — most of a book has none |
| PIE-touched revenue or margin | Needs a quote→order link | **No — the link does not exist** |
| Incremental gross margin | Needs that link *and* a counterfactual | No |

So the recommendation bills the **whole connected book**, and this is forced
rather than chosen. It costs nothing: the same money is collected at a lower
headline rate, because the rate falls by the covered share.

| Segment | Rate on PIE-touched revenue | Same money, on the whole book |
|---|---:|---:|
| Small | 0.243% | **0.156%** |
| Mid | 0.249% | **0.161%** |
| Large | 0.237% | **0.142%** |
| ₹100 Cr revenue | 0.248% | **0.160%** |
| ₹1,000 Cr revenue | 0.237% | **0.142%** |

`strategies.BASE_MEASURABILITY` grades every base `SYNCED` / `RECORDED` /
`INFERRED`, every percentage fee carries its grade in `Fee.basis`, and
`report.recommend` raises rather than return a structure built on a base that is
not `SYNCED` — because a later edit that quietly swapped the base back would
produce a plausible number nobody could invoice.

**What this costs elsewhere in the model.** Per-order pricing fell from 7th to
11th in the scorecard once measurement and auditability were scored against the
missing link rather than against an assumed one. And the value model's largest
driver — the uplift to quote-to-order conversion — is *unobservable* from synced
rows, which is why it sits in §11 as the single largest thing needing
validation rather than as something the platform can measure its way to.

---

## 3. The pricing-metric scorecard

Weighted from the objective function in the brief (maximise enterprise value ×
adoption × customer ROI × predictability; minimise friction × measurement
difficulty × gaming × churn). Every score is a **judgement**, graded `ASSUMED` —
no closed-won cohort, renewal or billing dispute exists to calibrate against.
The weights are exposed so disagreement can be structural rather than rhetorical.

| # | Metric | Score |
|---:|---|---:|
| 1 | Value-derived subscription (banded) | 8.05 |
| 2 | **Platform fee + % of invoiced revenue** | 7.98 |
| 3 | Platform fee + % gross margin | 7.48 |
| 4 | % of invoiced revenue (turnover) | 7.27 |
| 5 | Minimum commitment + usage | 7.21 |
| 6 | Enterprise licence, unlimited | 6.82 |
| 7 | Low platform fee + performance fee | 6.71 |
| 8 | % of gross margin | 6.52 |
| 9 | Per quote | 6.39 |
| 10 | Per user (seat) | 6.29 |
| 11 | Per order | 6.14 |
| 12 | Per RFQ | 6.12 |
| 13 | Per successful match | 5.73 |
| 14 | Savings share | 5.64 |
| 15 | **% of incremental gross margin** | 5.30 |

The two ends are the interesting part.

**% of incremental gross margin ranks last while scoring 10/10 on correlation
with value.** It is the most *correct* metric and the least *usable* one:
billing it requires a counterfactual that nobody can observe and that the paying
party has every interest in disputing. In a year when tooling demand rises 12%,
"that growth was the market" is not even a bad argument. Its place is as the
**renewal argument** — which is exactly what `attribution/` already builds — not
as the invoice.

**Per-RFQ ranks 12th and would be actively harmful.** It taxes the one behaviour
the platform most needs. A customer minimising the bill routes only the enquiries
it already expects to win, which is precisely the set PIE adds least to, and the
coverage that produces the value never happens. The cheapest bypass in the whole
table is "don't paste the email in".

**Per-seat is the only metric negatively correlated with the product's purpose.**
PIE exists so a desk of six quotes what twelve used to; a seat meter bills the
customer for not having adopted it.

---

## 4. Game theory: what a customer does after signing

Full register in `monetization/scorecard.py`; the five problems the brief names,
answered:

**Attribution.** Severe for incremental-margin and savings-share, low for GMV.
The decisive asymmetry: a GMV fee billed on the *whole connected book* needs no
attribution at all, which removes the argument rather than winning it.

**Bypass.** The core case — PIE identifies an equivalent, the customer notes the
part number and buys it elsewhere — is invisible to a proposal-based meter and
visible to an ERP-connected one. **The Zoho/ERP sync is a commercial asset, not
only a feature**: it is what turns an assertion into an observation. A low rate
on a broad base is also structurally robust here, because at 0.25% the saving
from routing around PIE is smaller than the operational cost of running two
quoting processes.

**Margin opacity.** Severe, and the reason margin-share loses to GMV despite
better alignment. A distributor's margin is its most closely held number — *this
codebase withholds it from its own salespeople* — and a fee keyed on it makes the
vendor a party to every cost decision. Expect landed cost to acquire freight,
handling and financing the quarter after signing.

**Under-reporting.** Low for GMV: the base is invoiced revenue that PIE syncs
*and* the customer files with the tax authority. Two independent records of one
number is as good as B2B measurement gets.

**Classification.** Removed entirely by billing the whole connected book instead
of a PIE-touched subset somebody has to classify.

**Should PIE earn more when the customer earns more?** Yes — but bounded. Perfect
alignment (incremental margin) is unbillable; zero alignment (flat licence) is
unarguable at renewal. The hybrid is alignment that can actually be invoiced, and
the cap on the variable component is what stops a good year turning into a
renegotiation.

---

## 5. Elasticity

Two curve forms, swept over three elasticities. The constant-elasticity runs are
reported and then set aside: they have no interior optimum by construction, and
their maxima sit either on the grid edge or at the saturation point where
adoption is clamped to 1 — both artefacts of the functional form, and both
flagged as such rather than reported as prices.

The bounded-logistic runs, over a blended 200-prospect pool:

| Elasticity | Revenue-maximising price | Adoption | Expected revenue |
|---|---:|---:|---:|
| −0.6 | ₹96 L (runs off the grid) | 6% | ₹12.0 Cr |
| −1.2 | ₹10.1 L | 36% | ₹7.4 Cr |
| −2.5 | ₹8.5 L | 57% | ₹9.7 Cr |

**Read the gap between this and §2, not the levels.** A single list price
maximises revenue at ₹8–10 L against an undifferentiated pool, while the
value model says a large distributor should pay ₹5.5 Cr — a 50× spread. That is
the numerical case for **segmented pricing over a single list price**, and it is
the most robust conclusion in this section. The levels themselves are not
trustworthy: `reference_win_rate` is `NEEDS_VALIDATION` because no deal has
closed at any price.

---

## 6. PIE's unit economics

At the recommended fee, per customer per year:

| Segment | Fee | Cost to serve | Gross margin | CAC | LTV | LTV/CAC | CAC payback |
|---|---:|---:|---:|---:|---:|---:|---:|
| Small | ₹11.0 L | ₹2.7 L | 75% | ₹11.0 L | ₹41 L | 3.8× | 20 months |
| Mid | ₹80.5 L | ₹4.6 L | 94% | ₹80.5 L | ₹4.71 Cr | 5.8× | 13 months |
| Large | ₹5.54 Cr | ₹14.6 L | 97% | ₹5.54 Cr | ₹34.2 Cr | 6.2× | 12 months |

Two corrections the model made to itself while being built, both worth recording
because the uncorrected versions were the plausible-looking ones:

- **Flat CAC reported an LTV/CAC of 569× on the large segment.** That is not a
  finding about the business; it is the input saying a ₹5.5 Cr enterprise
  contract is won for the same ₹6 L as an ₹11 L self-serve one. CAC now scales
  as `max(floor, 1.0 × first-year ACV)`, and the ratios above are the result.
  `unit_economics` now warns whenever LTV/CAC exceeds 20×, on the principle that
  an implausibly *good* number is evidence about the inputs exactly as an
  implausibly bad one is.
- **Purely fixed opex produced an 86% EBITDA margin in year five.** Engineering
  and G&A scale with the business; opex now carries a floor proportional to
  revenue.

Everything here inherits `support_cost_per_customer_year`,
`customer_success_cost_per_customer_year` and `cac_per_customer`, all
`NEEDS_VALIDATION`. `unit_economics` reports the **weakest** grade among its
inputs, never the average.

---

## 7. Five years

Base case, at the price book in §2 (blended ACV ₹1.0 Cr):

| Year | Customers | GMV under management | Revenue | Gross margin | EBITDA | EBITDA before growth spend |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 6 | ₹609 Cr | ₹3.0 Cr | 95% | −₹6.7 Cr | −₹0.7 Cr |
| 2 | 21 | ₹2,659 Cr | ₹14.4 Cr | 96% | −₹10.6 Cr | +₹7.1 Cr |
| 3 | 52 | ₹7,091 Cr | ₹42.3 Cr | 96% | −₹20.2 Cr | +₹20.9 Cr |
| 4 | 106 | ₹15,252 Cr | ₹100.1 Cr | 96% | −₹32.5 Cr | +₹50.0 Cr |
| 5 | 190 | ₹28,461 Cr | ₹205.4 Cr | 97% | −₹42.8 Cr | +₹103.6 Cr |

EBITDA is negative in every scenario through year five, and the last column is
why that is the expected shape rather than a problem: the installed base is
profitable from year two and the loss is the cost of acquiring the *next*
cohort at 1× ACV. Growth spending is a choice that can stop; an unprofitable
book is not. The column exists to tell those two apart and must never be quoted
as a nicer EBITDA.

**ARR at scale** (base price book): ₹10 Cr at 10 customers, ₹50 Cr at 50,
₹100 Cr at 100, ₹500 Cr at 500, ₹1,000 Cr at 1,000. Conservative: ₹660 Cr at
1,000. Aggressive: ₹1,544 Cr.

---

## 8. Experiments

Four designs in `monetization/experiments.py`, each with its hypothesis, arms,
sample, success and failure metrics, biases and reading. The most useful output
is a **refusal**: every design needs 150–350 qualified prospects *per arm* to
read as a randomised test, and PIE will have tens.

So none of these is run for significance. Each is run to observe one thing that
is informative at n = 1 to 10:

- **E1, three price levels.** Read the *counter-offers*, not the win rate. Three
  counters clustering at ₹6–8 L is a stronger willingness-to-pay signal than a
  win-rate difference this sample can never make significant.
- **E2, hybrid vs flat.** Answers one binary a prospect settles in the first
  meeting: *is a GMV meter acceptable at all, and is a cap demanded?* If the
  requested cap clusters at 2–3× the platform fee the hybrid is real; if at 1×,
  the market is buying a subscription and the meter is theatre.
- **E3, performance fee.** A measurement experiment, not a pricing one. One
  invoice raised on attributed value and paid without adjustment. n = 1 is
  sufficient — and a single dispute rules the model out, because that dispute
  recurs every quarter for the life of the contract.
- **E4, per-RFQ.** Measures routed share from PIE's own rows. A behavioural gap
  is decisive at n = 2 because the mechanism is not statistical: it is a person
  deciding whether to paste an email into a system that charges for it.

---

## 9. The hard answers

**1. What should PIE charge?** A platform fee plus a rate on whole connected-book GMV,
sized at 15% of measured economic value, floored at cost to serve and capped so
the customer keeps a 5× return. In money: **₹11 L / ₹80 L / ₹5.5 Cr** a year for
small / mid / large.

**2. What should the metric be?** **Hybrid: annual platform fee + % of the connected book's invoiced revenue** — turnover, not "GMV": PIE is not a marketplace and nothing flows through it.
Not margin (opacity), not incremental margin (unbillable), not RFQ (taxes
adoption), not seats (anti-correlated with the product). Whole-book invoiced revenue is the only base
that is auditable on both sides, requires no cost disclosure, needs no
quote-to-order link (there isn't one — §2b), and grows with the customer. A banded value-derived subscription scores marginally higher (8.05 vs
7.98) and is the right *first* contract; the hybrid is the right steady state,
because the subscription's two weaknesses — decoupling from value between
renewals, and no expansion without a renegotiation — are exactly what the
variable half fixes.

**3. Is 0.1% of margin viable?** **No.** Off by 20–50× against value and 3–27×
against cost to serve. Below cost on every reading of "margin", for every
segment.

**4. The first five customers?** **₹36 L/year** for a mid-market design partner
(₹24 L at ₹100 Cr revenue scale) — about 45% off list, floored at cost to serve
— in exchange for four things written into the same contract: a named reference
and case study after two quarters; a **baseline captured before go-live**; a
contractual step-up to list at the second renewal; and a full ERP connection.
The discount buys evidence, and a discount whose expiry is a future conversation
is a permanent discount.

**5. A small distributor?** **₹11 L/year** — which is the cost floor, not a
value-derived price, and which the model *refuses* under conservative
assumptions. Sell to this segment through a partner or self-serve, or not yet.

**6. A ₹100 Cr-scale (revenue) distributor?** **₹53 L/year** = ₹35 L platform +
0.160% of connected-book invoiced revenue (≈2.0% of gross profit). Range ₹22 L – ₹1.09 Cr across impact sets.

**7. A ₹1,000 Cr-scale distributor?** **₹4.66 Cr/year** = ₹3.08 Cr platform +
0.142%. Range ₹1.94 Cr – ₹9.54 Cr.

**8. What ROI should PIE target?** **5×** net to the customer, with payback
inside six months. 3× does not survive a sceptical CFO discounting PIE's own
value claims by half; 10× leaves 90% of created value on the table and, at the
small end, makes the segment unservable — the model shows the band going empty
before 10× is reachable. It is configurable (`PIE_MON_MIN_CUSTOMER_ROI`) and 5×
is the answer this model gives.

**9. Strongest long-term pricing power?** The hybrid, because pricing power here
comes from **being in the path of the transaction with the measurement to prove
it**. The platform fee makes revenue predictable; the GMV rate makes the account
grow without a negotiation; `attribution/` makes the renewal an argument from
evidence rather than from goodwill. The thing that compounds is not the rate — it
is that PIE holds the only record of what the enquiry-to-order path actually did.

**10. What makes PIE large rather than a software product?** Transaction
participation, but not as a replacement for the subscription — see §10 below,
because the numbers invert the intuition.

---

## 10. The strategic test: software, or industrial commerce?

The question resolves differently from how it is usually posed, and the model is
what changed the answer.

**PIE's recommended price is already about 0.15% of the whole connected book, or 0.49% of the GMV it touches.** At 1,000
customers the platform sits over ₹2.03 lakh crore of GMV. But:

| Customers | GMV under management | Subscription ARR | 10 bps | 25 bps | 50 bps | 100 bps |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | ₹2,030 Cr | ₹10 Cr | ₹2 Cr | ₹5 Cr | ₹10 Cr | ₹20 Cr |
| 100 | ₹20,297 Cr | ₹100 Cr | ₹20 Cr | ₹51 Cr | ₹101 Cr | ₹203 Cr |
| 1,000 | ₹2,02,971 Cr | ₹1,000 Cr | ₹203 Cr | ₹507 Cr | ₹1,015 Cr | ₹2,030 Cr |

**Ten basis points is not the bigger business — it is a fifth of the
subscription.** The flow only overtakes a value-derived subscription somewhere
above 50 bps. So "monetize the transaction rather than sell seats" is a false
choice: a value-derived fee *is* a take rate, charged as a fixed number.

What that reframes the decision to:

- PIE is **not** selling software seats, and should never price as though it
  were. The value scales with transaction flow, so the price must too. That much
  the model settles.
- The real choice is **whether the rate floats**. A take rate moves with the
  customer's book without a renegotiation, which is the compounding property;
  a subscription has to be re-argued every year.
- A true take rate is only collectable where PIE is **in the path of the order**.
  Today it is in the path of the *quote*. That is the gap to close, and it is a
  product decision — order capture, supplier-side mediation — before it is a
  pricing one.

So: **start with the banded value-derived subscription, move to the hybrid as
soon as the GMV meter is accepted (E2 answers that in one meeting), and earn the
right to a genuine take rate by getting into the order path.** Pricing follows
the product's position in the transaction, not the other way round.

---

## 11. What is known, assumed, estimated, and needs validating

**Known.** The tenant's own funnel and book, where an organization is connected:
`monetization/evidence.py` reads invoiced revenue, invoiced margin (from the
persisted customer-item projection), enquiry volume, quote volume and SKU count
from real rows. Anything it cannot read comes back UNKNOWN with a named gap —
never a plausible default. It deliberately does **not** derive conversion rates:
enquiry lines and quotes are different grains, and the defensible win-rate
reading belongs to `attribution/evaluator`, which guards the case where no
decided quote could ever have been won.

**Assumed (PIE's choice, cannot be wrong — only unwise).** 15% value capture,
5× minimum customer ROI, 6-month payback limit, the 66/34 platform-to-variable
split, the 2× cap on the variable component, and every score in §3.

**Estimated (derived from something measured).** Inference cost per RFQ (from
`AI_COST_PER_MTOK_*` and observed call shapes), embedding cost per SKU,
infrastructure, storage, third-party API.

**Needs validation — and the recommendation is sensitive to all of it.**

| Parameter | Why it matters |
|---|---|
| The impact set (uplift to conversion, order value, margin) | Drives a **4–5× spread** in every value-based price. The single largest uncertainty here — and the conversion half of it is *unobservable* from synced rows, per §2b. |
| `support_cost_per_customer_year` | Largest COGS line, no telemetry behind it. |
| `customer_success_cost_per_customer_year` | Same. |
| `cac_per_customer` | No closed-won cohort. Everything downstream of LTV/CAC inherits it. |
| `annual_gross_retention`, `net_revenue_retention` | No renewal has happened. |
| `reference_win_rate` | The elasticity anchor. Until E1 runs, every optimum is a shape, not a number. |
| `productivity_hourly_rate` | UNKNOWN by design. Labour savings are counted in hours and never valued, matching the attribution ledger's refusal to price time. |

`python3 -m app.monetization --json` emits this list generated from the grades
rather than written by hand, so it cannot fall out of step with the model.
