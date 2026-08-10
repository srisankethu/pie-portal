# Mechanism design and incentive robustness

`commercial/incentive.py` is not a formula, it is a **mechanism** — and
mechanisms get gamed. This is what CAF does today, where it leaks, what was
changed, and what was deliberately left alone.

Two things in this document are measurements rather than reasoning, and they
are marked as such. Everything else is read from the code.

---

## 1. What the mechanism does today

    CAF = q x (P - F) - K - 0.5 x Toolkit + Y

then earned through `c(d)` on receipt, weighted by `w(RSI)`, multiplied by `Q`,
shared at `r`, split 70/30.

**Two properties are load-bearing and correct.**

*Linearity is real and complete.* There is no threshold anywhere inside CAF —
not per order, per invoice, per line or per customer. Order-splitting is worth
exactly zero, and `discount_to_floor` is a subtraction rather than a fixed-point
solve. That is what makes the desk usable on a phone call, and it is the
property any new term has to preserve.

*Cost is absent by type, not by filtering.* `CustomerPoints`,
`SalespersonPayout`, `ResolvedFloor` and `Assessment` have no cost field to
populate; `report.assert_ops_clean` then sweeps free-form audit dicts. That is
stronger than a serialiser that strips, and it is the part of the design worth
changing least.

### 1.1 Time was priced in one direction only

`collection.factor` reads `days_late`, which `models.Payment` defines as
`receipt_date - due_date`. **The due date already embeds the agreed terms.** So
moving a customer from 30 days to 90 moves the due date out by sixty days and
leaves `c(d) = 1.00` — the invoice is still "paid on the due date". Credit was
free in the currency, and free in a way that looked like good collection
performance.

Meanwhile `vendor.value_extended_credit` computes
`amount x rate x days / 365` and credits `Y` when a *supplier* concedes credit
days to us. The identical arithmetic, pointed at the customer, did not exist.
The mechanism paid for extracting credit days and charged nothing for giving
them away — a directional bias in the currency, not a missing line item.

### 1.2 Three claims the code did not support

**"Free credit" was not closed.** Row 1 of `EXPLOIT_CLOSURE.md` lists it among
the wrappers `P` catches, on the grounds that `unit_price_net` is net of
"credit-period loading". No code computed such a loading, no test asserted one,
and the exploit-1 test proves the freight case by having the test author write
the loaded price into `P` by hand. **Now closed for real** — see §3.

**`Y` has a document check and no amount check.** `VendorYield.__post_init__`
requires `proof_type` to be one of four strings and `proof_ref` to be non-empty.
It never verifies the reference resolves, and never compares the claimed amount
to anything. `StandardBuyPrice` — described in `models.py` as "the reference `Y`
is measured against" — is defined there and **referenced nowhere else in the
repository**. `Y` is the one term where a larger declared number means more
money, and the one term with no arithmetic behind it. **Still open**; it is the
top of §5.

**The desk's collection lever is self-declared next to an unused measurement.**
`expected_days_late` is typed by the person it pays, defaulting to 0, and scales
their own CAF from 1.10 to 0.00. `insight/payments.py::lag()` already computes
that customer's P10/P50/P90 days-late from their own settled invoices, with a
three-settlement evidence floor and `None` below it — and `/payments` is open to
every role. The two have never been introduced. **Still open.**

---

## 2. What the book actually shows

*Measured against the live Zoho books, twelve months to July 2026. Two of the
three entities are reachable; the UPS connector resolves to the SLS
organisation, so **UPS is unmeasured**.*

| | SLS Engineers | 4U Precision |
|---|---:|---:|
| Revenue, 12m | ₹19.58 Cr | ₹30.79 L |
| Invoices | 2,551 | 98 |
| Receivables outstanding | ₹4.66 Cr | ₹10.71 L |
| Implied DSO | 87 days | 127 days |
| Agreed term, revenue-weighted | ≈30 days | ≈45 days |

The receivable book costs **₹57.2 L a year** at 12%, and it splits in the way
that decides the plan:

