"""The decision card serves the Outcome Tracker's answer for that decision.

`commercial/outcome_tracker` freezes a baseline when a decision is accepted and
recomputes the realised position on read, emitting PENDING / REALISED /
UNKNOWN. `/api/v1/outcomes` has always served that; the *card* — the screen
somebody actually opens to see whether their decision changed anything —
returned a hard-coded ``null``, so the measurement was computed and discarded.

What is asserted here:

  · a REALISED outcome reaches the card with the numbers the tracker computed
  · UNKNOWN stays UNKNOWN, with what is missing named — never a benign zero
  · no snapshot means ``null``, which is an absence, not a pending measurement
  · a salesperson's card carries no cost or margin anywhere in the outcome,
    absent from the body rather than hidden by the component (§1)
"""
from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import sessionmaker

from app import clock
from app.domain import models
from app.domain.enums import HumanAction
from app.repositories import DecisionRepository

ORG = "org_pie"


# ── fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture()
def api(engine):
    """The decisions API over the seeded org, with a real login per role."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.routers import decisions as decisions_router
    from app.routers import platform_auth
    from app.seed import SEED_PASSWORD, ensure_org_and_users

    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(decisions_router.router)

    def _override():
        sess = maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    client = TestClient(app)

    def _hdr(email: str) -> dict:
        r = client.post("/api/v1/auth/login",
                        json={"email": email, "password": SEED_PASSWORD})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    client.hdr = _hdr
    client.maker = maker
    return client


def _decision(session, *, metrics, stype="CUSTOMER_DECLINE",
              assigned_user_id=None, subject="cst_1"):
    sig = models.Signal(
        organization_id=ORG, signal_type=stype, subject_entity_type="CUSTOMER",
        subject_entity_id=subject, detector_version="v0",
        threshold_config_version="th_cardtest",
        window={"basis_period": {"start": "2026-02-01", "end": "2026-05-01"}},
        metrics=metrics, severity_base=50, evidence_refs=[], sufficiency={})
    session.add(sig)
    session.flush()
    d = models.Decision(
        organization_id=ORG, decision_type=stype,
        decision_key=f"dk_{sig.signal_id}", subject_entity_type="CUSTOMER",
        subject_entity_id=subject, assigned_user_id=assigned_user_id,
        assigned_role="SALESPERSON" if assigned_user_id else "SALES_MANAGER",
        signal_ids=[sig.signal_id], ai={"status": "PENDING"})
    session.add(d)
    session.flush()
    return d


def _accept(session, d, *, by="usr_owner"):
    DecisionRepository(session, ORG).record_human_action(
        d, HumanAction.ACT, actor_user_id=by)
    session.flush()


def _sale(session, *, customer, on, qty, price, ref):
    qty, price = Decimal(str(qty)), Decimal(str(price))
    session.add(models.SalesTxn(
        organization_id=ORG, external_ref=ref, customer_id=customer,
        product_id="prd_1", date=on, qty=qty, unit_price=price,
        line_revenue=qty * price,
        source_ref={"system": "zoho", "record_type": "invoice",
                    "record_id": ref}))


def _card(api, headers, decision_id: str) -> dict:
    r = api.get(f"/api/v1/decisions/{decision_id}/detail", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ── the measurement reaches the card ─────────────────────────────────────────
def test_the_card_serves_the_realised_outcome_the_tracker_computed(api):
    """Accepted 200 days ago, horizon 90 days, and the book is observed past
    the window end — so the tracker has a measurement, and the card must show
    the one it computed rather than a null.

    Hand-checkable: one order of 10 × ₹100 inside the window against a frozen
    baseline of ₹50,000 recent / ₹1,00,000 healthy.
    """
    s = api.maker()
    d = _decision(s, metrics={"recent_revenue": 50000.0,
                              "baseline_revenue": 100000.0})
    _accept(s, d)
    snap = s.query(models.OutcomeSnapshot).one()
    accepted = clock.now() - timedelta(days=200)
    snap.accepted_at = accepted
    # The org's own day, not UTC's. ``outcome_tracker`` converts ``accepted_at``
    # through ``clock.to_local`` before taking a date, because the evaluation
    # window is measured in the business's days — and the seeded org is on
    # Asia/Kolkata. Taking ``.date()`` on the UTC value here instead made this
    # assertion fail for every run at or after 18:30 UTC, when the two calendars
    # disagree: a five-and-a-half-hour window each day in which a correct
    # tracker looked wrong.
    start = clock.to_local(accepted).date()
    # Inside the window (start < date ≤ start+90), and one later sale so the
    # book is observed past the window end — otherwise the coverage gate is
    # right to answer UNKNOWN.
    _sale(s, customer="cst_1", on=start + timedelta(days=30), qty=10,
          price=100, ref="inv_in_window")
    _sale(s, customer="cst_1", on=start + timedelta(days=95), qty=1,
          price=1, ref="inv_after_window")
    decision_id = d.decision_id
    s.commit()
    s.close()

    card = _card(api, api.hdr("s.menon@pie.example"), decision_id)

    assert card["outcome"] is not None, (
        "the card dropped the Outcome Tracker's answer")
    outcome = card["outcome"]
    assert outcome["category"] == "CUSTOMER_DECLINE"
    assert outcome["horizon_days"] == 90
    assert outcome["thresholds_version"] == "th_cardtest"
    assert outcome["baseline_metrics"]["recent_revenue"] == 50000.0

    ev = outcome["evaluation"]
    assert ev["status"] == "REALISED"
    assert ev["window"]["start"] == start.isoformat()
    assert ev["window"]["end"] == (start + timedelta(days=90)).isoformat()
    assert ev["realised"]["window_revenue"] == "1000.00"
    assert ev["realised"]["window_orders"] == 1
    assert ev["delta"]["revenue_vs_recent"] == "-49000.00"
    assert ev["delta"]["revenue_vs_baseline"] == "-99000.00"


def test_a_pending_horizon_reaches_the_card_as_pending(api):
    """Accepted moments ago: nothing is asserted in either direction, and the
    card says so rather than showing nothing at all."""
    s = api.maker()
    d = _decision(s, metrics={"recent_revenue": 50000.0,
                              "baseline_revenue": 100000.0})
    _accept(s, d)
    decision_id = d.decision_id
    s.commit()
    s.close()

    outcome = _card(api, api.hdr("s.menon@pie.example"), decision_id)["outcome"]
    assert outcome is not None
    assert outcome["evaluation"]["status"] == "PENDING"
    assert outcome["evaluation"]["realised"] == {}
    assert outcome["evaluation"]["note"]


def test_an_unmeasurable_outcome_stays_unknown_on_the_card(api):
    """The horizon has elapsed but no sale is on record, so an empty window
    cannot be told apart from an unsynced one. UNKNOWN, with the gap named —
    never a zero (§1: absence of evidence is not a pass)."""
    s = api.maker()
    d = _decision(s, metrics={"recent_revenue": 50000.0,
                              "baseline_revenue": 100000.0})
    _accept(s, d)
    s.query(models.OutcomeSnapshot).one().accepted_at = (
        clock.now() - timedelta(days=200))
    decision_id = d.decision_id
    s.commit()
    s.close()

    ev = _card(api, api.hdr("s.menon@pie.example"),
               decision_id)["outcome"]["evaluation"]
    assert ev["status"] == "UNKNOWN"
    assert ev["missing"], "UNKNOWN must name what is missing"
    assert ev["realised"] == {} and ev["delta"] == {}


def test_a_decision_that_was_never_accepted_has_no_outcome(api):
    """No snapshot is an absence, not a measurement — and not a fabricated
    PENDING either."""
    s = api.maker()
    d = _decision(s, metrics={"recent_revenue": 1.0, "baseline_revenue": 2.0})
    decision_id = d.decision_id
    s.commit()
    s.close()

    card = _card(api, api.hdr("s.menon@pie.example"), decision_id)
    assert card["outcome"] is None


# ── role scoping (§1) ────────────────────────────────────────────────────────
def test_a_salespersons_card_carries_no_economics_in_the_outcome(api):
    """Asserted over the serialized outcome, so a restricted key added anywhere
    inside it cannot slip past a per-key assertion. The manager's card carries
    the same key, which is what stops this passing vacuously."""
    s = api.maker()
    d = _decision(s, metrics={"recent_revenue": 50000.0,
                              "baseline_revenue": 100000.0,
                              "recent_unit_cost": 63.0},
                  assigned_user_id="usr_sales")
    _accept(s, d, by="usr_sales")
    decision_id = d.decision_id
    s.commit()
    s.close()

    sales = _card(api, api.hdr("r.nair@pie.example"), decision_id)["outcome"]
    assert sales is not None, "the salesperson's own decision has an outcome"
    dumped = json.dumps(sales).lower()
    assert "cost" not in dumped
    assert "margin" not in dumped

    mgmt = _card(api, api.hdr("m.rao@pie.example"), decision_id)["outcome"]
    assert mgmt["baseline_metrics"]["recent_unit_cost"] == 63.0
