"""Customer × Item API, persistence, backfill and role gating.

End to end through the real database and the real endpoints: source rows in,
derived metrics persisted, portfolio and drill-down out — and every conclusion
traceable to the transactions it was computed from.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.commercial.compute import recompute
from app.db import Base, get_session
from app.domain import models
from app.domain.enums import SignalType
from app.routers import accounts, commercial, insight, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_sanketh"          # the seeded default org the demo users belong to
AS_OF = date(2026, 7, 1)


def _d(days_ago: int) -> date:
    return AS_OF - timedelta(days=days_ago)


def _seed_commercial_data(s) -> None:
    """One eroding relationship (c1/p1) plus three peers priced better."""
    for cid, name in (("c1", "Acme Engineering"), ("c2", "Beta Works"),
                      ("c3", "Gamma Tools"), ("c4", "Delta Precision")):
        s.add(models.Customer(customer_id=cid, organization_id=ORG, external_id=cid,
                              name=name))
    s.add(models.Product(product_id="p1", organization_id=ORG, external_id="ITEM-900",
                         name="CNMG 120408-MP insert", uom="pcs"))
    # cost 100 -> 124
    s.add(models.CostRecord(organization_id=ORG, external_ref="B1:1", product_id="p1",
                            date=_d(400), qty=Decimal("100"), unit_cost=Decimal("100"),
                            source_ref={"record_type": "bill", "record_id": "B1"}))
    s.add(models.CostRecord(organization_id=ORG, external_ref="B2:1", product_id="p1",
                            date=_d(120), qty=Decimal("100"), unit_cost=Decimal("124"),
                            source_ref={"record_type": "bill", "record_id": "B2"}))

    def sale(cust, days, qty, price, ref):
        q, p = Decimal(str(qty)), Decimal(str(price))
        s.add(models.SalesTxn(
            organization_id=ORG, external_ref=f"{ref}:1", customer_id=cust,
            product_id="p1", date=_d(days), qty=q, unit_price=p, line_revenue=q * p,
            rate=p, discount_percent=Decimal("0"),
            source_ref={"record_type": "invoice", "record_id": ref}))

    for i, days in enumerate([600, 500, 400, 300, 200]):
        sale("c1", days, 100, 135, f"INV-H{i}")          # historical ~25.9%
    for i, days in enumerate([80, 50, 20]):
        sale("c1", days, 100, 139, f"INV-R{i}")          # recent ~10.8%
    for i, days in enumerate([80, 40]):                  # peers priced better
        sale("c2", days, 50, 165, f"INV-C2{i}")
        sale("c3", days, 50, 170, f"INV-C3{i}")
        sale("c4", days, 50, 168, f"INV-C4{i}")
    s.flush()


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

    s = Maker()
    ensure_org_and_users(s)
    _seed_commercial_data(s)
    recompute(s, ORG, as_of=AS_OF)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(commercial.router)
    app.include_router(insight.router)
    app.include_router(accounts.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    tc = TestClient(app)
    tc.Maker = Maker
    return tc


def _hdr(c, email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── persistence ─────────────────────────────────────────────────────────────
def test_recompute_persists_one_row_per_relationship(client):
    s = client.Maker()
    rows = s.query(models.CustomerItemMetric).all()
    assert len(rows) == 4, "c1..c4, one item each"
    subject = next(r for r in rows if r.customer_id == "c1")
    assert abs(subject.historical_margin - 0.2593) < 1e-3
    assert abs(subject.current_margin - 0.1079) < 1e-3
    assert subject.erosion_kind == "COST_DRIVEN"
    assert subject.peer_count == 3
    assert subject.thresholds_version.startswith("ci_")
    s.close()


def test_recompute_is_idempotent(client):
    s = client.Maker()
    before = s.query(models.CustomerItemMetric).count()
    recompute(s, ORG, as_of=AS_OF)
    s.commit()
    assert s.query(models.CustomerItemMetric).count() == before, "upserted, not duplicated"
    s.close()


def test_recompute_never_touches_source_transactions(client):
    s = client.Maker()
    before = (s.query(models.SalesTxn).count(), s.query(models.CostRecord).count())
    recompute(s, ORG, as_of=AS_OF)
    s.commit()
    assert (s.query(models.SalesTxn).count(), s.query(models.CostRecord).count()) == before
    s.close()


def test_recompute_emits_customer_item_signals(client):
    s = client.Maker()
    types = {sig.signal_type for sig in s.query(models.Signal).all()}
    assert SignalType.CI_MARGIN_EROSION.value in types
    assert SignalType.CI_COST_NOT_PASSED.value in types
    assert SignalType.CI_LOW_PEER_PRICING.value in types
    s.close()


def test_targeted_recompute_only_touches_the_named_customer(client):
    """The scaling property: recomputing one account must not rewrite the
    organization's every row."""
    s = client.Maker()
    others = {r.customer_id: r.computed_at
              for r in s.query(models.CustomerItemMetric).all() if r.customer_id != "c1"}
    recompute(s, ORG, customer_ids={"c1"}, as_of=AS_OF)
    s.commit()

    for r in s.query(models.CustomerItemMetric).all():
        if r.customer_id != "c1":
            assert r.computed_at == others[r.customer_id], "untouched"
    s.close()


