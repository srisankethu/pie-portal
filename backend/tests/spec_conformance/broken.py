"""The connector that must fail, one violation at a time.

A suite nobody has watched fail is one that silently passes everything. This
codebase has the scar twice: a gate red on ``main`` for eight consecutive
merges that nobody read, and — in the same incident — a backend suite that had
stopped running at all while the build stayed green enough to merge. So every
check in ``harness.py`` is shown catching the thing it exists to catch, against
a connector built to break it.

Registered nowhere. It is not a ``ConnectorSpec`` and cannot be passed to
``erp.register``: it declares what a connector declares through
``harness.Declaration``, which is what the checks actually read.
``test_the_broken_connector_fails.py`` asserts it is absent from
``erp.catalog()`` too, because "not registered" is a claim worth holding rather
than a habit worth trusting.

Two shapes of violation, because a defect lives at one of two layers:

* **A broken source.** Canonical payloads with something wrong in them, driven
  through the real ``normalize``. This is what a connector written outside this
  repository actually gets wrong, and the headline case is here: a system that
  declares it records a source time and then does not send one.
* **A broken record.** A DTO built past its own validators, for the defects
  only a record can express — a line total that is the list rate while the unit
  price is net of a discount cannot be produced through ``normalize``, which is
  exactly why the check for it belongs on the record.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Optional

from app.domain import schemas

from .harness import UNDECLARED, Declaration, Emission

KEY = "broken_fixture"

_NOTE = "A fixture connector. Its ERP is imaginary and records everything."


def declaration(records_source_time: Any = True, note: str = _NOTE) -> Declaration:
    return Declaration(key=KEY, records_source_time=records_source_time,
                       source_time_note=note)


# ── a source speaking the canonical shape, with a seam for each defect ───────
def _customer() -> dict[str, Any]:
    return {"contact_id": "C-1", "contact_name": "Vaya Precision LLC",
            "status": "active"}


def _vendor() -> dict[str, Any]:
    return {"contact_id": "V-1", "contact_name": "Kennametal Inc",
            "status": "active"}


def _item() -> dict[str, Any]:
    return {"item_id": "I-1", "name": "CNMG 120408 MP", "sku": "CNMG120408-MP",
            "status": "active", "item_type": "inventory"}


def _invoice() -> dict[str, Any]:
    """A discounted sales line, stated the way a careful connector states one:
    the list ``rate`` beside the post-discount ``item_total``."""
    return {"invoice_id": "INV-1", "customer_id": "C-1", "date": "2026-06-04",
            "currency_code": "USD", "total": "2250.00", "balance": "2250.00",
            "created_time": "2026-06-04T09:14:22Z",
            "line_items": [{"line_item_id": "1", "item_id": "I-1",
                            "quantity": "10", "rate": "250.00",
                            "item_total": "2250.00"}]}


def _bill() -> dict[str, Any]:
    return {"bill_id": "BILL-1", "vendor_id": "V-1", "date": "2026-05-18",
            "currency_code": "USD", "total": "1710.00",
            "created_time": "2026-05-18T07:55:10Z",
            "line_items": [{"line_item_id": "1", "item_id": "I-1",
                            "quantity": "12", "rate": "158.00",
                            "item_total": "1710.00"}]}


@dataclass
class Source:
    """A canonical-speaking source, with each defect as a switch.

    Written as switches on one source rather than as six sources because the
    thing under test is one connector making one mistake at a time — and
    because a clean run of this same object is what makes the failing runs mean
    something. ``harness`` reporting nothing against ``Source()`` is the
    control every other variant is read against.
    """

    #: Drop ``created_time``, the exact omission behind ``spec.py``.
    drop_source_time: bool = False
    #: Drop the bill header's vendor, the contract's other EXPECTED field.
    drop_vendor: bool = False
    #: State money as Python floats — what a translator doing its own
    #: arithmetic hands to ``normalize``.
    money_as_float: bool = False
    #: Price every line at the list rate, so nothing is discounted and the
    #: net-of-discount rule has nothing to read.
    no_discount: bool = False
    #: Mint a new document id on every listing, the way a connector keyed on a
    #: fetch timestamp rather than on the source record would.
    unstable_ids: bool = False
    _pulls: int = dc_field(default=0)

    def _document(self, build: Callable[[], dict[str, Any]], key: str) -> dict[str, Any]:
        raw = build()
        if self.drop_source_time:
            raw.pop("created_time", None)
        if self.drop_vendor:
            raw.pop("vendor_id", None)
        if self.no_discount:
            for line in raw["line_items"]:
                line["item_total"] = str(Decimal(line["rate"]) * Decimal(line["quantity"]))
        if self.money_as_float:
            for line in raw["line_items"]:
                # A discount resolved in binary floating point, which is what
                # ``base.money`` and ``Decimal(str(...))`` exist to avoid.
                line["item_total"] = float(line["rate"]) * float(line["quantity"]) * 0.9
        if self.unstable_ids:
            self._pulls += 1
            raw[key] = f"{raw[key]}-{self._pulls}"
        return raw

    def list_contacts(self):
        return [_customer()]

    def list_vendors(self):
        return [_vendor()]

    def list_items(self):
        return [_item()]

    def list_invoices(self, skip=None):
        return [self._document(_invoice, "invoice_id")]

    def list_bills(self, skip=None):
        return [self._document(_bill, "bill_id")]


# ── records built past their own validators ─────────────────────────────────
#: A field the record simply does not carry — not one set to ``None``, which is
#: a different statement and one the schema permits.
_OMIT = object()


def _source_ref(**over: Any) -> schemas.SourceRef:
    fields = {"system": KEY, "record_type": "invoice", "record_id": "INV-1",
              "line_id": "1", "recorded_at": None}
    fields.update(over)
    return schemas.SourceRef.model_construct(**fields)


def _sales_txn(**over: Any) -> schemas.SalesTxnIn:
    fields: dict[str, Any] = {
        "external_ref": "INV-1:1", "customer_external_id": "C-1",
        "product_external_id": "I-1", "date": date(2026, 6, 4),
        "qty": Decimal("10"), "unit_price": Decimal("225.00"),
        "line_revenue": Decimal("2250.00"), "rate": Decimal("250.00"),
        "discount_percent": Decimal("10"), "source_ref": _source_ref(),
    }
    fields.update(over)
    dropped = [name for name, value in fields.items() if value is _OMIT]
    for name in dropped:
        fields.pop(name)
    return schemas.SalesTxnIn.model_construct(**fields)


def emission(record: Any, entity: str = "sales_txn",
             payload: Optional[dict[str, Any]] = None) -> Emission:
    return Emission(connector=KEY, entity=entity, payload=payload or {}, record=record)


def a_record_missing_a_required_field() -> list[Emission]:
    return [emission(_sales_txn(date=_OMIT))]


def a_record_whose_money_is_a_float() -> list[Emission]:
    """A Decimal field holding a float, which only ``model_construct`` allows.

    Dumping it warns — pydantic's serializer says the value is not what the
    field declares — and the check is what turns that warning into a named
    failure instead of a line of terminal noise nobody reads. The test that
    runs it silences the warning for exactly that reason.
    """
    return [emission(_sales_txn(unit_price=225.0))]


def a_line_totalled_at_the_list_rate() -> list[Emission]:
    """The live defect class: the unit price is net of the discount and the
    line total is not, so revenue is overstated by exactly the discount."""
    return [emission(_sales_txn(line_revenue=Decimal("2500.00")))]


def a_record_with_no_usable_identity() -> list[Emission]:
    return [emission(_sales_txn(external_ref="INV-1:None",
                                source_ref=_source_ref(record_id="")))]


def a_record_claiming_another_system() -> list[Emission]:
    return [emission(_sales_txn(source_ref=_source_ref(system="netsuite")))]


UNDECLARED_SOURCE_TIME = declaration(records_source_time=UNDECLARED, note="")
