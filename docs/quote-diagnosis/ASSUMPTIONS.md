# Quote Diagnosis Engine — Phase 0 audit

**Status: Phase 0 complete. No engine code written. Awaiting approval before Phase 1.**

This answers §2.1–§2.5 of the build prompt. Everything below §1 was measured against
the live Zoho Books tenants on 2026-09-16, not inferred from the schema. Where a
number is a sample rather than a census, the sample and its size are stated.

**This document contains no cost or margin value.** It reports *fill rates* and
*timestamp distributions* for cost-bearing fields, which are structural facts about
the data, not economics. That is deliberate: the audit has to be readable by whoever
is going to argue with it.

---

## 0. How this was measured, and what could not be

| Entity | Zoho org | Instrument | Coverage |
|---|---|---|---|
| **SLS Engineers** | `60063559751` | Books REST (`list_bills`, `list_invoices`, `list_estimates`, `list_items`) | bills: **census** (2,419 rows, 13 pages); invoices: counts are census, lag is a 400-row two-ended sample; quotes: **census** (360); items: 600-row sample |
| **4U Precision** | `60036630626` | Zoho Analytics warehouse `Zoho Books Analytics` (`403965000000068002`), SQL | **census** on every table queried |
| **UPS** | — | — | **Not measured.** See below |

**UPS could not be measured in this session.** The `ZohoUPS` connector resolves to
the SLS organization (`60063559751`), not to a separate UPS book. Either UPS is not a
distinct Zoho org, or this session's credentials do not reach it. Every per-entity
number below is therefore SLS and 4U only, and §6 asks which it is.

**The 4U Analytics workspace is SQL-queryable; the SLS one is not.** SLS has a
`Zoho ERP Analytics` workspace (`506247000000005002`) under a different Analytics org
(`60063559830`), and this session's token is refused on it (`SECURITY_NOT_PERMITTED`).
SLS was therefore measured through the Books REST API, which is why its samples are
shaped differently. Nothing depends on the difference.

**There is no local database in this container.** `backend/data/platform.db` does not
exist and no `DATABASE_URL` is set, so nothing here was measured from `sales_txns` or
`cost_records`. That turns out not to matter: those tables are derived from exactly
the Zoho documents measured, and — see §2.4 — the one timestamp this engine needs is
not in them at all.

---

## 1. Model inventory (§2.1)

### 1.1 What already exists and will be reused unchanged

Everything in this table is in `backend/app/domain/models.py`. **No new model duplicates
any of it.**

| Concern | Model | Table | Grain | Timestamps it carries |
|---|---|---|---|---|
| Invoice line (realized sale) | `SalesTxn` | `sales_txns` | invoice line | `date` (event), `created_at` (**PIE sync time**) |
| Bill line (purchase cost) | `CostRecord` | `cost_records` | bill line | `date` (event), `created_at` (**PIE sync time**) |
| Invoice header | `InvoiceDoc` | `invoices` | document | `date`, `due_date`, `created_at`, `updated_at` |
| Bill header | `BillDoc` | `bills` | document | `date`, `due_date`, `created_at`, `updated_at` |
| ERP-raised quote | `QuoteDoc` | `erp_quotes` | **document (header only)** | `date`, `expires_on`, `decided_on`, `client_viewed_at` |
| Platform quote (draft) | `QuoteDraft` | `quote_drafts` | quote, lines in JSON | `created_at`, `updated_at`, `archived_at` |
| Priced line snapshot | `QuoteDecision` | `quote_decisions` | quote line, **append-only** | `as_of`, `created_at` |
| Quote outcome | `QuoteOutcome` | `quote_outcomes` | quote | `sent_at`, `decided_at` |
| Sales order | `SalesOrderDoc` | `sales_orders` | document | `date`, `expected_ship_date` |
| Purchase order | `PurchaseOrderDoc` | `purchase_orders` | document | `date` |
| Item master | `Product` | `products` | item | — (`uom`, `hsn`, `category`, `manufacturer`, `source_item_type`, `source_item_category`) |
| Customer master | `Customer` | `customers` | customer | `first_seen` |
| Vendor master | `Vendor` | `vendors` | vendor | — |
| Derived C×I metrics | `CustomerItemMetric` | `customer_item_metrics` | customer × item, **upserted** | `computed_at`, `thresholds_version` |
| Customer/vendor/item sets | `EntityGroup` / `EntityGroupMember` | `entity_groups` | named set, versioned | — |
| Event log | `BusinessEvent` | `business_events` | event, append-only | `occurred_on`, `recorded_at` (**PIE sync time**) |
| Policy | `CommercialThresholds` | (content-hashed, not a table) | — | `version` |

There is **no price-list model** in pie-portal at all. See §3 for why that is correct.

### 1.2 The engine capability that already exists

This is the most important finding in §2.1, and it changes what Phase 1–4 should be.
A deterministic quote-line assessment engine **already exists and is in production**:

