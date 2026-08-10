# Regulatory and data protection

What the tax code makes expensive to miss, and what the platform can honestly
claim about the data it holds.

Two halves, and they are not equally valuable. The **statutory timing** half is
hard cliffs and pure date arithmetic over data already ingested — expensive to
miss, cheap to compute, and worth building. The **data-protection** half largely
documents engineering already done; it pays when the platform is sold to
somebody else rather than when the business is run. This document is weighted
accordingly, and says plainly which parts are built, which are blocked, and on
what.

**The line that matters most here:** the platform surfaces a date, an amount and
the rule. It never gives tax advice. A wrong margin costs a deal; a wrong tax
position is the owner's liability, and nobody in this codebase is their
accountant.

---

## 1. What is ingested today, and what that makes computable

### The supplier side, as it stands

| Model | Fields | Source |
|---|---|---|
| `Vendor` | name, `gstin`, `pan`, `payment_terms_days`, status, provenance triple | `list_vendors` → `contacts?contact_type=vendor` |
| `BillDoc` | number, vendor, **date**, **due_date**, status, total, **balance** | `list_bills` → `_record_payable`; header written from the payload the cost pull already fetches |
| `CostRecord` | line grain: qty, rate, discount, `item_total` → `unit_cost`, **pre-tax** | `normalize_bill` |
| `VendorPaymentDoc` | date, amount, mode, reference | `list_vendor_payments` |
| `BillPaymentApplication` | **bill_date, bill_due_date, paid_on, amount_applied**, per bill settled | detail call per payment |
| `PurchaseOrderDoc` | date, expected_date, **received_on**, received/ordered/pending | `list_purchase_orders` |
| `VendorPaymentTerm` | human-entered agreed `days` + `basis`, survives a re-sync | manual, org-scoped |

Window: `ZOHO_HISTORY_DAYS` defaults to 730. `ZOHO_MAX_PAGES × ZOHO_PAGE_SIZE`
caps a listing at 10,000 documents with only a warning log.

### Computable with no new field

1. **Days from bill date to settlement**, per bill, per vendor —
   `commercial/insight/payments.py` already does this at application grain with
   an evidence floor.
2. **Bills unpaid at day *N* since the bill date** — `_overdue_to_vendors` is
   this query with the predicate moved from `due_date` to `date + N`.
3. **Per-vendor purchase turnover per financial year** (`Σ BillDoc.total`) and
   the customer-side mirror.
4. **Quarterly gross profit on synced trade** — computed, but see §6 for why it
   is not the input to an advance-tax calculation.

### Blocked, and on exactly what

| Check | Missing |
|---|---|
| **43B(h) applicability** | MSME registration status. The only field between the platform and the whole watchlist. **Now built** — see §2. |
| **43B(h) deadline (15 vs 45)** | Whether a *written* agreement exists. `VendorPaymentTerm` records agreed days; it does not record that the agreement is written. **Now captured.** |
| **GST ITC anything** | The tax split. It is on the payload and dropped one layer early — see §3. |
| **Net payable per bill** | Vendor credit notes are not pulled. 43B(h) runs on the amount actually payable. |
| **s.15 start date** | Acceptance / deemed acceptance. `PurchaseOrderDoc.received_on` is the nearest proxy, and `BillDoc` carries no link to a purchase order. |
| **s.234C** | Everything except the run rate. See §6. |

One completeness caveat for any financial-year sum: incremental listing
deliberately does not set `listing_complete`, so the deletion sweep does not run
on those passes and a voided bill can persist until the next full pull.

---

## 2. The 43B(h) watchlist — built

Section 43B(h) disallows, for the year, a deduction for anything still owed to a
registered micro or small supplier beyond the limit in section 15 of the MSMED
Act. It is a cliff on a date: a bill settled on 30 March and the same bill
settled on 2 April are a financial year apart in consequence.

### Capturing the status

`VendorMsmeStatus` — a separate table, not a column on `Vendor`, for the reason
`VendorPaymentTerm` is separate: `upsert_vendor` rewrites the vendor row from
the payload on every sync, so anything a person typed there would survive
exactly until the next pull.