| Component | Per year | Priced by |
|---|---:|---|
| Contracted credit — agreed terms | **₹19.8 L** | nothing, until §3 |
| Settlement beyond terms | **₹37.4 L** | `c(d)` — built, inert |

Two methods agree on the contracted figure: `Σ revenue x d/365 x r` gives
₹19.77 L, and taking the agreed-term share of DSO off ₹57.2 L gives the same.

**The leak this analysis was commissioned for is the smaller half.** The larger
₹37.4 L is lateness, which `c(d)` already prices correctly and in detail, and
which is inert only because `r_by_entity` ships at `0.0000` and
`payout.rate_for_entity` refuses rather than pays.

### How this was measured, and what it does not support

Zoho's API accepts an invoice-date filter or a due-date filter but silently
drops one when both are sent, so per-invoice `d` is not extractable without
pulling 3,529 records. Count-and-sum queries over the two monthly series were
used instead: April 2026's ₹2.85 Cr revenue spike lands in due-date May
(₹2.36 Cr, 222 invoices against April's 226), pinning the SLS shift at ~30 days.

That inference rests largely on one spike. **It is enough to decide with and not
enough to publish a parameter from.** The exact distribution should come from
`InvoiceDoc` once synced. The share of invoices with no due date was not
measured, and the sums include all statuses. If UPS is 4U-scale it adds ~₹0.5 L;
if SLS-scale it could add ₹15–20 L.

---

## 3. The term charge

    CAF = q(P - F) - K - 0.5T + Y - q x P x rate x (d / 365)

**Shipped, behind `caf.term_charge.enabled`, defaulting to `false`.** Every
number the engine produces with the flag off is identical to what it produced
before the term existed, and there is a test asserting exactly that on every
path.

### Why the base is `q x P_net`

What is financed is the whole receivable, not the margin on it — so not
`(P - F)` — and not gross `P`, which would undercharge every discounted line.

### Why the days are *agreed*, not measured

Three reasons, and the first is decisive.

1. The charge prices **a decision the salesperson made**. Measured settlement is
   a property of the *customer*. Charging on it reintroduces precisely the
   defect that killed the realisation currency: two people earning differently
   for the same commercial act because of the customer's history.
2. Measured lateness is **already priced once**, by `c(d)`. Charging it again is
   double jeopardy and makes the collection bands unreadable.
3. `payments.lag()` returns `None` below three settlements. A charge depending
   on measured days would have to do *something* on a new account, and both
   options are wrong: zero rewards new accounts, a default invents a number.

Agreed days go in the charge; measured days stay in `c(d)`. One clock each. The
`terms_gap_days` figure `payments.py` already computes is the right *diagnostic*
for whether the agreed number is fiction, and belongs in a manager's queue
rather than in the formula.

### Why it is presented as a floor, not a sixth term

    CAF = q.P(1-k) - q.F  =  (1-k) . q . (P - F/(1-k)),   k = rate x d / 365

So the whole charge is a **floor that rises with the credit period** — one
number the salesperson already reads, that now moves when they change the
terms. `discount_to_floor` stays a subtraction and both inversions stay exact:

    d_disc = P - F/(1-k) - (K + 0.5T - Y) / (q(1-k))
    P      = F/(1-k) + d + (target + K + 0.5T - Y) / (q(1-k))

Both reduce to the previous expressions at `k = 0`. I5 survives, and the
round-trip is tested rather than asserted.

The multiplier is item-independent, so the entire disclosure is one table:

| Agreed credit | Floor multiplier | Uplift |
|---|---:|---:|
| Advance / against delivery | 1.0000 | — |
| 30 days | 1.0118 | 1.18% |
| 45 days | 1.0178 | 1.78% |
| 60 days | 1.0238 | 2.38% |
| 90 days | 1.0362 | 3.62% |
| 120 days | 1.0488 | 4.88% |

### Disclosure safety

`q`, `P_net` and `d` are on the salesperson's own quote; `rate_annual` is
published. The charge is computed **entirely inside the selling-price domain**
and adds no equation containing `F` or cost — which is exactly the property the
stock carrying rate lacks, since the drain is computed *from* cost.

**This is why `carrying_cost_annual_pct` was not reused**, and the suggestion to
promote it to a general cost-of-capital parameter was rejected. `config.py`
states the reason: monthly drain is `quantity x cost x rate / 12` and the
quantity is on the row, so that one org-wide constant inverts every purchase
cost in the catalogue, permanently — and `carrying_rate_is_published` exists to
pull the drain column off the salesperson's screen the moment it becomes public.
Using it at the desk *is* publishing it.

Instead, `vendor.valuation.cost_of_capital_annual` was promoted to a top-level
`cost_of_capital.annual` and `value_extended_credit` now reads it, so the buy
and sell sides quote the same cost of money. Two numerically similar parameters
in two files — one publishable, one not — is the correct end state.

`caf.term_charge.rate_annual` is `0.1416`, the cost of capital grossed up for
GST: the receivable actually funded is the tax-inclusive invoice, while
`unit_price_net` is tax-exclusive, so charging 0.12 on a tax-exclusive base
under-recovers by the GST share.

### An unrecorded term is not zero

Zero days reads as "against delivery" — the most valuable term in the book — so
defaulting to it would hand every unrecorded line the best possible treatment
and make *not recording* a term the profitable choice. Absence of evidence is
never a pass. Lines with no term are charged at
`assumed_days_when_unknown` (30, the book's own measured revenue-weighted term),
and the fact that it was a fallback travels on the result.

### What it would change

On the exploit suite's own baseline — 500 inserts, floor ₹280, price ₹340:

| Terms | Contribution | Term charge | CAF | c(d) | Collected |
|---|---:|---:|---:|---:|---:|
| Today, any terms | ₹30,000 | ₹0 | ₹30,000 | 1.00 | ₹30,000 |
| Advance | ₹30,000 | ₹0 | ₹30,000 | 1.10 | ₹33,000 |
| 60 days, on time | ₹30,000 | ₹3,957 | ₹26,043 | 1.00 | ₹26,043 |
| 90 days, on time | ₹30,000 | ₹5,936 | ₹24,064 | 1.00 | ₹24,064 |

The gap between asking for advance payment and conceding 90 days goes from 10%
— the `c(d)` bonus alone — to **37.1%**. `discount_to_floor` on that line falls
from ₹60.00 to ₹49.87, which is the behaviour change worth having: it hands
the salesperson a trade they can make in the room instead of leaving credit the
one concession that costs them nothing.

### Payout difference on real history

**₹0.** `r_by_entity` is `0.0000` for all three entities and
`payout.rate_for_entity` raises `UncalibratedRate` rather than paying, so no
payout has been computed from this mechanism yet and none changes. On the
measured book the charge would reduce annual CAF by roughly **₹19.8 L across
SLS and 4U** — about 1.0% of SLS revenue and 1.5% of 4U's — which is the number
that would flow into `r` at calibration, not into anybody's pay today.

### Before enabling

`approvals._covers` freezes `quoted_unit_price` and nothing else. While the flag
is false that is harmless. Turn it on without first putting credit days into the
approval subject and an approval granted at 30 days can be spent at 120 — the
same defeat the gate exists to prevent, through the one door it does not watch.
This is stated in the parameter block itself, where whoever flips the flag will
read it.

---

## 4. The floor family map

`parameters.yaml` publishes `m_floor` per family. **Nothing mapped a product
onto those families**, so `floor.resolve` received `family=None` from every
caller and every line in the catalogue priced at `default: 0.25`.

Two problems, not one:

- **The floors were wrong, family by family.** Machines carry a 0.12 floor but
  priced at 0.25; metrology 0.34 priced at 0.25. That is the calibration the
  parameter block exists to express, applied to nothing — and it is precisely
  the number a shadow run would solve `r` from.
- **The disclosure defence was down to one layer.** `floor.py` states that
  `m_floor` is unpublished *and varies by family*, so two observed lines in
  different families still do not invert to cost. On one multiplier that clause
  stopped being true. The repository already contained a test asserting *"a
  single markup makes every floor invertible"*; it passed, on a table nothing
  read.

`commercial/floor_families.py` resolves a product to one of the six by the
evidence order `categories.py` established — override, catalogue category where
it is specific enough to name a family, tariff heading, then the business line
for the three lines that settle it. The tariff earns its keep because it already
draws the distinction the families need and no catalogue category does: **8209**
is unmounted tips, **8207** the interchangeable tool, **8466** the holder.

Kept separate from `categories.py` because the questions differ — that one
places an item in a line for coverage and mix, this one in a pricing bucket, and
`CUTTING_TOOLS` spans three families. "Cutting Tools", which is what most
catalogues actually say, therefore resolves **nothing**.

**The family is owner zone.** It reaches `FloorReconciliation` and never
`ResolvedFloor`: telling a salesperson two items share a family tells them the
two share a multiplier, and one leaked cost would invert both.

It moves floors, deliberately: inserts −2.4%, solid carbide +0.8%, holders
+4.0%, metrology +7.2%, chemicals −5.6%, machines −10.4%.

### 4.1 What this leaves open, and why it was not closed here

The disclosure-control work (`06-disclosure-control.md`) landed while this was
in flight and made `m_floor_for_family` **strict** when the family comes off a
request: a name the table does not hold is refused rather than silently
defaulted, because otherwise a caller can sweep names to enumerate the table.

That closes enumeration. It does not close the **ratio**. A caller who supplies
one of the six *valid* names can still price one item as `inserts` and again as
`metrology`, and read `1.34 / 1.22` straight off the two floors — no invalid
name required. Repeat across the six and the whole table is known up to a single
scale factor; one leaked cost then fixes the scale.

Resolving the family **from the item** is what removes that, and after this
change it happens whenever the caller supplies nothing. But the request
parameter is still honoured when they do send one, so the ratio sweep survives
for anyone who does.

**Deliberately not fixed here.** The strictness is three days old and belongs to
another piece of work; changing whether its parameter is honoured at all is that
work's contract to change, not this one's. The item-side resolver is the
prerequisite and it now exists — removing `family` from `NegotiationRequest`, or
ignoring it on the operations path, is a one-line follow-up that should be taken
deliberately rather than as a side effect of a mechanism change.

---

## 5. The gaming paths still open, ranked

1. **`Y` is a self-declared positive term with no arithmetic check.** Every
   other term is negative — inflating `K` or toolkit is self-harm — or bounded
   by a published number. `Y` is credited 1:1 *upward* on a figure the claimant
   chooses, gated by a string from a four-item list and a non-empty reference. A
   `vendor_mail` proof with free text is a claim, not a document. *Fix:*
   reconcile `po_price_delta` claims against `StandardBuyPrice`, require the
   other proof types to resolve, cap unreconciled `Y` per period.
2. **`expected_days_late` is typed by the person it pays**, with that customer's
   measured distribution already computed and never consulted. *Fix:* default to
   the measured P50 where `lag()` returns one; free override upward, override
   downward with a reason; show P90 beside it. Where `lag()` is `None`, show the
   absence — never a fabricated default.
3. **The toolkit charge lands on the wrong person.** `caf.compute` drops
   `ToolkitSpend.salesperson_id` and charges the customer's first invoice in the
   batch. Contained while runs are per-salesperson; live the moment they are not.
4. **Approvals freeze price and only price** — see §3's enabling condition.
5. **Quota convexity, latent.** `apply_accelerator` kinks the payout at 1.30 x
   target, and `target_points` **has no producer anywhere in the repository**,
   so nothing fires today. When quotas are built the kink makes order timing
   rational specifically for people at or above the threshold and nobody else.
   The baseline module's claim that a rolling-12 window means "there is no
   period boundary, so there is nothing to sandbag into" is right about `B_c`
   and wrong about the payout period, which has both a target and gates.
   Measurable now: order-date histograms by day-of-quarter, per salesperson,
   against the invoice-date histogram.
6. **Which entity invoices is which salesperson earns.** Assist credit is
   company-funded, which is the detail that makes it work, and `resolve_group`
   correctly puts RSI on the GSTIN group. The unpriced case is ordering: three
   entities selling one catalogue to one GSTIN, and no rule about which invoices.

### One correction to the framing

CAF is **exactly indifferent** between high-price/low-volume and the reverse at
equal contribution — it is `q(P - F)`, so any pair with the same product scores
identically. That is I5, and it is deliberate. The preference did not exist; the
term charge is what creates it, because the charge scales with `q x P` while
contribution scales with `q(P - F)`. A low-margin high-revenue line is now
charged more per rupee of contribution, which is correct — it ties up more
capital for the same reward — and is an intended consequence rather than a
surprise to be discovered later.

---

## 6. What sounds advanced but is wrong here

**Promoting `carrying_cost_annual_pct` to the general cost-of-capital
parameter.** The most natural-sounding move and the one trap — see §3.

**Charging on measured settlement days.** More data-driven, and it prices the
customer's history rather than the salesperson's decision, double-charges
against `c(d)`, and has no defensible behaviour on a thin-evidence account.

**A cost-to-serve term in the same year.** The gap is real: CAF is indifferent
between one 500-piece line and fifty 10-piece lines at equal contribution. But
per-line handling cost is cost, and a per-line allowance recovered from cost is
a single constant that inverts. There is a safe version — a published flat
charge per invoice line and per delivery, entirely operational — but it is a
second mechanism to explain in the same quarter as the first, and CAF's
legibility is its main asset.

**An audit programme for `K`.** Check the arithmetic first. Declaring `K` costs
the salesperson `r.Q.w.K` in payout *on top of* the `K` they already fund;
concealing costs the `K` only. Concealment strictly dominates declaration by the
payout share, and the entire deterrent is the 3x clawback plus bank forfeiture.
Break-even detection probability is around `r.Q.w / 3` — low single digits at
any plausible `r`, not the ~25% the parameter comment claims. That makes the
existing control *stronger* than advertised. The thing to fix is the comment.

**Smoothing the accelerator kink.** The kink is not the problem; `target_points`
having no producer is. A continuous accelerator on a badly-set quota is worse
than a kinked one on a good quota.

**A DSO KPI for salespeople.** `collection.py` opens by saying `c(d)` "replaces
an entire collections KPI", and it is right. Adding a DSO objective beside a
mechanism that already prices settlement is how you get two currencies again —
the failure this module was rewritten to escape.

---

## 7. What to do next

Switching on `r` is **not a config change**, and that reordered everything. The
only thing in the platform that touches the engine is `commercial/incentive.py`,
which builds one prospective `InvoiceLine` for the desk. Nothing constructs
invoice lines from real invoices; the engine CLI has `show-config` and `verify`
and no run command. A shadow run is an unbuilt adapter layer.

The foundations are better than that suggests: `SalesTxn` is invoice-line grain
with a net unit price, `CostRecord` is bill-line grain so floors compute, and
`PaymentApplication` carries invoice date, due date, paid-on and amount — a near
one-to-one match for `models.Payment`.

**Phase A — make the engine runnable.** The family map (§4, done);
`SalesTxn.salesperson_id`, which Zoho already sends and the platform drops for
invoice lines; the two adapters; a `shadow-run` command; an owner-zone CAF total
per entity. The milestone worth stopping at is *what did CAF total last year* —
decision-grade, and reachable without any of Phase B.

**Phase B — payout-grade inputs.** RSI attributes, rolling-12 baselines, health,
gates. Two of `Q`'s six components have **no data source at all** — forecast
accuracy (10%) and trial conversion (15%). Either pin `Q` at 1.00 for the
shadow, re-weight the other four, or start capturing and accept that `Q` means
nothing for a year.

**Phase C — solve `r`.** `r` = incentive envelope ÷ total weighted points. The
envelope is a policy decision, not a computation.

**Independent of all of it:** `Y`'s amount check; a `customer_payment_terms`
table populated from invoice history rather than the stale contact default
(SLS contacts carry `payment_terms: 0 / "Due on Receipt"` from a CSV import
while their invoices run 30 days); the toolkit attribution fix; approvals
covering terms; the ~25% comment.