| File | What it already does | Maps to build-prompt section |
|---|---|---|
| `commercial/quantity.py` | `QuantityBand`, `band_for`, `lines_in_band` — configurable band edges `(1, 10, 50, 200)` | **§5 qty_band — done** |
| `commercial/economics.py` | `LineEconomics`, `cost_basis_asof`, `aggregate`, `in_window`; missing cost stays `None`; zero/negative cost treated as a placeholder and withheld | **§9 cost baseline — partly done** |
| `commercial/references.py` | `build_references` → `LAST_PRICE_PAID`, `BAND_PRICE`, `RECENT_AVG_PRICE`, `PEER_MEDIAN_PRICE`, plus four cost-derived ladder prices; each tagged `OPERATIONAL` or `RESTRICTED` | **§6.1 tiers 1–2, §6.2 partly** |
| `commercial/benchmark.py` | `ItemBenchmark` — same item, **all other customers**, median not mean, subject excluded, `is_reliable()` at `min_peer_customers` | **§6.2 peer axis — partly done** |
| `commercial/metrics.py` | `RelationshipMetrics`, `classify_erosion` → `COST_DRIVEN` / `PRICE_DRIVEN` / `MIXED` / `NONE`, `pass_through` | **§10 pricing vs cost effect — done, at relationship grain** |
| `commercial/quote_exceptions.py` | 13 deterministic rules, severity ranking by money, `boundary_refs` disclosure control | **§11 severity — partly done** |
| `commercial/quote_intelligence.py` | `assess_line` — the whole per-line assembly, pure, no DB | **the diagnosis engine's skeleton** |
| `commercial/quote_service.py` | `project(intel, role)` — the DB seam and the role gate | **§I3 — see §4.4 for why this is not enough** |
| `commercial/diagnosis.py` | Template-rendered plain-language sentences from computed metrics; explicitly not an AI surface | **§14 renderer — done, at relationship grain** |
| `commercial/backtest.py` | Replays stored `QuoteDecision` rows under a different floor, reusing the real evaluator | **§12 evaluation — the pattern to follow** |
| `commercial/outcome_tracker.py` | Append-only `OutcomeSnapshot`, `PENDING`/`REALISED`/`UNKNOWN`, evaluation computed on read and never persisted | **§12 outcome layer — the pattern to follow** |
| `ingestion/normalize.classify_outcome` | WON / LOST / **UNRECORDED** from source status, two positive allowlists, no expiry parameter, a decision must carry its date | **§8 evidence_class — done** |

`commercial/diagnose.py` is **not** related — despite the name it is a support CLI that
explains why the Customer × Item screen is empty. It is not a second diagnosis engine.

### 1.3 What is genuinely new

Only three things, and one of them is a column rather than a model.

1. **`QuoteDiagnosis`** — append-only, one row per quote line per run, holding the
   structured object, the evidence-hash and the two rendered views. `QuoteDecision` is
   the closest existing model and is deliberately *not* reused: it is the record of a
   price a human put in front of a customer, written at send time; a diagnosis is a
   read-time judgement that must be recomputable on a quote nobody has sent. Fusing
   them would mean either writing a decision row for an unsent quote or making the
   decision row mutable. Both are worse than a second table.
2. **`source_recorded_at` on `SalesTxn`, `CostRecord`, `InvoiceDoc`, `BillDoc`,
   `QuoteDoc`** — the ERP's own `created_time`. Not a model; a column. §2 and §4.1.
3. **Quote *line* storage.** `QuoteDoc` is header-grain on purpose, so quoted line
   prices exist nowhere in pie-portal. §8's `QUOTED_WON` / `QUOTED_LOST` evidence
   classes cannot be built without them. §4.2.

Everything else the build prompt asks for is a function over models that already exist.

### 1.4 pie-parser

**No pie-parser change is required for Phase 0, and none is expected before Phase 2.**
The diagnosis engine is commercial computation over ERP rows; the parser decodes
nomenclature. The one place they meet is §6.1 tier 3 and tier 6 — "same product
family" — where the parser's decoded designation is a better family key than the item
master's `cf_item_category`. That is a Phase 2 decision, recorded in §6 as an open
question. The `claude/pie-quote-diagnosis-engine-dsym0g` branch exists in pie-parser
and will stay empty unless that question resolves toward the decoder.

---

## 2. Timestamp reliability audit (§2.2) — measured

### 2.1 The headline: there are three clocks, and PIE stores the wrong two

| Clock | What it means | Where it lives today |
|---|---|---|
| **t1 `event_date`** | when the commercial fact happened | Zoho `date` → `SalesTxn.date`, `CostRecord.date`, `BusinessEvent.occurred_on` ✅ |
| **t2 ERP entry time** | when the fact became visible **to the business** | Zoho `created_time` → **nowhere. Not read, not stored.** ❌ |
| **t3 PIE sync time** | when *this platform* learned it | `SalesTxn.created_at`, `BusinessEvent.recorded_at` ✅ |

The build prompt's §3 `recorded_at` is **t2**. `BusinessEvent.recorded_at` is **t3** and
must not be mistaken for it — its docstring says "when this platform learned it", and it
defaults to `_now()` at insert. t3 is useless as a visibility cut-off for anything older
than the first sync: a backfill sync stamps ten years of history with one timestamp.

Confirmed by search: `created_time` appears **nowhere** in `ingestion/` except inside
`last_modified_time` handling. Every ERP connector in `ingestion/erp/` maps a modified
time and none maps a created time.

### 2.2 Entry lag: `created_time − date`, in days

**SLS Engineers — bills (census, n = 2,419, Apr 2025 → Sep 2026)**

| Cohort | n | min | p50 | p75 | p90 | p95 | p99 | max | ≤ 7d |
|---|---|---|---|---|---|---|---|---|---|
| **All** | 2,419 | −18 | **83** | 218 | **296** | 321 | 342 | 354 | 32.7% |
| Created 2026-03 (migration) | 1,632 | 1 | **165** | 260 | **312** | 328 | 344 | 354 | 4.0% |
| Created 2026-04 onward (live) | 787 | −18 | **3** | 4 | **7** | 13 | 156 | 342 | **92.1%** |

Live months, for stability: Apr p50 2 / p90 6 · May 2 / 11 · Jun 3 / 5 · Jul 3 / 7 ·
Aug 4 / 5 · Sep 3 / 156.

**SLS Engineers — invoices**

Total 3,625. **2,483 (68.5%) were created in March 2026.** Lag sample, two-ended:

| Sample | n | p50 | p90 | max | at lag 0 |
|---|---|---|---|---|---|
| 200 most recent by `created_time` (all live era) | 200 | **0** | 0 | 0 | **99.5%** |
| 200 oldest by `date` (all migration era) | 200 | 334 | 348 | 355 | 0% |

**SLS Engineers — quotes (census, n = 360, Mar 2026 → Sep 2026)**

96.7% at lag 0; min −116; max 1. Quotes were **not** migrated — the oldest quote in the
book is dated 2026-03-10.

**4U Precision (census, via Analytics)**