# ── portfolio ───────────────────────────────────────────────────────────────
def test_portfolio_answers_which_items_need_attention(client):
    body = client.get("/api/v1/commercial/customers/c1/portfolio",
                      headers=_hdr(client, "m.rao@sanketh.in")).json()

    assert body["customer"]["name"] == "Acme Engineering"
    summary = body["summary"]
    assert summary["active_items"] == 1
    assert summary["items_with_margin_erosion"] == 1
    assert summary["items_cost_not_passed"] == 1
    assert summary["items_below_peer_benchmark"] == 1
    assert summary["historical_margin_gap"] > 0

    flagged = body["items_requiring_attention"]
    assert len(flagged) == 1
    row = flagged[0]
    assert row["item_name"] == "CNMG 120408-MP insert"
    assert row["item_code"] == "ITEM-900"
    assert SignalType.CI_MARGIN_EROSION.value in row["signals"]


def test_portfolio_margin_is_profit_over_revenue_across_items(client):
    body = client.get("/api/v1/commercial/customers/c1/portfolio",
                      headers=_hdr(client, "m.rao@sanketh.in")).json()
    s = body["summary"]
    expected = s["gross_profit_12m"] / s["revenue_12m"]
    assert abs(s["gross_margin_12m"] - expected) < 1e-3


def test_portfolio_404s_for_an_unknown_customer(client):
    r = client.get("/api/v1/commercial/customers/nope/portfolio",
                   headers=_hdr(client, "m.rao@sanketh.in"))
    assert r.status_code == 404


# ── drill-down ──────────────────────────────────────────────────────────────
def test_drilldown_answers_all_six_questions(client):
    body = client.get("/api/v1/commercial/customers/c1/items/p1",
                      headers=_hdr(client, "m.rao@sanketh.in")).json()

    h = body["headline"]
    assert abs(h["current_margin"] - 0.1079) < 1e-3          # 1. deteriorating?
    assert abs(h["historical_margin"] - 0.2593) < 1e-3
    assert h["margin_change_pp"] < 0
    assert h["current_effective_cost"] and h["current_sell_price"]   # 2. cost vs price
    assert body["peers"]["peer_count"] == 3                          # 3. vs others
    assert h["historical_margin_gap"] > 0                            # 4. materiality
    assert body["volume_vs_margin"]                                  # 5. volume
    assert body["margin_periods"]["historical"] is not None


def test_drilldown_diagnosis_is_deterministic_prose_from_real_numbers(client):
    body = client.get("/api/v1/commercial/customers/c1/items/p1",
                      headers=_hdr(client, "m.rao@sanketh.in")).json()
    text = " ".join(body["diagnosis"])

    assert "25.9%" in text and "10.8%" in text, "the computed margins appear verbatim"
    assert "percentage points" in text
    assert "not recoverable profit" in text, "the gap must be framed as an estimate"
    assert "benchmark, not a target" in text, "peers are evidence, not a mandate"


def test_drilldown_peers_exclude_the_subject_and_flag_it_separately(client):
    body = client.get("/api/v1/commercial/customers/c1/items/p1",
                      headers=_hdr(client, "m.rao@sanketh.in")).json()
    peers = body["peers"]

    assert "c1" not in [p["customer_id"] for p in peers["rows"]]
    assert peers["subject"]["customer_id"] == "c1" and peers["subject"]["is_subject"]
    assert peers["is_reliable"] is True
    assert {p["name"] for p in peers["rows"]} == {"Beta Works", "Gamma Tools",
                                                  "Delta Precision"}


