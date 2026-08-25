# Procurement and category management

What the buy side of this book is worth looking at, what it is not, and the one
question that has to go to an accountant before anything else in this document
can be sized.

Two of these carry the programme's #1 and #2 ranked items. They are treated in
that order, and the first one deliberately ends in a question rather than a
change.

---

## 1. Rebate accrual treatment — a finding, and an open question

Under Ind AS 115/2, rebates expected to be earned reduce inventory cost as they
accrue rather than landing as other income at year end. If this book does the
second, every per-line margin the platform computes is understated all year, and
understated *unevenly across principals* — which would make margin comparisons
between lines wrong in a way no engineering fixes.

The investigation splits into a part that is settled and a part that cannot be
settled from inside this repository.

### Settled: no rebate reaches any margin, under any treatment

Three facts, each checkable:

**The platform did not ingest vendor credits at all.** `rg -ic "vendor.?credit"`
across `backend/` returned nothing. `ingestion/zoho_client.py` exposed contacts,
items, invoices, bills, vendors, customer payments, purchase orders, sales
orders, vendor payments and users. There was no `/vendorcredits` endpoint.

That is fixed — `list_vendor_credits`, `VendorCreditDoc` and
`VendorCreditApplication` ship, at header and bill grain, store-only. The
paragraph stays in the past tense on purpose: the measurement is what made the
case, and deleting it would leave the recommendation with nothing behind it.
**The conclusion below is unchanged by the fix**, because ingesting is not
adjusting — see §5.3.

**Cost has one source and nothing can adjust it afterwards.** `normalize_bill`
emits `CostRecordIn.unit_cost` as the bill line's effective post-*line*-discount
amount, and `_effective_unit_amount` states the boundary in as many words:
*"Taxes and document-level (non-line) adjustments are deliberately not touched."*
`economics.line_economics` then resolves cost as `cost_basis_asof(costs,
sale.date)` — the latest bill line on or before the sale.

**The scheme model is forward-looking only.** `VendorSchemeSlab` is typed by
hand, hangs off `VendorTarget`, is scoped per `organization_id`, and is read by
exactly one module: `insight/schemes.py`, whose whole output is `outlook()`.
Nothing writes back to cost, to inventory, or to any computed row.

So the conclusion holds regardless of the accounting answer: **whether a rebate
is booked as income, as a purchase reduction, or as an inventory adjustment, it
currently reaches no margin, no floor price and no principal comparison in this
platform.**

### Settled: every vendor credit in these books is a return, not a rebate

Read directly from Zoho Books across both connected orgs.

| Entity | Vendor credits | What they are |
|---|---|---|
| SLS Engineers | 13 | Returns and bill-specific price corrections |
| 4U Precision | 8 | 7 YG credits each naming a specific bill; 1 opening balance |