| Table | n | min | p50 | p75 | p90 | p99 | max | ≤ 0d | > 30d |
|---|---|---|---|---|---|---|---|---|---|
| Bills | 114 | 1 | **7** | 28 | **205** | 416 | 419 | 0% | 24.6% |
| Invoices | 178 | −11 | 0 | 0 | 0 | 73 | 77 | 97.8% | 2.2% |
| Quotes | 114 | −36 | 0 | 0 | 0 | 0 | 1 | 99.1% | 0% |

### 2.3 What these numbers actually say

**(a) The lag is directional, and it lands exactly on the cost side.** The sell side is
*authored* in Zoho — an invoice or a quote is created at the moment it is raised, so
t2 ≈ t1 by construction (99.5% / 96.7% at lag 0). The buy side is *transcribed* from a
supplier's document that arrives later, so t2 > t1 always. Live SLS bills: p50 3 days,
p90 7 days. **Every look-ahead risk in this engine is concentrated in the cost
baseline** — §9 and §10 of the build prompt — and almost none of it is on the price side.

**(b) Two thirds of the corpus is a migration artefact, not history.** SLS moved into
Zoho in **March 2026**: 1,632 of 2,419 bills (67.5%) and 2,483 of 3,625 invoices (68.5%)
were created that month. The cohort separates cleanly on more than the date — 1,371 of
the 1,632 migrated bills were created by user `sreeram` while 621 of 768 live bills were
created by `Business Operations`, and `cf_vendor_internal_doc_date` is filled on 479 live
bills and **zero** migrated ones.

For those rows **t2 does not exist and cannot be imputed.** The true entry time was in
the legacy system; what Zoho holds is the migration timestamp. `event_date + p90_lag`
would be a fabrication — it would claim a bill dated April 2025 was knowable in April
2025, when in fact nothing about it was in *this* system until March 2026.

**(c) Therefore the engine can only be honestly backtested from ~2026-04-01.** That is
**5.5 months**, ~787 SLS bills, ~1,100 SLS live invoices, 360 SLS quotes and 114 4U
quotes. A backtest run over the migrated era would be pure look-ahead and would look
excellent. This is precisely the failure the build prompt's §3 predicted; it is larger
here than the prompt assumed, and it is a *cohort* problem rather than a *lag* problem.

**(d) Negative lag is real and must be handled.** 16 of 178 4U invoices (9.0%), 11 of 114
4U quotes (9.6%) and 2 live SLS bills were created *before* their own document date —
down to −116 days on one quote. Post-dating is ordinary practice. It is harmless for the
rule `recorded_at < quote.created_at` (such a row is simply knowable earlier than its own
date), but it breaks any code that assumes `recorded_at >= event_date`, and it rules out
`max(event_date, created_time)` as a "safe" definition.

### 2.4 Consequences for the §3 correction

The build prompt's §3 is right, and the measurement sharpens it in three ways:

1. `recorded_at` must be **t2 (`created_time`)**, captured at ingestion. Neither
   `SalesTxn.date` nor `SalesTxn.created_at` is it.
2. The p90 imputation rule applies **only to live-era rows with a genuinely missing t2**
   — currently an empty set, since Zoho always returns `created_time`. It is a rule for
   future connectors (§4.1), not for the migration cohort.
3. The migration cohort needs its own flag and its own honest answer:
   `recorded_at_provenance = MIGRATED`, `recorded_at` set to the migration timestamp
   (which is what Zoho says, and is *conservative* — it under-claims knowability rather
   than over-claiming it), and **confidence capped at WEAK on any diagnosis leaning on
   such a row**, which is stricter than the prompt's §11 asks for.

---

## 3. Field availability matrix (§2.3) — measured

Legend: ✅ present · ⚠️ partially present · ❌ absent.

| Input the engine needs | Status | Fill rate (measured) | Notes |
|---|---|---|---|
| `event_date` | ✅ | 100% | Zoho `date` on every document |
| `recorded_at` (t2, ERP entry) | ❌ **in PIE** / ✅ in Zoho | 100% available, **0% captured** | `created_time` never read by `ingestion/` |
| `recorded_at` for migrated rows | ❌ | 67.5% of SLS bills, 68.5% of SLS invoices unknowable | §2.3(b) |
| **UOM** | ⚠️ | Item master: SLS 95.2% (`Nos`), 4U 98.7% (`pcs` 456 / `nos` 57). **Invoice lines carry no UOM column at all**; bill lines do (`Usage unit`, 89.0% on 4U) | The unit lives on the item, not the line |
| **UOM conversion factor** | ❌ | **No such field exists** in Zoho Books | See §3.1 |
| Currency | ✅ | 100% INR — 0 non-INR across SLS invoices (400 sampled), SLS bills (2,400), 4U invoices/bills/quotes (census) | |
| FX rate at date | ✅ but **vacuous** | `exchange_rate` present, **= 1.0 on every row measured** | See §3.2 |
| Quantity | ✅ | 100% on invoice and bill lines | |
| Unit price | ✅ | 100%; 0 rows at ≤ 0 | Net-of-discount is derivable: lines carry rate + discount |
| **Line-level cost** (bill line) | ⚠️ | Qty/price 100%; **`Product ID` only 89.0%** on 4U bill lines | 11% of bill lines are free-text and cannot be attributed to an item |
| **Landed cost** (item master) | ⚠️ **badly** | SLS: 2.8% at zero. **4U: 275 of 520 items (52.9%) have `Purchase Price` ≤ 0** | See §3.3 — this is a live correctness issue |
| Vendor | ✅ | 100% on bill headers; `CostRecord.vendor_id` nullable by design | Kennametal = 66.9% of SLS bills |
| **Price-list reference** | ⚠️→❌ | `PriceList ID` on 4U invoice *lines*: **0 of 373** | |
| **Price-list effective date** | ❌ | **No such column exists.** Zoho price lists carry only `Created Time` / `Last Modified Time` | See §3.4 |
| **Quote outcome (won/lost)** | ⚠️ | SLS (n=360): won 85 (23.6%), lost 11 (3.1%), **unrecorded/open 264 (73.3%)**. 4U (n=114): won 42, lost 13, open/expired 59 | See §3.5 |
| **Lost reason** | ❌ in Zoho / ✅ in PIE | Not a Zoho field. `QuoteOutcome.loss_reason` exists and is human-written | Only populated for quotes a human closed in PIE |
| **Quoted line price** | ❌ in PIE / ✅ in Zoho | `Quote Items` has `Product ID`, `Quantity`, `Item Price`, `Entity Discount Percent` | `QuoteDoc` is header-grain; §4.2 |
| Salesperson | ⚠️ | SLS: 200 of 400 sampled invoices — **all 200 live-era, none migrated**. 4U: 132 of 178 (74.2%) | |
| Customer segment | ❌ | No field on `Customer` or in Zoho | `EntityGroup` is the substrate; §4.3 |
| Product family | ⚠️ | SLS `cf_item_category` 85.0% (Grooving & Parting 143, Threading 125, Toolholding 89, General 83, Milling 51, Turning 15, Holemaking 2); `manufacturer` 69.5%; **`brand` 0%, `part_number` 0%**; SKU 30.7% | `Product.source_item_category` already stores this |
| HSN | ✅ | SLS 96.8%, 4U 95.4% | |
| Branch / location | ✅ | SLS: Head Office 2,352 / Bangalore 48 on bills | A second axis nobody has asked for yet |
| Customer PO reference | ⚠️ | `cf_customer_po_reference` on 160 of 400 sampled SLS invoices | |

