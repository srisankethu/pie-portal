# The PIE canonical ingestion spec

`spec_24e093775f` · 19 entities · 300 field contracts (142 REQUIRED, 20 EXPECTED, 138 OPTIONAL)

> **Generated — do not edit any file in this directory by hand.**
> `scripts/spec_export.py` writes it from `backend/app/domain/schemas.py`,
> and `scripts/verify.sh` regenerates the whole directory on every run and
> fails the build if one byte differs. An edit here is reverted by the next
> person who runs the gate.

This is what a connector must produce for PIE: the records, the fields on
each, and which of those fields may legitimately be absent. One file per
entity, each a standard JSON Schema — validate a payload against it with
any off-the-shelf validator, in any language, with no knowledge of this
platform.

The version above is a content hash over all 19 documents
together. It is the stamp a sync run records, so a row in this platform
can say which contract it was written under, and
it moves on any change to any document at all. [Checking the stamp by
hand](#checking-the-stamp-by-hand) below re-derives it from these files.

## `x-pie-expected` — the part no validator will check for you

Read this before writing a line of connector code. It is the one thing
this spec says that a JSON Schema cannot.

JSON Schema has two states for a field: required, or not. Every real
omission this contract exists to catch lives in the second one — the field
is optional **because a source system may genuinely not have the concept**,
and a connector that has the value and drops it is, to every validator ever
written, indistinguishable from one whose source has nothing to send. That
is not hypothetical. It is what happened here: a client dropped the source
record's creation timestamp from three document types, every row landed
without it, every downstream answer degraded to INSUFFICIENT_EVIDENCE, and
the sync reported success.

So each record definition that declares such a field carries
`x-pie-expected` beside its own `required` array — an object mapping the
field name to the reason its absence is a defect:

```json
"required": ["system", "record_type", "record_id"],
"x-pie-expected": {
  "recorded_at": "When the source system recorded the document. …"
}
```

**What it obliges you to do.** For every field listed below, exactly one of
these is true of your connector, and you have to know which:

1. Your source exposes the value → **you send it.** Omitting it is a defect
   in your connector, not a property of your ERP, and nothing downstream
   will tell you: the rows load, the sync goes green, and the answers go
   quiet.
2. Your source genuinely has no such concept → **say so, in writing, to
   whoever integrates you.** Absence then means something, and this
   platform can count it rather than impute it.

There is no third case, and "we will map it later" is case 1 with the
defect still in it. A stock validator will ignore `x-pie-expected` entirely
— it is a vendor extension, which is exactly why it is safe to ship inside
the schema — so this check is yours to run.

| Field | Declared on | Why absence is a defect |
|---|---|---|
| `source_ref.recorded_at` | all 19 entities | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |
| `vendor_external_id` | `cost_record` | Who the line was bought from, copied down from the bill header. Every bill has a vendor, so absence here is a connector or history gap, not a fact about the trade. Lines without it fold as unattributable and supplier concentration is silently answerable only per product. |

## Conformance — the two steps

1. **Validate** your payload for an entity against `<entity>.json` with any
   JSON Schema validator. That settles types, formats, enum members and the
   `required` fields.
2. **Assert the expectations** in the table above yourself. Step 1 passes
   without them by design.

Every field is in one of three states, and the per-entity tables below
state which:

- **REQUIRED** — the schema will not accept the record without it.
- **EXPECTED** — the schema accepts the record without it, and this
  contract does not. See the section above.
- **OPTIONAL** — absent is a legitimate answer. Absent is **not** zero,
  not today's date, and not an empty string; if you do not have the fact,
  leave the field out.

Paths are dotted, and `[]` marks a list element — `source_ref.recorded_at`,
`applications[].document_date`. A status below `[]` is asked of each
element that is present; it never means the list must be non-empty.

## Versioning and compatibility

**The stamp is a content hash, not a semantic version.**
`spec_24e093775f` is sha256 over all 19 documents, truncated. It
moves on *any* change to any of them,
including a reworded description — deliberately, because a contract whose
stated meaning can be rewritten under a stable stamp is not a contract. It
does not encode a major, a minor, or an ordering: two stamps are equal or
they are not.

The compatibility rule is therefore a rule about **changes**, applied by
whoever makes one:

- **Additive is a minor bump.** A new OPTIONAL field, a new entity, a
  loosened constraint, a clarified description. A connector in the field
  keeps working untouched, and may adopt the addition when it likes.
- **A newly REQUIRED field or a semantic change is major.** Promoting a
  field to `required`, promoting one to `x-pie-expected`, tightening a
  constraint, or changing what an existing field *means* while leaving its
  name and type alone. Every connector must be revisited before it ships,
  and the last of those is the dangerous one: it breaks nothing a validator
  can see.

**Which kind a change was is answerable from these files**, without trusting
anyone's summary. The counts in the header line move as follows: REQUIRED or
EXPECTED going up is major; OPTIONAL going up alone is additive; anything
going down is major. Beyond that, `git diff` over this directory shows every
changed constraint, and a description that changed while no count moved is
the semantic case — read it.

**How a connector declares which version it implements.** There is no field
in this artifact for a connector to fill in, and inventing one would be a
number nothing checks. What exists instead is a comparison you can actually
make: record the `spec_` stamp you built and tested against, in your own
connector's documentation or manifest; this platform records, per sync run,
the stamp the rows were written under. When the two differ, the change is
classified by the rule above — the file diff is the evidence, not a
changelog entry somebody remembered to write.

## Checking the stamp by hand

These files are not a rendering of the hashed bytes — they **are** the
hashed bytes, one document per file, under the same key ordering. So the
stamp can be re-derived from this directory alone, by anyone with a
checkout and no dependencies:

```bash
python3 - <<'EOF'
import hashlib, json, pathlib
docs = {p.stem: json.loads(p.read_text())
        for p in pathlib.Path("docs/spec").glob("*.json")}
pre = json.dumps(docs, sort_keys=True, separators=(",", ":"))
print("spec_" + hashlib.sha256(pre.encode()).hexdigest()[:10])
EOF
```

That prints `spec_24e093775f`. A hash does not invert, so publishing the
pre-image is what makes the stamp explainable rather than merely
distinguishable — the same reason this platform publishes the serialised
form behind its other policy stamps.

## The entities

| Entity | Schema | Fields | REQUIRED | EXPECTED | OPTIONAL |
|---|---|---:|---:|---:|---:|
| [`bill`](#bill) | [`bill.json`](bill.json) | 15 | 6 | 1 | 8 |
| [`cost_record`](#cost_record) | [`cost_record.json`](cost_record.json) | 14 | 10 | 2 | 2 |
| [`credit_note`](#credit_note) | [`credit_note.json`](credit_note.json) | 13 | 6 | 1 | 6 |
| [`credit_note_application`](#credit_note_application) | [`credit_note_application.json`](credit_note_application.json) | 14 | 9 | 1 | 4 |
| [`customer`](#customer) | [`customer.json`](customer.json) | 12 | 6 | 1 | 5 |
| [`invoice`](#invoice) | [`invoice.json`](invoice.json) | 19 | 7 | 1 | 11 |
| [`location`](#location) | [`location.json`](location.json) | 13 | 6 | 1 | 6 |
| [`payment_receipt`](#payment_receipt) | [`payment_receipt.json`](payment_receipt.json) | 20 | 12 | 1 | 7 |
| [`product`](#product) | [`product.json`](product.json) | 16 | 6 | 1 | 9 |
| [`purchase_order`](#purchase_order) | [`purchase_order.json`](purchase_order.json) | 18 | 6 | 1 | 11 |
| [`quote_doc`](#quote_doc) | [`quote_doc.json`](quote_doc.json) | 31 | 7 | 1 | 23 |
| [`sales_order`](#sales_order) | [`sales_order.json`](sales_order.json) | 17 | 6 | 1 | 10 |
| [`sales_txn`](#sales_txn) | [`sales_txn.json`](sales_txn.json) | 15 | 11 | 1 | 3 |
| [`stock_location_snapshot`](#stock_location_snapshot) | [`stock_location_snapshot.json`](stock_location_snapshot.json) | 12 | 7 | 1 | 4 |
| [`stock_snapshot`](#stock_snapshot) | [`stock_snapshot.json`](stock_snapshot.json) | 14 | 6 | 1 | 7 |
| [`vendor`](#vendor) | [`vendor.json`](vendor.json) | 13 | 6 | 1 | 6 |
| [`vendor_credit`](#vendor_credit) | [`vendor_credit.json`](vendor_credit.json) | 13 | 6 | 1 | 6 |
| [`vendor_credit_application`](#vendor_credit_application) | [`vendor_credit_application.json`](vendor_credit_application.json) | 12 | 8 | 1 | 3 |
| [`vendor_payment`](#vendor_payment) | [`vendor_payment.json`](vendor_payment.json) | 19 | 11 | 1 | 7 |

Each section below states what the entity is — the DTO's own prose, as
published in the schema's `description` — and every field the contract
asks of it, nested records flattened. Types, formats, enum members and
constraints are in the JSON file beside it; they are not repeated here,
because a second copy of a type is a copy that will one day disagree.

### bill

[`bill.json`](bill.json)

A bill's payable terms, header grain. The companion to the
``CostRecordIn`` list the same document produces.

| Field | Status | Why absence is a defect |
|---|---|---|
| `external_ref` | REQUIRED |  |
| `number` | OPTIONAL |  |
| `vendor_external_id` | OPTIONAL |  |
| `date` | REQUIRED |  |
| `due_date` | OPTIONAL |  |
| `status` | OPTIONAL |  |
| `total` | OPTIONAL |  |
| `balance` | OPTIONAL |  |
| `source_attributes` | OPTIONAL |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### cost_record

[`cost_record.json`](cost_record.json)

One line of one bill: what was bought, from whom, and what it cost.
Bill-line grain — ``external_ref`` is ``{bill_id}:{line_id}``, and the
bill's own payable terms are ``BillIn``.

A purchase event, not a valuation. It states what was paid on a date for a
quantity and says nothing about what stock is worth now; the holding, and
the last purchase price the source keeps beside it, are ``StockSnapshotIn``.

``unit_cost`` is effective — net of the line discount — and is the figure
every cost consumer in this platform reads. ``rate`` is the same line's
pre-discount list rate, carried for audit only; sending it as ``unit_cost``
overstates cost on every margin computed from it.

| Field | Status | Why absence is a defect |
|---|---|---|
| `external_ref` | REQUIRED |  |
| `product_external_id` | REQUIRED |  |
| `vendor_external_id` | EXPECTED | Who the line was bought from, copied down from the bill header. Every bill has a vendor, so absence here is a connector or history gap, not a fact about the trade. Lines without it fold as unattributable and supplier concentration is silently answerable only per product. |
| `date` | REQUIRED |  |
| `qty` | REQUIRED |  |
| `unit_cost` | REQUIRED |  |
| `rate` | REQUIRED |  |
| `discount_percent` | OPTIONAL |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### credit_note

[`credit_note.json`](credit_note.json)

A credit note's header. The mirror of ``InvoiceIn``, opposite sign.

Deliberately the same shape as the receivable it reduces: an invoice is what
a customer owes, a credit note is what was given back, and two different
shapes for one ledger would mean two ways to ask what a customer's position
actually is.

``balance`` here is what remains *unapplied* — credit the customer holds but
which has not yet been set against any invoice. It is passed through exactly
as Zoho states it, never derived from ``total`` minus the applications read
below, because a refund against the credit note would make that subtraction
wrong in the direction that overstates the credit still available.

| Field | Status | Why absence is a defect |
|---|---|---|
| `external_ref` | REQUIRED |  |
| `number` | OPTIONAL |  |
| `customer_external_id` | OPTIONAL |  |
| `date` | REQUIRED |  |
| `status` | OPTIONAL |  |
| `total` | OPTIONAL |  |
| `balance` | OPTIONAL |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### credit_note_application

[`credit_note_application.json`](credit_note_application.json)

One credit note set against one invoice, on one date.

The grain a historical receivable is reconstructed at, and the reason this
table exists at all: today's outstanding balance already nets applied credit
(Zoho states it and ``state/reducers/receivables`` reads it), but "what was
owed on 31 March" cannot be answered from a balance that only describes now.
That answer is invoices raised, minus receipts applied, minus *this*.

``invoice_date`` is carried here rather than joined from ``InvoiceDoc``, for
the same reason ``PaymentApplication`` carries it: a credit note landing
today may settle an invoice raised before the sync window starts, and a join
would silently drop exactly the oldest positions.

| Field | Status | Why absence is a defect |
|---|---|---|
| `external_ref` | REQUIRED |  |
| `credit_note_external_ref` | REQUIRED |  |
| `customer_external_id` | OPTIONAL |  |
| `invoice_external_ref` | REQUIRED |  |
| `invoice_number` | OPTIONAL |  |
| `invoice_date` | OPTIONAL |  |
| `applied_on` | REQUIRED |  |
| `amount_applied` | REQUIRED |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### customer

[`customer.json`](customer.json)

One party this business sells to, as the source system holds it.

``external_id`` is that system's own id for the record, and it is the
identity: every ``customer_external_id`` elsewhere in this contract joins on
it, while a name is not a key — two connected companies can each hold an
"ABC Industries" and they are two different customers.

A source that keeps one contact list for both sides of the trade has to
split it. A supplier is ``VendorIn``, even where the ERP separates the two
by nothing more than a type flag on one record.

| Field | Status | Why absence is a defect |
|---|---|---|
| `external_id` | REQUIRED |  |
| `name` | REQUIRED |  |
| `status` | OPTIONAL |  |
| `first_seen` | OPTIONAL |  |
| `assigned_user_id` | OPTIONAL |  |
| `source_attributes` | OPTIONAL |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### invoice

[`invoice.json`](invoice.json)

An invoice's receivable terms, header grain. The mirror of ``BillIn``.

Deliberately the same shape: one is what a supplier is owed, the other what
a customer owes us, and the two answer the same question in opposite
directions. A different shape for the receivable side would mean two ways to
ask "what is outstanding and how overdue".

The companion to the ``SalesTxnIn`` list the same document produces —
``balance`` and ``due_date`` are facts about one invoice, and copying them
onto every line would make "what is outstanding" a de-duplication problem
instead of a sum.

| Field | Status | Why absence is a defect |
|---|---|---|
| `external_ref` | REQUIRED |  |
| `number` | OPTIONAL |  |
| `customer_external_id` | OPTIONAL |  |
| `date` | REQUIRED |  |
| `due_date` | OPTIONAL |  |
| `status` | OPTIONAL |  |
| `total` | OPTIONAL |  |
| `balance` | OPTIONAL |  |
| `sales_orders` | OPTIONAL |  |
| `sales_orders[].external_ref` | REQUIRED |  |
| `sales_orders[].number` | OPTIONAL |  |
| `sales_orders[].is_primary` | OPTIONAL |  |
| `source_attributes` | OPTIONAL |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### location

[`location.json`](location.json)

One place the business trades from. Zoho calls these locations; the books
call them branches, and the invoice payload carries both names for the same
id.

Not merely a label. Two branches of one company can hold **separate tax
registrations**, and where they do the location on a document is the fact
that decides which registration raised it. That is what makes "which branch
earned this" a real question rather than a reporting preference — the answer
is already in the record, not a grouping picked at read time.

| Field | Status | Why absence is a defect |
|---|---|---|
| `external_ref` | REQUIRED |  |
| `name` | REQUIRED |  |
| `kind` | OPTIONAL |  |
| `parent_external_ref` | OPTIONAL |  |
| `is_active` | OPTIONAL |  |
| `is_primary` | OPTIONAL |  |
| `tax_reg_no` | OPTIONAL |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### payment_receipt

[`payment_receipt.json`](payment_receipt.json)

One payment in, at receipt grain — the money that arrived, and which
documents it was set against.

One receipt settling four invoices is one record carrying four
``applications``: ``amount`` is the cash and the applications are how it was
distributed, so a connector emitting a receipt per invoice counts the same
money four times.

``applications`` may legitimately be empty — money received against no
invoice yet. Say that with ``is_advance`` and ``unapplied_amount`` rather
than leaving an empty list to carry the meaning.

| Field | Status | Why absence is a defect |
|---|---|---|
| `external_ref` | REQUIRED |  |
| `customer_external_id` | REQUIRED |  |
| `date` | REQUIRED |  |
| `amount` | REQUIRED |  |
| `mode` | OPTIONAL |  |
| `is_advance` | OPTIONAL |  |
| `unapplied_amount` | OPTIONAL |  |
| `applications` | OPTIONAL |  |
| `applications[].external_ref` | REQUIRED |  |
| `applications[].document_external_ref` | REQUIRED |  |
| `applications[].document_number` | OPTIONAL |  |
| `applications[].document_date` | REQUIRED |  |
| `applications[].document_due_date` | OPTIONAL |  |
| `applications[].amount_applied` | REQUIRED |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### product

[`product.json`](product.json)

One item as the catalogue holds it — the master record, not a price, a
cost or a position. What is on the shelf is ``StockSnapshotIn``; what it
cost is ``CostRecordIn``.

``category``, ``manufacturer``, ``source_item_type`` and
``source_item_category`` are carried in the source's own words and mapped
only at read time, so send them verbatim rather than translated — and leave
them out where the source keeps no such taxonomy rather than inferring one
from the item's name.

``manufacturer`` is who makes the item and never who it was bought from;
the supplier on a purchase is ``CostRecordIn.vendor_external_id``.

| Field | Status | Why absence is a defect |
|---|---|---|
| `external_id` | REQUIRED |  |
| `name` | REQUIRED |  |
| `uom` | OPTIONAL |  |
| `hsn` | OPTIONAL |  |
| `category` | OPTIONAL |  |
| `manufacturer` | OPTIONAL |  |
| `source_item_type` | OPTIONAL |  |
| `source_item_category` | OPTIONAL |  |
| `active` | OPTIONAL |  |
| `source_attributes` | OPTIONAL |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### purchase_order

[`purchase_order.json`](purchase_order.json)

One order placed on a supplier, and how much of it has arrived. Header
grain, and the supply-side mirror of ``SalesOrderIn``.

``ordered_qty`` and ``pending_qty`` are the document's own totals across
every line — the source's sum, in whatever units those lines carried — and
not a quantity for any one item: this record holds no lines at all.

``received_on`` is absent both when an order is still open and when a
receipt was never logged. The record cannot tell those apart, so do not
derive it from ``status`` to make the field look complete.

| Field | Status | Why absence is a defect |
|---|---|---|
| `external_ref` | REQUIRED |  |
| `number` | OPTIONAL |  |
| `vendor_external_id` | OPTIONAL |  |
| `date` | REQUIRED |  |
| `expected_date` | OPTIONAL |  |
| `status` | OPTIONAL |  |
| `received_status` | OPTIONAL |  |
| `ordered_qty` | OPTIONAL |  |
| `pending_qty` | OPTIONAL |  |
| `total` | OPTIONAL |  |
| `received_on` | OPTIONAL |  |
| `source_attributes` | OPTIONAL |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### quote_doc

[`quote_doc.json`](quote_doc.json)

One quote as an ERP raised it. What was offered, and how the ERP says it
ended.

The demand-side document the platform has never read. Sales orders are what
a customer committed to; this is everything that was *offered* — the ~290
estimates behind them, including the ones nobody ordered. Without it a win
rate has no denominator and a lost quote leaves no trace anywhere.

Two status fields, on purpose. ``source_status`` is the ERP's own word,
carried verbatim and never mapped on the way in; ``outcome`` is this
platform's classification of it. Keeping only the first would put the
WON/LOST mapping in every reader that ever asks, and the third copy of that
mapping is the one that reads ``expired`` as a loss. Keeping only the
second would make the classification unauditable — nothing left to group by
when somebody asks which statuses fell through.

**Nothing here carries a judgement.** No loss reason, no "lost to", no
note, no platform status: an ERP list row cannot know why a customer said
no, and a field for it would be a field somebody eventually fills in from a
payload that never held the answer. Why a quote was lost is a human fact
and lives on ``quote_outcomes``, which no sync writes. This type is the
second layer of that guarantee — the first being that the table it feeds
has no such column either.

``lines`` carries the breakdown where the pull bought the detail call, and is
empty where it did not — a resumed sync refreshes every header and re-reads
no lines, so empty means "not read on this pass" rather than "this quote had
none". The reader has to keep those apart; the caller that writes them does
too, which is why the sync only replaces stored lines when it has some.

No cost, no margin, no unit economics. ``total`` is the quote's own selling
total, which is what was put in front of the customer.

| Field | Status | Why absence is a defect |
|---|---|---|
| `external_ref` | REQUIRED |  |
| `number` | OPTIONAL |  |
| `source_reference` | OPTIONAL |  |
| `customer_external_id` | OPTIONAL |  |
| `customer_ref` | OPTIONAL |  |
| `date` | REQUIRED |  |
| `expires_on` | OPTIONAL |  |
| `source_status` | OPTIONAL |  |
| `outcome` | OPTIONAL |  |
| `decided_on` | OPTIONAL |  |
| `total` | OPTIONAL |  |
| `salesperson_external_id` | OPTIONAL |  |
| `client_viewed_at` | OPTIONAL |  |
| `source_attributes` | OPTIONAL |  |
| `lines` | OPTIONAL |  |
| `lines[].external_ref` | REQUIRED |  |
| `lines[].line_number` | OPTIONAL |  |
| `lines[].item_external_id` | OPTIONAL |  |
| `lines[].item_code` | OPTIONAL |  |
| `lines[].description` | OPTIONAL |  |
| `lines[].qty` | OPTIONAL |  |
| `lines[].unit` | OPTIONAL |  |
| `lines[].rate` | OPTIONAL |  |
| `lines[].amount` | OPTIONAL |  |
| `lines[].discount_percent` | OPTIONAL |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### sales_order

[`sales_order.json`](sales_order.json)

One customer order, header grain. The demand-side mirror of
``PurchaseOrderIn`` and deliberately the same shape.

| Field | Status | Why absence is a defect |
|---|---|---|
| `external_ref` | REQUIRED |  |
| `number` | OPTIONAL |  |
| `customer_external_id` | OPTIONAL |  |
| `date` | REQUIRED |  |
| `expected_ship_date` | OPTIONAL |  |
| `status` | OPTIONAL |  |
| `invoiced_status` | OPTIONAL |  |
| `shipped_status` | OPTIONAL |  |
| `total` | OPTIONAL |  |
| `salesperson_external_id` | OPTIONAL |  |
| `source_attributes` | OPTIONAL |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### sales_txn

[`sales_txn.json`](sales_txn.json)

One invoice line: what one customer bought, of one product, on one day.
Invoice-line grain — ``external_ref`` is ``{invoice_id}:{line_id}``, and the
invoice's own header, balance and due date are ``InvoiceIn``.

A line, never a document: an invoice of six lines is six of these beside one
``InvoiceIn``, and rolling them into one record discards the per-product
grain every number in this platform is computed at.

``unit_price`` and ``line_revenue`` are both net of the line discount and
before tax — what the customer actually paid for the goods. ``rate`` is the
same line's pre-discount list price, carried for audit only; sending it as
``unit_price`` overstates revenue, and so does a tax-inclusive
``line_revenue``.

| Field | Status | Why absence is a defect |
|---|---|---|
| `external_ref` | REQUIRED |  |
| `customer_external_id` | REQUIRED |  |
| `product_external_id` | REQUIRED |  |
| `date` | REQUIRED |  |
| `qty` | REQUIRED |  |
| `unit_price` | REQUIRED |  |
| `line_revenue` | REQUIRED |  |
| `rate` | OPTIONAL |  |
| `discount_percent` | OPTIONAL |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### stock_location_snapshot

[`stock_location_snapshot.json`](stock_location_snapshot.json)

What one item held at one location, on one day.

**Deliberately not columns on ``StockSnapshotIn``.** That record is
organization-grain — one row per item per day — and several readers count on
exactly that. Adding a location column would turn one row into one row per
location and silently multiply every existing total. Two grains, two
records, the same reason ``InvoiceDoc`` sits beside ``SalesTxn``.

| Field | Status | Why absence is a defect |
|---|---|---|
| `product_external_id` | REQUIRED |  |
| `location_external_ref` | REQUIRED |  |
| `as_of` | REQUIRED |  |
| `on_hand` | OPTIONAL |  |
| `available` | OPTIONAL |  |
| `asset_value` | OPTIONAL |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### stock_snapshot

[`stock_snapshot.json`](stock_snapshot.json)

What one item held across the whole organization, on one day. One row per
item per day; the same item at one location is ``StockLocationSnapshotIn``.

A position at an instant, not a movement. Nothing here is a receipt, an
issue or an adjustment, and no history can be reconstructed from it — a
source that reports only a current number leaves a series exactly as dense
as the days on which something wrote one down.

An absent quantity is unknown and never zero. That matters most on
``reorder_level``, where "no reorder point set" is not "reorder at zero",
and on ``tracked``: a service or other non-inventory item sets it false
rather than reporting a holding of nothing.

| Field | Status | Why absence is a defect |
|---|---|---|
| `product_external_id` | REQUIRED |  |
| `as_of` | REQUIRED |  |
| `on_hand` | OPTIONAL |  |
| `available` | OPTIONAL |  |
| `actual_available` | OPTIONAL |  |
| `reorder_level` | OPTIONAL |  |
| `purchase_rate` | OPTIONAL |  |
| `tracked` | OPTIONAL |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### vendor

[`vendor.json`](vendor.json)

One party this business buys from, as the source system holds it. The
buy-side counterpart of ``CustomerIn``, and a separate record rather than a
flag on one — a source that keeps a single contact list has to send each
side under its own entity.

A vendor is who was paid, which is not who made the goods:
``ProductIn.manufacturer`` is the maker, and for an authorised distributor
the two usually coincide without being the same fact.

``payment_terms_days`` of 0 is a real term, "due on receipt". Only absence
means the terms are unknown, so leave the field out rather than sending zero
for a vendor nobody has recorded terms for.

| Field | Status | Why absence is a defect |
|---|---|---|
| `external_id` | REQUIRED |  |
| `name` | REQUIRED |  |
| `gstin` | OPTIONAL |  |
| `pan` | OPTIONAL |  |
| `payment_terms_days` | OPTIONAL |  |
| `status` | OPTIONAL |  |
| `source_attributes` | OPTIONAL |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### vendor_credit

[`vendor_credit.json`](vendor_credit.json)

A vendor credit's header — money a supplier gave back, at document grain.

The buy-side mirror of ``CreditNoteIn``, and read for a different reason. A
customer credit note was needed to reconstruct a *past* receivable; a vendor
credit is read because material value returned against a supplier reduces
nothing the platform computes — not the cost of a line, not a principal's
slab base, not a supplier's credit-note rate. ``11-procurement.md`` measured
that on a single supplier document, one carrying a return large enough that
whether it nets off the slab base changes where the slab sits. One document
was enough to make the case, because the gap is structural rather than a
matter of size: no vendor credit reaches a computed number under any
treatment, so a larger one only makes the same hole wider.

**This is store-only, and deliberately so.** Nothing here adjusts
``CostRecord`` and nothing here feeds ``economics.line_economics``. Doing
that moves every per-line margin in the platform, and it turns on an
accounting question ``11-procurement.md`` §1 establishes cannot be settled
from inside this repository — whether a rebate is booked as income, as a
purchase reduction, or against inventory. Ingesting the evidence is what
lets that conversation happen against numbers rather than impressions.

``balance`` is what remains unapplied, passed through exactly as Zoho states
it and never derived as ``total`` minus the applications below: a refund
against the credit (``vendor_credit_refunds``) would make that subtraction
overstate the credit still available.

| Field | Status | Why absence is a defect |
|---|---|---|
| `external_ref` | REQUIRED |  |
| `number` | OPTIONAL |  |
| `vendor_external_id` | OPTIONAL |  |
| `date` | REQUIRED |  |
| `status` | OPTIONAL |  |
| `total` | OPTIONAL |  |
| `balance` | OPTIONAL |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### vendor_credit_application

[`vendor_credit_application.json`](vendor_credit_application.json)

One vendor credit set against one bill.

**Carries no application date, and that is a refusal rather than an
omission.** ``CreditNoteApplicationIn`` has ``applied_on`` because Zoho's
``invoices_credited`` states both the invoice's date and the application's.
``bills_credited`` states one ``date`` and does not say which it is, and the
observed evidence points at it being the bill's: on a single supplier credit
the eight applications carry eight distinct dates spread over five months,
while the document's own system comments record every one of them applied on
two days. A date that was genuinely the application's could not disagree with
the document's own record of when those applications were made. Storing it
under ``applied_on`` would put money on a timeline it never sat on.

Nothing this table is for needs it. A return reducing a principal's slab
base is dated by the credit's own header date; a bill-specific price credit
is placed by the bill it names. So the ambiguous field is dropped rather
than guessed, and a re-sync rebuilds this table from Zoho if a later reader
settles the question and wants the column — which is the whole point of
``ingestion/`` writing derived rows.

| Field | Status | Why absence is a defect |
|---|---|---|
| `external_ref` | REQUIRED |  |
| `vendor_credit_external_ref` | REQUIRED |  |
| `vendor_external_id` | OPTIONAL |  |
| `bill_external_ref` | REQUIRED |  |
| `bill_number` | OPTIONAL |  |
| `amount_applied` | REQUIRED |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |

### vendor_payment

[`vendor_payment.json`](vendor_payment.json)

One payment out. Amount is required — a payment with no amount is not a
payment, and defaulting it to zero would understate cash out silently.

| Field | Status | Why absence is a defect |
|---|---|---|
| `external_ref` | REQUIRED |  |
| `vendor_external_id` | OPTIONAL |  |
| `date` | REQUIRED |  |
| `amount` | REQUIRED |  |
| `mode` | OPTIONAL |  |
| `reference` | OPTIONAL |  |
| `applications` | OPTIONAL |  |
| `applications[].external_ref` | REQUIRED |  |
| `applications[].document_external_ref` | REQUIRED |  |
| `applications[].document_number` | OPTIONAL |  |
| `applications[].document_date` | REQUIRED |  |
| `applications[].document_due_date` | OPTIONAL |  |
| `applications[].amount_applied` | REQUIRED |  |
| `source_ref` | REQUIRED |  |
| `source_ref.system` | REQUIRED |  |
| `source_ref.record_type` | REQUIRED |  |
| `source_ref.record_id` | REQUIRED |  |
| `source_ref.line_id` | OPTIONAL |  |
| `source_ref.recorded_at` | EXPECTED | When the source system recorded the document. A row without it is not usable as point-in-time evidence: it is counted, never imputed, and every quote line that needed it answers INSUFFICIENT_EVIDENCE. A connector whose source exposes a creation timestamp and omits this is defective; one whose source has none must say so, because silence here is what the incident behind this module looked like. |
