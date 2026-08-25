"""A cost sitting on another desk's quote line must not reach the assessment.

``POST /quote-intelligence/assess`` accepts a ``quote_id`` and reads the
server-held landed cost off that quote's lines, so the gate judges the same cost
the grid shows. ``store.line_cost`` is scoped to the *organization* and says so —
it refuses another tenant's quote. Inside one book it refused nothing, and the
store is one process-wide dict whose ids are enumerable (``q{run}-{counter}``),
so a salesperson could name any desk's quote and borrow the cost on its line.

**Why that is a leak rather than the residual §1 already accepts.**
``quote_intelligence`` uses ``item_master_cost`` as the *fallback* consulted when
the books hold no cost for the product. So borrowing a stranger's line did not
merely expose a boundary the caller was entitled to — it **manufactured one where
none existed**. Measured before the fix, on a product with no ``cost_records``
row and a borrowed cost of 500, sweeping ``proposed_price`` moved the verdict
twice:

    APPROVAL_REQUIRED -> REVIEW_EXPECTED  between 567.5 and 570.0
    REVIEW_EXPECTED   -> clear            between 587.5 and 590.0

which is ``cost/(1 - min_margin)`` = 500/0.88 = 568.18 and
``cost/(1 - margin_floor)`` = 500/0.85 = 588.24, exactly. The same sweep with no
``quote_id`` answered ``NO_COST_BASIS`` at every price. §1's budget is one
boundary per distinct action the recipient can take, and on another desk's
uncosted line they can take none.

The refusal **degrades rather than raises**: an id the caller does not hold
answers exactly as no id does. A distinct refusal would confirm the quote
exists, which is most of what an enumeration is after — the same reasoning
``_visible_customer_ref`` gives for blanking a name instead of returning 403.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.domain import models
from app.seed import SEED_PASSWORD
from app.store import Line, store

ORG = settings.DEFAULT_ORG_ID
SALES = "r.nair@pie.example"
MANAGER = "m.rao@pie.example"

#: A round number so the two boundaries it implies are easy to read in a
#: failure message: 500/0.88 = 568.18 and 500/0.85 = 588.24.
BORROWED_COST = 500.0

#: Prices either side of both boundaries. If the borrowed cost reaches the
#: assessment, the verdict changes across this span; if it does not, every one
#: of them answers the same way.
SWEEP = (100.0, 520.0, 575.0, 600.0, 1200.0)


def _hdr(client, email: str) -> dict:
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _codes(response) -> list[str]:
    assert response.status_code == 200, response.text
    line = (response.json().get("lines") or [{}])[0]
    return sorted(e.get("code") for e in line.get("exceptions") or [])


def _assess(client, email: str, *, price: float, quote_id: str | None):
    body: dict = {
        "customer": "Beta Works",
        "lines": [{"line_id": "L1", "product": "UNCOSTED-WIDGET",
                   "qty": 10, "proposed_price": price}],
    }
    if quote_id is not None:
        body["quote_id"] = quote_id
    return client.post("/api/v1/quote-intelligence/assess", json=body,
                       headers=_hdr(client, email))


@pytest.fixture()
def another_desks_quote(session):
    """A product the books do not cost, on a quote belonging to another desk."""
    session.add(models.Customer(customer_id="c2", organization_id=ORG,
                                external_id="c2", name="Beta Works",
                                assigned_user_id="usr_manager"))
    session.add(models.Product(product_id="p-uncosted", organization_id=ORG,
                               external_id="p-uncosted",
                               name="UNCOSTED-WIDGET", active=True))
    session.commit()
    # The premise of the whole test: the books hold no cost for this product, so
    # any boundary that appears can only have come from the borrowed line.
    assert session.query(models.CostRecord).filter_by(
        organization_id=ORG, product_id="p-uncosted").count() == 0

    quote = store.create("Beta Works", customer_id="c2", organization_id=ORG)
    quote.lines.append(Line(
        id="L1", raw="UNCOSTED-WIDGET x10", reqCode="UNCOSTED-WIDGET",
        reqDesc="UNCOSTED-WIDGET", reqQty=10, rel="EXACT",
        supplyCode="UNCOSTED-WIDGET", candidates=[], outcome="OK",
        semantics="EXACT"))
    quote.lines[0].cost = BORROWED_COST
    return quote


def test_a_salesperson_cannot_borrow_another_desks_cost_into_an_assessment(
        api_client, another_desks_quote):
    """The sweep must not move. A verdict that changes across these prices is a
    boundary, and a boundary here is a cost the books do not hold."""
    verdicts = {price: _codes(_assess(api_client, SALES, price=price,
                                      quote_id=another_desks_quote.id))
                for price in SWEEP}
    distinct = {tuple(v) for v in verdicts.values()}
    assert len(distinct) == 1, (
        "The assessment's verdict moved as the price swept across "
        f"{BORROWED_COST}/(1 - margin), so another desk's landed cost reached "
        f"it: {verdicts}")
    assert "NO_COST_BASIS" in next(iter(distinct)), (
        "A product the books do not cost must assess as having no cost basis, "
        f"whatever quote is named: {verdicts}")


def test_an_unheld_quote_id_assesses_exactly_as_no_quote_id_does(
        api_client, another_desks_quote):
    """Degrade, never refuse — otherwise the refusal itself says the quote is
    real, which is the enumeration the 404 elsewhere on this router avoids."""
    for price in SWEEP:
        borrowed = _assess(api_client, SALES, price=price,
                           quote_id=another_desks_quote.id)
        none_named = _assess(api_client, SALES, price=price, quote_id=None)
        assert borrowed.status_code == none_named.status_code == 200
        assert _codes(borrowed) == _codes(none_named), (
            f"at {price}, naming another desk's quote answered differently "
            "from naming no quote at all")


def test_the_desk_that_holds_the_quote_still_gets_its_own_line_cost(
        api_client, session):
    """The fix must not cost the legitimate case. A quote the caller holds still
    supplies its line cost, which is the whole reason the seam exists — the gate
    judging the same cost the grid shows."""
    session.add(models.Product(product_id="p-uncosted2", organization_id=ORG,
                               external_id="p-uncosted2",
                               name="UNCOSTED-WIDGET", active=True))
    session.commit()
    mine = store.create("Acme Engineering", customer_id="c1",
                        organization_id=ORG)
    mine.lines.append(Line(
        id="L1", raw="UNCOSTED-WIDGET x10", reqCode="UNCOSTED-WIDGET",
        reqDesc="UNCOSTED-WIDGET", reqQty=10, rel="EXACT",
        supplyCode="UNCOSTED-WIDGET", candidates=[], outcome="OK",
        semantics="EXACT"))
    mine.lines[0].cost = BORROWED_COST

    # A manager is never narrowed, so this reads the line cost and the verdict
    # moves across the boundary the cost implies.
    low = _codes(_assess(api_client, MANAGER, price=100.0, quote_id=mine.id))
    high = _codes(_assess(api_client, MANAGER, price=1200.0, quote_id=mine.id))
    assert low != high, (
        "the held quote's line cost no longer reaches the assessment at all — "
        f"the fix went too far: {low} vs {high}")
    assert "NO_COST_BASIS" not in low
