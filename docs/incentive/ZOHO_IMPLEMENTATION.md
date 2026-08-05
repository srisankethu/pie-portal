# Zoho implementation — fields, validations and query tables

Per entity: **SLS 60063559751 · 4U 60036630487 · UPS 60069661597**.

This specifies what to build. The pipeline itself is separate work (Section 10
of the brief).

## Custom fields

| Object | Field | Type | Zone | Notes |
|---|---|---|---|---|
| Item | `cf_floor_price` | Currency | **Operations — visible** | F. Monthly-refreshed price list |
| Item | `cf_m_floor_family` | Decimal | **Owner only** | Field-level permission: owner profile only. This is the parameter that keeps cost hidden; exposing it inverts F |
| Item | `cf_item_family` | Picklist | Operations | Drives which `m_floor` applies |
| Item | `cf_standard_buy_price` | Currency | **Owner only** | The `Y` reference |
| Item | `cf_stock_age_days` | Number | Operations | From the inventory ageing report, refreshed nightly |
| Item | `cf_aged_causer` | Lookup (User) | Operations | Q1(d)/Q3(b). Set at PO, carried to the item |
| Sales Order | `cf_third_party_incentive` | Currency | Operations | `K`. **Mandatory before invoicing** |
| Sales Order | `cf_k_form` | Picklist | Operations | cash / gift / travel / consultancy / other |
| Sales Order | `cf_k_declared_at` | DateTime | Operations | Auto-stamped on first save with `K > 0` |
| Sales Order | `cf_customer_po_ref` | Text | Operations | G2 gate |
| Contact | `cf_customer_group_gstin` | Text | Operations | Q7 group resolution |
| Contact | `cf_is_restricted` | Checkbox | Operations | **PSU / government / defence.** Drives the I2 hard block |
| Contact | `cf_live_contacts` | Number | Operations | RSI contact depth, verified annually |
| PO / Bill | `cf_vendor_yield` | Currency | Operations | `Y` |
| PO / Bill | `cf_y_proof_type` | Picklist | Operations | credit_note / debit_note / vendor_mail / po_price_delta |
| PO / Bill | `cf_y_proof_attachment` | File | Operations | **Mandatory when `cf_vendor_yield` > 0** |
| PO / Bill | `cf_commitment_qty` | Number | Operations | Q3(b). Blank unless the discount was commitment-linked |
| Expense | `cf_toolkit_customer` | Lookup (Contact) | Operations | Toolkit attribution |
| Expense | `cf_toolkit_category` | Picklist | Operations | trial / study / training / VMI / support |

## Validation rules — the ones that are load-bearing

### I2 — the PSU hard block (make this unsaveable, not merely flagged)

On **Sales Order**, a validation that blocks save:

```
IF (Contact.cf_is_restricted == true && cf_third_party_incentive > 0)
    BLOCK SAVE
    "A third-party incentive cannot be recorded against a PSU, government or
     defence-supply-chain customer. The Prevention of Corruption Act covers the
     giver, and the realistic consequence is GeM blacklisting. This is a system
     block, not an approval step — there is no one to escalate to."
```

Implement as a **Zoho Books validation rule on the record, not a Deluge warning
on the form.** A warning can be dismissed and an API write bypasses the form
entirely. The engine mirrors this in `models.ThirdPartyIncentive`, so a record
that somehow reaches the pipeline still cannot produce a payout — but the block
belongs at the point of entry, because that is where the legal exposure is.

### K must be declared before invoicing

```
ON Invoice create FROM Sales Order:
    IF (SO.cf_third_party_incentive > 0 && SO.cf_k_declared_at > SO.invoice_date)
        BLOCK
```

Declaring after the fact is not declaring. The whole point of the pre-invoice
requirement is that the number was committed to before anyone knew whether it
would be noticed.

### Y requires a document

```
IF (cf_vendor_yield > 0 && cf_y_proof_attachment is empty)  BLOCK SAVE
```

### G2 data integrity — at order entry, not at payout

```
IF (line item not in Item master)                 BLOCK
IF (Item.cf_floor_price is empty or <= 0)         BLOCK
IF (Item.hsn_or_sac is empty)                     BLOCK
IF (cf_customer_po_ref is empty)                  WARN, block on approval
```

A floor of zero would pay the full selling price as contribution and make every
unmastered item a jackpot. This is the gate that pays for itself against the
existing master-hygiene backlog.

## Zoho Analytics query tables

Monthly refresh unless noted.

| Table | Grain | Feeds |
|---|---|---|
| `qt_invoice_lines` | invoice line | `InvoiceLine` — with `floor_price` joined at invoice date, not today |
| `qt_receipts` | payment application | `Payment` — the invoice-level application, not the payment header |
| `qt_customer_group` | GSTIN | Q7 resolution. **Build this first** — everything else keys off it |
| `qt_rsi` | customer group | RSI, six components |
| `qt_rolling12_caf` | customer group × month | Baselines, with `is_aged_stock` excluded |
| `qt_aged_stock` | item | Age bands, causer, current on-hand. Nightly |
| `qt_vendor_yield` | PO | `Y`, commitment quantity, sold-to-date |
| `qt_trials` | trial | Q2. Needs a Trial custom module — Books has no native object |
| `qt_gate_status` | salesperson × period | **Owner zone dashboard** |
| `qt_points_ledger` | salesperson × customer × period | Operations dashboard |

## The row to get right at build time

> **Salesperson dashboard — operations zone. Points and prices only. `F` and
> `P` are visible; cost, `m_floor`, gross profit and margin are not.**

Build the dashboard from `qt_points_ledger`, and **build that query table
without a cost column at all** — not a hidden column, not a column with a
permission on it. The same pattern the quote-builder split already uses, and
the same reason: a permission is a setting somebody can change, whereas a
column that does not exist cannot be exposed by a future report, a CSV export
or an API call.

If the ledger table has a cost column "for reconciliation", I1 is already
broken and it is only a matter of time.

## Deluge posting

Two functions, both idempotent on `(invoice_id, line_id)`:

- **On invoice create/update** → post banked CAF to the points ledger.
- **On payment receipt** → post the `c(d)` conversion against the banked entry.

Idempotent because Zoho will retry, and a points ledger that double-posts on a
retry is a payout error nobody will find until somebody disputes their payslip.

## Sequencing

Phase 0 order matters. `qt_customer_group` first (everything keys off it), then
`cf_floor_price` for the top 500 SKUs by contribution, then the `K` and `Y`
capture fields, then RSI and the opening baselines. **Do not start the shadow
run until F is published** — a shadow run against an incomplete floor schedule
measures the schedule, not the salespeople.