- `classification` — MICRO / SMALL / MEDIUM / NOT_REGISTERED / **UNKNOWN**
- `enterprise_activity` — MANUFACTURER / SERVICE / **TRADER** / UNKNOWN
- `written_agreement` — nullable, three genuinely different states
- `agreed_days`, `udyam_number`, `evidence`, `effective_from`, `set_by_user_id`

**Nothing is ever inferred** — not from turnover, not from bill size, not from a
supplier's name. `UNKNOWN` is the default and produces a data-gap row, never a
silent pass. Same rule `Customer.incentive_eligibility` already follows.

Three capture routes, cheapest first: the Udyam number most registered
suppliers print on their tax invoice (already in the document set); a Zoho
contact custom field; a portal lookup. The API refuses a classification with
`evidence = NONE` — a status nobody can source is one nobody can defend.

**The capture backlog is the unlock.** Status is collected by a person, one
supplier at a time, and the book has hundreds. `msme.capture_backlog` ranks
unknown-status suppliers by spend × the share of their bills already settled
past the limit, so the job is roughly twenty suppliers rather than four hundred.
It reports `shown` / `unestablished` / `unestablished_spend` rather than
truncating in silence — a coverage list that shows 25 rows and no denominator
reads as "these are all of them".

### The watchlist

Row grain is **one open bill**, not one supplier: the deduction is disallowed
bill by bill.

Deadline = start + (15 if no written agreement, else `min(agreed, 45)`), where
start is the bill date, or a recorded goods receipt where one exists. Every row
carries `deadline_basis` and `deadline_start_basis` as **visible columns, not
tooltips**, because every date rests on a stated proxy.

Ranking: current financial year first, then nearest deadline, then largest
balance — absolute, so a row does not move because an unrelated one appeared.

Two numbers, never summed:

- **Amount at risk** — the balance. A fact.
- **Estimated carry cost** — `balance × tax_rate × carrying_cost_annual_pct`,
  and `None` when no rate is set.

Plus a **gap band**, reported beside the confirmed total and never added to it:
what would be at risk if the unclassified suppliers turn out to be covered.

### What the arithmetic gets right

- **Fifteen days is the default, not forty-five.** s.15 allows fifteen absent a
  *written* agreement and caps a written one at forty-five. `insight/terms.py`
  already establishes that Zoho's payment terms are a fixed dropdown real
  agreements get filed under, so reading one as an agreement would understate
  exposure on exactly the suppliers with no contract — the small ones, who are
  the ones the section protects. The API refuses days without a recorded
  agreement rather than resolving the ambiguity.
- **A disallowance is a timing difference.** The deduction returns in the year
  the money is actually paid. `balance × tax_rate` overstates the cost by
  roughly an order of magnitude and is the first thing an accountant notices.
  The real cost is a year's carry on tax brought forward.
- **Registered traders and medium enterprises are out of scope.** Medium is
  outside 43B(h) entirely; wholesale and retail traders hold Udyam registration
  for priority-sector lending without the s.15 payment protection. For a
  cutting-tool distributor buying a large share of stock from dealers, ignoring
  this would raise most of the list against parties the rule does not reach.

`effective_tax_rate` is owner-set with **no default** — three entities, possibly
different constitutions and regimes, and a plausible-looking 0.25 would be a
made-up number behind a figure somebody plans a payment run around. Unset, the
watchlist still reports the deadline and the amount and simply shows no cost.

### Known limit, stated on every row

Section 15 runs from acceptance or deemed acceptance, which this platform does
not hold. `BillDoc` carries no link to a purchase order, so the goods-receipt
date that would be the better proxy cannot be joined. Every row reports
`deadline_start_basis` as `BILL_DATE`. `msme.deadline_for` accepts receipts for
when that link exists; the router deliberately does not pass an always-empty
resolver, because that would look like the feature exists.

---

## 3. GST input-credit blockage — not built, and why

**Not computable today.** `normalize_bill` is tested against `tax_amount` and
`tax_percentage` on a line — the codebase *knows* those fields are on the
payload — but `list_bills` and `list_invoices` do not pass them through, and
neither `BillDoc` nor `InvoiceDoc` has a tax column. The platform holds every
rupee of cost and revenue pre-tax and no rupee of tax.