def test_drilldown_exposes_the_transactions_behind_every_conclusion(client):
    body = client.get("/api/v1/commercial/customers/c1/items/p1",
                      headers=_hdr(client, "m.rao@sanketh.in")).json()
    txns = body["transactions"]

    assert len(txns) == 8
    row = txns[0]
    for field in ("date", "invoice_id", "qty", "rate", "discount_percent",
                  "net_sell_price", "effective_cost", "gross_profit", "margin"):
        assert field in row, f"{field} must be inspectable"
    assert row["cost_source"], "the bill the cost came from is named"
    # revenue really is the sum of the lines the headline was computed from:
    # 5 x 100 x 135 historical + 3 x 100 x 139 recent
    assert abs(sum(t["revenue"] for t in txns) - 109_200) < 1.0


def test_drilldown_404s_for_an_item_this_customer_never_bought(client):
    s = client.Maker()
    s.add(models.Product(product_id="p2", organization_id=ORG, external_id="ITEM-901",
                         name="Unrelated"))
    s.commit()
    s.close()
    r = client.get("/api/v1/commercial/customers/c1/items/p2",
                   headers=_hdr(client, "m.rao@sanketh.in"))
    assert r.status_code == 404


# ── role gating ─────────────────────────────────────────────────────────────
def test_a_salesperson_cannot_reach_any_of_this(client):
    """Every response here is cost and margin throughout. There is no
    salesperson-safe projection of a margin analysis."""
    sales = _hdr(client, "r.nair@sanketh.in")
    assert client.get("/api/v1/commercial/customers/c1/portfolio",
                      headers=sales).status_code == 403
    assert client.get("/api/v1/commercial/customers/c1/items/p1",
                      headers=sales).status_code == 403
    assert client.post("/api/v1/commercial/recompute", headers=sales).status_code == 403


def test_an_owner_can_recompute_from_already_synced_data(client):
    r = client.post("/api/v1/commercial/recompute",
                    headers=_hdr(client, "s.menon@sanketh.in"),
                    json={"customer_id": "c1"})
    assert r.status_code == 200
    body = r.json()
    assert body["relationships"] == 1
    assert body["relationships_with_cost"] == 1
    assert body["failures"] == []


# ── insufficient data ───────────────────────────────────────────────────────
def test_a_thin_relationship_reports_insufficiency_rather_than_a_conclusion(client):
    s = client.Maker()
    s.add(models.Customer(customer_id="c9", organization_id=ORG, external_id="c9",
                          name="One Order Ltd"))
    s.add(models.SalesTxn(organization_id=ORG, external_ref="INV-ONE:1", customer_id="c9",
                          product_id="p1", date=_d(20), qty=Decimal("1"),
                          unit_price=Decimal("139"), line_revenue=Decimal("139"),
                          source_ref={"record_type": "invoice", "record_id": "INV-ONE"}))
    s.flush()
    recompute(s, ORG, as_of=AS_OF)
    s.commit()
    s.close()

    body = client.get("/api/v1/commercial/customers/c9/items/p1",
                      headers=_hdr(client, "m.rao@sanketh.in")).json()
    assert body["data_quality"]["data_sufficiency"] == "INSUFFICIENT"
    assert "Not enough data" in body["diagnosis"][0]

    s = client.Maker()
    row = s.query(models.CustomerItemMetric).filter_by(customer_id="c9").one()
    assert row.signals == [], "no signal may be raised on one transaction"
    s.close()


# ── the health timeline: scoped first, then margin absent rather than hidden ─
def _assign_to_salesperson(client, customer_id: str) -> None:
    s = client.Maker()
    s.query(models.Customer).filter_by(customer_id=customer_id).one() \
        .assigned_user_id = "usr_sales"
    s.commit()
    s.close()


