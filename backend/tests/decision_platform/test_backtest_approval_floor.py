"""Backtesting a margin-policy change over recorded quote decisions.

The property that matters most is the one asserted first: the replay must agree
with the evaluator the live quote screen uses. A backtest that quietly disagrees
with the product would send an owner to change a threshold on the strength of a
number the platform never produced.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.commercial import backtest
from app.commercial.config import CommercialThresholds
from app.commercial.quote_intelligence import assess_line
from app.domain import models

ORG = "org_test"
DAY = date(2025, 6, 30)


def _th(**overrides) -> CommercialThresholds:
    return replace(CommercialThresholds(), **overrides)


def _decision(session, *, quote_id: str, line_id: str, price: str, cost: str,
              qty: str = "10", requires_approval: bool = False,
              overridden: bool = False, customer_ref: str = "Acme",
              product_ref: str = "EM-6", as_of: date = DAY,
              thresholds_version: str = "") -> models.QuoteDecision:
    row = models.QuoteDecision(
        organization_id=ORG, quote_id=quote_id, quote_line_id=line_id,
        customer_ref=customer_ref, product_ref=product_ref,
        quantity=Decimal(qty), quoted_unit_price=Decimal(price),
        unit_cost=Decimal(cost), requires_approval=requires_approval,
        overridden=overridden, as_of=as_of,
        thresholds_version=thresholds_version or CommercialThresholds().version)
    session.add(row)
    session.flush()
    return row


# ── the agreement property ───────────────────────────────────────────────────
def test_replayed_verdict_matches_the_live_quote_assessment():
    """``verdict_for`` must reproduce what ``assess_line`` decides.

    Both go through ``build_references`` + ``evaluate``; this asserts the
    backtest's narrower call really does select the same approval outcome, so a
    change to the exception rules moves both together or fails here.
    """
    th = _th(min_margin=0.12)
    cases = [
        # (price, cost) — below cost, below floor, on the floor, comfortably above
        ("90", "100"), ("105", "100"), ("115", "100"), ("200", "100"),
    ]
    for price, cost in cases:
        live = assess_line(
            line_id="L1", customer_id="c1", product_id="p1",
            qty=Decimal("10"), proposed_price=Decimal(price),
            lines=[], costs=[], metrics=None, benchmark=None, family=None,
            as_of=DAY, th=th, item_master_cost=Decimal(cost))
        replayed = backtest.verdict_for(
            quantity=Decimal("10"), quoted_unit_price=Decimal(price),
            unit_cost=Decimal(cost), as_of=DAY, th=th)
        assert replayed.requires_approval == live.requires_approval, (
            f"backtest and quote screen disagree at price={price} cost={cost}")


def test_verdict_is_deterministic_and_does_not_read_the_clock():
    th = _th(min_margin=0.12)
    first = backtest.verdict_for(quantity=Decimal("5"),
                                 quoted_unit_price=Decimal("105"),
                                 unit_cost=Decimal("100"), as_of=DAY, th=th)
    second = backtest.verdict_for(quantity=Decimal("5"),
                                  quoted_unit_price=Decimal("105"),
                                  unit_cost=Decimal("100"), as_of=DAY, th=th)
    assert first == second


# ── the question the tool exists to answer ───────────────────────────────────
def test_raising_the_floor_gates_the_lines_between_the_two_margins(session):
    # 20% margin — clears both floors.  13.04% — clears 12, fails 14.
    _decision(session, quote_id="q1", line_id="l1", price="125", cost="100")
    _decision(session, quote_id="q2", line_id="l2", price="115", cost="100")

    report = backtest.run(session, ORG, min_margin=0.14)

    assert report.lines_examined == 2
    assert [c.quote_line_id for c in report.newly_requires_approval] == ["l2"]
    assert report.no_longer_requires_approval == []


def test_lowering_the_floor_releases_lines_in_the_other_direction(session):
    # 10.71% margin: needs approval under the 12% default, not under 10%.
    # The margin has to straddle the *baseline* policy the harness actually
    # loads. Stamping the row with some other version's hash would not move it:
    # a ``ci_`` hash cannot be resolved back to the values it stood for, which
    # is the same limitation ``baseline_disagreements`` exists to report.
    _decision(session, quote_id="q1", line_id="l1", price="112", cost="100",
              requires_approval=True)

    report = backtest.run(session, ORG, min_margin=0.10)

    assert [c.quote_line_id for c in report.no_longer_requires_approval] == ["l1"]
    assert report.newly_requires_approval == []


def test_shortfall_is_measured_against_the_new_floor(session):
    # cost 100 at a 20% floor -> floor price 125. Quoted 115 on 10 units.
    _decision(session, quote_id="q1", line_id="l1", price="115", cost="100",
              qty="10")

    report = backtest.run(session, ORG, min_margin=0.20)

    (changed,) = report.newly_requires_approval
    assert changed.shortfall_to_new_floor == Decimal("100")
    assert changed.line_revenue == Decimal("1150")
    assert report.shortfall_newly_gated == Decimal("100")
    assert report.revenue_newly_gated == Decimal("1150")


def test_a_line_already_below_the_old_floor_is_not_counted_again(session):
    """Raising the floor must not re-report what the old floor already caught."""
    _decision(session, quote_id="q1", line_id="l1", price="105", cost="100",
              requires_approval=True)

    report = backtest.run(session, ORG, min_margin=0.14)

    assert report.newly_requires_approval == []
    assert report.no_longer_requires_approval == []


# ── absence of evidence is not a pass (CLAUDE.md §1) ─────────────────────────
def test_a_line_with_no_cost_is_unjudgeable_rather_than_passing(session):
    row = models.QuoteDecision(
        organization_id=ORG, quote_id="q1", quote_line_id="l1",
        customer_ref="Acme", product_ref="EM-6", quantity=Decimal("10"),
        quoted_unit_price=Decimal("115"), unit_cost=None, as_of=DAY,
        thresholds_version=CommercialThresholds().version)
    session.add(row)
    session.flush()

    report = backtest.run(session, ORG, min_margin=0.40)

    assert report.unjudgeable == 1
    assert report.newly_requires_approval == []
    assert report.to_dict()["unjudgeable_no_cost_on_record"] == 1


def test_a_placeholder_zero_cost_is_treated_as_no_cost(session):
    _decision(session, quote_id="q1", line_id="l1", price="115", cost="0")

    report = backtest.run(session, ORG, min_margin=0.40)

    assert report.unjudgeable == 1
    assert report.newly_requires_approval == []


def test_a_row_priced_under_an_unrebuildable_policy_is_surfaced(session):
    """A ``ci_`` hash cannot be resolved back to the values it stood for.

    So a row stamped by an older policy may carry a verdict this replay cannot
    reproduce. That is counted and reported, never folded silently into the
    totals.
    """
    _decision(session, quote_id="q1", line_id="l1", price="125", cost="100",
              requires_approval=True, thresholds_version="ci_deadbeef01")

    report = backtest.run(session, ORG, min_margin=0.14)

    assert report.baseline_disagreements == 1
    assert report.to_dict()["thresholds_versions_seen"] == {"ci_deadbeef01": 1}


# ── scoping, grouping and reporting ──────────────────────────────────────────
def test_the_date_window_bounds_what_is_replayed(session):
    _decision(session, quote_id="q1", line_id="l1", price="115", cost="100",
              as_of=date(2025, 1, 15))
    _decision(session, quote_id="q2", line_id="l2", price="115", cost="100",
              as_of=date(2025, 6, 15))

    report = backtest.run(session, ORG, min_margin=0.14,
                          since=date(2025, 6, 1), until=date(2025, 6, 30))

    assert report.lines_examined == 1
    assert [c.quote_line_id for c in report.newly_requires_approval] == ["l2"]


def test_another_organizations_quotes_are_never_read(session):
    _decision(session, quote_id="q1", line_id="l1", price="115", cost="100")
    other = models.QuoteDecision(
        organization_id="org_other", quote_id="q2", quote_line_id="l2",
        quantity=Decimal("10"), quoted_unit_price=Decimal("115"),
        unit_cost=Decimal("100"), as_of=DAY)
    session.add(other)
    session.flush()

    report = backtest.run(session, ORG, min_margin=0.14)

    assert report.lines_examined == 1


def test_grouping_ranks_by_revenue_exposed(session):
    _decision(session, quote_id="q1", line_id="l1", price="115", cost="100",
              qty="100", customer_ref="Big", product_ref="EM-6")
    _decision(session, quote_id="q2", line_id="l2", price="115", cost="100",
              qty="1", customer_ref="Small", product_ref="EM-8")

    report = backtest.run(session, ORG, min_margin=0.14)

    assert [b["name"] for b in report.by_customer()] == ["Big", "Small"]
    assert [b["name"] for b in report.by_product()] == ["EM-6", "EM-8"]


def test_the_report_serialises_money_as_quantized_strings(session):
    """Money as a string, and rounded to paise rather than to the last digit of
    a division. cost 100 at a 14% floor is 116.2790697…, so a 115 quote on ten
    units is short by 12.7906976… — printed as 12.79."""
    _decision(session, quote_id="q1", line_id="l1", price="115", cost="100",
              qty="10")

    payload = backtest.run(session, ORG, min_margin=0.14).to_dict()

    assert payload["shortfall_newly_gated"] == "12.79"
    line = payload["lines"]["newly_requires_approval"][0]
    assert line["quoted_unit_price"] == "115.00"
    assert line["line_revenue"] == "1150.00"


def test_the_report_names_both_policy_versions(session):
    _decision(session, quote_id="q1", line_id="l1", price="115", cost="100")

    report = backtest.run(session, ORG, min_margin=0.14)

    assert report.baseline_version.startswith("ci_")
    assert report.variant_version.startswith("ci_")
    assert report.baseline_version != report.variant_version


def test_the_backtest_writes_nothing(session):
    _decision(session, quote_id="q1", line_id="l1", price="115", cost="100")
    session.commit()

    backtest.run(session, ORG, min_margin=0.14)

    assert not session.new and not session.dirty and not session.deleted


# ── over HTTP ────────────────────────────────────────────────────────────────
# The replay was reachable only as `python -m app.commercial.backtest`, so the
# one edit on the settings screen with a blast radius across every future quote
# was also the one edit nobody could model first. These pin the surface that
# fixed that, and the role boundary it has to hold to be allowed to exist.
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import dbsupport  # noqa: E402
from app.db import get_session  # noqa: E402
from app.routers import admin as admin_router  # noqa: E402
from app.routers import platform_auth  # noqa: E402
from app.seed import SEED_PASSWORD, ensure_org_and_users  # noqa: E402

SEEDED_ORG = "org_pie"
OWNER = "s.menon@pie.example"
MANAGER = "m.rao@pie.example"
SALESPERSON = "r.nair@pie.example"
PATH = "/api/v1/admin/margin-policy/backtest"


@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    # One line comfortably above a 12% floor and under a 25% one, so raising
    # the floor has something to gate and the assertions are about the surface
    # rather than about whether anything was found.
    s.add(models.QuoteDecision(
        organization_id=SEEDED_ORG, quote_id="q1", quote_line_id="l1",
        customer_ref="Acme", product_ref="EM-6", quantity=Decimal("10"),
        quoted_unit_price=Decimal("115"), unit_cost=Decimal("100"),
        requires_approval=False, as_of=DAY,
        thresholds_version=CommercialThresholds().version))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    # Mirrors main.py: /admin is not plan-gated, and the roles are per route.
    app.include_router(admin_router.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def _hdr(c, email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_an_owner_can_model_a_floor_change_before_making_it(client):
    r = client.get(PATH, params={"min_margin": 0.25},
                   headers=_hdr(client, OWNER))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lines_examined"] == 1
    assert body["newly_requires_approval"] == 1
    assert body["policy"]["variant_min_margin"] == 0.25
    assert body["policy"]["baseline_version"] != body["policy"]["variant_version"]


@pytest.mark.parametrize("email", [MANAGER, SALESPERSON])
def test_nobody_but_the_owner_reads_the_replay(client, email):
    """Cost falls out of this response in closed form, not by walking it.

    ``shortfall_to_new_floor`` is ``(floor − price) × quantity`` and the caller
    supplies the margin, so ``cost = (price + shortfall/qty) × (1 − min_margin)``
    — exact, from a single response. It is correct to serve that to an owner,
    who may see cost outright, and to nobody else. The manager half of this is
    a deliberate tightening rather than a §1 requirement: a manager may read
    economics, but only an owner can change a floor, so only an owner has a use
    for modelling one.
    """
    r = client.get(PATH, params={"min_margin": 0.25}, headers=_hdr(client, email))
    assert r.status_code == 403
    assert "shortfall" not in r.text


def test_a_margin_passed_as_a_percentage_is_refused_not_replayed(client):
    """14 for 14% would gate every line in the book and look like a finding.

    The bound is structural rather than a policy choice — the floor is
    ``cost / (1 - margin)``, so 1.0 divides by zero — and it happens to catch
    the mistake the CLI help has always warned about.
    """
    r = client.get(PATH, params={"min_margin": 14}, headers=_hdr(client, OWNER))
    assert r.status_code == 422


def test_modelling_a_floor_does_not_change_the_saved_policy(client):
    """The whole point is asking before committing. Asking must not commit."""
    hdr = _hdr(client, OWNER)
    before = client.get("/api/v1/admin/policy", headers=hdr)
    client.get(PATH, params={"min_margin": 0.25}, headers=hdr)
    after = client.get("/api/v1/admin/policy", headers=hdr)
    assert before.status_code == 200 and before.json() == after.json()