That is the identical gap `_record_payable` already closed, with the same fix
available: *written from the same payload the cost pull already fetches — no
extra API call, no extra scope.*

Three measures, increasing in what they need:

1. **Cash-cycle financing cost — needs nothing.** Money out on
   `BillPaymentApplication.paid_on`, money in on `PaymentApplication.paid_on`,
   amount × days × `carrying_cost_annual_pct / 365`. Both sides fully ingested,
   and it is the larger number. Build first.
2. **Output-tax-vs-ITC float — needs one field per side.** Output GST is payable
   by the 20th of M+1 regardless of collection; ITC lands when the supplier
   files. The gap, carried until it unwinds, is a genuine interest-free loan to
   the government in rupees × days at a published rate.
3. **Accumulated / unclaimed ITC — needs GSTR-2B as a second source.** The
   `gst-reconciliation` skill produces this today, offline, from exported files.
   Nothing carries it back as a persisted, dated series, so it is a one-off
   report where the value is the trend.

**Do not use `SALES_TAX_RATE`** (0.18) for any of this. It is a flat headline
rate for totalling a quote before an ERP prices it; applied to a mixed-HSN book
it produces a plausible wrong number. The document's own tax is the only correct
source, and it is already on the wire.

---

## 4. 194Q — built, and smaller than expected

Section 206C(1H), the seller-side collection obligation that used to sit
opposite 194Q with each disapplying the other, was **omitted with effect from
1 April 2025**. The interaction that made this area genuinely hard is gone, so
what remains is one buy-side rule rather than a decision table.

`commercial/insight/withholding.py`: one event per supplier per financial year,
from `Σ BillDoc.total`. Not a running tally on a screen — the obligation begins
at the crossing and that is the useful moment.

**Silent until the gate is confirmed.** Whether *our own* turnover crossed the
statutory limit last year spans three legal entities and lives in Tally, not
here. `s194q_org_gate_met` is off by default and the endpoint reports that it is
*gated* rather than returning an empty list that reads as "nobody crossed".

Basis is stated and deliberately conservative: bill totals are GST-inclusive
until §3 lands, so a crossing fires slightly early. Early is the safe direction
for this alert, and saying so beats implying precision the figures lack.

---

## 5. The DPDP control map

A scoping point that shrinks the surface honestly: DPDP obligations attach to
**personal data of natural persons**. A company is not a Data Principal, and
most of what this platform holds — bills, margins, company GSTINs — is not
personal data at all. Where it genuinely is:

- **`User` rows** (email, name, password hash) — employees of the tenant. The
  clearest personal data here, excluded from the export and not under the DEK.
- **`Vendor.pan`** — for the proprietorships that make up a real share of an SME
  supplier book, that PAN is an individual's PAN. Plaintext.
- **`salesperson_name` / `salesperson_id`** — named individuals, ingested.
- **Break-glass justification free text**, which names staff.

| Obligation | Control that exists | Verdict |
|---|---|---|
| s.8(4) security safeguards | Per-tenant DEK envelope (`trust/keys.py`), Fernet for credentials, org-scoped queries, 404-not-403 | **Gap** — see #4 below |
| s.8(5)/(6) breach notification | Nothing. `AccessEvent` records *authorised* access, not compromise | **Gap, unmitigated** |
| s.8(7) erasure | `erasure.erase` — DEK destruction, signed receipt, tombstone | **Partial** — #1, #2 |
| s.5 / s.6 notice and consent | None in-platform | Tenant's obligation for their customers; **no notice or consent record for platform users** |
| s.6(1) purpose limitation | `trust/disclosure.ALLOWED` / `NEVER` is a published contract enforced by `check()` and `check_names()` against real payloads and asserted in tests; `ai/telemetry.py` records how a call went, never content; `ModelPayload` stores the text under the tenant's DEK | **Strongest control in the codebase** — one gap, #3, now closed |
| s.8(3) accuracy | No writeback, Zoho is the record, a re-sync rebuilds | **Clean. Nothing to fix.** |
| s.11–14 principal rights | `/trust/export`, owner-scoped, tenant-grain | Right for tenant-as-customer; **not** an individual access right, and no grievance officer named |
| s.9 children / s.10 SDF | N/A at B2B and at this volume | Watch if sold widely |