The two largest on SLS are Kennametal: `01/FY25` for ₹4,83,328 (notes: *"Stocks
Returned."*) and `8` for ₹3,36,335.40 (*"Credit note issued by party
BN2502000349"*). Both carry item-linked inventory lines posting to **Purchases
GST**, with storage-out movements. A third, `BN2503000361` for ₹93,456, is a
single unlinked line described **"Rate Difference"** — also to Purchases GST,
still unapplied.

**Not one is a volume-rebate settlement.**

SLS's chart of accounts *does* carry the natural home for a rebate-as-income,
migrated from Tally: **"Discount on Purchses"** (code 4296, other income),
**"Discount_tally"** (2151), **"Commission Received"** (79501), **"Subsidy
Received"**. All read zero.

### Not settled — and not to be asserted

Those zero balances prove nothing, and reading them as good news would be the
`absence of evidence is not a pass` mistake in its purest form.

- The current fiscal year began **1 April 2026**. At the time of writing it is
  four months old. **A year-end rebate would not yet be booked under *either*
  treatment.**
- SLS's Zoho org was created **12 January 2026**, so it contains no completed
  year-end cycle at all. FY26 was closed in Tally, not here.
- Manual journals reachable through the API cover only the current FY. The only
  "discount" entries in them are ₹434.40, ₹0.27 and ₹6,237 — settlement
  rounding.

The accounts that would receive a rebate-as-income exist and are named as such;
no rebate has ever flowed through a vendor credit; and the API cannot see the
year-end at which the question is decided. **The treatment must be established by
asking.**

### The questions to put to the accountant

1. For FY 2025-26, how was each principal's turnover rebate recorded — a credit
   to *Discount on Purchses* / *Discount_tally* (other income), a reduction of
   purchases, or a reduction of closing inventory value? Point to the ledger and
   the voucher.
2. Was any rebate **accrued during** the year — a period-end entry recognising
   rebate receivable before the principal's statement arrived — or was the entry
   made only on receipt? If accrued, on what basis was the rate estimated?
3. When a principal settles, does it arrive as a GST credit note against a
   payable, a bank receipt, or an adjustment on the next invoice? This decides
   whether it can ever appear in Zoho as a vendor credit at all, and therefore
   whether the platform could see it without manual entry.
4. Is the rebate computed on purchases **net of returns**? These books carry
   material returns — ₹4.83 lakh on one Kennametal credit alone — and whether
   they reduce the slab base changes where the slab actually sits.
5. Is any closing inventory carried at a cost already reduced by a rebate, or is
   inventory always at gross bill cost? This is the Ind AS 2 question and it
   decides whether a year-end inventory adjustment exists at all.
6. **Are the three entities' purchases from a given principal aggregated against
   one target and one slab, or does each entity hold an independent agreement?**

A separate observation, raised as a question rather than a finding: on 4U, the
YG credit examined posts its lines to **Input Tax Credits** with no `item_id`
and `product_type: "service"` — ₹3.16 lakh of sub-total on that one document. If
that pattern holds across all seven, those returns reduced neither inventory nor
purchases. One of seven was opened, and this is an accountant's call.

**Nothing in this repository changes on the strength of any of the above.** If
rebates turn out to be booked as year-end income, that invalidates margin figures
across the platform and is the owner's decision to act on, not a code change to
be made on an engineer's judgement.

---

## 2. Marginal slab economics — what the next purchase rupee costs

`insight/schemes.py` already models the slab correctly: the rate is paid on what
was bought, the threshold is only the gate, a flat percentage is the one-slab
case, and the marginal-rate scheme is deliberately not modelled because no
principal here runs one. What was missing is not a new scheme shape. It is the
**effective cost of the increment**.

### The arithmetic

Let `S` be purchases so far, `r_c` the rate of the highest slab cleared (zero if
none), and `T_n` / `r_n` the next slab's threshold and rate. Total rebate is a
step function `R(x) = r(x)·x`. The effective cost of buying `Δ` more is what you
pay less what it earns:

```
m = 1 − [ R(S+Δ) − R(S) ] / Δ
```

Inside a slab this collapses to `m = 1 − r_c`, which is dull and true. The
interesting regime is crossing. Buying exactly the gap to the next rung:

```
m = 1 − ( r_n·T_n − r_c·S ) / ( T_n − S )
```

Because the rate is paid on the whole amount, the crossing bonus arrives as a
**lump**, so its value per rupee is set entirely by how far there still is to go.
A ₹50 lakh rung paying 2.5% — a lump of ₹1,25,000:

| Gap still to buy | Rebate it unlocks | Earned per ₹1 | Effective cost | Reads as |
|---:|---:|---:|---:|---|
| ₹10,00,000 | ₹1,25,000 | 0.125 | 0.875 | 12.5% off |
| ₹5,00,000 | ₹1,25,000 | 0.250 | 0.750 | 25% off |
| ₹2,50,000 | ₹1,25,000 | 0.500 | 0.500 | 50% off |
| **₹1,25,000** | ₹1,25,000 | 1.000 | 0.000 | **free — the boundary** |
| ₹1,00,000 | ₹1,25,000 | 1.250 | −0.250 | paid ₹25,000 to take it |
| ₹50,000 | ₹1,25,000 | 2.500 | −1.500 | paid ₹75,000 to take it |

The free zone has a closed form, and it is the sentence worth remembering:

```
the increment costs nothing once   T_n − S  <  r_n·T_n − r_c·S
i.e. once there is less left to buy than the rung pays
```

### The baseline matters, and the multi-slab case shows why

The marginal discount is measured against **what would have been earned anyway**,
not against zero. With 2% at ₹40 lakh and 3% at ₹60 lakh:

- at ₹42 lakh, ₹18 lakh from the 3% rung → `m = 0.947`, **5.3% off**. Better than
  the 2% earned by standing still, but ₹18 lakh is a long way and this is not a
  call to action.
- at ₹58 lakh, ₹2 lakh from the same rung → `m = 0.68`, **32% off**.

Same scheme, same rung, two completely different decisions.

### Measured above the projection, never above what has been bought

If the run rate already carries the book past the rung, it clears without doing
anything, and pricing that as an opportunity would sell somebody stock the
quarter was going to buy regardless. The discretionary increment is therefore
`T_n − projected_close`.

This makes the marginal number inherit the projection's evidence floors exactly.
Below them there is no projection, so **there is no marginal number and the named
reason travels instead**. A zero there would render as "free", which is the most
expensive available way to be wrong on this screen.

Three refusals, kept separate from `REFUSALS` because they are not evidence
failures and the fixes differ:

| Reason | Means | Fix |
|---|---|---|
| `NO_SCHEME` | Nobody has typed in what this principal pays | Enter the scheme |
| `NO_PROJECTION` | Too early, or too few bills, to say where the period lands | Wait |
| `LANDS_ANYWAY` | The run rate already clears every rung | Nothing — this is good news |

`LANDS_ANYWAY` in particular must not be folded in with "too early to project":
a book comfortably clearing its top rung would then read as one the platform
cannot see.

### Composing across principals under a cash constraint

The schemes do not interact — each principal's rebate depends only on its own
purchases. What competes is **cash**. Each principal with a live gap offers: spend
`Ĝ`, earn `B`. Buying *half* a gap earns nothing, so this is **not divisible** —
it is a 0/1 knapsack, maximise `Σ B` subject to `Σ Ĝ ≤ C`.

The natural ranking, `B/Ĝ`, is exactly `1 − m`, so "rank by marginal discount" is
the greedy heuristic and falls out for free. Greedy is not optimal on a 0/1
knapsack, but with four principals there are sixteen subsets — **enumerate them.**
The exact answer fits in a loop a person can read, which matters more here than
elegance.

### Across the three entities

`VendorTarget` and `VendorSchemeSlab` are both scoped by `organization_id`, so
the platform today assumes independence. Whether that is right depends on an
agreement the code cannot see — question 6 above.

1. **Separate agreements per entity.** Slabs are genuinely independent; the
   platform is already correct. The only cross-entity question is cash.
2. **The principal counts the group against one target.** Then the platform is
   wrong today, and wrong in a specific direction: three targets each measured on
   one entity's bills, none seeing the group figure, so **every entity looks
   further from its slab than the group actually is** — the error that causes a
   slab to be missed by inaction.
3. **Per-entity agreements, fungible purchase.** If SLS is ₹2 lakh from a rung
   and 4U is ₹8 lakh from the same principal's rung, the same stock earns ₹1.25
   lakh bought through one and nothing through the other.

Case 3 needs the discipline `state/opportunities/supplier.py` applies to
sole-source. The platform knows which entity *has* bought from a principal. It
does not know which entity is *permitted* to, whether stock can be moved
afterwards, or what that costs — **an inter-entity transfer between distinct
GSTINs is a taxable supply.** So a card juxtaposes and never instructs: *"SLS is
₹2 lakh from a rung paying ₹1.25 lakh; 4U is ₹8 lakh from the same rung"* — never
*"buy through SLS"*. The decision is the owner's and it carries consequences the
platform cannot see.

### What must not happen

**None of this may be pushed into per-line margin.** A rebate is period-level,
principal-level and contingent on aggregate volume; putting it on a line requires
an allocation, and every allocation is a choice. Once it is in the line it moves
a negotiation floor on the strength of an accrual that may not be earned. It
belongs in a separate, plainly-labelled principal-level view. If rebates ever
*are* accrued, they enter through `CostRecord` at the point the accrual is
booked, never as an allocation inside `economics.py`.

---

## 3. The buy-side concepts that earn their place

~~**Ingest vendor credits.**~~ **SHIPPED, store-only.** A ₹4.83 lakh stock
return across eight bills reduced nothing the platform computed, because nothing
read it. Two distinct effects were named and **neither is taken**: returns should
reduce the *slab base*, and bill-specific price credits should reduce the *cost*
of affected lines. What ships is the evidence, at header and bill grain.

The attribution difficulty decided the schema. The credits examined carry
`bill_item_id: ""`, naming the item but not the bill line, and the "Rate
Difference" credit names no item at all — so line attribution would be an
inference, and the pull **drops line items entirely**, the same way
`list_credit_notes` does on the sell side and for a reason that mirrors it
exactly: a credit line is negative cost against a product, and cost already has
one owner in `CostRecord`. The bill linkage *is* exact and is stored, because
that is the grain a later cost adjustment will need.

One field was refused rather than stored. `bills_credited` carries a single
unlabelled `date` per row, and on the Kennametal document its eight values are
spread over five months while that document's own system comments record every
application made on two days in May 2026 — so it is the bill's date, not the
application's. A column named `applied_on` holding a bill's date is worse than no
column, and nothing this table is for needs one: a return is dated by the
credit's header, a price correction is placed by the bill it names. The table is
derived by contract, so a re-sync adds the column if somebody later settles the
question with Zoho.

`test_a_vendor_credit_does_not_change_what_a_line_cost` is the assertion that
keeps this honest, and its docstring says what it costs to break: the change that
makes it fail owes the platform the accountant conversation in §1 first.

**A rebate-aware principal P&L, kept out of line margin.** Once the treatment is
known: revenue riding on each principal's product (`dependency.py` already
computes `downstream_revenue`), gross profit at bill cost, and rebate earned and
at stake as a separate line.

