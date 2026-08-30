"""The monetization console's gate, and the evidence module's honesty.

The gate is the more important half. Everything behind this router is PIE's own
commercial position — cost to serve, intended capture, the price each segment
should pay — and a tenant who could read it would be negotiating against the
vendor's reservation price. So the tests here are mostly *refusals*, and they
assert two properties an ordinary role check does not have:

**Empty allowlist means closed.** The most common state of any deployment is
one that never set the variable, and a console that opens itself there is the
benign default CLAUDE.md §1 forbids.

**No role reaches it.** Not a salesperson, not a manager, and specifically not
an owner — an owner who could grant themselves this would defeat the whole
mechanism, which is exactly why it is not a ``Role``.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.config import settings
from app.db import get_session
from app.domain import models
from app.monetization import evidence
from app.routers import monetization, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"
OWNER = "s.menon@pie.example"
MANAGER = "m.rao@pie.example"
SALES = "r.nair@pie.example"

#: Every endpoint the operator gate guards. Enumerated so a new one added
#: without a gate fails this file rather than shipping open.
GUARDED = ("/api/v1/monetization/parameters",
           "/api/v1/monetization/segments",
           "/api/v1/monetization/scorecard",
           "/api/v1/monetization/report",
           f"/api/v1/monetization/observed/{ORG}")


@pytest.fixture()
def client_and_maker():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(monetization.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app), Maker


@pytest.fixture(autouse=True)
def _closed_by_default(monkeypatch):
    """Every test starts from the unset state, and opts in explicitly."""
    monkeypatch.setattr(settings, "PIE_OPERATOR_EMAILS", "", raising=False)


def _login(client, email):
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.mark.parametrize("path", GUARDED)
def test_an_unset_allowlist_closes_every_endpoint(client_and_maker, path):
    client, _ = client_and_maker
    assert client.get(path, headers=_login(client, OWNER)).status_code == 403


@pytest.mark.parametrize("email", [OWNER, MANAGER, SALES])
def test_no_tenant_role_reaches_the_console(client_and_maker, monkeypatch, email):
    """Even with an allowlist configured, a role is never the key.

    The allowlist here names somebody else entirely, so this is the real test:
    a signed-in owner of the tenant is refused while an operator is not.
    """
    monkeypatch.setattr(settings, "PIE_OPERATOR_EMAILS", "ops@pie.internal",
                        raising=False)
    client, _ = client_and_maker
    headers = _login(client, email)
    for path in GUARDED:
        assert client.get(path, headers=headers).status_code == 403


def test_the_refusal_says_nothing_about_whether_a_console_exists(
        client_and_maker, monkeypatch):
    """Identical body and status whether the allowlist is empty or just
    excludes you — otherwise a 403 answers "does this deployment have one"."""
    client, _ = client_and_maker
    headers = _login(client, OWNER)
    closed = client.get(GUARDED[0], headers=headers)
    monkeypatch.setattr(settings, "PIE_OPERATOR_EMAILS", "ops@pie.internal",
                        raising=False)
    excluded = client.get(GUARDED[0], headers=headers)
    assert closed.status_code == excluded.status_code == 403
    assert closed.json() == excluded.json()


def test_the_access_probe_answers_false_rather_than_refusing(client_and_maker):
    """A 200 with ``false``, so the shell asks a question instead of probing a
    403 and filling error monitoring with refusals that are not failures."""
    client, _ = client_and_maker
    r = client.get("/api/v1/monetization/access", headers=_login(client, OWNER))
    assert r.status_code == 200
    assert r.json() == {"operator": False}


def test_an_allowlisted_operator_gets_the_whole_console(client_and_maker,
                                                        monkeypatch):
    monkeypatch.setattr(settings, "PIE_OPERATOR_EMAILS",
                        f"someone.else@pie.internal, {OWNER.upper()} ",
                        raising=False)
    client, _ = client_and_maker
    headers = _login(client, OWNER)
    assert client.get("/api/v1/monetization/access",
                      headers=headers).json() == {"operator": True}
    for path in GUARDED:
        assert client.get(path, headers=headers).status_code == 200, path


def test_calculate_returns_every_panel_in_one_round_trip(client_and_maker,
                                                         monkeypatch):
    monkeypatch.setattr(settings, "PIE_OPERATOR_EMAILS", OWNER, raising=False)
    client, _ = client_and_maker
    body = {
        "profile": {
            "name": "Test", "annual_rfqs": 20000, "pie_rfq_share": 0.6,
            "quote_conversion": 0.5, "order_conversion": 0.3,
            "average_order_value": "150000", "gross_margin": 0.23,
            "sales_engineers": 10, "cost_per_employee_year": "1000000",
            "rfq_processing_minutes": 12, "quotation_minutes": 18},
        "impact": {"quote_conversion_uplift_pp": 0.05,
                   "order_conversion_uplift_pp": 0.02, "aov_uplift": 0.03,
                   "gross_margin_uplift_pp": 0.006,
                   "procurement_saving_rate": 0.004,
                   "minutes_saved_per_rfq": 6, "minutes_saved_per_quote": 12},
    }
    r = client.post("/api/v1/monetization/calculate", json=body,
                    headers=_login(client, OWNER))
    assert r.status_code == 200, r.text
    payload = r.json()
    assert set(payload) >= {
        "waterfall", "margin_hypothesis", "transaction_ladder",
        "subscription_ladder", "hybrids", "all_strategies", "recommendation",
        "cost_floor", "pie_unit_economics", "parameters_version"}
    assert payload["recommendation"]["structure"]["platform_fee"]


def test_invalid_calculator_input_is_refused_not_clamped_silently(
        client_and_maker, monkeypatch):
    """A conversion rate of 4 is rejected at the schema, before any arithmetic.

    The engine also clamps, deliberately — but a request that could only be a
    mistake should come back as a 422 naming the field rather than as a
    confident answer to a question nobody asked.
    """
    monkeypatch.setattr(settings, "PIE_OPERATOR_EMAILS", OWNER, raising=False)
    client, _ = client_and_maker
    r = client.post("/api/v1/monetization/calculate", json={
        "profile": {"name": "x", "annual_rfqs": -5, "pie_rfq_share": 4.0,
                    "quote_conversion": 0.5, "order_conversion": 0.3,
                    "average_order_value": "1", "gross_margin": 0.2,
                    "sales_engineers": 1, "cost_per_employee_year": "1",
                    "rfq_processing_minutes": 1, "quotation_minutes": 1},
        "impact": {}}, headers=_login(client, OWNER))
    assert r.status_code == 422


def test_an_unknown_impact_set_is_named_rather_than_defaulted(client_and_maker,
                                                              monkeypatch):
    monkeypatch.setattr(settings, "PIE_OPERATOR_EMAILS", OWNER, raising=False)
    client, _ = client_and_maker
    r = client.get("/api/v1/monetization/report?impact=optimistic",
                   headers=_login(client, OWNER))
    assert r.status_code == 400


# ── evidence ────────────────────────────────────────────────────────────────
def test_an_organization_with_no_rows_reports_unknown_not_zero(client_and_maker):
    """The whole point of the module. An unconnected organization has no
    measurable GMV, which is not the same as a small one."""
    _, Maker = client_and_maker
    s = Maker()
    observed = evidence.observe(s, ORG)
    s.close()
    assert observed.annual_revenue is None
    assert observed.annual_rfqs is None
    assert observed.gross_margin is None
    fields = {gap["field"] for gap in observed.gaps}
    assert {"annual_revenue", "gross_margin", "annual_rfqs"} <= fields


def test_grounding_with_nothing_measured_is_entirely_archetype(client_and_maker):
    _, Maker = client_and_maker
    s = Maker()
    profile, source = evidence.ground(evidence.observe(s, ORG))
    s.close()
    assert set(source.values()) == {"archetype"}
    assert profile.annual_revenue > 0


def test_a_measured_book_replaces_the_guess_and_says_which_fields_moved(
        client_and_maker):
    """Real invoiced revenue and a real costed margin displace the archetype's,
    and the provenance map names exactly which fields moved."""
    from datetime import date, datetime, timedelta
    from decimal import Decimal

    _, Maker = client_and_maker
    s = Maker()
    today = date.today()
    for i in range(20):
        s.add(models.SalesTxn(
            organization_id=ORG, external_ref=f"inv{i}:1", customer_id="cx",
            product_id="pr", date=today - timedelta(days=i), qty=Decimal("1"),
            unit_price=Decimal("500000"), line_revenue=Decimal("500000")))
        s.add(models.InvoiceDoc(
            organization_id=ORG, external_ref=f"inv{i}",
            date=today - timedelta(days=i), total=Decimal("500000")))
        s.add(models.InboundLine(
            organization_id=ORG, raw_text="CNMG 120408", channel="EMAIL",
            received_at=datetime.now() - timedelta(days=i)))
    s.add(models.CustomerItemMetric(
        organization_id=ORG, customer_id="cx", product_id="pr",
        revenue_12m=Decimal("10000000"), gross_profit_12m=Decimal("2500000")))
    s.commit()

    observed = evidence.observe(s, ORG)
    profile, source = evidence.ground(observed)
    s.close()

    assert observed.annual_revenue == Decimal("10000000.00")
    assert observed.gross_margin == 0.25
    assert observed.annual_rfqs == 20
    assert profile.gross_margin == 0.25
    assert profile.annual_rfqs == 20
    assert source["gross_margin"] == "measured"
    assert source["annual_rfqs"] == "measured"
    assert source["average_order_value"] == "solved from measured GMV"
    # And the funnel now reconciles to the invoiced book rather than to the
    # archetype's order value, which is the only reason to solve it at all.
    assert abs(profile.annual_revenue - Decimal("10000000")) < Decimal("100")


def test_a_thin_costed_share_is_named_as_a_gap(client_and_maker):
    """A margin measured over 8% of the book is a fact about 8% of the book.

    This is the "absence of evidence is not a pass" case in its quietest form:
    the ratio is real, and reading it as the book's margin is the mistake.
    """
    from decimal import Decimal

    _, Maker = client_and_maker
    s = Maker()
    s.add(models.CustomerItemMetric(
        organization_id=ORG, customer_id="cx", product_id="p1",
        revenue_12m=Decimal("100000"), gross_profit_12m=Decimal("25000")))
    s.add(models.CustomerItemMetric(
        organization_id=ORG, customer_id="cx", product_id="p2",
        revenue_12m=Decimal("900000"), gross_profit_12m=None))
    s.commit()
    observed = evidence.observe(s, ORG)
    s.close()
    assert observed.gross_margin == 0.25
    assert observed.costed_revenue_share == 0.1
    assert any(g["field"] == "gross_margin" and "10%" in g["why"]
               for g in observed.gaps)


def test_credit_notes_are_reported_and_never_netted(client_and_maker):
    """A tax-inclusive credit total cannot be subtracted from pre-tax revenue.

    Doing it anyway over-deducts by exactly the GST — a wrong number in the
    direction that flatters the customer, which is still the wrong number to
    invoice on. So it is reported beside the revenue with the reason, and the
    contract settles the basis.
    """
    from datetime import date
    from decimal import Decimal

    _, Maker = client_and_maker
    s = Maker()
    today = date.today()
    s.add(models.SalesTxn(
        organization_id=ORG, external_ref="i1:1", customer_id="cx",
        product_id="pr", date=today, qty=Decimal("1"),
        unit_price=Decimal("1000000"), line_revenue=Decimal("1000000")))
    s.add(models.CreditNoteDoc(
        organization_id=ORG, external_ref="cn1", date=today,
        total=Decimal("118000")))
    s.commit()
    observed = evidence.observe(s, ORG)
    s.close()

    assert observed.annual_revenue == Decimal("1000000.00")
    assert observed.credit_notes_total == Decimal("118000.00")
    assert any(g["field"] == "credit_notes" and "not subtractable" in g["why"]
               for g in observed.gaps)


def test_several_connected_companies_raise_the_double_counting_gap(
        client_and_maker):
    """Inter-entity sales are counted once in each book, and nothing in the
    schema marks a related party — so the only honest move is to say so."""
    from datetime import date
    from decimal import Decimal

    _, Maker = client_and_maker
    s = Maker()
    today = date.today()
    for i, conn in enumerate(("conn_sls", "conn_4u", "conn_ups")):
        s.add(models.SalesTxn(
            organization_id=ORG, connector="zoho", connection_id=conn,
            external_ref=f"i{i}:1", customer_id="cx", product_id="pr",
            date=today, qty=Decimal("1"), unit_price=Decimal("500000"),
            line_revenue=Decimal("500000")))
    s.commit()
    observed = evidence.observe(s, ORG)
    s.close()

    assert observed.contributing_connections == 3
    assert any(g["field"] == "annual_revenue" and "billed twice" in g["why"]
               for g in observed.gaps)


def test_the_billable_revenue_definition_names_what_it_cannot_settle():
    """Three items the schema cannot answer, marked rather than assumed away."""
    from app.monetization.strategies import BILLABLE_REVENUE

    unsettled = [row for row in BILLABLE_REVENUE if row[1] == "UNSETTLED"]
    assert len(unsettled) == 3
    subjects = " ".join(row[0].lower() for row in unsettled)
    assert "credit notes" in subjects
    assert "entities" in subjects
    for _, _, why in BILLABLE_REVENUE:
        assert why
