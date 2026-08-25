"""Pure adaptation: canonical raw payloads → validated canonical DTOs.

Deterministic and side-effect free. Source data is preserved (external ids +
``source_ref`` retained); values are never invented beyond a documented,
deterministic fallback (line_revenue = qty × rate when the source omits a line
total). Malformed/missing rows raise :class:`NormalizationError`, which the sync
layer records and skips — never a silent drop, never a silent mutation.

**The payload shape this module reads is the platform's canonical wire shape.**
It descends from the Zoho Books API because Zoho was the first connector, and
it is kept deliberately: one normalizer means one place where dates, decimals
and the discount ladder are validated, for every connector alike. A Zoho pull
feeds Zoho's own payloads straight in; every other connector's adapter in
``ingestion/erp`` translates its native records into this shape first, and the
sync passes the connector's name as ``system`` so provenance says where a row
truly came from. Two normalizers that differ in one edge case would disagree on
a row nobody looks at — the translation layer varies per connector, the
validation must not.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from ..clock import utc_stamp
from ..domain.enums import CustomerStatus, QuoteDocOutcome
from ..domain.schemas import (BillIn, CostRecordIn, CreditNoteApplicationIn,
                             CreditNoteIn, CustomerIn, DocumentApplicationIn,
                             InvoiceIn, InvoiceSalesOrderRef, LocationIn,
                             PaymentReceiptIn, ProductIn, PurchaseOrderIn,
                             QuoteDocIn, SalesOrderIn, SalesTxnIn, SourceRef,
                             StockLocationSnapshotIn, StockSnapshotIn, VendorIn,
                             VendorCreditApplicationIn, VendorCreditIn,
                             VendorPaymentIn)

#: The default ``system`` stamped into provenance, for callers written when
#: Zoho was the only connector. The sync layer always passes its connection's
#: connector explicitly — ``test_multi_connector_sync`` pins that a non-Zoho
#: pull leaves no row claiming Zoho — so the default exists for the historical
#: call sites and their tests, not as something a new caller may lean on.
ZOHO = "zoho"

_HUNDRED = Decimal("100")


class NormalizationError(ValueError):
    """A source record could not be adapted. Carries a machine code + detail."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _require(raw: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in raw or raw[key] in (None, ""):
        raise NormalizationError("MISSING_FIELD", f"{ctx}: missing '{key}'")
    return raw[key]