**Supplier scorecards — only the dimensions with evidence.** `insight/supply.py`
already refuses lead time and OTIF, and the refusal is correct: promised dates
are blank on effectively every order here, so an average lead time would be a
statement about admin rather than about suppliers. **Do not undo it.** What is
answerable: *price stability* (variance of unit cost per item per vendor,
straight out of `cost_records`), *credit-note rate* once credits are ingested,
and *settlement behaviour*, which `insight/payments.py` already measures from one
implementation covering both sides of the ledger.

**The credit-note rate is built**, now that §5.3 has put the credits in the
book — and the interesting part of it is the refusal, not the ratio. Almost
every supplier here has issued no credit, so a screen dividing zero by four and
printing 0% hands a clean record to a supplier nobody measured. That is the
mistake `08-intermittent-demand.md` §1 found in the reorder group, in a column
where it flatters the wrong party. So the floor is **derived from the book's own
credit rate** rather than picked: `(1 − p)ⁿ ≤ 0.05` solved for *n* — how many
bills a supplier would have had to send before a clean run became surprising.
At a 4% book rate that is 74 bills; at 10% it is 29. Below it the rate is null
with the bill and credit counts beside it, and a book that has read *no* credits
at all gets no floor at all, because a missing vendor-credit grant and a
faultless supply base look identical from here.

