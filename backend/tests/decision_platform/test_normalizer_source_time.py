"""Every normalizer that builds a ``SourceRef`` passes the source's own
creation stamp through it.

The contract publishes ``SourceRef.recorded_at`` as EXPECTED keyed on the
declaring type, so the marker reaches all nineteen entities (``domain/spec.py``
argues why that keying is right). ``normalize`` used to honour it in three
places: ``normalize_invoice``, ``normalize_bill`` and
``normalize_quote_document``. The other fourteen built a ``SourceRef`` without
it and dropped whatever the connector had carried — including
``normalize_bill_terms`` and ``normalize_invoice_terms``, which read *the same
payload* the two line-grain normalizers read ``created_time`` out of.

A connector that reads its ERP's creation stamp and a normalizer that throws it
away are indistinguishable downstream from an ERP that has no such concept,
which is the shape of the incident behind the whole spec module: PIE's own Zoho
client dropped ``created_time`` from three document projections, every row
landed unusable as evidence, every quote line answered INSUFFICIENT_EVIDENCE,
and the sync reported success.

Three properties, over every normalizer rather than a chosen few:

* a placeable stamp reaches the record;
* an absent one stays absent — **never** imputed from the document's own date,
  which is the substitution Prophet 21's adapter was found making. A bill dated
  before a quote but entered three weeks after it is not evidence the quoter
  had;
* an unplaceable one degrades to ``None`` rather than taking the document with
  it, and is not quietly replaced by the document date either.

The fourth test is the vacuity guard. The payload table below is written out —
one payload per normalizer, because the required keys genuinely differ — and
held against the module, so a normalizer added without one fails here rather
than inheriting a clean report it was never run against.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable, Iterator

import pytest
from pydantic import BaseModel

from app.domain.schemas import SourceRef
from app.ingestion import normalize

#: A document date deliberately far from the creation stamp below, so "not
#: imputed from ``date``" is a statement the assertions can actually make.
_DOC_DATE = "2026-05-18"
_STAMP = "2026-06-04T09:14:22Z"
_PLACED = datetime(2026, 6, 4, 9, 14, 22, tzinfo=timezone.utc)

#: A creation stamp with no UTC offset. ``clock.utc_stamp`` refuses one rather
#: than assuming a zone — the assumption that turns an offset into a
#: five-and-a-half-hour error on this book — and ``normalize._recorded_at``
#: drops what it refuses. Three registered connectors state their stamp this
#: way, so this is the live shape rather than a hypothetical one.
_UNPLACEABLE = "2026-06-04 09:14:22"

_AS_OF = date(2026, 6, 30)

_LINES = [{"line_item_id": "1", "item_id": "itm-1", "quantity": "2",
           "rate": "100.00", "item_total": "180.00"}]

#: ``normalizer -> a payload it accepts``. Every entry is the smallest payload
#: that normalises, plus whatever it takes to reach a second ``SourceRef`` (the
#: credit notes carry one per application as well as one on the header).
_PAYLOADS: dict[Callable[..., Any], dict[str, Any]] = {
    normalize.normalize_customer: {
        "contact_id": "cst-1", "contact_name": "Acme", "status": "active"},
    normalize.normalize_product: {"item_id": "itm-1", "name": "Insert"},
    normalize.normalize_vendor: {"contact_id": "vnd-1", "contact_name": "KMT"},
    normalize.normalize_stock: {"item_id": "itm-1", "track_inventory": True,
                                "stock_on_hand": "5"},
    normalize.normalize_invoice: {
        "invoice_id": "inv-1", "customer_id": "cst-1", "date": _DOC_DATE,
        "line_items": _LINES},
    normalize.normalize_invoice_terms: {
        "invoice_id": "inv-1", "customer_id": "cst-1", "date": _DOC_DATE},
    normalize.normalize_bill: {
        "bill_id": "bil-1", "vendor_id": "vnd-1", "date": _DOC_DATE,
        "line_items": _LINES},
    normalize.normalize_bill_terms: {
        "bill_id": "bil-1", "vendor_id": "vnd-1", "date": _DOC_DATE},
    normalize.normalize_payment: {
        "payment_id": "pay-1", "customer_id": "cst-1", "date": _DOC_DATE,
        "amount": "180.00"},
    normalize.normalize_vendor_payment: {
        "payment_id": "vpy-1", "vendor_id": "vnd-1", "date": _DOC_DATE,
        "amount": "180.00"},
    normalize.normalize_sales_order: {"salesorder_id": "so-1", "date": _DOC_DATE},
    normalize.normalize_purchase_order: {"purchaseorder_id": "po-1", "date": _DOC_DATE},
    normalize.normalize_quote_document: {"estimate_id": "est-1", "date": _DOC_DATE},
    normalize.normalize_location: {"location_id": "loc-1", "location_name": "Main"},
    normalize.normalize_item_location: {"item_id": "itm-1", "location_id": "loc-1",
                                        "on_hand": "5"},
    normalize.normalize_credit_note: {
        "creditnote_id": "cn-1", "customer_id": "cst-1", "date": _DOC_DATE,
        "invoices_credited": [{"invoice_id": "inv-1", "amount_applied": "50.00"}]},
    normalize.normalize_vendor_credit: {
        "vendor_credit_id": "vc-1", "vendor_id": "vnd-1", "date": _DOC_DATE,
        "bills_credited": [{"bill_id": "bil-1", "amount": "50.00"}]},
}

#: The two that take the snapshot day as a positional argument. Passed in
#: rather than read from a clock, so a pull that crosses midnight lands on one
#: date; the stamp under test is unaffected by it either way.
_NEEDS_AS_OF = {normalize.normalize_stock, normalize.normalize_item_location}


def _call(fn: Callable[..., Any], raw: dict[str, Any]) -> Any:
    return fn(raw, _AS_OF) if fn in _NEEDS_AS_OF else fn(raw)


def _source_refs(result: Any) -> Iterator[SourceRef]:
    """Every ``SourceRef`` a normalizer's return value carries.

    Normalizers return one DTO, a list of them, or a tuple of a document and
    its applications; walking the result rather than knowing which does what
    keeps this blind to the table above.
    """
    if isinstance(result, BaseModel):
        ref = getattr(result, "source_ref", None)
        if isinstance(ref, SourceRef):
            yield ref
        return
    if isinstance(result, (list, tuple)):
        for item in result:
            yield from _source_refs(item)


def _ids(fn: Callable[..., Any]) -> str:
    return fn.__name__


@pytest.mark.parametrize("fn", list(_PAYLOADS), ids=_ids)
def test_a_placeable_created_time_reaches_every_record(fn):
    refs = list(_source_refs(_call(fn, dict(_PAYLOADS[fn], created_time=_STAMP))))
    assert refs, f"{fn.__name__} built no SourceRef, so nothing was screened"
    for ref in refs:
        assert ref.recorded_at == _PLACED, (
            f"{fn.__name__} dropped the source's created_time: {ref!r}")


@pytest.mark.parametrize("fn", list(_PAYLOADS), ids=_ids)
def test_an_absent_created_time_is_not_imputed_from_the_document_date(fn):
    """Absent stays absent. The document's own date is a different clock and
    substituting it manufactures evidence the business never had."""
    refs = list(_source_refs(_call(fn, dict(_PAYLOADS[fn]))))
    assert refs, f"{fn.__name__} built no SourceRef, so nothing was screened"
    for ref in refs:
        assert ref.recorded_at is None, (
            f"{fn.__name__} invented a recorded_at from a payload with no "
            f"created_time: {ref.recorded_at!r}")


@pytest.mark.parametrize("fn", list(_PAYLOADS), ids=_ids)
def test_an_unplaceable_created_time_costs_the_stamp_and_not_the_document(fn):
    """``_recorded_at``'s existing bargain, now made at fourteen more sites.

    A stamp with no zone is refused by ``clock.utc_stamp`` and dropped here —
    and it must be dropped to ``None``, not to the document date, which would
    turn a refusal into a confident wrong answer.
    """
    result = _call(fn, dict(_PAYLOADS[fn], created_time=_UNPLACEABLE))
    refs = list(_source_refs(result))
    assert refs, f"{fn.__name__} built no SourceRef, so nothing was screened"
    for ref in refs:
        assert ref.recorded_at is None, (
            f"{fn.__name__} placed an unplaceable stamp: {ref.recorded_at!r}")


def test_every_normalizer_in_the_module_is_screened_here():
    """The vacuity guard, and the reason the table above is a table.

    A normalizer added to ``normalize`` without an entry would otherwise
    inherit a clean report from a screen it never ran through — the failure
    mode ``spec_conformance`` is built around and the one this file is a
    smaller copy of.
    """
    declared = {getattr(normalize, name) for name in dir(normalize)
                if name.startswith("normalize_")
                and callable(getattr(normalize, name))}
    assert declared, "no normalizers found — this file would screen nothing"
    missing = sorted(fn.__name__ for fn in declared - set(_PAYLOADS))
    assert not missing, (
        f"these normalizers have no payload here, so the source-time "
        f"properties are unscreened for them: {missing}")
