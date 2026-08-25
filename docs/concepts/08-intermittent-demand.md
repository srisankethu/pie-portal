# 08 — Intermittent demand and stocking policy

**Verdict: the category was ranked below the line, and that was right — but for
a stronger reason than "not enough scale yet", and it was hiding a live defect
worth more than the item that ranked.**

Croston's method and the Syntetos-Boylan approximation are not merely premature
on this book. They are **not estimable**, and no amount of growth fixes that.
Meanwhile the one adjacent item that did rank — #6, dead-stock liquidation as an
NPV decision — is real but roughly one-fifth the size the stock screen implies,
because the screen was counting stock bought ten weeks ago as dead.

Everything below was measured against the live books on 2026-08-09.

| | SLS ENGINEERS | 4U PRECISION |
|---|---|---|
| items in master | 3,200 | 473 active (+40 inactive) |
| items holding stock | 2,720 | — |
| stock value at cost | ₹55,53,168 | — |
| invoices | 3,367 (₹26.2 cr) | — |
| invoice history begins | 2025-04-01 | 2025-04-04 |
| earliest item record | 2026-02-24 | 2025-03-15 |

UPS was not reachable during this work; its connector appeared once and dropped.
Everything here is two entities of three.

---

## 1. What the stock layer genuinely knows, and what it refuses

`insight/stock.py` knows: on-hand, available and actual-available as three
distinct facts from Zoho's own netting; the last sale date; cumulative units
sold with a first-sold date so a rate has a real denominator; the last purchase
cost by *bill date* rather than read order; and who has bought the line before.

Two of its decisions are better than they look and should be left alone:

- **`priority()` is monthly holding cost, not a composite score.** The docstring's
  reasoning is right: a weighted 0–100 blend of age, quantity and value changes
  meaning whenever a weight moves and nobody can check it. Rupees per month is
  arithmetic anyone can redo.
- **`lines_from_state` keeps rows where `tracked` is absent** but drops those
  where it is `False`, because absent means "the observation predates the flag"
  and the safe reading of *we don't know* is the one that keeps the row visible.

It refuses weeks-of-cover, reorder points, recovery probability, expected
recovery value, per-warehouse split, and supplier attribution of a stock level.
`simulate.py` additionally refuses per-item supplier delay, because purchase
orders are read at header grain.

### The refusal it under-states

`stock.py` says Zoho's `reorder_level` is *"blank on most of this book's items."*
Measured:

```
reorder level set:   0 of 3,200 SLS items   (0 of 2,720 holding stock)
                     0 of   473 4U active items
                     0 of 3,673 across both entities
```

Not most. **All.** `BELOW_REORDER` is a group that can never contain a row and
`below_reorder` is a predicate that is structurally always `False`. The refusal
to *invent* a reorder point must stand. But this is no longer a data-quality
footnote — it is the finding that this business has no stocking policy recorded
anywhere, for anything, and it belongs on the screen as a headline.

**Now it is one, and the worse half of this was on the screen rather than in the
data.** The count already existed — `counts["no_reorder_point"]` and a
`COLLECTABLE` line in `unavailable`, both at the foot of the screen. Above them
sat the empty group, under which the client rendered its standing line for a
group with nothing in it:

> Nothing here. That is the good answer.

An impossible zero read back as a clean bill of health, which is `CLAUDE.md` §1's
*absence of evidence is not a pass* with a reassuring sentence attached. Two
changes, and the split between them is the point: the group now carries
`empty_means` — **the server decides, because only the server holds the
population behind the group** — and a `NO_REORDER_POINT` headline card sits with
the other counts, drilling through to exactly the lines it counted. The card is
suppressed at zero, so a book that has done the work carries no reminder that it
did, and `test_an_empty_group_on_a_book_that_has_set_them_is_still_good_news`
pins that this does not cry wolf.

---

## 2. Does the book's scale justify Croston? No, and not by a small margin

Croston separates demand into **size** and **interval** — two things you can
measure and state separately rather than one composite to disbelieve. That
framing is right. The problem is the interval: estimating it needs several
inter-arrival observations. One sale gives you *zero* intervals. Two give you one.

**4U Precision — complete invoice-line history, 2025-04-04 → 2026-07-30 (16
months), 171 distinct SKUs sold:**

| sale days in 16 months | SKUs | share |
|---|---|---|
| 1 | 107 | **62.6%** |
| 2 | 31 | 18.1% |
| 3 | 18 | 10.5% |
| 4 | 7 | 4.1% |
| 5 | 5 | 2.9% |
| 11 | 2 | 1.2% |
| 15 | 1 | 0.6% |

81% of SKUs that sold at all sold on two days or fewer. **Three SKUs out of 171
(1.8%)** have enough occasions to fit anything, and the best of them sells
slightly *less* than monthly.

**SLS — top 20 lines by stock value, invoices in the trailing 365 days:**

```
22, 16, 10, 6, 2, 2, 2, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
median = 0        lines with >=6 occasions/yr = 4 of 20
```