Counts, never values: a value ratio would be a fraction of purchase spend — cost
by another name, in the sense that already scopes this screen — and a scorecard
asks how often a supplier gets an order wrong, not what the corrections came to.
Credited bills are counted **distinctly**, because one credit spreads over
several bills and one bill can draw several credits; counting applications would
report a supplier as having more corrections than invoices.

**Price stability is built too**, and it was never blocked by the ingestion — it
comes straight out of `cost_records`, one `GROUP BY` over bill lines with
`HAVING count >= 2`. Same discipline: the spread `(max − min) / min` per
(supplier, item), the median across a supplier's repeat-bought lines, and a
refusal below three such lines because a "typical" over two items is two items.
Ratios only, no rupee levels — `/supply` is manager-and-above so cost would be
permitted, but a spread is scale-free and the levels are not, and keeping it
scale-free is what would let this be shown more widely later without reopening
the question.

**On this book the refusal is the common case**, and that is worth stating rather
than discovering. `08-intermittent-demand.md` measured that most of the catalogue
moves once; a line bought from the same supplier twice is the exception, so the
spread appears for a minority of lines. Those are the lines an annual negotiation
is about anyway, which is why this is still worth having.

**What a spread cannot tell you.** A supplier that raised its price once at the
annual revision and one whose price bounces on every order can produce the *same*
spread, and only the second is unstable. Separating them needs the ordered series
— how far the cost ended up from where it started, against how far it ranged in
between — and that is deliberately not fetched: the aggregate is one `GROUP BY`,
and pulling the series for every pair to answer a second question is a cost this
screen has not been asked to pay. Read a wide spread as *worth opening the line*,
not as *this supplier is erratic*. If the negotiation pack ever wants the
distinction, that is where it belongs.

