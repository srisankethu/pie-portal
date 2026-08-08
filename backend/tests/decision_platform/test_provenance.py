"""Evidence names the system a record actually came from.

Provenance is what makes a decision auditable — CLAUDE.md's whole trust story is
that every figure traces to a source record. Three places built that trace by
asserting ``"zoho"`` rather than reading it, and a fourth defaulted it in the
domain layer, so no adapter ever had to say where a record came from.

While there is one connector that is invisible: the guess is right. The moment
there are two it is a citation that confidently names the wrong system, which is
worse than one admitting it does not know — a reader can act on "unknown".
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.commercial.compute import _evidence
from app.commercial.economics import line_economics
from app.domain.schemas import SourceRef
from app.signals.base import UNRECORDED_SOURCE, CostRow, SaleRow, evidence_ref


def _sale(system: str | None) -> SaleRow:
    ref = {"record_type": "invoice", "record_id": "INV-1", "line_id": "L1"}
    if system is not None:
        ref["system"] = system
    return SaleRow(customer_id="c1", product_id="p1", date=date(2026, 3, 1),
                   qty=Decimal("2"), unit_price=Decimal("100"),
                   line_revenue=Decimal("200"), source_ref=ref,
                   external_ref="INV-1:L1")


def _cost(system: str) -> CostRow:
    return CostRow(product_id="p1", date=date(2026, 1, 1), qty=Decimal("10"),
                   unit_cost=Decimal("60"),
                   source_ref={"system": system, "record_type": "bill",
                               "record_id": "BILL-9", "line_id": "BL1"},
                   external_ref="BILL-9:BL1")


# ── the domain layer no longer guesses ──────────────────────────────────────
def test_a_source_ref_must_say_which_system_it_came_from():
    """It defaulted to "zoho", so forgetting to set it was silent."""
    with pytest.raises(ValidationError):
        SourceRef(record_type="invoice", record_id="INV-1")


def test_the_zoho_adapter_states_its_own_system():
    """`normalize` is the layer entitled to know which ERP this is."""
    from app.ingestion.normalize import normalize_customer

    out = normalize_customer({"contact_id": "1", "contact_name": "Bharat Forge"})
    assert out.source_ref.system == "zoho"


# ── evidence reads the record ───────────────────────────────────────────────
def test_a_signal_ref_reports_the_records_own_system():
    ref = evidence_ref({"system": "tally", "record_type": "invoice",
                        "record_id": "INV-1"})
    assert ref["source_system"] == "tally"


def test_a_signal_ref_admits_when_the_system_was_never_recorded():
    """Rows written before provenance existed carry none, and nothing can
    attribute them after the fact."""
    ref = evidence_ref({"record_type": "invoice", "record_id": "INV-1"})
    assert ref["source_system"] == UNRECORDED_SOURCE


def test_metric_evidence_cites_each_record_to_its_own_system():
    """The invoice and the bill need not have come from the same book."""
    line = line_economics(_sale("tally"), [_cost("zoho")])
    refs = _evidence([line])

    by_type = {r["record_type"]: r["source_system"] for r in refs}
    assert by_type == {"invoice": "tally", "bill": "zoho"}


def test_metric_evidence_does_not_invent_a_system():
    line = line_economics(_sale(None), [])
    refs = _evidence([line])

    assert [r["source_system"] for r in refs] == [UNRECORDED_SOURCE]


def test_a_costed_line_carries_both_provenances():
    line = line_economics(_sale("zoho"), [_cost("zoho")])

    assert line.source_ref is not None and line.cost_source_ref is not None
    assert line.source_ref["record_id"] == "INV-1"
    assert line.cost_source_ref["record_id"] == "BILL-9"
