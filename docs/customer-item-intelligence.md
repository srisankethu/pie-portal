# Customer × Item commercial intelligence

Turns invoice-line history into auditable commercial decisions at the grain that
actually explains margin: **one customer, one item, over time**.

Customer-level margin says *that* an account is deteriorating. It cannot say
*which item* is responsible, *why*, or *how much it is worth*. This layer does.

---

## The six questions

For any Customer × Item relationship:

1. Is the margin deteriorating?
2. Is cost increasing faster than selling price?
3. How does this item's pricing/margin compare with other customers buying it?
4. How financially material is the margin gap?
5. Did lower margin produce enough additional volume to potentially justify it?

And at customer level:

6. Which items are driving this customer's margin performance?

Everything below exists to answer one of those. Nothing else was built.

---

## Architecture it was built on

This extends the existing platform rather than paralleling it. What was reused:

| Existing component | How it is used |
|---|---|
| `Customer` / `Product` | The canonical customer and item. No new entity. |
| `SalesTxn` | Already invoice-**line** grain — the observation. No new transaction table. |
| `CostRecord` | Already bill-**line** grain with effective post-discount `unit_cost`. The authoritative cost source. |
| `aggregates.cost_basis_asof()` | The established effective-cost-at-a-date methodology. Reused verbatim. |
| `SignalDraft` / `Sufficiency` / `SignalRepository` | Detectors emit ordinary signals; decisions flow through the existing `DecisionService`. |
| `EvidenceSufficiency` | The existing three-level data-quality enum, reused rather than inventing a parallel four-level one. |
| `SignalThresholds` version stamping | The same pattern: one dataclass, env overrides, a content hash stamped onto output. |
| `RESTRICTED_FACT_FIELDS` gating | This surface is cost/margin throughout, so it is manager/owner only end to end. |

The one genuinely new persistent object is `CustomerItemMetric` — a derived,
recomputable projection. It holds no source facts of its own; every number on it
is traceable to the `SalesTxn` and `CostRecord` rows it was computed from, and
it can be dropped and rebuilt from them at any time.

---

## Unit economics — the foundation

Every analysis below is only as good as one line's economics. The normalized
values, per invoice line:

| Value | Source |
|---|---|
| Quantity | `SalesTxn.qty` |
| Rate (list, pre-discount) | `SalesTxn.rate` |
| Discount % | `SalesTxn.discount_percent` |
| **Net selling price per unit** | `SalesTxn.unit_price` — *after* line discount |
| Revenue | `SalesTxn.line_revenue` (pre-tax) |
| Effective cost per unit | `cost_basis_asof(costs, line.date).unit_cost` |
| COGS | effective cost × quantity |
| Gross profit | revenue − COGS |
| Gross margin % | gross profit ÷ revenue |

**A bug was found and fixed here.** `SalesTxn.unit_price` stored the raw Zoho
line `rate` — the *pre-discount* list price — while `line_revenue` stored the
*post-discount* `item_total`. A 10%-discounted line therefore reported a selling
price 10% higher than the customer actually paid, and every margin computed from
that field was overstated. This is the exact mirror of the bill-side discount
bug fixed earlier; sales lines now go through the same resolution, and `rate` +
`discount_percent` are preserved alongside for audit.

Revenue is **pre-tax** throughout. Tax never becomes revenue or margin.

**Missing cost is never invented.** A line whose product has no applicable
`CostRecord` at its date contributes revenue and quantity but no COGS, no gross
profit and no margin — and the relationship is marked with a cost-coverage
figure so the UI can say *"margin unavailable for 3 of 11 transactions"* instead
of quietly reporting a wrong one.

---

## Aggregation rule

Aggregated margin is **always** total gross profit ÷ total revenue — never the
arithmetic mean of per-transaction margin percentages. A ₹4,00,000 line at 20%
and a ₹1,000 line at 60% is a 20.1% relationship, not 40%.

`margin_change_pp` is a **percentage-point** difference (26.1% → 18.0% is
−8.1 pp). Percentage *change* of a percentage is never reported, because it is
almost always misread.

---

## Same-item peer benchmark

For an item, the current customer's position against every *other* customer
buying it:

- **Median**, not mean — commercial pricing contains outliers, and one
  distress-priced deal should not move the benchmark.