### The gaps, in order

**1. The export was not "everything".** `EXPORTED` was written when the platform
held customers, products and the analysis over them. The whole supply and
payables layer arrived afterwards and none of it was decided about — and
`_manifest` read the same list, so the signed erasure receipt attested to an
equally short inventory. **Closed:** split into `MANIFESTED` (every
tenant-scoped table, what the receipt covers) and `EXPORTED` (what travels),
both filled, each exclusion carrying its reason, and a test that fails when a
model with an `organization_id` is in neither list. Three tables are excluded
for size rather than secrecy — `business_events` and the two projections folded
from it are derived, rebuildable, and would be a download in the hundreds of
megabytes.

**2. Crypto-shredding erases identity, not data — and one identifier survives
it.** Only `NameVaultEntry.name_ciphertext` and `ModelPayload.payload_ciphertext`
are written under the DEK. `vault.py` concedes `customers.name` and
`products.name`. What it did not say: `customer_connector_records.gstin` is
plaintext and **indexed**, and `vendors.gstin` / `vendors.pan` were plaintext and
not vaulted at all. A GSTIN is a government identifier and sits in
`disclosure.NEVER`. **Still open** — closing it needs a blind index, because
`.gstin` is a live identity-matching key, not a display field. This is the gap
to close before the erasure claim is made to a buyer.

**3. Suppliers were absent from the entire trust layer** — not vaulted, no
pseudonym, and `check_names` scanned two thirds of a tenant's names so a payload
naming a supplier passed clean. Every argument in `vault.py` for splitting
customer names applies verbatim. **Closed.**

**4. One key does four jobs.** `CREDENTIAL_ENCRYPTION_KEY` is the credential
Fernet key, the KEK wrapping every DEK, the HMAC key deriving every pseudonym,
*and* the HMAC key signing every erasure receipt. Rotating it changes every
pseudonym (invalidating cached interpretations and breaking rehydration) and
makes every past erasure receipt fail verification — so the proof of a completed
erasure evaporates on a routine security action. The receipt is also HMAC'd with
the operator's own key, making it self-attested: fine as an internal record, not
proof to a third party. The default value is public in source, guarded only when
`APP_ENV == production`. **Still open.** An HKDF split into four purpose-derived
subkeys plus an asymmetric receipt signature would fix it, and both need a
re-wrap migration.

**5. `AI_LOG_PAYLOADS` defaults on** while `disclosure.log_result`'s docstring
says a deployment that has not enabled it should not quietly accumulate the
table. Defensible, but default and stated intent disagree.

The limit `keys.py` already states — the KEK is ours, so this defends against a
stolen backup and not against us — is correct and should stay worded exactly
that way. Customer-managed keys are a different control and a different tier,
and offering them before #2 and #4 are closed would sell a stronger claim on a
weaker foundation.

### Retention, and the DEK interaction

There is no retention policy anywhere. The interaction is real and has a clean
resolution that is worth writing down: Companies Act s.128 requires 8 years, GST
s.36 requires 72 months, reassessment reaches 10 years — and DEK destruction
makes retention physically impossible. That is safe here **only because this
platform holds no statutory record.** Zoho is the system of record, the platform
never writes back, and everything is rebuildable from a re-sync. That single
sentence is what makes the erasure feature offerable, and it depends entirely on
the never-write-back invariant. If the platform ever becomes a source of record
for anything, erasure needs a retention hold.

---

## 6. What sounds advanced but is wrong here

1. **Letting the AI say anything about tax.** A wrong margin costs a deal; a
   wrong tax position is the operator's liability. Surface a date, an amount and
   a basis, then stop. The `commercial/` ↔ `ai/` import boundary already
   enforces this mechanically.
2. **`balance × tax_rate` as the cost of missing 43B(h).** A timing difference,
   not a loss. Overstates by roughly an order of magnitude.
