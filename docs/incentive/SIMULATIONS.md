# Simulations — personalities and stress

## Personality runs

Each personality is a strategy played consistently for a year against the
mechanism. The column that matters is the last: *which control bound them*. A
personality nobody has to manage is a personality the arithmetic managed.

| Personality | Strategy | Trajectory | Bound by |
|---|---|---|---|
| **Heavy discounter** | Closes on price | Discovers in one quarter that discount is 100% self-funded — ₹10 off a 500-piece line is ₹5,000 of their own points. Migrates to `Y` extraction (free to them) and the Toolkit (half price) | `P` is net realised |
| **Margin protector** | Holds price, hunts nothing | Thrives on `P − F`, then finds `w_inc` at 1.60–1.80 on New and Developing accounts is worth more than another two rupees on a mature line | `w_inc` band structure |
| **Farmer** | Holds the book, adds nothing | `w_base` gives a genuine annuity — the design stops punishing them for holding. But `w_base` declines with maturity and rolling-12 makes last year's book *become* this year's baseline, so standing still is a slow decline | declining `w_base` + rolling baseline |
| **Hunter** | New logos only | 1.80× on New is a real prize. Then `c(d)` stops them dumping to bad payers, the Bayesian hold stops fake logos, and the retention gate stops them abandoning what they landed | Bayesian hold + retention gate |
| **Lazy** | Minimum effort | Needs no manager conversation. Yesterday's book is today's baseline; income declines automatically and visibly | rolling-12 baseline |
| **Aggressive closer** | Promises anything | Returns and disputes hit the dispute component of `Q`; over-commitment hits forecast accuracy | `Q` |
| **Customer pleaser** | Gives price and agrees kickbacks | CAF collapses immediately. Fastest learner in the system — the feedback is same-period and unambiguous | `P` and `K` at 1:1 |
| **Long-term thinker** | Protects proving, multi-threads, works the vendor | Highest earner by a wide margin: full trial conversion, high RSI, high `Q`, high `Y` | nothing — the design is built for them |
| **System-gamer** | Runs the whole exploit list | Every route earns less than selling. `test_the_system_gamer_finds_selling_is_the_best_route` asserts exactly this over six routes at once | the arithmetic |

The system-gamer result is the load-bearing one, and it is a test rather than an
assertion: honest play beats discounting to close, paying the kickback, routing
it as a discount, selling below floor, laundering toolkit spend, and skipping
the vendor call — simultaneously.

## Stress scenarios

**In every case the correct response is a published parameter change, never a
formula change.** That is I6, and it is what separates a mechanism from a
discretionary bonus.

| Scenario | Does the design hold? | Correct lever |
|---|---|---|
| **Economic downturn** | Yes on cost — payout is a fraction of CAF, so incentive spend falls automatically with contribution. The risk is on *retention*: income collapse drives attrition | Recoverable draw (never a giveaway) + a **declared, dated** temporary lift in `w_inc` |
| **Price war** | Yes. `F` is the owner's lever, not the salesperson's. Effort still pays at a lower floor, and the salesperson naturally redirects to `Y` — which is where value remains | Publish a revised `F` schedule with a start and end date |
| **Supply shortage / allocation** | **No, unaided.** CAF is lost through no fault and next year's baseline is polluted | **Allocation neutrality clause**: periods with fill rate below a stated threshold are excluded from baseline computation and from the retention gate |
| **Excess / aged inventory** | Yes, and this is the design's most elegant moment. The aged floor plus the recovery pool turn dead stock into a company-wide bounty hunt funded from value that would otherwise be written off | `floor.aged_floor_schedule` + `recovery.bounty_rate_by_age`, refreshed monthly |
| **Cash flow crisis** | Yes. `c(d)` already prices speed | Temporarily steepen `c(d)` and raise the advance factor from 1.10 to 1.25. Declared and time-boxed |
| **Loss of a large customer** (Pitti at 4U, Lokesh at SLS) | Partly. The concentration component of `Q` already discourages the dependence — but at 4U, Pitti is ~93% of revenue and no incentive parameter fixes that | Baseline reset on appeal where the cause is exogenous. **The concentration problem at 4U is a strategy problem, not an incentive problem** |
| **Vendor withdraws a line** | Partly. `Y` disappears and `F` may move against us | Re-publish `F`; exclude the affected brand from the baseline for one cycle |

## The one that the mechanism cannot fix

4U Precision at ~93% revenue concentration in one customer and ~98% purchase
concentration in one vendor. The `Q` concentration component will sit near its
floor permanently for whoever holds that book, and no parameter setting changes
that — it is an accurate measurement of a real risk.

Two options, both outside the incentive design: accept it and set 4U's
concentration weight to zero with the reason recorded, or treat the ratio as a
strategic target with its own plan. **Leaving it to drag `Q` silently is the
worst of the three**, because it converts a strategic fact into a compensation
penalty for a salesperson who cannot act on it.
