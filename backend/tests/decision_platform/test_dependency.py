"""What the book leans on, and the two claims this view must never make.

The arithmetic is shares and a target's pace. The claims worth pinning:

* a principal's weight is **revenue riding on their product**, not spend — the
  two are different numbers and the gap between them is the finding;
* a sale nobody could attribute to a principal is **unattributed**, never
  bucketed into an "unknown" vendor that would accumulate revenue and win the
  concentration headline;
* target progress reports **pace beside achievement**, because 60% of a number
  with 80% of the quarter gone is behind and an achievement figure alone hides
  that until the last week;
* the view refuses to say **how much a customer depends on us** — we see what
  they buy here and nothing of what they buy elsewhere.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.commercial.config import CommercialThresholds
from app.commercial.insight import dependency as dep

TH = CommercialThresholds()
AS_OF = date(2026, 8, 7)


def _flow(customer: str, vendor: str | None, revenue: float,
          day: date = date(2026, 6, 1), product: str = "p1", category=None):
    return dep.Flow(customer_id=customer, product_id=product, date=day,
                    revenue=revenue, vendor_id=vendor, category=category)


def _spend(vendor: str, amount: float, day: date = date(2026, 6, 1),
           product: str = "p1"):
    return dep.Spend(vendor_id=vendor, product_id=product, date=day,
                     amount=amount)


def _build(flows, spends=(), **kw):
    kw.setdefault("vendor_names", {})
    kw.setdefault("customer_names", {})
    return dep.build(flows, spends, AS_OF, thresholds=TH, **kw)


def _vendor(result, vendor_id):
    return next(r for r in result["vendors"]["rows"] if r["entity_id"] == vendor_id)


def _customer(result, customer_id):
    return next(r for r in result["customers"]["rows"]
                if r["entity_id"] == customer_id)


# ── the two directions ───────────────────────────────────────────────────────
def test_a_principals_weight_is_the_revenue_riding_on_them_not_the_spend():
    """The finding is the gap between the two: cheap to buy from, and a third
    of what goes out of the door."""
    result = _build(
        [_flow("c1", "v1", 3400.0), _flow("c2", "v2", 6600.0)],
        [_spend("v1", 120.0), _spend("v2", 880.0)])
    v1 = _vendor(result, "v1")
    assert v1["share"] == pytest.approx(0.12)             # of purchasing
    assert v1["downstream_share"] == pytest.approx(0.34)  # of revenue
    assert v1["money"] == 120.0
    assert v1["downstream_revenue"] == 3400.0


def test_a_sale_with_no_principal_is_unattributed_not_bucketed():
    """An 'unknown' vendor would accumulate revenue and, at enough volume, win
    the concentration headline outright — the same refusal the supplier reducer
    already makes for spend."""
    result = _build([_flow("c1", "v1", 400.0), _flow("c2", None, 600.0)],
                    [_spend("v1", 100.0)])
    assert [r["entity_id"] for r in result["vendors"]["rows"]] == ["v1"]
    assert result["attribution"]["revenue_attributed"] == 400.0
    assert result["attribution"]["revenue_total"] == 1000.0
    assert result["attribution"]["share"] == pytest.approx(0.4)


def test_the_customer_side_ranks_by_revenue_and_counts_principals():
    result = _build([_flow("big", "v1", 900.0), _flow("big", "v2", 100.0),
                     _flow("small", "v1", 50.0)])
    rows = result["customers"]["rows"]
    assert [r["entity_id"] for r in rows] == ["big", "small"]
    assert _customer(result, "big")["counterparties"] == 2
    assert _customer(result, "small")["counterparties"] == 1


def test_concentration_reports_the_largest_and_the_top_five_together():
    flows = [_flow(f"c{i}", "v1", float(10 - i)) for i in range(10)]
    result = _build(flows)
    conc = result["customers"]["concentration"]
    assert conc["count"] == 10
    # 10 of 55, then 10+9+8+7+6 of 55.
    assert conc["top_share"] == pytest.approx(10 / 55, abs=1e-4)
    assert conc["top_n_share"] == pytest.approx(40 / 55, abs=1e-4)


def test_a_customer_who_buys_wider_shows_more_lines():
    result = _build([_flow("c1", "v1", 100.0, category="CUTTING_TOOLS"),
                     _flow("c1", "v2", 100.0, category="COOLANTS"),
                     _flow("c1", "v2", 100.0, category="UNCATEGORISED")])
    lines = [ln["category"] for ln in _customer(result, "c1")["lines"]]
    assert lines == ["COOLANTS", "CUTTING_TOOLS"]
    assert "UNCATEGORISED" not in lines


def test_the_supplier_half_is_absent_when_the_caller_may_not_see_it():
    result = _build([_flow("c1", "v1", 100.0)], [_spend("v1", 10.0)],
                    with_suppliers=False)
    assert result["vendors"] is None
    assert result["customers"]["rows"]


# ── targets ──────────────────────────────────────────────────────────────────
def _target(vendor="v1", amount=1000.0, basis=dep.ON_PURCHASE,
            start=date(2026, 7, 1), end=date(2026, 9, 30)):
    return dep.Target(vendor_id=vendor, period_start=start, period_end=end,
                      basis=basis, amount=amount)


def test_progress_reports_pace_beside_achievement():
    """60% of a number with 80% of the quarter gone is behind, and an
    achievement figure on its own hides that until the last week."""
    # Q3 runs 92 days from 1 Jul; as_of 7 Aug is day 38.
    got = dep.progress_of("v1", [_target(amount=1000.0)], AS_OF,
                          purchased=200.0, sold=0.0)
    assert got["achieved"] == pytest.approx(0.2)
    assert got["period_elapsed"] == pytest.approx(38 / 92, abs=1e-3)
    assert got["on_pace"] is False
    assert got["gap"] == 800.0
    assert got["days_left"] == 92 - 38


def test_a_target_ahead_of_its_period_reads_as_on_pace():
    got = dep.progress_of("v1", [_target(amount=1000.0)], AS_OF,
                          purchased=800.0, sold=0.0)
    assert got["on_pace"] is True


def test_the_basis_decides_which_actual_is_measured():
    """A purchase target compared against a sales figure is the error nobody
    catches until a quarter closes wrong."""
    on_purchase = dep.progress_of("v1", [_target(basis=dep.ON_PURCHASE)], AS_OF,
                                  purchased=300.0, sold=900.0)
    on_sales = dep.progress_of("v1", [_target(basis=dep.ON_SALES)], AS_OF,
                               purchased=300.0, sold=900.0)
    assert on_purchase["actual"] == 300.0
    assert on_sales["actual"] == 900.0


def test_the_shortest_covering_period_is_the_live_one():
    """A principal setting a quarter inside an annual number means the quarter,
    and reporting the year while somebody chases the quarter answers the wrong
    question."""
    year = _target(amount=10_000.0, start=date(2026, 4, 1), end=date(2027, 3, 31))
    quarter = _target(amount=2_500.0)
    live = dep.current_target("v1", [year, quarter], AS_OF)
    assert live is quarter


def test_no_target_is_none_rather_than_a_zero():
    """A principal with no number on record has not missed it."""
    assert dep.progress_of("v1", [], AS_OF, purchased=500.0, sold=0.0) is None
    assert dep.current_target("v1", [_target(vendor="other")], AS_OF) is None


def test_a_target_outside_its_period_does_not_apply():
    old = _target(start=date(2025, 1, 1), end=date(2025, 3, 31))
    assert dep.current_target("v1", [old], AS_OF) is None


def test_the_required_run_rate_is_arithmetic_and_not_a_forecast():
    got = dep.progress_of("v1", [_target(amount=1000.0)], AS_OF,
                          purchased=200.0, sold=0.0)
    assert got["required_run_rate"] == pytest.approx(800.0 / (92 - 38), abs=0.01)
    # Already there: nothing is required, and a negative rate would be nonsense.
    done = dep.progress_of("v1", [_target(amount=1000.0)], AS_OF,
                           purchased=1200.0, sold=0.0)
    assert done["required_run_rate"] is None


def test_the_target_travels_onto_the_vendor_row():
    result = _build([_flow("c1", "v1", 500.0, day=date(2026, 7, 15))],
                    [_spend("v1", 300.0, day=date(2026, 7, 15))],
                    targets=[_target(amount=1000.0)])
    target = _vendor(result, "v1")["target"]
    assert target["amount"] == 1000.0
    assert target["actual"] == 300.0          # PURCHASE basis
    assert target["basis_label"] == dep.BASIS_LABEL[dep.ON_PURCHASE]


# ── the refusals ─────────────────────────────────────────────────────────────
def test_the_view_refuses_to_say_how_much_they_depend_on_us():
    """We see what they buy here and nothing of what they buy elsewhere."""
    reasons = _build([_flow("c1", "v1", 100.0)])["unavailable"]
    assert any("depend on us" in r["what"] for r in reasons)
    assert any("elsewhere" in r["why"] for r in reasons)


def test_the_view_refuses_to_say_a_second_source_exists():
    reasons = _build([_flow("c1", "v1", 100.0)])["unavailable"]
    assert any("second source" in r["what"].lower() for r in reasons)


def test_the_result_carries_the_version_that_produced_it():
    assert _build([_flow("c1", "v1", 1.0)])["thresholds_version"] == TH.version


def test_an_empty_book_returns_the_shape_rather_than_nothing():
    result = _build([])
    assert result["customers"]["rows"] == []
    assert result["customers"]["concentration"]["top_share"] is None
    assert result["attribution"]["share"] is None


# ── the book as one picture ──────────────────────────────────────────────────
def _sankey(flows, **kw):
    kw.setdefault("vendor_names", {})
    kw.setdefault("customer_names", {})
    return dep.sankey(flows, **kw)


def _node(chart, key):
    return next(n for n in chart["nodes"] if n["key"] == key)


def test_the_flow_runs_principal_through_line_to_customer():
    chart = _sankey([_flow("c1", "v1", 100.0, category="CUTTING_TOOLS")])
    stages = {n["key"]: n["stage"] for n in chart["nodes"]}
    assert stages["v1"] == dep.STAGE_VENDOR
    assert stages["CUTTING_TOOLS"] == dep.STAGE_LINE
    assert stages["c1"] == dep.STAGE_CUSTOMER
    assert len(chart["links"]) == 2
    assert chart["total"] == 100.0


def test_every_band_reconciles_with_the_total_on_both_sides():
    """A flow picture whose bands do not add up teaches people to distrust the
    page — the same rule the revenue waterfall is held to."""
    flows = [_flow("c1", "v1", 60.0, category="CUTTING_TOOLS"),
             _flow("c2", "v2", 40.0, category="COOLANTS")]
    chart = _sankey(flows)
    left = sum(link["money"] for link in chart["links"]
               if link["source"].startswith(f"{dep.STAGE_VENDOR}:"))
    right = sum(link["money"] for link in chart["links"]
                if link["source"].startswith(f"{dep.STAGE_LINE}:"))
    assert left == chart["total"] == right == 100.0


def test_untraced_revenue_gets_its_own_band_rather_than_vanishing():
    """Omitting it would draw a smaller, tidier business than the real one and
    would not reconcile with the totals on every other screen."""
    chart = _sankey([_flow("c1", None, 30.0, category="CUTTING_TOOLS"),
                     _flow("c2", "v1", 70.0, category="CUTTING_TOOLS")])
    untraced = _node(chart, dep.UNTRACED)
    assert untraced["money"] == 30.0
    assert untraced["residual"] is True
    assert chart["total"] == 100.0


def test_trade_in_unplaced_items_gets_its_own_band_too():
    chart = _sankey([_flow("c1", "v1", 25.0, category=None)])
    assert _node(chart, dep.UNPLACED)["money"] == 25.0


def test_the_tail_folds_and_the_fold_says_how_many_went_into_it():
    """A band per customer is a hairball at two hundred; 'Other (183)' is a band
    somebody can read."""
    flows = [_flow(f"c{i}", f"v{i}", float(100 - i), category="CUTTING_TOOLS")
             for i in range(20)]
    chart = _sankey(flows, top_vendors=3, top_customers=3)
    assert chart["folded"] == {"vendors": 17, "customers": 17}
    assert "17" in _node(chart, dep.OTHER_VENDORS)["label"]
    assert "17" in _node(chart, dep.OTHER_CUSTOMERS)["label"]
    # Folding moves money between bands; it never loses any.
    assert sum(link["money"] for link in chart["links"]
               if link["source"].startswith(f"{dep.STAGE_VENDOR}:")) == chart["total"]


def test_an_empty_book_draws_no_bands_rather_than_failing():
    chart = _sankey([])
    assert chart["nodes"] == [] and chart["links"] == []
    assert chart["total"] == 0.0


# ── scoping both halves to one connected company ────────────────────────────
#
# This screen's output is *shares of a total* — "Kennametal is 38% of spend" is
# the sentence somebody acts on. The shared row filter hides rows and
# deliberately never restates a total, which would have put one company's list
# under three companies' arithmetic. So the scope goes to the server.
#
# The two halves take different bounds, and that is the part worth testing:
# neither `SalesTxn` nor `CostRecord` carries a connection, so sales scope by
# **customer** and purchases by **vendor** — the two ends the money is actually
# attributed to.


@pytest.fixture()
def client():
    from decimal import Decimal

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base, get_session
    from app.domain import models
    from app.routers import insight, platform_auth
    from app.seed import ensure_org_and_users

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    org = "org_sanketh"
    for conn, label in (("c_sls", "SLS Engineers"), ("c_4u", "4U Precision")):
        s.add(models.ZohoConnection(connection_id=conn, organization_id=org,
                                    label=label, zoho_organization_id=conn))
    # One customer, one vendor, one item and one sale per company — so every
    # figure on both halves differs between the two.
    for i, conn in enumerate(("c_sls", "c_4u")):
        s.add(models.Customer(customer_id=f"cust_{conn}", organization_id=org,
                              external_id=f"ec_{conn}", name=f"Buyer {conn}",
                              connection_id=conn))
        s.add(models.Vendor(vendor_id=f"v_{conn}", organization_id=org,
                            external_id=f"ev_{conn}", name=f"Principal {conn}",
                            connection_id=conn))
        s.add(models.Product(product_id=f"p_{conn}", organization_id=org,
                             external_id=f"ep_{conn}", name=f"Item {conn}",
                             hsn="82071900", active=True, source_ref={}))
        s.add(models.CostRecord(
            cost_record_id=f"cr_{conn}", organization_id=org,
            external_ref=f"b_{conn}:1", product_id=f"p_{conn}",
            vendor_id=f"v_{conn}", date=date(2026, 1, 5),
            qty=10, unit_cost=100 * (i + 1)))
        s.add(models.SalesTxn(
            organization_id=org, external_ref=f"inv_{conn}",
            customer_id=f"cust_{conn}", product_id=f"p_{conn}",
            date=date(2026, 6, 1), qty=Decimal("1"),
            unit_price=Decimal("500"), line_revenue=Decimal("500"),
            source_ref={"record_id": f"inv_{conn}"}))
    s.add(models.VendorTarget(target_id="t1", organization_id=org,
                              vendor_id="v_c_sls",
                              period_start=date(2026, 1, 1),
                              period_end=date(2026, 12, 31),
                              basis="PURCHASE", amount=5000))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def _head(client):
    from app.seed import SEED_PASSWORD
    r = client.post("/api/v1/auth/login",
                    json={"email": "m.rao@sanketh.in", "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _dep(client, head, scope=""):
    q = f"?connection_id={scope}" if scope else ""
    r = client.get(f"/api/v1/insight/dependency{q}", headers=head)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_screen_offers_every_connected_company(client):
    body = _dep(client, _head(client))
    assert [c["label"] for c in body["companies"]] == ["4U Precision",
                                                       "SLS Engineers"]
    assert body["scoped_to"] is None


def test_scoping_narrows_the_customer_half_by_customer(client):
    head = _head(client)
    assert len(_dep(client, head)["customers"]["rows"]) == 2

    one = _dep(client, head, "c_sls")
    assert [r["label"] for r in one["customers"]["rows"]] == ["Buyer c_sls"]
    assert one["scoped_to"] == "c_sls"


def test_scoping_narrows_the_supplier_half_by_vendor(client):
    """The half that needed its own bound. A customer-derived bound would have
    returned nothing here, which reads as "this company buys from nobody"
    rather than as a mistake."""
    head = _head(client)
    assert len(_dep(client, head)["vendors"]["rows"]) == 2

    one = _dep(client, head, "c_4u")
    assert [r["label"] for r in one["vendors"]["rows"]] == ["Principal c_4u"]


def test_the_concentration_headline_is_restated_not_left_org_wide(client):
    """The whole reason this is server-side. Two principals at ₹1,000 and
    ₹2,000 make the larger 67% of the book; alone, it is all of its own."""
    head = _head(client)
    assert _dep(client, head)["vendors"]["concentration"]["top_share"] == 0.6667

    one = _dep(client, head, "c_4u")
    assert one["vendors"]["concentration"]["top_share"] == 1.0
    assert one["vendors"]["concentration"]["count"] == 1


def test_a_target_for_a_scoped_out_supplier_does_not_follow(client):
    """It would show as 0% achieved against a company that never buys from
    them — a principal apparently missed by a book that was never theirs."""
    head = _head(client)
    sls = _dep(client, head, "c_sls")["vendors"]["rows"]
    assert any(r["target"] for r in sls)

    four_u = _dep(client, head, "c_4u")["vendors"]["rows"]
    assert all(r["target"] is None for r in four_u)