def test_a_salesperson_cannot_read_the_timeline_of_an_account_that_is_not_theirs(client):
    """The accounts list already narrows a salesperson to their own accounts.
    A per-customer route that skips the same check is a way around all of it,
    because the id is then the only thing in the way and ids travel."""
    unassigned = client.get("/api/v1/insight/customers/c2/timeline",
                            headers=_hdr(client, "r.nair@sanketh.in"))
    assert unassigned.status_code == 404
    # 404 rather than 403: a 403 would confirm c2 exists, which is most of what
    # an enumeration wants.
    assert "not found" in unassigned.text.lower()

    # The same account is readable by a manager, so this is scope and not a
    # missing customer.
    assert client.get("/api/v1/insight/customers/c2/timeline",
                      headers=_hdr(client, "m.rao@sanketh.in")).status_code == 200

    _assign_to_salesperson(client, "c2")
    assert client.get("/api/v1/insight/customers/c2/timeline",
                      headers=_hdr(client, "r.nair@sanketh.in")).status_code == 200


def test_the_item_picker_applies_the_same_scope_rule_as_the_timeline(client):
    """One rule, two answer shapes, and both unprobeable.

    The rule is `authz.can_view_customer`, which used to be written out inline in
    each of these two endpoints — and a scope rule with two copies is one that
    eventually disagrees with itself about a reassigned account.

    They answer differently on purpose and it is not an inconsistency to iron
    out: a picker feeding a dropdown returns an empty list, because a field that
    errors is a field that breaks, while a screen returns 404, because drawing
    itself empty would claim the account exists. What both must do is give the
    *same* answer for "not yours" as for "not real", which is what makes an id
    useless for enumeration.
    """
    sales = _hdr(client, "r.nair@sanketh.in")

    not_theirs = client.get("/api/v1/accounts/c2/items", headers=sales)
    never_existed = client.get("/api/v1/accounts/c_nope/items", headers=sales)
    assert not_theirs.status_code == 200 and not_theirs.json() == []
    assert never_existed.status_code == 200 and never_existed.json() == []
    assert not_theirs.json() == never_existed.json(), (
        "an account they cannot see must be indistinguishable from one that "
        "does not exist")

    # And the same two ids on the sibling: 404 both times, for the same reason.
    assert client.get("/api/v1/insight/customers/c2/timeline",
                      headers=sales).status_code == 404
    assert client.get("/api/v1/insight/customers/c_nope/timeline",
                      headers=sales).status_code == 404

    # Scope, not a missing customer: assigning c2 opens both.
    _assign_to_salesperson(client, "c2")
    assert client.get("/api/v1/accounts/c2/items", headers=sales).status_code == 200
    assert client.get("/api/v1/insight/customers/c2/timeline",
                      headers=sales).status_code == 200