With this, all three dimensions §3 names are built or already existed, and the
two that were refused — lead time and OTIF — stay refused.

**The annual negotiation pack.** Mostly assembly rather than computation:
purchases by principal by period, downstream revenue riding on the line, the
share of that line which is sole-sourced, price movement per SKU, rebate history.
One export, once a year, at the moment the target and the slab are being set. The
argument is already in `dependency.py`'s docstring — *"12% of our purchasing and
34% of our revenue"* — and the second half is what goes on the table.

---

## 4. What sounds advanced but is wrong here

**Should-cost modelling.** Valuable where you buy machined parts and can build up
material, process and overhead. This book buys **branded carbide from the brand
owner**. There is no should-cost: the cost is the principal's list less a
distributor discount negotiated annually, not derived. A should-cost model here
produces a number with no counterparty on the other side of it. The real version
of the question is the slab and the annual discount.

**Dual-sourcing priced against a stockout cost.** Two problems, either fatal. The
platform cannot price a stockout — it sees orders won, not enquiries lost, so the
denominator is invisible. And for an *authorised distributor*, dual-sourcing a
principal's product is usually not commercially available: you cannot buy
Kennametal inserts from anyone but Kennametal, and cross-sourcing through another
distributor is generally an agreement breach. `supplier.py`'s
`QUALIFY_SECOND_SOURCE` action is already the honest version — prompt a human to
find out, do not price it.

**A composite supplier risk score.** `supplier.py` already refuses this and gives
the right reason: a combined number is something nobody can argue with, which in
a queue means something nobody reads.

**Cross-supplier price comparison.** Refused today because the item master has
known duplicates. A price gap that is really two names for one part is worse than
no comparison.

**An optimiser for the cash allocation.** A handful of live gaps at any moment.
Exhaustive enumeration is exact, instant and readable; an LP buys nothing and
costs auditability.

---

## 5. Sequence

1. **Ask the six questions.** One conversation. Nothing else here can be sized
   until 1, 2 and 6 are answered, and 6 gates the entire multi-entity strand.
2. **The marginal number.** Needs no accounting answer and no new data. *Landed
   in this change.*
3. ~~**Ingest vendor credits**, store only.~~ **DONE.** Independent of
   everything above, and it puts the evidence on the table the six questions
   have to be argued over. It corrects no number, because correcting one is
   step 4's job and step 1's answer decides which correction is right.
4. **Then** the principal P&L and the negotiation pack.

---

## What was actually checked

- **Read in full:** `insight/schemes.py`, `commercial/economics.py`,
  `state/opportunities/supplier.py`; docstrings and relevant bodies of
  `insight/dependency.py`, `insight/supply.py`, `commercial/principals.py`,
  `ingestion/normalize.py`, `ingestion/zoho_client.py`, `domain/models.py`
  (`VendorTarget`, `VendorSchemeSlab`), `routers/insight.py` (`/schemes`).
- **Zoho Books API, live:** organizations, income chart of accounts and the full
  vendor-credit list for both connected orgs; four vendor credits opened in
  detail (Kennametal `01/FY25`, `8`, `BN2503000361`; YG `1`); the 100 most recent
  manual journals on SLS.
- **Not checked:** 17 of 21 vendor credits were characterised from list metadata
  and bill linkage rather than opened individually. **UPS was not connected to
  this session — findings cover SLS and 4U only.** No pre-FY27 journals were
  reachable through the API.
