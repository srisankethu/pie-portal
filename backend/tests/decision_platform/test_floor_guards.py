"""The negotiation floor, and the guards that decide it has one at all.

`floor.py` computes the one economics number a salesperson is *allowed* to see —
F = cost x (1 + m_floor) — so what it refuses to compute matters as much as what
it returns. Its own header names the exploit: an item whose cost we have never
seen has no floor, and defaulting one to zero would make `P - F` the entire
selling price, "the largest single exploit the mechanism has to be closed
against".

A broad mutation sweep found the zero-cost half of that guard unprotected. Ten
tests touched this module and none of them supplied a placeholder cost, so
`cost > 0` could be loosened to `cost >= 0` and the suite stayed green — at
which point a bill booked at zero produces a floor of ₹0.00, every rupee of the
selling price reads as contribution, and the incentive pays out on it.

The same guard exists in `economics.line_economics` and *is* tested there
(`test_zero_or_placeholder_cost_is_treated_as_missing`). This is the mirror. The
two are written from the same rule and the comment in `floor.py` says so, which
is exactly the situation where only one of them ends up pinned.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.commercial import floor
from app.commercial.references import price_at_margin
from app.domain import models

ORG = "org_floor"
AS_OF = date(2026, 7, 1)
PRODUCT = "p_floor"


def _cost(session, unit_cost, *, on="2026-01-01", ref="B1", product=PRODUCT):
    session.add(models.CostRecord(
        organization_id=ORG, external_ref=f"{ref}:1", product_id=product,
        date=date.fromisoformat(on), qty=Decimal("100"),
        unit_cost=Decimal(str(unit_cost)),
        source_ref={"record_type": "bill", "record_id": ref}))
    session.flush()


def _resolve(session, **kw):
    return floor.resolve(session, ORG, PRODUCT, family=None, as_of=AS_OF, **kw)


# ── a floor exists only when a real purchase price does ─────────────────────
def test_a_costed_item_has_a_floor_above_cost(session):
    """The control for everything below: without it the refusals could all be
    passing because the fixture never produces a floor for anything."""
    _cost(session, 100)
    resolved = _resolve(session)
    assert resolved.floor_price > Decimal("100")
    # Operations zone: the type carries no cost and no multiplier, by design.
    assert not hasattr(resolved, "unit_cost")
    assert not hasattr(resolved, "m_floor")


@pytest.mark.parametrize("placeholder", ["0", "0.00", "-5"])
def test_a_placeholder_cost_yields_no_floor_rather_than_a_zero_one(
        session, placeholder):
    """Zero is the case that matters, and it is the boundary.

    A zero unit cost is an incomplete bill, not a purchase at no cost. Treated
    as real it makes the floor ₹0.00 — so the whole selling price reads as
    contribution and the incentive pays on it. Refused, with the reason.
    """
    _cost(session, placeholder)
    with pytest.raises(floor.FloorUnavailable) as raised:
        _resolve(session)
    assert "no purchase record" in str(raised.value).lower()


def test_an_item_with_no_cost_at_all_is_refused_not_defaulted(session):
    with pytest.raises(floor.FloorUnavailable):
        _resolve(session)


def test_a_later_real_cost_restores_the_floor_a_placeholder_denied(session):
    """The placeholder is skipped as a *basis*, not treated as poisoning the
    item — otherwise one bad bill would remove an item from the desk for good."""
    _cost(session, 0, on="2026-05-01", ref="B-bad")
    with pytest.raises(floor.FloorUnavailable):
        _resolve(session)
    _cost(session, 120, on="2026-06-01", ref="B-good")
    assert _resolve(session).floor_price > Decimal("120")


def test_the_cost_basis_is_the_one_in_force_on_the_day(session):
    """Same rule as `economics.cost_basis_asof`, including the boundary: a bill
    dated the day of the quote is the applicable one."""
    _cost(session, 100, on="2026-01-01", ref="B-old")
    _cost(session, 200, on=AS_OF.isoformat(), ref="B-today")
    on_the_day = floor.reconcile(session, ORG, PRODUCT, family=None, as_of=AS_OF)
    assert on_the_day.unit_cost == Decimal("200")

    day_before = floor.reconcile(session, ORG, PRODUCT, family=None,
                                 as_of=date(2026, 6, 30))
    assert day_before.unit_cost == Decimal("100")


def test_the_reconciliation_shows_the_workings_the_floor_hides(session):
    """Two types for two audiences: the salesperson's carries no cost, the
    manager's must, or nobody can check that the floor is sane."""
    _cost(session, 100)
    owner_view = floor.reconcile(session, ORG, PRODUCT, family=None, as_of=AS_OF)
    assert owner_view.unit_cost == Decimal("100")
    assert owner_view.m_floor > 0
    assert owner_view.floor_price == _resolve(session).floor_price


# ── the sibling guard, in the other direction ───────────────────────────────
def test_price_at_margin_refuses_a_cost_it_cannot_price_from():
    """`references.price_at_margin` guards the same placeholder cost, and the
    margin band alongside it. A zero cost returns a zero price; a margin of 1
    divides by zero."""
    assert price_at_margin(Decimal("100"), 0.2) == Decimal("125")
    # Boundaries: 0 margin is legal and means "sell at cost"; 1 is not.
    assert price_at_margin(Decimal("100"), 0.0) == Decimal("100")
    assert price_at_margin(Decimal("100"), 1.0) is None
    assert price_at_margin(Decimal("100"), -0.01) is None
    # …and the cost side, which is the boundary the sweep found unguarded.
    assert price_at_margin(Decimal("0"), 0.2) is None
    assert price_at_margin(Decimal("-1"), 0.2) is None


def test_price_at_margin_is_margin_on_price_not_a_markup():
    """`cost / (1 - m)`, not `cost * (1 + m)`. Getting it backwards understates
    every floor by several points, and both forms look plausible on screen."""
    assert price_at_margin(Decimal("100"), 0.25) == Decimal("400") / Decimal("3")
    assert price_at_margin(Decimal("100"), 0.25) != Decimal("125")