3. **Reading Zoho's `payment_terms` as a written 45-day agreement.** Understates
   exposure on exactly the suppliers the section exists to protect.
4. **Treating every Udyam-registered supplier as in scope.** Medium is out;
   registered traders are out. Ignore both and the list cries wolf on most rows.
5. **Inferring MSME status from anything** — turnover, name, bill size. UNKNOWN
   is a backlog row, not a value.
6. **Bill date as the s.15 start, silently.** Usually conservative, not always.
   State the basis on every row.
7. **Building a GST return, an ITC ledger, or anything that files.** Read-only
   on Zoho is the invariant the rest of the design rests on. Measure what the
   timing costs; do not reconcile the return.
8. **A "compliance dashboard".** Four items on four cadences do not share a
   screen. 43B(h) is a dated worklist; ITC blockage is a number on the cash
   screen; 194Q is a one-time-per-party event; s.234C belongs to the accountant.
9. **s.234 advance-tax interest from the profit run rate.** The weakest of the
   four and deliberately not built. The platform computes gross margin on synced
   trade in a bounded window, not taxable profit; bridging needs opex,
   depreciation, disallowances, regime and constitution, almost all of it in
   Tally. A run-rate estimate would be a confident wrong number about a
   statutory liability. At most, hand over the input clearly labelled as not
   being taxable profit.
10. **Customer-managed keys as the answer to operator trust.** Right control,
    wrong order — close what the current design already claims first.

---

## 7. Also assessed

**e-invoicing (IRN) and e-way bill as ingestion sources, not obligations.** IRN
is an obligation Zoho already discharges. As *sources*: the IRP-signed invoice
carries the authoritative line-level tax split and HSN — exactly the gap in §3 —
and is signed, which makes it better evidence than an export. The e-way bill
carries dispatch and delivery dates, the closest available proxy for the
**acceptance date** s.15 actually runs from, which is the weakest assumption in
§2. Want both for those reasons, not for compliance ones. Neither before MSME
status exists.

**Quote validity as a written option.** There is none — `QuoteDraft`,
`QuoteDecision` and `QuoteOutcome` have no `valid_until`. An offer with no
stated validity is revocable but *open* until revoked, so a quote priced against
a carbide cost that has since moved 15% stays acceptable and binds the
distributor below floor. The fix composes with what exists: a validity term, and
a lapsed quote that gets accepted re-enters the approval gate rather than being
honoured silently. Real money, low cost — but it belongs to the Quote Builder,
not to this category.

**Hash-chained approval log.** `ApprovalRequest` already stamps
`thresholds_version` and keeps an append-only thread, and already reasons about
laundering an approval. Adding `prev_hash` / `entry_hash` makes "signed under
`th_abc`" tamper-evident. Be honest about the ceiling: with the key the
operator's own and the head anchored nowhere, it detects accidental mutation,
not a determined operator. It becomes evidence when the head is published
somewhere the operator cannot rewrite — a daily head hash emailed to the owner
is enough. Worth it only for `ApprovalRequest` and `ErasureReceipt`, the two
things a human signed.

---

## 8. What was built, and what was left

**Built:** the 43B(h) watchlist and its capture backlog
(`commercial/insight/msme.py`, `VendorMsmeStatus`, three endpoints, a screen);
the 194Q crossing detector (`commercial/insight/withholding.py`); the
export/manifest split with its completeness test; suppliers into the vault,
the pseudonym map and the payload leak check.

**Left, with the reason:**

| Not built | Why |
|---|---|
| GST tax-field passthrough and the ITC float | A separate change with its own migration; §3 sequences it |
| GSTIN under the DEK | Needs a blind index — `.gstin` is a live matching key, not a display field |
| HKDF key split, asymmetric receipts | Touches every stored ciphertext; needs a re-wrap migration |
| s.234C | Deliberately never — see §6.9 |
| Quote validity, hash-chained approvals | Real, argued above, and not this category's job |
| MSMED s.16 contingent interest | Needs a fourth owner-set rate and only arises if the supplier claims; the watchlist is complete without it |
