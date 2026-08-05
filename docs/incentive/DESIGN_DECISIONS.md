# Design decisions — Section 5 closed

Phase A of the brief. Section 4 is not re-derived; the currency and the four
hard invariants are taken as settled. What follows closes the seven open
parameter questions, with the alternatives considered, the exploit each
alternative opens, and where I am guessing rather than knowing.

**Where I am guessing is marked.** Six of the numbers below are priors that a
shadow run will move. Saying which ones is more useful than defending all of
them equally.

---

## A note on what already exists in this repository

`backend/app/commercial/incentive.py` is a live *negotiation calculator*, built
earlier: it computes a salesperson's incentive on price realisation against
what a customer last paid, so a salesperson can see on the phone what a
discount costs them.

**It is not this mechanism and it uses a different currency.** It measures
against the customer's own last price; CAF measures against a published Floor
Price. Both were built to solve the same I1/I4 tension and the brief's solution
is the better one — a published F with a hidden, family-varying `m_floor` gives
an exact, self-computable number without a customer-specific reference that
drifts every time the customer buys.

The two should not coexist indefinitely. **Recommendation:** once F is
published for the top 500 SKUs (Phase 0), rebuild the negotiation desk to
compute in CAF and delete the realisation currency. Until then the desk is
useful and harmless — it never enters a payout — but two currencies in one
product is exactly the redundancy the repository's own working agreement warns
about, and it should not survive Phase 2.

---

## Q1 — Aged inventory clearance

### (a) Separate Recovery Bounty pool, not normal CAF

**Recommendation: a separate pool, computed against a published write-down
floor.**

The alternative is to lower `F` on aged stock and let CAF do the work, which is
the established position in the brief and is elegant. It fails on one point,
and the brief itself flags it: *"beyond some age `F_aged < cost`, so CAF on
those lines can exceed normal-margin CAF on a cash-losing sale."*

That is not a rounding problem, it is a **semantic** one. CAF's meaning has to
stay exactly one thing — *above the policy margin* — because that meaning is
what the salesperson is learning from every line they price. The moment "above
floor" sometimes means "below cost", the price discipline CAF carries stops
being legible, and a salesperson who has internalised "clear the floor and I am
fine" has internalised something false.

| Option | Exploit / failure it opens |
|---|---|
| Aged lines through normal CAF | Corrupts the margin signal. A salesperson optimising CAF cannot tell a healthy line from a subsidised clearance, and the highest-CAF-per-hour activity becomes clearing stock at a loss |
| Separate pool, published write-down floor | Two instruments to explain instead of one — a real cost, paid once, in the explainer |
| No aged mechanism at all | 100% write-off beats 60% recovery. Strictly worse |

The aged floor schedule, as a **fraction of the published floor** (never of
cost — expressing it against cost would require cost at the point of computing
an operations number, which is the leak the whole design avoids):

| Age | Floor fraction | Aged? |
|---|---|---|
| 0–180 d | 1.00 | no |
| 181–365 d | 0.85 | yes |
| 366–545 d | 0.70 | yes |
| 546–730 d | 0.55 | yes |
| > 730 d | 0.40 | yes |

Bounty rate on `(realised price − aged floor) × qty`, **declining with age**:
0.25 / 0.20 / 0.15 / 0.10 across those bands.

Declining is the only shape that works. A flat rate makes waiting free. A rate
that *rises* with age pays people to let stock rot until it is worth more to
clear. Declining creates urgency without creating a reason to delay.

*Guessing:* the fractions and the rates. What settles them is the actual
realised-price distribution on aged clearances over the last two years — query
in §Data queries below.

### (b) Baseline pollution — the exclusion flag

`InvoiceLine.is_aged_stock` propagates into `baseline.build`, which sums aged
CAF into `aged_excluded` and keeps it out of both `current_caf` and
`baseline_caf`. It is reported rather than silently dropped, so a reader can
see why the baseline does not tie to the invoice total.

This is not a nicety. Without it, ₹5L of clearance lands in next year's
baseline, the salesperson is punished for a windfall they cannot repeat, and
**the rational response is to not clear the stock at all** — the exact opposite
of what the pool exists to cause.

### (c) Back office — the proposer / closer split

