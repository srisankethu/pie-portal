"""The 194Q crossing alert, and the gate that keeps it quiet.

The interesting behaviour here is the refusal: an obligation that depends on
our own prior-year turnover cannot be asserted from a platform that does not
hold it, and the detector's most important property is that it says nothing at
all until somebody confirms the gate.
"""
from __future__ import annotations

from datetime import date

from app.commercial.config import CommercialThresholds
from app.commercial.insight import withholding

OFF = CommercialThresholds()
ON = CommercialThresholds(s194q_org_gate_met=True)
NAMES = {"v1": "Acme Tools", "v2": "Second Supplier"}


def _p(vendor="v1", on=date(2026, 6, 1), amount=1_000_000.0):
    return withholding.Purchase(vendor_id=vendor, date=on, amount=amount)


def test_no_crossings_emitted_while_the_org_gate_is_unset():
    built = withholding.crossings([_p(amount=90_000_000.0)], NAMES,
                                  as_of=date(2026, 8, 1), th=OFF)

    assert built["gate_confirmed"] is False
    assert built["crossings"] == []
    assert "not in this platform" in built["note"]


def test_the_refusal_is_distinguishable_from_nobody_crossing():
    """An empty list means two different things and the caller must be able to
    tell them apart."""
    silent = withholding.crossings([_p(amount=90_000_000.0)], NAMES,
                                   as_of=date(2026, 8, 1), th=OFF)
    genuine = withholding.crossings([_p(amount=1_000.0)], NAMES,
                                    as_of=date(2026, 8, 1), th=ON)

    assert silent["crossings"] == genuine["crossings"] == []
    assert silent["gate_confirmed"] != genuine["gate_confirmed"]


def test_a_supplier_below_the_threshold_is_not_reported():
    built = withholding.crossings([_p(amount=1_000_000.0)], NAMES,
                                  as_of=date(2026, 8, 1), th=ON)

    assert built["crossings"] == []


def test_a_crossing_names_the_bill_date_that_took_them_over():
    purchases = [
        _p(on=date(2026, 5, 1), amount=3_000_000.0),
        _p(on=date(2026, 6, 1), amount=3_000_000.0),   # crosses 50L here
        _p(on=date(2026, 7, 1), amount=1_000_000.0),
    ]

    built = withholding.crossings(purchases, NAMES, as_of=date(2026, 8, 1), th=ON)
    row = built["crossings"][0]

    assert row["crossed"] is True
    assert row["crossed_on"] == "2026-06-01"
    assert row["purchases"] == 7_000_000.0
    assert row["excess"] == 2_000_000.0


def test_one_event_per_vendor_per_fy():
    """Four bills past the line are still one crossing."""
    purchases = [_p(on=date(2026, 5 + i, 1), amount=3_000_000.0) for i in range(4)]

    built = withholding.crossings(purchases, NAMES, as_of=date(2026, 9, 1), th=ON)

    assert len(built["crossings"]) == 1


def test_purchases_from_a_previous_financial_year_do_not_count():
    """The threshold is per financial year and resets on 1 April."""
    purchases = [
        _p(on=date(2026, 3, 31), amount=4_900_000.0),   # FY2025-26
        _p(on=date(2026, 4, 1), amount=4_900_000.0),    # FY2026-27
    ]

    built = withholding.crossings(purchases, NAMES, as_of=date(2026, 8, 1), th=ON)

    assert built["financial_year"] == "FY2026-27"
    assert built["crossings"][0]["purchases"] == 4_900_000.0
    assert built["crossings"][0]["crossed"] is False


def test_a_supplier_approaching_the_line_is_flagged_before_crossing_it():
    built = withholding.crossings([_p(amount=4_800_000.0)], NAMES,
                                  as_of=date(2026, 8, 1), th=ON)
    row = built["crossings"][0]

    assert row["approaching"] is True
    assert row["crossed"] is False
    assert row["excess"] == 0


def test_crossed_suppliers_rank_above_approaching_ones():
    purchases = [
        _p("v1", amount=4_800_000.0),                  # approaching
        _p("v2", amount=6_000_000.0),                  # crossed
    ]

    built = withholding.crossings(purchases, NAMES, as_of=date(2026, 8, 1), th=ON)

    assert [r["vendor_id"] for r in built["crossings"]] == ["v2", "v1"]


def test_threshold_basis_is_declared_as_tax_inclusive():
    built = withholding.crossings([_p(amount=6_000_000.0)], NAMES,
                                  as_of=date(2026, 8, 1), th=ON)

    assert "include GST" in built["basis_note"]
    assert "not tax advice" in built["basis_note"]


def test_crossings_are_deterministic_for_a_fixed_as_of():
    purchases = [_p(on=date(2026, 5, 1), amount=3_000_000.0),
                 _p(on=date(2026, 5, 1), amount=3_000_000.0)]

    first = withholding.crossings(purchases, NAMES, as_of=date(2026, 8, 1), th=ON)
    second = withholding.crossings(purchases[::-1], NAMES,
                                   as_of=date(2026, 8, 1), th=ON)

    assert first == second


def test_fy_bounds_span_april_to_april():
    assert withholding.fy_bounds("FY2026-27") == (date(2026, 4, 1), date(2027, 4, 1))