Even among the lines with the most money tied up, the median is **zero orders in
a year**.

### Why growth does not fix this

This is not a volume problem. Distribution of specialist tooling *is*
one-order-per-SKU trade; a book ten times this size has ten times as many
one-order SKUs. And the platform's own evidence floors already settle it —
`min_transactions: 3` and `min_transactions_strong: 6` in
`commercial/config.py` would reject the demand series for **156 of 4U's 171
SKUs**. Building Croston would mean building something the config then refuses
to display.

**Recommendation: do not implement Croston or SBA.** The honest half of the idea
survives without it — the fold already stores `units_sold`, `sale_lines`,
`first_sold_on` and `last_sold_on`. *"4 orders, 16 months, last one 90 days ago,
20 pieces a time"* is two measured quantities and a date, is not a forecast, and
is more useful to the owner than a smoothed rate. State that; fit nothing.

---

## 3. The defect this analysis found

`StockLine.health` read:

```python
days = self.idle_days(as_of)
if days is None or days >= c.dead_days:
    return DEAD
```

A line with **no sale date** took the worst verdict on the screen no matter how
long it had been there, and `recommended_action` then returned `WRITE_OFF`. The
identical rule sat independently in the decision queue: `_Idle._drafts` read
`days = idle if idle is not None else at_least`, making a missing sale date
satisfy every idleness bound automatically.

This is **CLAUDE.md §1 with the sign flipped**. Absence of evidence became
evidence of a *problem* rather than a pass. The tell is the one §1 already
names: a `None` folded into the same branch as a real measurement.

### What it cost, measured

Of the SLS top 50 lines by stock value (95.3% of the shelf), 11 had zero
invoices in 365 days:

| | value at cost |
|---|---|
| what the screen would band DEAD | **₹13,13,822** |
| …stock whose item record is under 10 weeks old | ₹10,64,345 (**81%**) |
| …genuinely dead | **₹2,49,478** |

A **5.3× overstatement**, every rupee of it in the direction of telling the
owner to discard stock they had just paid for. Nine of the eleven were created
in the master on 2026-07-04.

### Why a fourth band and not a tuned threshold

No value of `dead_stock_days` is right for both books:

- **At SLS** the never-sold cohort is dominated by ten-week-old stock. The entire
  item master is younger than 168 days, so `idle_days is None` mostly means
  *too new to judge*.
- **At 4U**, 304 of 473 active items have never been invoiced against a
  **17-month** master. There the same `None` much more plausibly means
  *genuinely dead*.

Identical input to `health()`, opposite correct answers, and nothing in
`idle_days` can tell them apart. The ambiguity is not *how long since a sale* but
*how long there has been an opportunity to sell* — and the fold can measure that.

**Implemented:** the INVENTORY reducer now MINs `first_observed_on` and
`first_purchased_on`, mirroring the existing `first_sold_on` MIN for the same
documented reason (a rate needs a period). `health()` returns a fourth band,
`UNKNOWN`, when a line has never sold and either has no established opportunity
window or has had less than `dead_days` of one. Its action is `HOLD`. The queue
skips such a line rather than substituting `at_least`, and reads the same two
fold fields so the screen and the queue cannot disagree.

`UNKNOWN` is surfaced rather than swallowed — its own group, count, KPI card,
filter and `unavailable` entry — because a dead-stock total that quietly shrinks
gets reported as a bug.

---

## 4. #6 — dead-stock liquidation, worked out

### The decision is a frontier, not a verdict

Carrying at 12%/yr is 1%/month. Liquidating at discount *d* costs `V·d` once;
holding costs `V·r/12` every month. They break even at **T = 12d/r**, which at
12% is **T = 100·d months**. The value cancels, so one number serves the shelf,
and there is no forecast anywhere in it:

| take discount | worth it iff the line would otherwise have sat longer than |
|---|---|
| 10% | 10 months |
| 25% | **25 months** |
| 40% | 40 months |
| 50% | 50 months |
| 75% | 75 months |

The platform supplies the arithmetic; the owner supplies the one judgement it
cannot make — *would this have sat that long?* That is the same contract
`break_even_volume_change` already provides for a price change, which is why
**implemented as `simulate.break_even_idle_months`**, alongside it and in the
same shape.

Two limits, both stated in the response rather than buried:

1. **It is first-order.** Discounting properly moves 25% → **28.2** months and
   40% → **48.9** months (at *i* = 10%, *s* = 2%). The linear rule is therefore
   *shorter* than the truth — it errs toward clearing too eagerly. Safe only
   while disclosed, so it is disclosed.
2. **The rate cannot be split.** A true NPV needs cost-of-capital (which comes
   *back* when stock sells) separated from storage and obsolescence (which merely
   *stops*). `carrying_cost_annual_pct` is one number and cannot express it.
   **This is the one config change this category justifies** — deliberately not
   made here, because siblings are editing that file concurrently and it deserves
   its own change.