**Recommendation: 40 / 60, open to anyone in the company, salesperson *or*
written customer acceptance required, 90-day clawback on return or dispute.**

60 to the closer because the closer carries the relationship cost of a bad
substitution and the proposer carries none. A 50/50 split would price the two
roles as equally exposed, which they are not.

Open to anyone — back office, stores, other salespeople — because the constraint
is that back office can only act at *allocation*, and a bounty only they can
earn would leave the salesperson bearing the relationship risk of a
substitution somebody else is paid for. Opening it to everyone makes dead stock
a company-wide bounty hunt funded entirely from value that would otherwise be
written off.

Acceptance is required or there is no bounty at all: an unaccepted substitution
is one person spending another's relationship.

### (d) The causation control

**Recommendation: the causer earns 0.25× the normal bounty rate on that
specific stock. Not zero.**

Zero is the intuitive answer and it is wrong. At zero, the causer's best move
is to *hide* the stock — leave it unflagged, unmatched and uncleared — and
hidden dead stock is strictly worse for the company than cheaply-cleared dead
stock. 0.25 removes the create-then-clear arbitrage while keeping clearing
better than concealing.

Attribution reads `InvoiceLine.aged_causer_id`: the salesperson whose forecast,
order or accepted vendor volume-commitment drove the PO.

Net effect on exploit 19: creating dead stock costs the commitment residual
(Q3(b)) *and* then pays a quarter rate to clear it. Strictly worse than not
creating it.

### (e) The loop to Q3 — written as one mechanism

`recovery.bounty` and `vendor.release` both read `causer_id` and both apply
`recovery.causer_bounty_multiplier`. There is one rule in one config block. If
they were written twice they would drift, and the drift reopens the highest-
return exploit in the design.

---

## Q2 — The hold-up problem

The highest-value question, and the one where the honest answer is that the
incentive can only do part of the work.

### (a) Proving Protection — soft gate, deliberately

**Recommendation: a trial with no pre-commitment document counts in the
DENOMINATOR of trial conversion and can never count in the numerator.**

Not a hard gate on the CAF from that account. Considered and rejected:

| Option | What it produces |
|---|---|
| Hard gate — no CAF from an unprotected trial | The salesperson stops recording trials. We lose the data and keep the problem |
| Multiplier on the account's subsequent CAF | Rewards proving directly, which funds a free option for the customer — the exact failure the brief names |
| **Denominator-only** | Unprotected proving is *costly* without being *banned*. Proving is the differentiator; the design must not stop it |

### (b) Trial Conversion Rate in `Q`

**N = 6 months. Weight 15%.** It displaces five points each from logo
retention (30 → 25), concentration (20 → 15) and forecast accuracy (15 → 10).

Six months because a tooling repeat cycle in this business runs one to two
quarters; twelve would make the metric respond too slowly to change behaviour,
three would count the calendar rather than the conversion. Trials younger than
N are excluded from the denominator entirely — including last week's trial
would punish a salesperson for the date.

The displacement is chosen so that no component falls below 10: a 5%-weighted
component is noise a rational agent ignores.

### (c) Structural lock-in, ranked by cost-to-us against switching-cost-created

| Lock-in | Cost to us | Switching cost created | Reward it? |
|---|---|---|---|
| **Process-level proving** (cycle time, cost-per-component) | Low — we do the study anyway | **Highest** — the data is ours, not a part number anyone can quote | **Yes** |
| **Multi-threading** (production + quality own the result) | Zero | **Highest** — purchase cannot unilaterally switch | **Yes** |
| **Rate contract executed immediately post-trial** | Zero | High — contractual | **Yes** |
| Exclusive brand / special geometry / reground | Medium — limits sourcing | Medium — a competitor must find an equivalent | Implicitly, via CAF |
| Consignment / VMI | **High** — working capital | High — physical | No separate reward; it already runs through the Toolkit |

The first three are rewarded because they are cheap and effective. VMI is not
separately rewarded because it is expensive and already available through the
Toolkit at 50 paise — paying for it twice would make the costliest lock-in the
most attractive.

Mechanically: `Trial.lock_in_factors`. Two or more and a conversion counts full
weight; a converted-but-unlocked trial counts 0.5, because the order came and
can leave just as easily.