def _parse_date(value: Any, ctx: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise NormalizationError("BAD_DATE", f"{ctx}: unparseable date {value!r}")


def _parse_decimal(value: Any, ctx: str, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise NormalizationError("BAD_NUMBER", f"{ctx}: {field} not numeric ({value!r})")


def normalize_customer(raw: dict[str, Any], *, system: str = ZOHO) -> CustomerIn:
    cid = _require(raw, "contact_id", "contact")
    status = str(raw.get("status", "active")).lower()
    return CustomerIn(
        external_id=str(cid),
        name=str(_require(raw, "contact_name", "contact")),
        status=CustomerStatus.ACTIVE if status == "active" else CustomerStatus.INACTIVE,
        source_ref=SourceRef(system=system, record_type="contact", record_id=str(cid)),
    )


def normalize_product(raw: dict[str, Any], *, system: str = ZOHO) -> ProductIn:
    iid = _require(raw, "item_id", "item")
    status = str(raw.get("status", "active")).lower()
    return ProductIn(
        external_id=str(iid),
        name=str(_require(raw, "name", "item")),
        uom=(str(raw["unit"]) if raw.get("unit") else None),
        hsn=(str(raw["hsn_or_sac"]) if raw.get("hsn_or_sac") else None),
        category=(str(raw["category_name"]) if raw.get("category_name") else None),
        manufacturer=(str(raw["manufacturer"]) if raw.get("manufacturer")
                      else None),
        active=(status == "active"),
        source_ref=SourceRef(system=system, record_type="item", record_id=str(iid)),
    )


def normalize_invoice(raw: dict[str, Any], *, system: str = ZOHO) -> list[SalesTxnIn]:
    """One invoice → one SalesTxnIn per line item (invoice-line grain).

    ``unit_price`` is the *net* selling price — after the line discount, which
    is what the customer actually paid and the only figure a margin may be
    computed from. ``rate`` (the original pre-discount list price) and
    ``discount_percent`` are preserved alongside it for audit; nothing
    downstream should compute economics from them.
    """
    inv_id = str(_require(raw, "invoice_id", "invoice"))
    customer_ext = str(_require(raw, "customer_id", f"invoice {inv_id}"))
    when = _parse_date(_require(raw, "date", f"invoice {inv_id}"), f"invoice {inv_id}")
    lines = raw.get("line_items") or []
    if not lines:
        raise NormalizationError("NO_LINES", f"invoice {inv_id}: no line_items")
    out: list[SalesTxnIn] = []
    for i, ln in enumerate(lines):
        line_id = str(ln.get("line_item_id") or i)
        ctx = f"invoice {inv_id} line {line_id}"
        product_ext = str(_require(ln, "item_id", ctx))
        qty = _parse_decimal(_require(ln, "quantity", ctx), ctx, "quantity")
        rate = _parse_decimal(_require(ln, "rate", ctx), ctx, "rate")
        net_price, discount_pct = _effective_unit_amount(rate, qty, ln, ctx)
        # Prefer the source line total; fall back to qty×net deterministically.
        # Both are post-discount, so revenue and unit_price cannot disagree.
        revenue = (_parse_decimal(ln["item_total"], ctx, "item_total")
                   if ln.get("item_total") not in (None, "") else qty * net_price)
        out.append(SalesTxnIn(
            external_ref=f"{inv_id}:{line_id}",
            customer_external_id=customer_ext,
            product_external_id=product_ext,
            date=when, qty=qty, unit_price=net_price, line_revenue=revenue,
            rate=rate, discount_percent=discount_pct,
            source_ref=SourceRef(system=system, record_type="invoice", record_id=inv_id, line_id=line_id),
        ))
    return out


def _parse_percent(value: Any, ctx: str, field: str) -> Decimal:
    """A Zoho line-item discount: either a bare number (percent) or a string
    like "50%" — both forms appear across Books API versions/organizations."""
    if isinstance(value, str):
        value = value.strip().rstrip("%")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise NormalizationError("BAD_DISCOUNT", f"{ctx}: {field} not numeric ({value!r})")


def _effective_unit_amount(rate: Decimal, qty: Decimal, ln: dict[str, Any],
                           ctx: str) -> tuple[Decimal, Optional[Decimal]]:
    """The actual per-unit amount after the line's discount, plus the discount
    percent for audit.

    Shared by bills (purchase cost) and invoices (net selling price) — the
    discount shapes Zoho emits are identical on both sides, and so is the
    correct resolution. ``rate * (1 - discount% / 100)`` is only the fallback;
    Zoho's own resolved values are preferred whenever present:

    1. ``item_total`` — the line's post-discount, pre-tax total. Most
       authoritative: Zoho has already resolved whatever discount shape it used.
    2. ``discount_amount`` — Zoho's own resolved monetary discount for the line,
       sidestepping whether ``discount`` itself is a percentage or an amount.
    3. ``discount`` — parsed as a percentage of ``rate`` (the documented formula).
    4. Nothing present — no discount; the amount equals the list rate.

    Taxes and document-level (non-line) adjustments are deliberately not
    touched: the existing definition of both cost and revenue here has always
    been pre-tax, and this does not change that.
    """
    item_total, discount_amount, discount_raw = (
        ln.get("item_total"), ln.get("discount_amount"), ln.get("discount"))
    discount_pct: Optional[Decimal] = None

    if item_total not in (None, "") and qty > 0:
        amount = _parse_decimal(item_total, ctx, "item_total") / qty
    elif discount_amount not in (None, "") and qty > 0:
        off = _parse_decimal(discount_amount, ctx, "discount_amount")
        amount = rate - (off / qty)
    elif discount_raw not in (None, ""):
        discount_pct = _parse_percent(discount_raw, ctx, "discount")
        if discount_pct < 0 or discount_pct > _HUNDRED:
            raise NormalizationError(
                "BAD_DISCOUNT", f"{ctx}: discount {discount_pct}% out of range [0, 100]")
        amount = rate * (Decimal("1") - discount_pct / _HUNDRED)
    else:
        amount = rate

    if amount < 0:
        raise NormalizationError(
            "BAD_DISCOUNT", f"{ctx}: resolved a negative unit amount ({amount})")

    if discount_pct is None and rate > 0:
        # Derive the audit percent from whichever authoritative value supplied
        # the amount, so it stays consistent regardless of which Zoho field it
        # came from — this is what lets the platform later say "rate ₹3,166,
        # discount 50%, effective ₹1,583" no matter which path computed it.
        discount_pct = (Decimal("1") - amount / rate) * _HUNDRED

    return amount, discount_pct


def normalize_bill(raw: dict[str, Any], *, system: str = ZOHO) -> list[CostRecordIn]:
    """One bill → one CostRecordIn per line item (bill-line grain).

    ``unit_cost`` is the *effective*, post-discount cost — what every margin,
    pricing and decision calculation must read. ``rate`` (the original,
    pre-discount list rate) and ``discount_percent`` are preserved alongside it
    purely for audit; nothing downstream should compute from them.
    """
    bill_id = str(_require(raw, "bill_id", "bill"))
    when = _parse_date(_require(raw, "date", f"bill {bill_id}"), f"bill {bill_id}")
    # From the header, onto every line. Not required: a bill from a supplier the
    # vendor pull did not return is still a cost, and dropping the line would
    # understate what an item cost in order to say who sold it.
    vendor_ext = str(raw["vendor_id"]) if raw.get("vendor_id") else None
    lines = raw.get("line_items") or []
    if not lines:
        raise NormalizationError("NO_LINES", f"bill {bill_id}: no line_items")
    out: list[CostRecordIn] = []
    for i, ln in enumerate(lines):
        line_id = str(ln.get("line_item_id") or i)
        ctx = f"bill {bill_id} line {line_id}"
        product_ext = str(_require(ln, "item_id", ctx))
        qty = _parse_decimal(_require(ln, "quantity", ctx), ctx, "quantity")
        rate = _parse_decimal(_require(ln, "rate", ctx), ctx, "rate")
        unit_cost, discount_pct = _effective_unit_amount(rate, qty, ln, ctx)
        out.append(CostRecordIn(
            external_ref=f"{bill_id}:{line_id}",
            product_external_id=product_ext,
            vendor_external_id=vendor_ext,
            date=when, qty=qty, unit_cost=unit_cost, rate=rate,
            discount_percent=discount_pct,
            source_ref=SourceRef(system=system, record_type="bill", record_id=bill_id, line_id=line_id),
        ))
    return out


# ── supply, stock and cash ───────────────────────────────────────────────────


def normalize_vendor(raw: dict[str, Any], *, system: str = ZOHO) -> VendorIn:
    vid = _require(raw, "contact_id", "vendor")
    status = str(raw.get("status", "active")).lower()
    terms = raw.get("payment_terms")
    return VendorIn(
        external_id=str(vid),
        name=str(_require(raw, "contact_name", "vendor")),
        gstin=(str(raw["gst_no"]) if raw.get("gst_no") else None),
        pan=(str(raw["pan_no"]) if raw.get("pan_no") else None),
        # 0 days is "due on receipt", a term somebody agreed. Only a genuinely
        # absent value is unknown, so the test is against None and "" — not
        # falsiness, which would erase every due-on-receipt supplier.
        payment_terms_days=(int(terms) if terms not in (None, "") else None),
        status=CustomerStatus.ACTIVE if status == "active" else CustomerStatus.INACTIVE,
        source_ref=SourceRef(system=system, record_type="vendor", record_id=str(vid)),
    )


def normalize_stock(raw: dict[str, Any], as_of: date, *, system: str = ZOHO) -> StockSnapshotIn:
    """The stock fields riding along on the item payload.

    ``as_of`` is passed in rather than read from the clock here so a whole pull
    lands on one date: an item list that takes four minutes must not produce
    two snapshot days because it crossed midnight halfway through.
    """
    iid = _require(raw, "item_id", "item stock")
    item_type = str(raw.get("item_type") or "").lower()
    return StockSnapshotIn(
        product_external_id=str(iid),
        as_of=as_of,
        on_hand=raw.get("stock_on_hand"),
        available=raw.get("available_stock"),
        actual_available=raw.get("actual_available_stock"),
        reorder_level=raw.get("reorder_level"),
        purchase_rate=raw.get("purchase_rate"),
        # A service has no shelf. Counting it as "zero on hand" would put every
        # service line in the out-of-stock list forever.
        tracked=bool(raw.get("track_inventory")) and item_type != "service",
        source_ref=SourceRef(system=system, record_type="item", record_id=str(iid)),
    )


def _applications(raw: dict[str, Any], payment_id: str, ctx: str, *,
                  listed_under: str, document_key: str,
                  number_key: str, application_key: str,
                  ) -> list[DocumentApplicationIn]:
    """Which documents one payment settled, from either side of the ledger.

    One function rather than two because the shape Zoho returns is the same on
    both — a list of documents, each carrying its own date, due date and the
    amount applied — and the only differences are the four key names. Two
    copies would be two places for the "no document date means no observation"
    rule to be relaxed, and it is the rule that keeps the median honest.
    """
    out: list[DocumentApplicationIn] = []
    for a in raw.get(listed_under) or []:
        document_id = str(a.get(document_key) or "")
        if not document_id:
            continue
        raw_date = a.get("date")
        if not raw_date:
            # Without the document's own date there is no days-to-pay to
            # compute. Dropping the application is right; defaulting it to the
            # payment date would manufacture a book that always pays same-day.
            continue
        due = a.get("due_date")
        out.append(DocumentApplicationIn(
            external_ref=str(a.get(application_key) or f"{payment_id}:{document_id}"),
            document_external_ref=document_id,
            document_number=(str(a[number_key]) if a.get(number_key) else None),
            document_date=_parse_date(raw_date, ctx),
            document_due_date=(_parse_date(due, ctx) if due else None),
            amount_applied=a.get("amount_applied"),
        ))
    return out


def normalize_payment(raw: dict[str, Any], *, system: str = ZOHO) -> PaymentReceiptIn:
    pid = _require(raw, "payment_id", "payment")
    ctx = f"payment {pid}"
    applications = _applications(
        raw, str(pid), ctx, listed_under="invoices", document_key="invoice_id",
        number_key="invoice_number", application_key="invoice_payment_id")
    return PaymentReceiptIn(
        external_ref=str(pid),
        customer_external_id=str(_require(raw, "customer_id", ctx)),
        date=_parse_date(_require(raw, "date", ctx), ctx),
        amount=raw.get("amount"),
        mode=(str(raw["payment_mode"]) if raw.get("payment_mode") else None),
        is_advance=bool(raw.get("is_advance_payment")),
        unapplied_amount=raw.get("unused_amount"),
        applications=applications,
        source_ref=SourceRef(system=system, record_type="customerpayment", record_id=str(pid)),
    )


def normalize_sales_order(raw: dict[str, Any], *, system: str = ZOHO) -> SalesOrderIn:
    """One customer order. Header grain, mirroring ``normalize_purchase_order``.

    ``expected_ship_date`` is left as ``None`` when Zoho has none rather than
    derived from the order date plus an assumed lead time: "we did not promise a
    date" and "we promised this date" are different facts, and only one of them
    can make an order late.
    """
    soid = _require(raw, "salesorder_id", "sales order")
    ctx = f"sales order {soid}"
    ship = raw.get("shipment_date")
    return SalesOrderIn(
        external_ref=str(soid),
        number=(str(raw["salesorder_number"]) if raw.get("salesorder_number") else None),
        customer_external_id=(str(raw["customer_id"]) if raw.get("customer_id") else None),
        date=_parse_date(_require(raw, "date", ctx), ctx),
        expected_ship_date=(_parse_date(ship, ctx) if ship else None),
        status=str(raw.get("status") or ""),
        invoiced_status=(str(raw["invoiced_status"]) if raw.get("invoiced_status") else None),
        shipped_status=(str(raw["shipped_status"]) if raw.get("shipped_status") else None),
        total=raw.get("total"),
        salesperson_external_id=(str(raw["salesperson_id"])
                                 if raw.get("salesperson_id") else None),
        source_ref=SourceRef(system=system, record_type="salesorder", record_id=str(soid)),
    )


def _parse_timestamp(value: Any, ctx: str, field: str) -> datetime:
    """A source timestamp as an aware UTC ``datetime``.

    Placement is delegated to ``clock.utc_stamp`` rather than parsed here, so
    there is one answer to "can this stamp be put on the UTC line" — it already
    refuses a naive stamp instead of assuming a zone, which is the assumption
    that turns an offset into a five-and-a-half-hour error on this book.

    Raises rather than returning ``None`` for a value that is present and
    unplaceable, following ``_parse_date``'s rule for a present-but-malformed
    date: absent is the caller's business, malformed is a defect and is
    reported. It matters more here than usual because the one field that uses
    this is read downstream as "the customer never opened it" when it is null,
    so a silent ``None`` would not be a missing timestamp — it would be a
    false statement about the customer.
    """
    placed = utc_stamp(value)
    if placed is None:
        raise NormalizationError(
            "BAD_TIMESTAMP", f"{ctx}: {field} is not a placeable timestamp ({value!r})")
    return datetime.strptime(placed, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


#: Per source system: the status words that mean "the customer accepted this"
#: and the ones that mean "the customer said no". Two positive allowlists, and
#: nothing outside them decides anything.
#:
#: A system with no entry here gets ``(frozenset(), frozenset())`` from the
#: lookup below and therefore yields UNRECORDED for every row and LOST for
#: none. That is the right default for a connector whose vocabulary nobody has
#: read yet: it under-claims, and the counts say so.
_QUOTE_VOCABULARY: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    # Zoho Books. ``invoiced`` is a won quote already billed; ``accepted`` is
    # the customer saying yes with no invoice raised yet.
    #
    # ``approved`` is deliberately absent, and it is the easiest one in this
    # vocabulary to get wrong. That is *our own* internal approval step, not
    # the customer accepting anything — reading it as WON would invent a
    # customer decision out of our own approval queue. Same class of error as
    # ``expired`` -> LOST, and harder to see, because the word sounds like the
    # customer's.
    #
    # ``expired``, ``sent``, ``viewed``, ``draft`` and ``pending_approval`` are
    # in neither set, which is the whole point of this module reading estimates
    # at all. That silence spans "nobody worked it", "the customer never
    # answered" and "we lost it to a competitor"; only the third is a loss, and
    # roughly 215 of this book's ~290 estimates sit in it. Calling them lost
    # would manufacture two hundred labels out of nothing.
    ZOHO: (frozenset({"invoiced", "accepted"}), frozenset({"declined"})),
}

# A word in both allowlists would make the answer depend on which membership
# test ran first, and it would look right. Checked at import, so the build
# fails where the mistake was made rather than in a win rate somebody reads six
# months later.
assert not any(won & lost for won, lost in _QUOTE_VOCABULARY.values())

#: Per source system: the status words that are positive evidence the quote was
#: put in front of the customer.
#:
#: An allowlist, and it has to be one. The tempting shape is a denylist — "not
#: a draft, therefore sent" — and it is wrong in exactly the way ``approved`` is
#: wrong above: Zoho's ``pending_approval`` and ``approved`` are *our own*
#: internal sign-off states, raised and awaiting a decision nobody outside this
#: business has been asked for. A denylist reads both as sent and writes a
#: send that never happened onto the one table a human owns. So the question
#: asked below is "is there evidence it was sent", never "is it explicitly a
#: draft" — the guard wraps the claim rather than the objection.
#:
#: ``expired`` is here because an offer can only lapse after it was made; it
#: says nothing about whether the customer answered, which is
#: ``classify_outcome``'s question and deliberately not this one.
#:
#: A system with no entry gets an empty set, so every one of its quotes reads
#: as not-known-to-be-sent. That under-claims for a connector whose vocabulary
#: nobody has mapped, which is the same default the two allowlists above take.
_QUOTE_SENT_STATUSES: dict[str, frozenset[str]] = {
    ZOHO: frozenset({"sent", "viewed", "expired", "accepted", "declined",
                     "invoiced"}),
}

# A quote the customer decided is a quote the customer saw. Stated as an
# import-time check rather than a comment, because the two dictionaries are
# edited by different features — a connector's WON word added without its sent
# word would make "decided but never sent" representable, and nothing
# downstream would know which of the two to believe.
assert all(
    (won | lost) <= _QUOTE_SENT_STATUSES.get(system, frozenset())
    for system, (won, lost) in _QUOTE_VOCABULARY.items())


def reached_the_customer(source_status: Any, *, system: str = ZOHO) -> bool:
    """Did this ERP status say the quote was actually put in front of anybody?

    The one place that answers it, so a reader outside ``ingestion`` never has
    to hold an ERP's vocabulary of its own — the same reason ``classify_outcome``
    exists rather than a status comparison in each caller. ``quote_service``
    asks this when it opens a human outcome row against an ERP-raised quote:
    SENT is a positive claim about a customer, and it may only be made where
    the source system made it first.

    Unknown word, unknown system, blank, ``None``: ``False``. Not "probably
    sent" — a status this platform cannot read is not evidence of anything, and
    the caller's honest opening state is the one that claims nothing.
    """
    return (str(source_status or "").strip().lower()
            in _QUOTE_SENT_STATUSES.get(system, frozenset()))



def classify_outcome(source_status: Any,
                     accepted_on: Optional[date],
                     declined_on: Optional[date],
                     *,
                     won: frozenset[str],
                     lost: frozenset[str]) -> tuple[QuoteDocOutcome, Optional[date]]:
    """A quote's source status and decision dates -> ``(outcome, decided_on)``.

    The only function in the platform that can return LOST for an ERP-raised
    quote, and it is written so the obvious mistake is unreachable rather than
    merely avoided.

    **Note the signature: there is no expiry date parameter.** An expired quote
    is the single most tempting thing to read as a loss — the customer did not
    order, the offer lapsed, it feels decided — and it is not one: it is a
    quote nobody chased, a customer who never replied, or a genuine loss, and
    those are three facts, not one. Keeping the expiry date *out of scope*
    means no expression in here has one to hand. Refusing to write the rule is
    weaker; this way there is nothing to refuse.

    Two positive allowlists and no else-branch that asserts anything. An
    unrecognised status — a signature variant, a new Zoho state, a connector
    whose vocabulary nobody has mapped — falls through to UNRECORDED, which is
    the true statement about it.

    **A decision must carry its date, on both sides.** An ``invoiced`` estimate
    with no ``accepted_date`` resolves to UNRECORDED, not WON. The codebase
    already treats an undated decision as unusable — ``DecidedQuote.decided_on``
    is a required date and the outcome reader skips a decided row without one —
    so this moves that rule one layer earlier, into the one place that can make
    the judgement. The cost is real and must not be silent: the caller counts
    every row whose status is in a vocabulary but whose date is missing, and
    ``source_status`` is stored verbatim beside the outcome so a GROUP BY shows
    exactly which statuses fell through.

    The ``is not None`` guards wrap the *claim* — a WON or a LOST — and never
    the objection. Absence of a date cannot produce a decided outcome here; it
    can only fail to produce one.
    """
    s = str(source_status or "").strip().lower()
    if s in lost and declined_on is not None:
        return QuoteDocOutcome.LOST, declined_on
    if s in won and accepted_on is not None:
        return QuoteDocOutcome.WON, accepted_on
    return QuoteDocOutcome.UNRECORDED, None


def dropped_an_undated_decision(q: QuoteDocIn, *, system: str = ZOHO) -> bool:
    """True when the ERP said this quote was decided and the date it needed to
    be usable was not there, so ``classify_outcome`` called it unrecorded.

    The price of the date gate, made visible. ``classify_outcome`` refuses to
    return WON or LOST without a decision date, which is the right rule — the
    outcome readers require the date and already skip a decided row without one,
    so admitting a dateless decision here would only move the discard further
    from the place that could explain it. But a rule that silently discards
    evidence is exactly the shape of defect §1 of ``CLAUDE.md`` is about, so the
    caller counts every row it costs and the run report carries the number.

    Lives here rather than in the sync because the vocabulary lives here. A
    second copy of "which statuses mean decided" in the caller would be the
    third copy of the WON/LOST mapping the two status columns exist to prevent,
    and it would drift the first time a connector's words are added.

    Deliberately answers *only* about the drop. A quote whose status is in
    neither allowlist is unrecorded because nobody decided it, not because
    anything was lost, and counting those together would put ~215 rows into a
    figure whose whole purpose is to be small enough to investigate.
    """
    if q.outcome is not QuoteDocOutcome.UNRECORDED:
        return False
    won, lost = _QUOTE_VOCABULARY.get(system, (frozenset(), frozenset()))
    return q.source_status.strip().lower() in (won | lost)


#: The quote fields this business's own Zoho configuration adds, plus the
#: branch it was raised at. Carried verbatim under the source's own key names:
#: the taxonomy is the business's, it already exists, and re-deriving a quote
#: type from anything else would be inventing a second answer to a question
#: somebody already answered on the document.
_QUOTE_ATTRIBUTE_KEYS = ("cf_quote_type", "cf_pricing_type", "cf_procurement_type",
                         "branch_id")


def normalize_quote_document(raw: dict[str, Any], *, system: str = ZOHO) -> QuoteDocIn:
    """One quote as its ERP raised it. Header grain, mirroring
    ``normalize_sales_order`` — an offer rather than a commitment.

    Keyed on ``estimate_id`` because that is the canonical wire shape (see the
    module docstring): it descends from Zoho, whose noun for this document is
    "estimate", and every other connector's adapter translates its own record
    into this shape before it arrives. The platform's own noun stays "quote"
    everywhere past this line, including in provenance — ``system`` already
    says which ERP the row came from, so the record type does not need to
    repeat a vendor's vocabulary.

    Nothing here decides anything a person should. The status becomes an
    outcome through ``classify_outcome`` and its two allowlists; the ERP's own
    word is carried through untouched beside it; and no reason, note or winner
    is read from the payload, because an estimate list row does not hold one.
    """
    qid = str(_require(raw, "estimate_id", "quote"))
    ctx = f"quote {qid}"
    expires = raw.get("expiry_date")
    accepted = raw.get("accepted_date")
    declined = raw.get("declined_date")
    viewed = raw.get("client_viewed_time")
    won, lost = _QUOTE_VOCABULARY.get(system, (frozenset(), frozenset()))
    outcome, decided_on = classify_outcome(
        raw.get("status"),
        _parse_date(accepted, ctx) if accepted else None,
        _parse_date(declined, ctx) if declined else None,
        won=won, lost=lost)
    return QuoteDocIn(
        # ``estimate_id``, not ``estimate_number``, and that choice is
        # load-bearing outside this function. Zoho's record id is system-wide,
        # so three connected books issue disjoint id spaces: that is why
        # ``quote_service.sole_erp_quote``'s refusal is unreachable on this
        # book, and why the capture grid can key its rows on the bare reference
        # and know they are unique. ``estimate_number`` is a per-company
        # sequence and is carried below as ``number`` for a person to read.
        # Re-keying this onto the human-readable number — which a capture
        # screen is exactly the kind of screen to ask for — changes both of
        # those properties, not the display. This is the one place the property
        # is asserted; the other two point here.
        external_ref=qid,
        number=(str(raw["estimate_number"]) if raw.get("estimate_number") else None),
        source_reference=(str(raw["reference_number"]) if raw.get("reference_number")
                          else None),
        customer_external_id=(str(raw["customer_id"]) if raw.get("customer_id") else None),
        # Carried beside the id, not instead of it. The id is what links; this
        # is what a person reads when the link does not resolve, and an empty
        # string is the honest value when the payload named nobody.
        customer_ref=str(raw.get("customer_name") or ""),
        date=_parse_date(_require(raw, "date", ctx), ctx),
        # No expiry is left as None rather than derived from the quote date
        # plus an assumed validity. "This offer lapses on the 14th" and "nobody
        # said when this lapses" are different facts, and only one of them can
        # put a quote on a chase list as overdue.
        expires_on=(_parse_date(expires, ctx) if expires else None),
        source_status=str(raw.get("status") or ""),
        outcome=outcome,
        decided_on=decided_on,
        total=raw.get("total"),
        salesperson_external_id=(str(raw["salesperson_id"])
                                 if raw.get("salesperson_id") else None),
        client_viewed_at=(_parse_timestamp(viewed, ctx, "client_viewed_time")
                          if viewed else None),
        # Only the keys the source actually set. An absent custom field is not
        # a category and must not become one: a quote with no cf_quote_type is
        # a quote nobody classified, which is a different fact from every
        # unclassified quote sharing a bucket called "other".
        attributes={k: raw[k] for k in _QUOTE_ATTRIBUTE_KEYS
                    if raw.get(k) not in (None, "")},
        source_ref=SourceRef(system=system, record_type="quote", record_id=qid),
    )


def normalize_bill_terms(raw: dict[str, Any], *, system: str = ZOHO) -> BillIn:
    """The payable header of a bill, from the payload ``normalize_bill``
    already receives.

    Separate from ``normalize_bill`` rather than folded into it because the two
    have different grains and different failure modes: a bill with no line
    items is useless as cost and is rejected there, but it is still money owed
    and must survive here. Returning a tuple from one function would tie the
    fates of both together.

    ``balance`` is passed through as Zoho states it. Deriving it from ``total``
    minus payments read elsewhere would be wrong the moment a credit note is
    applied to the bill, and wrong in the direction that overstates what is
    owed.
    """
    bill_id = str(_require(raw, "bill_id", "bill"))
    ctx = f"bill {bill_id}"
    due = raw.get("due_date")
    return BillIn(
        external_ref=bill_id,
        number=(str(raw["bill_number"]) if raw.get("bill_number") else None),
        vendor_external_id=(str(raw["vendor_id"]) if raw.get("vendor_id") else None),
        date=_parse_date(_require(raw, "date", ctx), ctx),
        # No terms on the bill means it cannot be aged. Left as None rather
        # than defaulted to the bill date, which would report every untermed
        # bill as overdue from the day it was raised.
        due_date=(_parse_date(due, ctx) if due else None),
        status=str(raw.get("status") or ""),
        total=raw.get("total"),
        balance=raw.get("balance"),
        source_ref=SourceRef(system=system, record_type="bill", record_id=bill_id),
    )


def _invoice_sales_orders(raw: dict[str, Any]) -> list[InvoiceSalesOrderRef]:
    """Every order this invoice bills against, from both places Zoho states it.

    The ``salesorders`` array is the truth: partial invoicing is normal here, so
    one order produces several invoices, and one invoice can consolidate several
    orders. The scalar ``salesorder_id`` is Zoho's own "primary" and is a strict
    subset of the array in every payload observed — but it is *unioned* rather
    than assumed to be, because the failure modes are not symmetric. A scalar
    that names an order the array omits is a link this platform would otherwise
    lose; a scalar already in the array simply gets flagged.

    Order is preserved and duplicates are dropped on the id, so an array that
    repeats an order does not produce two links to it. Nothing is inferred: an
    invoice naming no order at all returns an empty list, which is how a
    counter sale reaches ``order_to_cash`` as an unknown lag rather than a
    same-day one.
    """
    primary = str(raw["salesorder_id"]) if raw.get("salesorder_id") else None
    out: list[InvoiceSalesOrderRef] = []
    seen: set[str] = set()
    listed = list(raw.get("salesorders") or [])
    if primary and not any(str(so.get("salesorder_id") or "") == primary
                           for so in listed):
        listed.append({"salesorder_id": primary,
                       "salesorder_number": raw.get("salesorder_number")})
    for so in listed:
        ref = str(so.get("salesorder_id") or "")
        if not ref or ref in seen:
            continue
        seen.add(ref)
        number = so.get("salesorder_number")
        out.append(InvoiceSalesOrderRef(
            external_ref=ref,
            number=(str(number) if number else None),
            is_primary=(ref == primary),
        ))
    return out


def normalize_invoice_terms(raw: dict[str, Any], *, system: str = ZOHO) -> InvoiceIn:
    """The receivable header of an invoice, from the payload
    ``normalize_invoice`` already receives.

    The mirror of ``normalize_bill_terms``, and separate from
    ``normalize_invoice`` for the same reason: the two have different grains and
    different failure modes. An invoice with no line items is useless as revenue
    and is rejected there, but it is still money owed and must survive here.

    ``balance`` is passed through as Zoho states it. Deriving it from ``total``
    minus receipts read elsewhere would be wrong the moment a credit note is
    applied to the invoice, and wrong in the direction that overstates what is
    collectable — which is the direction that gets somebody chased for money
    they do not owe.
    """
    invoice_id = str(_require(raw, "invoice_id", "invoice"))
    ctx = f"invoice {invoice_id}"
    due = raw.get("due_date")
    return InvoiceIn(
        external_ref=invoice_id,
        number=(str(raw["invoice_number"]) if raw.get("invoice_number") else None),
        customer_external_id=(str(raw["customer_id"]) if raw.get("customer_id") else None),
        date=_parse_date(_require(raw, "date", ctx), ctx),
        # No terms on the invoice means it cannot be aged. Left as None rather
        # than defaulted to the invoice date, which would report every untermed
        # invoice as overdue from the day it was raised — and put a customer on
        # a collections list for an obligation nobody ever gave them.
        due_date=(_parse_date(due, ctx) if due else None),
        status=str(raw.get("status") or ""),
        total=raw.get("total"),
        balance=raw.get("balance"),
        sales_orders=_invoice_sales_orders(raw),
        source_ref=SourceRef(system=system, record_type="invoice", record_id=invoice_id),
    )


def normalize_location(raw: dict[str, Any], *, system: str = ZOHO) -> LocationIn:
    """One place the business trades from."""
    loc_id = str(_require(raw, "location_id", "location"))
    return LocationIn(
        external_ref=loc_id,
        name=str(raw.get("location_name") or ""),
        kind=(str(raw["type"]) if raw.get("type") else None),
        parent_external_ref=(str(raw["parent_location_id"])
                             if raw.get("parent_location_id") else None),
        # Absent means Zoho did not say, and an unstated flag is not a false
        # one: a location the pull cannot classify is better read as live than
        # quietly dropped out of every branch total.
        is_active=(True if raw.get("is_location_active") is None
                   else bool(raw["is_location_active"])),
        is_primary=bool(raw.get("is_primary_location")),
        tax_reg_no=(str(raw["tax_reg_no"]) if raw.get("tax_reg_no") else None),
        source_ref=SourceRef(system=system, record_type="location", record_id=loc_id),
    )


def normalize_item_location(raw: dict[str, Any], as_of: date, *, system: str = ZOHO) -> StockLocationSnapshotIn:
    """One item's holding at one location, on one day.

    Quantities and the valuation are passed through exactly as Zoho states
    them, including blanks. A blank is "the source did not say" and must not be
    read as zero — a location reporting no figure is not a location holding
    nothing, and treating it as empty would understate the shelf and overstate
    every return computed against it.
    """
    item_id = str(_require(raw, "item_id", "item location"))
    location_id = str(_require(raw, "location_id", f"item {item_id} location"))
    return StockLocationSnapshotIn(
        product_external_id=item_id,
        location_external_ref=location_id,
        as_of=as_of,
        on_hand=raw.get("on_hand"),
        available=raw.get("available"),
        asset_value=raw.get("asset_value"),
        source_ref=SourceRef(system=system, record_type="item_location",
                             record_id=item_id, line_id=location_id),
    )


def normalize_credit_note(
    raw: dict[str, Any], *, system: str = ZOHO,
) -> tuple[CreditNoteIn, list[CreditNoteApplicationIn]]:
    """One credit note → its header, and each invoice it was applied to.

    Returns a tuple rather than nesting the applications on the header, because
    the two have different failure modes: a credit note raised and not yet
    applied to anything is a complete, valid record with an empty list, and
    nesting would invite a reader to treat the empty list as a parse failure.

    **Why this does not reuse ``_applications``.** That helper drops any
    application whose document carries no date, which is right for days-to-pay:
    with no invoice date there is no interval to measure, and defaulting it
    would manufacture a book that always settles same-day. Here the same rule
    would be a defect. A dropped credit application does not lose an
    observation, it loses *money* — and it loses it in the direction that
    overstates what a customer historically owed, which is the precise error
    this table exists to correct. So an application with no invoice date is
    kept, with ``invoice_date`` left ``None``, and whatever reconstructs a
    timeline reports it as unplaceable rather than silently dropping it.
    """
    note_id = str(_require(raw, "creditnote_id", "credit note"))
    ctx = f"credit note {note_id}"
    customer_ext = str(raw["customer_id"]) if raw.get("customer_id") else None
    header = CreditNoteIn(
        external_ref=note_id,
        number=(str(raw["creditnote_number"]) if raw.get("creditnote_number") else None),
        customer_external_id=customer_ext,
        date=_parse_date(_require(raw, "date", ctx), ctx),
        status=str(raw.get("status") or ""),
        total=raw.get("total"),
        balance=raw.get("balance"),
        source_ref=SourceRef(system=system, record_type="credit_note", record_id=note_id),
    )
    applications: list[CreditNoteApplicationIn] = []
    for i, a in enumerate(raw.get("invoices_credited") or []):
        invoice_ref = str(a.get("invoice_id") or "")
        if not invoice_ref:
            # Credit with no invoice on it is unapplied credit, which the
            # header's own ``balance`` already reports. It is not an error and
            # it is not an application.
            continue
        amount = a.get("amount_applied")
        if amount in (None, ""):
            amount = a.get("credited_amount")
        if amount in (None, ""):
            # An application with no amount cannot reduce anything. Skipped
            # rather than read as zero, which would assert the credit was
            # applied for nothing.
            continue
        applied_raw = a.get("date") or raw.get("date")
        invoice_raw = a.get("invoice_date")
        applications.append(CreditNoteApplicationIn(
            external_ref=str(a.get("creditnote_invoice_id")
                             or f"{note_id}:{invoice_ref}:{i}"),
            credit_note_external_ref=note_id,
            customer_external_id=customer_ext,
            invoice_external_ref=invoice_ref,
            invoice_number=(str(a["invoice_number"]) if a.get("invoice_number") else None),
            invoice_date=(_parse_date(invoice_raw, ctx) if invoice_raw else None),
            applied_on=_parse_date(applied_raw, ctx),
            amount_applied=_parse_decimal(amount, ctx, "amount_applied"),
            source_ref=SourceRef(system=system, record_type="credit_note",
                                 record_id=note_id, line_id=invoice_ref),
        ))
    return header, applications


def normalize_vendor_credit(
    raw: dict[str, Any], *, system: str = ZOHO,
) -> tuple[VendorCreditIn, list[VendorCreditApplicationIn]]:
    """One vendor credit → its header, and each bill it was set against.

    A tuple rather than a nested list, for the same reason
    ``normalize_credit_note`` returns one: a credit raised and not yet applied
    is a complete, valid record with an empty list, and nesting invites a reader
    to treat the empty list as a parse failure. ``OP/C902273`` in the SLS book
    is exactly that — ₹3.87 lakh open against Renishaw with no bill named.

    **No application date is produced.** ``bills_credited`` carries one
    unlabelled ``date`` per row and the evidence says it is the bill's, not the
    application's; ``VendorCreditApplicationIn`` records the measurement. The
    field is dropped rather than guessed, because a date stored under the wrong
    name is worse than a date nobody has.
    """
    vc_id = str(_require(raw, "vendor_credit_id", "vendor credit"))
    ctx = f"vendor credit {vc_id}"
    vendor_ext = str(raw["vendor_id"]) if raw.get("vendor_id") else None
    header = VendorCreditIn(
        external_ref=vc_id,
        number=(str(raw["vendor_credit_number"])
                if raw.get("vendor_credit_number") else None),
        vendor_external_id=vendor_ext,
        date=_parse_date(_require(raw, "date", ctx), ctx),
        status=str(raw.get("status") or ""),
        total=raw.get("total"),
        balance=raw.get("balance"),
        source_ref=SourceRef(system=system, record_type="vendor_credit",
                             record_id=vc_id),
    )
    applications: list[VendorCreditApplicationIn] = []
    for i, a in enumerate(raw.get("bills_credited") or []):
        bill_ref = str(a.get("bill_id") or "")
        if not bill_ref:
            # A credit naming no bill is unapplied credit, which the header's
            # own ``balance`` already reports. Not an error, and not an
            # application.
            continue
        amount = a.get("amount")
        if amount in (None, ""):
            # An application with no amount cannot reduce anything. Skipped
            # rather than read as zero, which would assert the credit was
            # applied for nothing.
            continue
        applications.append(VendorCreditApplicationIn(
            external_ref=str(a.get("vendor_credit_bill_id")
                             or f"{vc_id}:{bill_ref}:{i}"),
            vendor_credit_external_ref=vc_id,
            vendor_external_id=vendor_ext,
            bill_external_ref=bill_ref,
            bill_number=(str(a["bill_number"]) if a.get("bill_number") else None),
            amount_applied=_parse_decimal(amount, ctx, "amount"),
            source_ref=SourceRef(system=system, record_type="vendor_credit",
                                 record_id=vc_id, line_id=bill_ref),
        ))
    return header, applications


def normalize_vendor_payment(raw: dict[str, Any], *, system: str = ZOHO) -> VendorPaymentIn:
    """One payment out, with the bills it settled. The amount is required.

    A payment row with no amount is malformed, not a zero-rupee payment, and
    defaulting it would understate cash out by exactly as much as the row was
    worth — silently, and in the direction that flatters liquidity.

    A payload carrying no ``bills`` list normalises to a payment with no
    applications, not to a failure: an older fixture, or a source that does not
    report the breakdown, still records money leaving the bank. It simply
    produces no observation about how long we take to pay, which is the honest
    outcome rather than a same-day one.
    """
    pid = _require(raw, "payment_id", "vendor payment")
    ctx = f"vendor payment {pid}"
    return VendorPaymentIn(
        external_ref=str(pid),
        vendor_external_id=(str(raw["vendor_id"]) if raw.get("vendor_id") else None),
        date=_parse_date(_require(raw, "date", ctx), ctx),
        amount=_parse_decimal(_require(raw, "amount", ctx), ctx, "amount"),
        mode=(str(raw["payment_mode"]) if raw.get("payment_mode") else None),
        reference=(str(raw["reference_number"]) if raw.get("reference_number") else None),
        applications=_applications(
            raw, str(pid), ctx, listed_under="bills", document_key="bill_id",
            number_key="bill_number", application_key="bill_payment_id"),
        source_ref=SourceRef(system=system, record_type="vendorpayment", record_id=str(pid)),
    )


def normalize_purchase_order(raw: dict[str, Any], *, system: str = ZOHO) -> PurchaseOrderIn:
    poid = _require(raw, "purchaseorder_id", "purchase order")
    ctx = f"purchase order {poid}"
    expected = raw.get("expected_delivery_date")
    # Zoho records receipts as a list; the last one is when the order finished
    # arriving. No receives at all means either still open or never logged —
    # left as None, because those two are different and the screen says so.
    receives = [r.get("date") for r in (raw.get("receives") or []) if r.get("date")]
    received_on = _parse_date(max(receives), ctx) if receives else None
    return PurchaseOrderIn(
        external_ref=str(poid),
        number=(str(raw["purchaseorder_number"]) if raw.get("purchaseorder_number") else None),
        vendor_external_id=(str(raw["vendor_id"]) if raw.get("vendor_id") else None),
        date=_parse_date(_require(raw, "date", ctx), ctx),
        expected_date=(_parse_date(expected, ctx) if expected else None),
        status=str(raw.get("status") or ""),
        received_status=(str(raw["received_status"]) if raw.get("received_status") else None),
        ordered_qty=raw.get("total_ordered_quantity"),
        pending_qty=raw.get("quantity_yet_to_receive"),
        total=raw.get("total"),
        received_on=received_on,
        source_ref=SourceRef(system=system, record_type="purchaseorder", record_id=str(poid)),
    )