def test_a_salesperson_s_timeline_has_no_margin_field_anywhere(client):
    """Absent, not masked. The rule is that there is nothing in the network tab
    to read — so this asserts on the raw bytes, not on the parsed value being
    None, because a null still tells a reader the field exists and is theirs to
    go looking for."""
    _assign_to_salesperson(client, "c1")
    r = client.get("/api/v1/insight/customers/c1/timeline",
                   headers=_hdr(client, "r.nair@sanketh.in"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["series"], "expected trading months for c1"
    for point in body["series"]:
        assert "margin" not in point
        assert "cost_coverage" not in point
    assert "margin" in r.text, "the refusal itself should be named in `unavailable`"
    assert any(u["series"] == "margin" for u in body["unavailable"])


def test_a_manager_s_timeline_carries_the_margin_and_its_coverage(client):
    r = client.get("/api/v1/insight/customers/c1/timeline",
                   headers=_hdr(client, "m.rao@sanketh.in"))
    assert r.status_code == 200, r.text
    series = r.json()["series"]
    traded = [p for p in series if p["revenue"] > 0]
    assert traded, "expected trading months for c1"
    assert all("margin" in p and "cost_coverage" in p for p in series)
    assert any(p["margin"] is not None for p in traded)


def test_the_timeline_names_what_it_cannot_show_for_every_role(client):
    """Payment behaviour is in the specification and is not in this platform.
    Both roles are told so, rather than one of them seeing three series where
    four were promised and being left to wonder."""
    _assign_to_salesperson(client, "c1")
    for email in ("r.nair@sanketh.in", "m.rao@sanketh.in"):
        body = client.get("/api/v1/insight/customers/c1/timeline",
                          headers=_hdr(client, email)).json()
        assert any(u["series"] == "payment_behaviour" for u in body["unavailable"])


# ── the negotiation desk: same numbers, different projection ────────────────
#
# The item costs 124 as of the most recent bill, and the default floor markup
# is 25%, so the floor is 155.00 and a line of 100 at 200 contributes 4,500.
# Those three numbers are asserted rather than recomputed in the test, because
# a test that repeats the implementation's arithmetic verifies nothing.
FLOOR = 155.0


def _classify(client, customer_id: str, eligibility: str) -> None:
    s = client.Maker()
    row = s.get(models.Customer, customer_id)
    row.incentive_eligibility = eligibility
    s.commit()
    s.close()


def _negotiate(client, email, **over):
    body = {"customer_id": "c1", "product_id": "p1", "qty": 100,
            "agreed_price": 200, "customer_discount": 2,
            "vendor_concession": 10, "target_caf": 5000}
    body.update(over)
    return client.post("/api/v1/insight/negotiate", json=body,
                       headers=_hdr(client, email))


def test_a_salespersons_negotiation_carries_no_cost_and_no_margin(client):
    """The invariant this whole feature had to be designed around. The floor is
    disclosable because the markup behind it is family-varying and unpublished;
    the cost itself is simply not in the payload."""
    _assign_to_salesperson(client, "c1")

    r = _negotiate(client, "r.nair@sanketh.in")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["negotiable"] is True
    assert body["floor_price"] == pytest.approx(FLOOR)
    for field in ("unit_cost", "gross_profit", "margin_at_floor", "m_floor"):
        assert field not in body, field
    # And the omission is named rather than left as a hole on the screen.
    assert any(u["series"] == "cost_and_margin" for u in body["unavailable"])


def test_a_manager_gets_the_reconciliation_behind_the_floor(client):
    """Somebody with cost scope has to be able to check that the floor is sane,
    which is the one thing a salesperson cannot do for themselves."""
    body = _negotiate(client, "m.rao@sanketh.in").json()
    assert body["unit_cost"] == pytest.approx(124.0)
    assert body["margin_at_floor"] == pytest.approx(0.2, abs=0.001)
    assert body["gross_profit"] == pytest.approx((200 - 2 - 124) * 100)


def test_both_roles_get_the_same_contribution(client):
    """One computation, two projections. Two code paths computing a
    contribution two ways is how a screen and a payslip disagree."""
    _assign_to_salesperson(client, "c1")

    theirs = _negotiate(client, "r.nair@sanketh.in").json()
    managers = _negotiate(client, "m.rao@sanketh.in").json()
    for field in ("floor_price", "contribution", "caf", "collected_caf",
                  "discount_to_floor_per_unit", "price_to_hold_target"):
        assert theirs[field] == managers[field], field
    # 100 units at 198 net against a 155 floor, plus the vendor ask at 1:1.
    assert theirs["contribution"] == pytest.approx(4300.0)
    assert theirs["caf"] == pytest.approx(5300.0)


def test_an_item_we_have_never_bought_has_no_floor_and_is_refused(client):
    """A missing floor is not zero. Zero would make the whole selling price
    contribution and turn every unmastered item into a jackpot."""
    s = client.Maker()
    s.add(models.Product(product_id="p-nocost", organization_id=ORG,
                         external_id="ITEM-NOCOST", name="Unmastered", uom="pcs"))
    s.commit()
    s.close()

    body = _negotiate(client, "m.rao@sanketh.in", product_id="p-nocost").json()
    assert body["negotiable"] is False
    assert "no purchase record" in body["empty_reason"].lower()


def test_a_third_party_payment_is_refused_on_an_unclassified_account(client):
    """I2 fails closed: nobody has said this account is private, so it is
    treated exactly like a PSU. The response is a refusal, not a number."""
    r = _negotiate(client, "m.rao@sanketh.in", third_party_incentive=5000)
    assert r.status_code == 422
    assert "blacklisting" in r.json()["detail"]


def test_a_third_party_payment_is_priced_on_a_private_account(client):
    """Classified private, it is charged at 100 paise in the rupee — which is
    what makes the salesperson's decision the owner's decision."""
    _classify(client, "c1", "PRIVATE")
    body = _negotiate(client, "m.rao@sanketh.in", third_party_incentive=5000).json()
    assert body["third_party_allowed"] is True
    assert body["third_party_charged"] == pytest.approx(5000.0)
    assert body["caf"] == pytest.approx(300.0)


def test_the_toolkit_costs_half_of_what_paying_someone_costs(client):
    """The compliant lever is reached for because it is cheaper, not because
    anyone was told to reach for it."""
    _classify(client, "c1", "PRIVATE")
    paid = _negotiate(client, "m.rao@sanketh.in", third_party_incentive=4000).json()
    kit = _negotiate(client, "m.rao@sanketh.in", toolkit_spend=4000).json()
    assert kit["caf"] - paid["caf"] == pytest.approx(2000.0)


def test_the_response_records_both_parameter_versions(client):
    """Thresholds and the incentive block are cut on different schedules. A row
    stamped with only one of them cannot be explained later."""
    body = _negotiate(client, "m.rao@sanketh.in").json()
    assert body["thresholds_version"]
    assert body["incentive_config_version"]


def test_the_desk_is_scoped_like_every_other_per_customer_route(client):
    assert _negotiate(client, "r.nair@sanketh.in",
                      customer_id="c2").status_code == 404


# ── the directory: active by default, and choosable from ────────────────────
def _mark_inactive(client, customer_id: str) -> None:
    s = client.Maker()
    s.get(models.Customer, customer_id).status = "INACTIVE"
    s.commit()
    s.close()


def test_the_directory_shows_active_accounts_by_default(client):
    """The pull reads inactive contacts because their history has to resolve.
    That is not a reason to put a dormant account in the list somebody scans
    before a call — so it is one click away, and never more than that."""
    _mark_inactive(client, "c2")
    hdr = _hdr(client, "m.rao@sanketh.in")

    default = client.get("/api/v1/accounts", headers=hdr).json()
    assert "c2" not in [a["customer_id"] for a in default]

    inactive = client.get("/api/v1/accounts?status=inactive", headers=hdr).json()
    assert [a["customer_id"] for a in inactive] == ["c2"]

    every = client.get("/api/v1/accounts?status=all", headers=hdr).json()
    assert "c2" in [a["customer_id"] for a in every]


def test_the_directory_carries_trade_so_it_can_be_chosen_from(client):
    """A list of names can only be searched. What somebody actually wants to
    know before calling is when this account last ordered and whether they are
    still worth the call."""
    body = client.get("/api/v1/accounts", headers=_hdr(client, "m.rao@sanketh.in")).json()
    c1 = next(a for a in body if a["customer_id"] == "c1")
    assert c1["last_order"], "c1 has invoices in the fixture"
    assert c1["orders_12m"] >= 1
    assert c1["revenue_12m"] > 0


def test_an_account_that_has_never_ordered_says_so_rather_than_showing_zero(client):
    """Never ordered and ordered-but-not-this-year are different facts, and a
    zero in a date column is neither of them."""
    s = client.Maker()
    s.add(models.Customer(customer_id="c-new", organization_id=ORG,
                          external_id="c-new", name="Freshly added"))
    s.commit()
    s.close()

    body = client.get("/api/v1/accounts", headers=_hdr(client, "m.rao@sanketh.in")).json()
    fresh = next(a for a in body if a["customer_id"] == "c-new")
    assert fresh["last_order"] is None
    assert fresh["orders_12m"] == 0


def test_the_directory_carries_no_cost_and_no_margin(client):
    """Revenue is operational — a salesperson sees it on every other screen.
    Cost and margin are not, and this endpoint is on a salesperson's path."""
    _assign_to_salesperson(client, "c1")
    body = client.get("/api/v1/accounts", headers=_hdr(client, "r.nair@sanketh.in")).json()
    for row in body:
        assert not [k for k in row if "cost" in k or "margin" in k or "profit" in k]


def test_a_discontinued_item_is_not_offered_by_default(client):
    """It is in the master now — the pull reads inactive items so their history
    resolves — but it should not be the item picked by accident on a live
    quote."""
    s = client.Maker()
    s.get(models.Product, "p1").active = False
    s.commit()
    s.close()
    hdr = _hdr(client, "m.rao@sanketh.in")

    assert client.get("/api/v1/accounts/c1/items", headers=hdr).json() == []
    offered = client.get("/api/v1/accounts/c1/items?status=all", headers=hdr).json()
    assert [i["product_id"] for i in offered] == ["p1"]
    assert offered[0]["active"] is False
    # Named, not numbered: the picker exists so nobody has to know an item id.
    assert offered[0]["name"] == "CNMG 120408-MP insert"