### (d) Switch-loss — soft, via `Q`

**Recommendation: portfolio-level, through trial conversion. Not the
Relationship Bank.**

The brief says err toward soft and it is right. The salesperson does not control
whether C switches. A deal-level hit to the Bank for an outcome outside their
control produces **concealment** — trials stop being recorded — and a control
that destroys its own measurement is worse than no control.

### (e) Order Integrity — diagnostic only

**Recommendation: measure it, do not put it in `Q`.**

It correlates heavily with trial conversion, so adding it double-counts one
behaviour. And every additional `Q` component is a new arbitrage surface: the
count of moving parts is itself a design constraint. Diagnostic, reviewed
quarterly, never paid on.

---

## Q3 — Vendor discretionary discount

### (a) Timing — surfaced, never paid on

**Recommendation: a vendor-calendar field and an extraction-opportunity flag,
both diagnostic. `Y` at 1:1 does the motivating.**

Making the *timing* itself payable creates an arbitrage — a salesperson would
delay a legitimate ask to land it in a flagged window. Surfacing the window
costs nothing and helps; paying for hitting it does not.

### (b) Commitment-linked discount — the dangerous case

**Recommendation: released pro-rata as the committed stock sells; unsold
residual charged back at 18 months under the Q1(d) causer rule.**

```
released = Y × (committed_qty_sold_to_date ÷ committed_qty)
residual at 18 months = Y − released, charged to causer_id
```

Pro-rata rather than all-or-nothing at a threshold: a threshold at, say, 80%
sold would make the last 20% worth dumping at any price to trigger the
release. Pro-rata has no such edge.

Eighteen months because it is longer than the 12-month `aged` threshold — the
stock must have had a genuine chance to move before the residual bites — and
short enough that the charge lands while the person who accepted the commitment
is still accountable for it.

*Guessing:* 18 months. What settles it is the realised sell-through curve on
past volume commitments.

### (c) `Y` measurement — the standard buy price reference

`StandardBuyPrice(entity_id, brand, item_id, standard_price, effective_from,
effective_to)`. `Y = (standard − actual PO price) × qty`, measured against the
reference **in force at PO date**.

- **Refresh:** quarterly, plus immediately on any brand list revision.
- **Mid-year revision:** a new row with a new `effective_from`. The old row is
  closed, never edited. A PO priced under the old list still measures against
  the old list — editing history would silently restate past payouts.

Non-price valuation conventions:

| Concession | Convention | Why |
|---|---|---|
| Free tooling | 100% of standard buy price | It is stock we did not buy |
| Demo stock | **50%** | It comes back |
| Training | Market day rate, capped ₹25,000/day | Uncapped, this is the easiest number to inflate |
| Extended credit | `cost_of_capital × amount × days ÷ 365` | The brief's own convention |
| Co-op marketing | Invoiced amount | Verifiable |

### (d) Pass-through — confirmed

If S extracts `Y` and passes all of it to the customer as price: `Y` rises by
X, `P` falls by X, CAF is unchanged. S gains nothing and will not do it
gratuitously — but *will* do it to win an order otherwise lost, which is
correct. **Holds under these parameters** (`vendor_yield_credit = 1.00`), and it
is pinned by `test_a_rupee_from_the_vendor_equals_a_rupee_held_on_price`.

**It stops holding if `α > 1`.** At α = 1.25, passing through *gains* the
salesperson 25 paise in the rupee, and buying orders with vendor money becomes
profitable to them independently of whether the order was winnable. The Part-12
"over-incentivise" variant is therefore **not recommended**, and the config
ships at 1.00.

---

## Q4 — Calibrating `r`

```
r = target annual variable pay pool ÷ expected Σ(w · c · Q · CAF)
```

**Per entity, and the shipped config has all three at zero.** That is not an
oversight: an uncalibrated scheme must pay nothing rather than pay whatever a
developer typed. `payout.rate_for_entity` raises on an unknown entity rather
than inheriting a rate — SLS at ~₹19Cr and 4U at ~₹37L cannot share one.

**Procedure, annually, never mid-year:**