- The selected customer is **excluded from its own benchmark**.
- Each peer is summarised over the same recent window, carrying its quantity,
  transaction count and recency so a stale or trivial peer is visible as such.

This is a **benchmark, not a mandate.** Another customer's price is evidence
about the market, not proof that this customer's price is wrong — volume
commitments, freight, payment terms and relationship history are all outside
this data.

---

## Margin gap — two separate numbers

Deliberately kept apart, because they mean different things:

| Gap | Meaning |
|---|---|
| **Historical margin gap** | Recent revenue × (this relationship's own historical margin − recent margin). What the relationship used to earn on today's volume. |
| **Peer benchmark gap** | Recent revenue × (same-item peer median margin − recent margin). What the relationship would earn at the peer benchmark. |

Neither is "lost profit". The historical gap assumes the old margin was
sustainable; the peer gap assumes this customer should trade like the median of
its peers. Both are estimates that frame a review, and the UI says so.

**Annualization requires sufficient history** — a gap is only annualized when
the relationship has enough span and transactions to make a yearly figure
meaningful. Otherwise the window figure stands alone.

Prioritisation is by **rupees, not percentage points.** A 3 pp slip on ₹40 lakh
outranks a 10 pp collapse on ₹20,000 — the first is worth someone's afternoon.

---

## Detectors

All deterministic, all emitting ordinary `Signal` rows through the existing
engine, all carrying evidence refs back to source records.

| Detector | Fires when |
|---|---|
| `CI_MARGIN_EROSION` | Recent margin materially below this relationship's own historical baseline |
| `CI_COST_NOT_PASSED` | Effective cost rose materially; net selling price did not follow sufficiently |
| `CI_LOW_PEER_PRICING` | Margin materially below the same-item peer median, with enough peers to mean something |
| `CI_MARGIN_DECLINE_NO_VOLUME` | Margin fell while volume stayed flat or fell — the strongest evidence of pure leakage |
| `CI_MARGIN_DECLINE_WITH_VOLUME` | Margin fell but volume grew materially — possibly a deliberate, working trade-off, classified separately and never as leakage |
| `CI_MATERIAL_MARGIN_GAP` | The rupee impact clears the materiality floor |

All six carry cost/margin and are therefore **RESTRICTED** — routed to managers
and owners, never to a salesperson.

---

## Data sufficiency

Weak data must not produce confident conclusions. Reusing the existing
`EvidenceSufficiency` levels:

| Level | Meaning |
|---|---|
| `INSUFFICIENT` | Too little to judge — no detector fires |
| `PARTIAL` | Enough to show, flagged as limited; severity is damped |
| `SUFFICIENT` | Enough transactions, span and cost coverage to stand behind |

One historical transaction never produces a confident erosion alert. Two
customers never establish a strong peer benchmark. Stale prices are aged out of
the recent window rather than treated as current.

---

## Configuration

Every threshold lives in `app/commercial/config.py` — one dataclass, env
overrides, content-hashed `version` stamped onto every metric row and signal, so
any number can be reproduced against the exact thresholds that produced it.

See that file for the current defaults and their env variable names.

---

## Recomputation and backfill

Metrics are **derived and disposable**. They are recomputed:

- automatically after each Zoho sync, scoped to the customers the sync touched;
- on demand for one customer or the whole organization;
- from already-synced local data — **a Zoho re-sync is never required** just
  because the metric logic changed.

```bash
# whole organization, from data already in the database
python -m app.commercial.backfill --org org_sanketh

# one customer
python -m app.commercial.backfill --org org_sanketh --customer <customer_id>

# see what would happen, change nothing
python -m app.commercial.backfill --org org_sanketh --dry-run
```

Idempotent: running it twice produces the same rows. It never deletes or
duplicates source transactions — only the derived projection.

---

## Tracing a number back to its source

Every figure on the drill-down is traceable:

```
Portfolio  →  Customer  →  Customer × Item  →  Transactions
```

The transaction table at the bottom of the drill-down is the evidence: date,
invoice, quantity, rate, discount, net selling price, effective cost, gross
profit and margin, per line — with the Zoho invoice id each came from. Every
conclusion above it is an aggregate of exactly those rows.