### 3.1 There is no UOM conversion factor, and §5's rule would exclude everything

Zoho Books has no unit-conversion table (that is a Zoho *Inventory* feature these books
do not use). Applied literally, §5 — "if no conversion factor exists for that item, the
row is **excluded**" — excludes **100% of rows**, because no factor exists for any item.

The rule is still right; the risk it guards against is just not the one the prompt
imagined. Within one `item_id` the unit is fixed by the master, so two transactions for
the same item are already in the same unit and comparable without any factor. The real
UOM hazard here is **synonym units across items**: 4U spells the same unit `pcs` on 456
items and `nos` on 57. Two spellings of "each" would make an item look like a different
commercial object from its own sibling on any cross-item tier.

**Proposal.** Normalize the *unit label* to a canonical token, not a magnitude. Exclude
a row only when the item's unit is blank (SLS 4.8%, 4U 1.3%) **or** when a tier compares
across items whose canonical units differ. Count both in the `unnormalizable` bucket the
prompt asks for. No conversion factor is invented, and §5's exclusion discipline is kept.

### 3.2 Multi-currency has zero live instances

Every document measured is INR at rate 1.0. Test 29 ("a test that proves the current
rate is not used") therefore has **no live instance** and must be written against a
synthetic fixture. The normalization layer should still be built — a second org or an
export invoice would need it, and Zoho does carry a per-document `exchange_rate`, which
*is* an as-of rate — but nobody should expect the corpus to exercise it. Flagging it
because a layer that never runs on real data is a layer that rots.

### 3.3 The 4U item master's landed cost is half placeholder

**275 of 520 4U items (52.9%) carry `Purchase Price` ≤ 0**, and 281 (54.0%) carry
`Sales Price` ≤ 0. SLS is far healthier at 2.8% / 2.5%.

This matters immediately, not just for Phase 3. `quote_intelligence.assess_line` falls
back to `item_master_landed_cost` when bill history has nothing, and it already guards
`item_master_cost > 0` — so on 4U that fallback silently does nothing on half the
catalogue, and the line lands on `NO_COST_BASIS`. The guard is correct. The *reporting*
is not: "no cost on record" and "the master holds a zero placeholder" are different
facts and a cost-side `INSUFFICIENT_EVIDENCE` should say which.

### 3.4 A Zoho price list cannot be point-in-time evidence

Price lists have no validity period — only `Created Time` and `Last Modified Time`, and
they are rewritten in place. There is no way to know what a price list said on a past
date. Combined with `PriceList ID` being empty on 100% of 4U invoice lines, this closes
the question: **price lists are out of scope for the evidence builder**, and §9's "known
price lists with known effective dates" has no data behind it. §10's `KNOWN_COST_CHANGE`
must be derived from bill history alone, which is consistent with §9's "ERP data only".

### 3.5 Outcome data is thin, and the LOST half is nearly empty

SLS, census of 360 quotes: `expired` 195 · `invoiced` 65 · `sent` 48 · `accepted` 20 ·
`draft` 13 · `pending_approval` 8 · `declined` 6 · `rejected` 5. `accepted_date` is
filled on exactly 85 rows, `declined_date` on **6**.

So §8's resistance evidence — "if this customer has lost quotes at or below the top of
the computed band" — rests on **6 rows across the entire SLS book**, spread over an
unknown number of customers. It will essentially never fire per-customer. The mechanism
should still be built (it is cheap, it is correct, and the evidence will accumulate), but
the evidence summary must say out loud that outcome data is absent, as §8 requires, and
nobody should expect `price_resistance_observed` to appear in Phase 6.

`expired` at 54% is the large unrecorded pile, and `classify_outcome` already refuses to
read it as a loss. That refusal is correct and must not be relaxed to make §8 look alive.

---

## 4. Gaps, and the minimum schema delta (§2.4)

**Proposed, not applied.** Nothing below has been written to a migration.

### 4.1 Capture the ERP's own `created_time` — the one blocking gap

Without it there is no point-in-time evidence builder, only a cosmetic one.

```
SalesTxn      + source_recorded_at   DateTime(timezone=True)  NULL
              + recorded_at_provenance  String(16)  NULL    # SOURCE | MIGRATED | IMPUTED
CostRecord    + source_recorded_at   DateTime(timezone=True)  NULL
              + recorded_at_provenance  String(16)  NULL
InvoiceDoc    + source_recorded_at   DateTime(timezone=True)  NULL
BillDoc       + source_recorded_at   DateTime(timezone=True)  NULL
QuoteDoc      + source_recorded_at   DateTime(timezone=True)  NULL
```

- Nullable, nothing backfilled at migration time. A row written before this existed
  cannot be attributed after the fact — the same argument `connector` / `connection_id`
  already make on these tables.
- Populated by a **full re-sync**, which re-reads `created_time` from Zoho. Both books
  are small enough for that to be routine.
- `recorded_at_provenance` is what keeps the migration cohort honest. It is set to
  `MIGRATED` when `created_time` falls inside a configured migration window for that
  connection, and the evidence builder caps confidence at WEAK on any tier-1–2 row
  carrying it. `IMPUTED` is reserved for a future connector that exposes no created
  time, under §3's `event_date + p90_lag` rule.
- The connector contract (`ingestion/erp/*.py`) gains a `created_time` key alongside the
  `last_modified_time` all seven already map. A connector that cannot supply one leaves
  it null and its rows impute.
- **The migration window is configuration, not a literal.** It belongs on the
  `ZohoConnection` row, because it is a fact about one company's book.

`SaleRow` and `CostRow` in `signals/base.py` gain the same field, since they are the
projection every deterministic consumer reads.

### 4.2 Pull quote line items

`QuoteDoc` is header-grain by deliberate design ("the line-level split would cost one
API call per quote"). §8 needs the lines. SLS has 360 quotes and 4U has 114, so the
one-call-per-quote cost is ~474 calls on a full sync and near zero incrementally.

```
QuoteLine (new)   quote_document_id, product_id, product_ref, qty,
                  rate, discount_percent, net_unit_price, line_total,
                  external_ref (quote_id:line_id), source_ref,
                  connector, connection_id, source_recorded_at
```

Net-of-discount, for the reason `SalesTxn.unit_price` is: a 4U quote line observed at
`Item Price` 1,936.00 with `Entity Discount Percent` 55% is a ₹871.20 offer, and a band
built from the gross figure would be wrong by a factor of two.

### 4.3 Customer segment — use `EntityGroup`, add no column

§6.2's peer axis needs "comparable customer segment" and no such field exists. It must
**not** become a column on `Customer`: that table is derived and a full re-sync would
delete it silently — exactly the argument `EntityGroup`'s own docstring makes.

`EntityGroup(entity_kind=CUSTOMER)` already is a named, versioned, hand-drawn set of
customers whose version hash includes the sorted roster. That is the segment. Phase 2
reads it; if an organization has drawn no groups, the peer axis degrades to "all other
customers" — which is exactly what `ItemBenchmark` does today — and says so in the
evidence summary rather than pretending to a segment.

### 4.4 The operations view needs a type, not a filter

`quote_service.project(intel, role)` takes the full `QuoteLineIntelligence` and removes
`RESTRICTED` fields for a salesperson. It is careful and it is well-tested. It is still a
**filter over a cost-bearing object**, and I3 asks for something stronger: a record type
with no cost or margin field on it, so a leak is structurally impossible.

The repo's own history is the argument for taking I3 literally. `filterCounts.MFLOOR` was
a correct guard with one un-guarded line below it. The rule-code leak was a correct
redaction of *reasoning* that left a walkable predicate. Both were filters that were
right except where they weren't.

So: `OperationsDiagnosis` is a separate frozen dataclass constructed from
`OwnerDiagnosis`, carrying price evidence, direction, evidence strength and rendered
prose — and declaring no cost, margin, opportunity-value or peer-band field at all. The
cost-driven case renders as the prompt's sentence, *"Margin on this line is compressed by
supply cost, not by your price. No price change needed."*, with no figure to withhold.

**Stated trade-off.** CLAUDE.md §7 warns against abstractions added to look SOLID, and
two types where one filter would do is exactly the shape that warning describes. The
justification is I3 plus two incidents, not symmetry. `project()` is not deleted — it
keeps serving the existing quote-intelligence endpoint — but the diagnosis engine does
not route through it.

### 4.5 `cost_basis_asof` currently looks ahead, and always has

```python
def cost_basis_asof(costs: list[CostRow], as_of: date) -> Optional[CostRow]:
    applicable = [c for c in costs if c.date <= as_of]   # c.date is t1
```

`economics.in_window` filters the same way. Both are **event-date** filters, so today's
quote assessment can be costed against a bill that was not keyed in until days or months
after the quote went out. Given §2.2 that is a live, measurable defect: median 3 days on
the live SLS cohort, median 165 days on the migrated one.

This is not a bug to fix silently inside the new engine. The quote screen and the
analysis screen share these functions precisely so they cannot disagree, and changing
the filter changes both. **Proposal:** add a second, explicit entry point
`cost_basis_knowable_at(costs, as_of, knowable_by)` that filters on
`c.source_recorded_at < knowable_by` and orders by `c.date`, and leave `cost_basis_asof`
exactly as it is for the live-screen path, whose question genuinely is "what does this
item cost now". Two functions, two different questions, neither pretending to be the
other. Phase 4 decides whether the live screen should move over too — that is a product
decision about whether a quoter should see a cost that arrived this morning.

### 4.8 There is already a MAD — extend it, do not write a second

`commercial/insight/payments.py:_spread` computes a median absolute deviation, with the
same argument §7 makes ("a standard deviation would let [one outlier] redefine the
customer; the MAD does not"). It is private, takes `list[int]` (days), and returns a
rounded float **spread** — not an exclusion set.

`commercial/benchmark.py:median_decimal` is public, Decimal-safe, and was made public for
exactly this reason: "a second copy would have been a second rounding behaviour for the
same kind of number."

So §7 needs one shared primitive, in `commercial/` rather than in `insight/`:

```
mad_exclusions(values) -> (median, mad, [(index, direction, deviation), ...])
```

Decimal throughout, built on `median_decimal`, returning the per-row direction and
deviation §7 requires for `excluded_low` / `excluded_high` and for traceability (I4).
`_spread` then calls it for its spread rather than keeping its own two lines. Two MADs
that disagree about the middle of a distribution is precisely the semantic duplication
CLAUDE.md §2 is about.

### 4.6 What must be captured going forward

- `created_time` on every document, from every connector (4.1).
- A loss reason on every lost quote. `QuoteOutcome.loss_reason` exists and `set_outcome`
  already refuses a LOST transition without one; what is missing is anyone using it —
  6 declined rows in SLS. This is an operations habit, not a schema gap.
- Dismissal reasons from §14's card. There is no model for these yet; `QuoteDiagnosis`
  should carry a `dismissed_at` / `dismissal_reason_code` / `dismissal_reason` triple
  written by the UI. The prompt is right that it is the cheapest labelled data available.
- 4U's item-master purchase prices (§3.3) — an operations fix, and
  `master_hygiene_watch` already flags exactly this.

### 4.7 Not proposed, deliberately

- **No price-list model** (§3.4 — the data cannot support point-in-time use).
- **No UOM conversion table** (§3.1 — nothing to populate it from; unit-label
  canonicalization instead).
- **No new thresholds table.** §11's constants belong in `CommercialThresholds`, which is
  already content-hashed and stamped on every computed row. Note the consequence: adding
  fields moves `version`, so every existing stamp becomes visibly older. That is correct
  behaviour and worth saying before it surprises anyone reading a diff.
- **No second scorecard, no second peer calculation, no second erosion classifier.**
  §6.2 extends `ItemBenchmark` with a qty-band filter and a segment filter; §10 reuses
  `classify_erosion`.

---

## 5. What the corrections mean, given the measured data

| Build-prompt correction | Verdict against the data |
|---|---|
| §3 `recorded_at`, not `event_timestamp` | **Right, and understated.** The problem is a 67.5% migration cohort, not a lag distribution. Honest backtesting starts ~2026-04-01. |
| §5 normalization as a layer | **Right, wrong hazard.** No conversion factors exist anywhere; the live hazard is `pcs`/`nos` synonym units. |
| §5 qty band as a hard filter | **Right, and already built** (`quantity.py`, edges `(1, 10, 50, 200)`). |
| §6.2 peer axis | **Right, and the highest-value new thing here.** Partly built (`ItemBenchmark`); needs qty-band + segment. `BELOW_PEER_BAND_STRUCTURAL` has no existing equivalent. |
| §7 symmetric MAD outliers | **Right, and half-built.** A MAD exists (`insight/payments._spread`) but computes a *spread* over ints, not an exclusion set; `benchmark.median_decimal` is the Decimal-safe median it needs. See §4.8. |
| §8 evidence classes | **Right, and starved.** `classify_outcome` gives the taxonomy; the data gives 6 LOST rows in SLS. Build it, expect silence. |
| §9 ERP data only, no special prices | **Right, and now provable** — there is no price-list effective date to reach for even if someone wanted to. |
| §10 pricing vs cost effect | **Right, and largely built** at relationship grain (`classify_erosion`, `pass_through`). |
| §11 explicit thresholds | **Right.** Note `min_quote_exception_impact = 500.0` already exists and is the model to follow for the absolute floors. |
| §12 outcome layer strictly separate | **Already satisfied structurally.** `QuoteDecision` is append-only, `QuoteOutcome` has no write path into it, `outcome_tracker` computes evaluations on read. |
| §13 reproducibility | **Achievable.** `QuoteDecision` already freezes values and stamps `thresholds_version` + `engine_version`. The evidence-hash and `rediagnose()` are new. |

---

## 6. Open questions (§2.5)

**Four of these were answered at the Phase 4 gate; the answers are recorded
against them below and in §9.** The rest are still open.


1. **UPS.** Is it a separate Zoho organization? The `ZohoUPS` connector returns SLS's
   org id. If it exists, it needs a connection and a re-measure; if it does not, the
   "three legal entities" framing in `CLAUDE.md` needs correcting for this engine's scope.

2. ~~**The SLS migration window.**~~ **Answered: it depends on the connection's own sync
   history, so it is not a constant.** `history_loaded_before` stays a per-connection,
   human-settable date, and `quote_diagnosis/cutover.py` reads the boundary off that
   connection's creation stamps and *reports* it with the counts behind it. It never
   applies what it finds — see §9. Still to do: set the column on each live connection
   from what the detector shows.

3. **Was there a legacy entry-time record?** If the pre-Zoho system (Tally?) can export a
   voucher entry date, the migrated cohort becomes usable and the backtest window goes
   from 5.5 months to 18. If not, I plan to treat it as unknowable rather than impute.
   This is the single biggest lever on how much evidence Phase 6 has.

4. **Product family for tiers 3 and 6.** Three candidates: the item master's
   `cf_item_category` (85% filled, 7 values, and `Product.source_item_category`'s own
   docstring warns it is "evidence about an item, not a verified fact" — it files a
   reamer as a Tap); `manufacturer` (69.5%); or pie-parser's decoded designation
   (authoritative, but couples the two repos). My inclination is `cf_item_category` for
   v1 with the parser as a Phase 2 upgrade — but tier 3 is a *ranking* tier, and the
   docstring says ranking with it is fine while gating on it is not, so this is defensible.
   Confirm?

5. **Customer segment.** Are there existing `EntityGroup` customer sets to use as the
   peer segment, or does the peer axis ship in "all other customers" mode until somebody
   draws them?

6. ~~**The absolute floors in §11.**~~ **Answered: keep as set** — ≥5% of the band
   median, ≥₹25 per unit, ≥₹500 on the line. The ₹500 matches
   `min_quote_exception_impact` so the two engines cannot argue about the same line.

7. ~~**Does the live quote screen move to `recorded_at` filtering too**~~ (§4.5)
   **Answered: no — the diagnosis engine only.** `cost_basis_asof` is untouched and keeps
   answering "what does this cost now"; `costs_knowable_at` answers "what could we have
   known then". Two functions, two questions, neither pretending to be the other.

8. **Bill lines with no item (11% on 4U).** Excluded from cost evidence and counted, or
   matched by description? I plan to exclude and count — guessing an item from free text
   is the kind of inference the parser exists to do properly and this engine should not
   do casually.

---

## 7. Redundancy & SOLID self-review — `docs/quote-diagnosis/ASSUMPTIONS.md`

**Capability search run:** yes — searched code for
`(def|class) \w*(resolve|normali[sz]|validate|compute|diagnos|band|benchmark|peer)\w*`
across `backend/app`, plus targeted reads of the 30 modules in `commercial/` (`insight/` included), the
`SalesTxn` / `CostRecord` / `QuoteDoc` / `QuoteDecision` / `QuoteOutcome` /
`CustomerItemMetric` / `BusinessEvent` / `EntityGroup` models, `signals/base.py`,
`ingestion/normalize.py`, and `tests/decision_platform/` (198 files) for quote-related
coverage. Searched data: Zoho Books + Zoho Analytics for both reachable orgs.

**Near-matches found:** many, and they are the point of §1.2 —
`commercial/quote_intelligence.py:assess_line`, `commercial/references.py:build_references`,
`commercial/benchmark.py:ItemBenchmark`, `commercial/quantity.py:band_for`,
`commercial/metrics.py:classify_erosion`, `commercial/economics.py:cost_basis_asof`,
`commercial/diagnosis.py:diagnose`, `ingestion/normalize.py:classify_outcome`,
`commercial/backtest.py`, `commercial/outcome_tracker.py`.
Two the first pass missed and a second, narrower search found:
`commercial/insight/payments.py:_spread` (a median absolute deviation — §7's rule
already exists in the codebase, over ints, as a spread rather than an exclusion)
and `commercial/benchmark.py:median_decimal` (public, Decimal-safe median). §4.8
is the result, and it changed a "right and unbuilt" into an "extend it".
Also a **name** collision worth knowing about: `commercial/diagnose.py` is an empty-screen
support CLI, unrelated to this engine.

**Reuse rejected because:** nothing rejected; one near-match found late and folded in (§4.8). The audit's conclusion is that the majority
of the build prompt is already implemented at relationship grain and needs extending
rather than rebuilding. Three genuinely new things are proposed (§1.3) and one of them is
a column. The one place a new type is proposed over an existing filter —
`OperationsDiagnosis` vs `quote_service.project` — is argued from I3 and two named
incidents in §4.4, with the trade-off stated rather than waved through.

**Could this have been pack data instead of code?** n/a — pie-portal has no pack layer,
and no pie-parser change is proposed (§1.4).

**Duplicate scan:** not run — this change adds one Markdown document and no code.

**Invariant checks (§1 of CLAUDE.md):**
- *AI never computes a number* — clean. Every figure in this document came from SQL or
  from arithmetic over ERP payloads; no model produced one. The engine design keeps the
  LLM renderer behind a flag, over an already-complete structured object (build prompt I2).
- *Cost and margin never reach a salesperson* — clean, and strengthened in §4.4. This
  document reports fill rates and timestamps only; it contains no cost or margin value,
  and §3.3's "52.9% of items have `Purchase Price` ≤ 0" is a structural fact, not a price.
- *Absence of evidence is not a pass* — this is the document's main theme. §2.3(b),
  §3.4 and §3.5 each identify a place where the benign default would have been wrong.
- *Thresholds carry a version* — §4.7 notes that adding §11's constants to
  `CommercialThresholds` moves `version`, and that this is correct.
- *Layer boundaries* — nothing added to `commercial/`, `signals/`, `ingestion/` or
  `state/`; no import changes at all.

**SOLID:** n/a — documentation only. The design notes in §4 flag SRP (§4.5: one function
answering two different questions) and ISP/DIP (§4.4: a type rather than a filter) as
Phase-1 decisions to be re-reviewed when code exists.

**Below the size floor (§7):** n/a — no code.

**Corpus / gate:** not run — no code changed, so `make verify` would report the state of
`main` and prove nothing about this commit. It runs at the end of Phase 1, against code.

**Verdict:** APPROVED — Phase 0 deliverable, stopping for review as §2 requires.

---

## 8. What phases 1–4 built, and where they departed from this audit

Added after the Phase 4 stop gate, because an audit that does not say how its own
plan survived contact is worth less on the second reading than the first.

### Built

| Layer | Module | What it does |
|---|---|---|
| Capture | `domain/schemas.SourceRef.recorded_at`, `ingestion/normalize._recorded_at`, migration `n2recorded` | The ERP's own `created_time`, through the normalizer, promoted to a queryable column |
| Phase 1 | `commercial/quote_diagnosis/evidence.py` | Evidence classes, unit canonicalisation, the `knowable_at` filter, the exclusion ledger |
| Phase 2 | `commercial/quote_diagnosis/comparables.py` | Six customer tiers, the band as a hard filter on 1–3, the independent peer axis |
| Phase 3 | `commercial/dispersion.py`, `.../baselines.py` | One shared MAD/quantile primitive; price and cost baselines, trimmed symmetrically |
| Phase 4 | `.../rules.py` | Strength, the diagnosis codes, the surfacing gate, `OwnerDiagnosis` / `OperationsDiagnosis` |

### Departures, with reasons

1. **`source_recorded_at` went on three tables, not five.** `sales_txns`,
   `cost_records` and `erp_quotes` have readers; the invoice and bill *headers*
   do not — they answer accounts payable and receivable, which are not
   point-in-time questions. A column with no reader is one the next migration
   has to explain.

2. **No `recorded_at_provenance` column.** §4.1 proposed one. Instead the
   migration cohort is derived at read time from
   `ZohoConnection.history_loaded_before`, which is the idiom `Product.category`
   and `manufacturer` already follow: a value rewritten at sync time could never
   be re-read under a corrected cut-over date without a full re-sync, and the
   cut-over is exactly the kind of fact a human corrects.

3. **A zero MAD needed a fallback, and the tests found it.** More than half the
   observations being identical is what a stable price history *is*, not an edge
   case, and it drives the MAD to zero. Declining to trim then left a ₹100 and a
   ₹9,000 sitting in a band of ₹1,000s as its own low and high. So with a zero
   MAD the scale comes from the median itself — `diagnosis_degenerate_band_pct`,
   default 10% — and `zero_spread_fallback` records that it happened.

4. **Baseline *selection* is asymmetric, and the trim is not.** §7's symmetry is
   about which observations are representative, and it holds exactly. Which
   *level* a quote faces is a different question: a single purchase above the
   rest is indistinguishable by spread from a genuine step up, so the latest
   purchase may raise `expected_cost` and may **never** lower it. Raising it
   risks saying "no price change needed" when nothing was wrong; lowering it is
   §9's unexplained cheap purchase, which makes every later normal purchase read
   as erosion. `baselines.cost_baseline` states the asymmetry where it happens.

5. **No FX layer, deliberately.** `ingestion.sync._refuses_currency` rejects a
   foreign document at the seam, so every row reaching the engine is in the
   book's own currency by construction — stronger than an as-of conversion, and
   an FX layer here would be code that can never run. Test 29 asserts the
   *type* has no currency field rather than asserting a conversion.

6. **Units are canonicalised, never converted.** No conversion factor exists
   anywhere in these books, so §5 applied literally excludes every row. Equal
   canonical labels are comparable, unequal ones are excluded and counted, and
   no magnitude is ever invented. `pcs` and `nos` fold together; `metre` and
   `each` do not.

7. **A missing cost baseline is a qualifier, not a veto.** `NO_COST_EVIDENCE`
   sits in `context`, so the price-side finding still stands and still surfaces —
   §9's "still producing a price-side diagnosis if the price evidence is sound".
   What it must block is a *money* figure, and that restraint belongs in the
   opportunity layer (Phase 6), not in the gate.

### Not built, and therefore not working yet

- **Quote line items are still not ingested** (§4.2). `QuoteDoc` remains
  header-grain, so nothing produces `QUOTED_WON` or `QUOTED_LOST` evidence
  today. The classes, the baseline exclusion and the resistance rule are all in
  place and tested against constructed rows; they will stay inert until the sync
  pulls estimate lines. Until then every band is built from `REALIZED` alone —
  which is the survivorship bias §8 exists to correct, still uncorrected.
- **The `created_time` key is Zoho-only.** The six `ingestion/erp/` connectors
  do not supply it and none is guessed at in code; a book on one of them
  diagnoses nothing rather than diagnosing from evidence it could not have had.
  `docs/connectors.md` names the field each vendor calls it.
- **Phases 5–8** — renderer, opportunity, outcome layer, UI — are not started.
  Phase 6 is the next stop gate.

---

## 9. Phases 5 and 6, and the four answers

### Built

| Layer | Module | What it does |
|---|---|---|
| Phase 5 | `quote_diagnosis/render.py` | `OperationsCard` and `OwnerReport`, template-assembled, plus the dismissal vocabulary |
| Phase 6 | `quote_diagnosis/opportunity.py` | The potential range, truncated by resistance, qualified when cost is unknown |
| — | `quote_diagnosis/cutover.py` | Detects a connection's bulk load and reports it. Never applies it |

`render_operations` takes `OperationsDiagnosis` and nothing else — not an owner
diagnosis it filters, not a pair it chooses between. If it needed the owner
object for anything, that would be the bug, and a test reads its signature.

### The four answers, and what each one changed

1. **Opportunity shows with a qualifier, rather than being withheld.** The range
   is `(band low − quoted)` to `(band median − quoted)` × qty and touches no cost
   at any point, so it is computable on the half of a catalogue that has no
   usable cost. What it cannot do without a cost baseline is rule out a
   cost-driven cause, so `cost_on_record` travels with it and the sentence says
   so. Owner-only either way: `OperationsDiagnosis` has no field for it.

2. **The cut-over is per-connection and comes from that connection's own
   history.** `cutover.detect` looks for the shape a migration has and live entry
   does not — a large share of a book's documents created inside one month,
   describing events spread across the months before it. Both halves are
   required: a busy month is just a busy month, and a few late entries are
   ordinary. It suggests the first day of the month *after* the peak, with the
   counts, and a person confirms. **Nothing falls back to a detected value**: a
   boundary inferred from row counts moves every time the counts do, and a band
   that changed shape after a sync would be unexplainable.

3. **The live quote screen keeps `cost_basis_asof`.** See open question 7.

4. **The surfacing floors stay where they were set.** See open question 6.

### Two things the wording has to keep doing

**Potential, never missed.** `_opportunity_sentence` says "historical evidence
suggests a potential margin opportunity of ₹X–₹Y … an estimate of what was
plausibly achievable, not profit forgone", and a test asserts the loss
vocabulary never appears. The difference is not politeness: nobody knows what
this customer would have paid, and a tool that claims to lose its reader the
first time a salesperson can explain one of those lines.

**A cost-driven card carries no figure at all.** The specification's own example
template printed "historical acquisition cost was ₹700, current cost is ₹900" on
a salesperson's screen. The replacement is one sentence — *"Margin on this line
is compressed by supply cost, not by your price. No price change needed."* — and
a test serialises the whole card and asserts neither number appears in it.

### Still not built

- **Quote line ingestion** (§4.2), so `QUOTED_WON` / `QUOTED_LOST` still have no
  producer and every band is `REALIZED` only. §8's resistance truncation is
  coded and tested against constructed rows; under the current band construction
  it is a guard rather than an active adjustment, and `opportunity._target` says
  why it is there anyway.
- **`history_loaded_before` is unset on every live connection.** The detector
  exists; nobody has run it against a real book and confirmed a date. Until then
  the engine reports `backfill_cutover_unknown` on every diagnosis and bulk-loaded
  rows are not excluded.
- **Phases 7 and 8** — the outcome layer (`rediagnose`, the evidence hash, the
  `diagnosis_vs_outcome` view, dismissal persistence) and the UI.