1. Run the shadow quarter (Phase 1). Compute Σ(w · c · Q · CAF) per entity.
2. Annualise. For 4U, weight for Pitti seasonality rather than ×4.
3. Set the pool: variable pay at 25–35% of total target compensation per head.
4. `r = pool ÷ annualised points`.
5. **Sanity-check both bounds.** Total incentive cost must land at 10–12% of
   entity CAF. If `r` satisfies the per-head target but breaches the portfolio
   bound, the target compensation is wrong, not `r`.
6. Publish before the year starts.

*Cannot be computed here.* It needs shadow-run output that does not exist yet.
Anything I put in the file would be a number people would anchor on.

---

## Q5 — Validating the RSI weights

The weights are a prior. The backtest that would settle them:

1. **Do the bands separate customers by realised multi-year contribution?**
   Compute RSI as at 2023-04-01 from history available then. Regress
   2023-04-01 → 2026-03-31 collected CAF on the band. If the four bands do not
   produce monotonically increasing three-year contribution, the weights are
   wrong — not the bands.
2. **Which components actually carry the signal?** Univariate correlation of
   each of the six against three-year realised contribution. My prior: tenure
   and regularity carry most of it, share-of-wallet is the weakest because it
   is an estimate the salesperson supplies. If share-of-wallet does not
   correlate, cut it to 10 and move 5 to regularity.
3. **Does the earnings distribution across the existing book look sane?**
   Compute payouts under the mechanism for the last four quarters. If one
   salesperson captures more than about 40% of the pool purely from an
   inherited book, `w_base` is too high relative to `w_inc`.
4. **Would the retention gate have fired for everyone?** If so it is too tight.
   If for nobody, it is decorative.

---

## Q6 — Cure periods and clawback magnitudes

**Undeclared `K`: 3× clawback plus Relationship Bank forfeiture. Justified, not
revised.**

A salesperson concealing `K` faces `−3K` if caught and keeps `K` if not, so
concealment is negative-expected-value whenever detection probability exceeds
25%. Given the K must be recorded before invoicing and the customer-side rep is
a witness, 25% is a low bar. A 2× multiple would need >33% detection; 5× is
punitive enough to look like a trap and invites disputes over honest
misclassification.

**Cure: 60 days on all three gates**, uniform on purpose — a per-gate cure
period is one more parameter to argue about, and there is no reason a
receivables cure should take longer than a data-integrity one.

**"Formally escalated" for G3, operationally:** a written recovery plan on the
invoice record, naming an owner, carrying dated actions, and recording *either*
a payment commitment from the customer *or* a decision to place the account on
credit hold or refer it. Logged before day 120. A verbal follow-up is not an
escalation, and neither is a reminder email with no owner and no date.

---

## Q7 — Multi-entity and shared customers

**Resolution: GSTIN → `customer_group_id`. RSI, baselines and concentration all
computed at group level. Points attribute to the salesperson on the invoice's
own entity.**

The relationship is shared; the sale is not. A customer buying from SLS and UPS
is one relationship with one tenure and one payment history — scoring the
ledgers separately counts the tenure twice and makes splitting a customer into
three ledger entities a strategy that pays.

Where two salespeople serve one group across two entities, the group baseline
`B_c` is shared and apportioned **pro-rata by each salesperson's own collected
CAF contribution to that group** in the period. Neither is credited with the
other's base, and neither is penalised for the other's decline except through
the group-level retention gate, which is correct — the relationship declines
together.

`attribution.resolve_group` falls back to the ledger id for an unmapped
customer rather than raising. A salesperson must not go unpaid because somebody
had not finished a master-data task.

---

## Data queries that would settle the guesses

| Guess | Query |
|---|---|
| Aged floor fractions and bounty rates | Realised price ÷ landed cost on every clearance of stock >180 days old, last 24 months, by age band |
| `r` per entity | Shadow-run Σ(w·c·Q·CAF) per entity per quarter |
| RSI weights | Three-year realised collected CAF regressed on RSI-as-at-2023 and on each component |
| Commitment residual horizon (18 months) | Sell-through curve on past vendor volume commitments: months to 80% sold |
| Trial conversion window (6 months) | Distribution of trial date → first repeat order date, last 3 years |
| Toolkit caps by band | Toolkit spend as % of customer CAF today, by RSI band, to see whether the caps bind |