### The real dead stock, and why discounting is not the first move

₹2,49,478 at cost — **₹2,495/month, ₹29,937/year**. Eleven lines. But:

```
placeholder selling price (₹0 or ₹1)     7 lines   ₹1,90,571   76% of dead value
selling price set exactly equal to cost  2 lines     ₹30,247   0% margin
genuinely priced above cost              2 lines     ₹28,660   both at 9.4%
```

**Not one of the eleven has a usable clearance price.** Seven cannot be quoted at
all. Two are priced at exactly their purchase cost. The remaining two sit at
9.4% margin — below both `margin_floor` (0.15) and the hard `min_margin` (0.12),
so they would trip the approval gate before any discount applied.

That inverts the priority order. Discounting is not the first move on this
stock; it is not currently *possible*. The sequence is: fix the eleven prices →
then the frontier becomes a decision someone can take. Both of the largest lines
are Kennametal, so `RETURN` to the principal is the cheaper first call anyway.

---

## 5. Which refusals survive this conversation intact

**Survive, unchanged:**

- **Weeks of cover.** The reasoning is exactly right and the data above makes it
  stronger, not weaker. Do not revisit.
- **Reorder point.** Refusing to compute one stands; only the *reporting* of how
  many are unset should be promoted.
- **`recovery_probability` and `expected_recovery_value`.** Both need the forecast
  this book cannot support. §4's frontier is the honest replacement and needs
  neither — it is a boundary, not a probability.
- **`priority()` as the drain rather than a composite score.**
- **Branch, supplier-and-brand attribution, `SUPPLIER_DELAY_BY_ITEM`.** All three
  describe real data limits accurately.

**Did not survive, and should not have:**

- **`health()` mapping never-sold → DEAD**, and `recommended_action` mapping it to
  `WRITE_OFF`. Replaced with `UNKNOWN`/`HOLD` (§3). This is not the weakening of
  a refusal — it *adds* one, where the module was previously asserting.
- **`IDLE_AFTER_DAYS = 180` as a module constant** while `health()` read
  `c.slow_days`. One idea with two owners, agreeing only because the defaults
  matched: setting `CI_SLOW_STOCK_DAYS=90` produced a screen whose IDLE group,
  SLOW band and heading disagreed — and the heading still said 180. That is the
  responsibility duplication CLAUDE.md §2 names. `idle()` now takes `Carrying`.

---

## 6. What sounds advanced but is wrong here

**Croston / SBA.** §2. Not estimable for 98% of SKUs that sold at all, undefined
for the majority of the master that never sold.

**The newsvendor critical fractile `Cu / (Cu + Co)`.** Wrong twice over. *Co* is
available, but ***Cu* is not measurable anywhere in this book** — there is no
lost-sale record, no lost-quote outcome, no stockout event. A fractile computed
from a guessed *Cu* is a policy decision laundered through arithmetic, which is
precisely what `stock.py` refuses when it declines to invent a reorder level.
Worse, the model is wrong in kind: newsvendor is a *single-period perishable*
decision, and a carbide insert is a re-orderable industrial consumable from a
principal. A stockout is a lead-time delay, not a lost season.

**Safety stock from lead-time mean and variance.** Blocked twice, and
`simulate.py` documents both: POs are read at header grain so lead time is
per-supplier not per-item, and `expected_delivery_date` is blank on effectively
every order, so even supplier lead time is age-since-order rather than variance
against a promise. Attributing Kennametal's average to every Kennametal SKU
would give hundreds of items a measurement-shaped number with no measurement in
it.

**ABC/XYZ.** The XYZ axis is demand variability — the same series Croston needs.
ABC alone is the value ranking, which already gives the answer: **the top 10
lines are 77.1% of stock value, the top 50 are 95.3%.** Any stocking policy
worth having here is a policy about *ten lines*, reviewed by a person.

**EOQ.** Needs an ordering cost per PO. Not recorded.

---

## 7. What would actually move this business, in order

1. **Set reorder levels on the top ten lines.** Zero of 3,673 are set. Ten
   judgements by a person beat any method and unblock a permanently empty group.
   Still the work, and still a person's: the platform's half is done — it now
   says on the screen that nobody has chosen a policy, which is the opposite of
   choosing one for them.
2. **Fix the eleven dead-stock prices.** 76% of genuinely dead value cannot be
   quoted at all, so it cannot be cleared at any discount.
3. **Fix the master before modelling on it.** 621 SLS stocked items and 361 of
   473 active 4U items carry a placeholder selling price ≤₹1. 246 of 473 4U items
   have no purchase cost, so more than half that catalogue is invisible to every
   money figure on the stock screen. And **7 SLS items are held at a cost above
   their selling price** (₹7,37,814 at cost) — BT50-SMA27-350AV is held at
   ₹50,872 and priced at ₹37,812. That is found by comparing two columns and is
   worth more than every forecasting method in this category combined.
