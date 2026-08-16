"""The Outcome Tracker: snapshot-on-accept, deterministic evaluation, and the
role-scoped surface.

The evaluator tests hand-compute every expected number in comments — the point
of a deterministic evaluator is that a reader can check it with a pencil. The
API test asserts the §1 shape: a salesperson's response carries no economics,
absent from the body rather than hidden by the client.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.commercial import outcome_tracker
from app.domain import models
from app.domain.enums import HumanAction, OutcomeStatus
from app.repositories import DecisionRepository

ORG = "org_outcomes"


# ── fixture helpers ──────────────────────────────────────────────────────────
def _signal(session, *, stype, subject_type, subject_id, metrics,
            org=ORG, version="th_testversion"):
    s = models.Signal(
        organization_id=org, signal_type=stype,
        subject_entity_type=subject_type, subject_entity_id=subject_id,
        detector_version="v0", threshold_config_version=version,
        window={"basis_period": {"start": "2026-02-01", "end": "2026-05-01"}},
        metrics=metrics, severity_base=50, evidence_refs=[], sufficiency={})
    session.add(s)
    session.flush()
    return s


def _decision(session, *, signal, org=ORG, assigned_user_id=None):
    d = models.Decision(
        organization_id=org, decision_type=signal.signal_type,
        decision_key=f"dk_{signal.signal_id}",
        subject_entity_type=signal.subject_entity_type,
        subject_entity_id=signal.subject_entity_id,
        assigned_user_id=assigned_user_id,
        assigned_role="SALESPERSON" if assigned_user_id else "SALES_MANAGER",
        signal_ids=[signal.signal_id], ai={"status": "PENDING"})
    session.add(d)
    session.flush()
    return d


def _sale(session, *, product, customer, on, qty, price, org=ORG, ref=None):
    qty = Decimal(str(qty))
    price = Decimal(str(price))
    ref = ref or f"inv_{customer}_{product}_{on.isoformat()}"
    session.add(models.SalesTxn(
        organization_id=org, external_ref=ref, customer_id=customer,
        product_id=product, date=on, qty=qty, unit_price=price,
        line_revenue=qty * price,
        source_ref={"system": "zoho", "record_type": "invoice",
                    "record_id": ref.split(":")[0]}))


def _cost(session, *, product, on, unit_cost, org=ORG, ref=None):
    session.add(models.CostRecord(
        organization_id=org, external_ref=ref or f"bill_{product}_{on.isoformat()}",
        product_id=product, date=on, qty=Decimal("1"),
        unit_cost=Decimal(str(unit_cost)),
        source_ref={"system": "zoho", "record_type": "bill"}))


def _snapshot(session, *, category, subject_type, subject_id, baseline,
              accepted, horizon=90, org=ORG, version="th_testversion"):
    row = models.OutcomeSnapshot(
        organization_id=org, decision_id=f"dec_{category}_{subject_id}",
        signal_id=f"sig_{category}_{subject_id}", category=category,
        subject_entity_type=subject_type, subject_entity_id=subject_id,
        baseline_metrics=baseline, baseline_window={},
        thresholds_version=version, horizon_days=horizon, accepted_at=accepted)
    session.add(row)
    session.flush()
    return row


#: Accepted 2026-05-01 10:00 UTC → 15:30 IST, so the business day is 2026-05-01
#: and a 90-day horizon ends 2026-07-30.
ACCEPTED = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
WINDOW_END = date(2026, 7, 30)


# ── capture on accept ────────────────────────────────────────────────────────
def test_accepting_a_decision_writes_one_snapshot_with_the_signals_own_stamp(session):
    metrics = {"baseline_margin_pct": 0.35, "current_margin_pct": 0.22,
               "margin_drop_points": 0.13}
    sig = _signal(session, stype="MARGIN_DETERIORATION", subject_type="PRODUCT",
                  subject_id="prd_1", metrics=metrics, version="th_abc123def4")
    d = _decision(session, signal=sig)

    repo = DecisionRepository(session, ORG)
    repo.record_human_action(d, HumanAction.ACT, actor_user_id="usr_owner")
    session.flush()

    snap = session.scalar(select(models.OutcomeSnapshot))
    assert snap is not None
    assert snap.decision_id == d.decision_id
    assert snap.signal_id == sig.signal_id
    assert snap.category == "MARGIN_DETERIORATION"
    assert snap.baseline_metrics == metrics
    # Verbatim from the signal — the stamp that actually judged it, never the
    # other family's hash.
    assert snap.thresholds_version == "th_abc123def4"
    assert snap.horizon_days == 90
    assert snap.accepted_by_user_id == "usr_owner"

    # Accept → reopen → accept again: one snapshot, anchored at the first
    # acceptance. A second row would be a second baseline for one card.
    repo.record_human_action(d, HumanAction.REOPEN, actor_user_id="usr_owner")
    repo.record_human_action(d, HumanAction.ACT, actor_user_id="usr_owner")
    session.flush()
    count = len(session.scalars(select(models.OutcomeSnapshot)).all())
    assert count == 1


def test_a_decision_with_no_signal_captures_nothing(session):
    """A STATE-derived decision has no signal baseline to freeze; capture is a
    documented no-op, not a row with an empty baseline pretending otherwise."""
    d = models.Decision(
        organization_id=ORG, decision_type="INV_DEAD_STOCK", decision_key="dk_s",
        subject_entity_type="PRODUCT", subject_entity_id="prd_9",
        assigned_role="SALES_MANAGER", origin="STATE", signal_ids=[],
        ai={"status": "NOT_APPLICABLE"})
    session.add(d)
    session.flush()
    DecisionRepository(session, ORG).record_human_action(
        d, HumanAction.ACT, actor_user_id="usr_owner")
    session.flush()
    assert d.status == "ACTIONED"
    assert session.scalar(select(models.OutcomeSnapshot)) is None


def test_dormancy_gets_its_own_default_horizon(session):
    sig = _signal(session, stype="CUSTOMER_DORMANCY", subject_type="CUSTOMER",
                  subject_id="cst_1", metrics={"actual_gap_days": 80})
    d = _decision(session, signal=sig, assigned_user_id="usr_sales")
    DecisionRepository(session, ORG).record_human_action(
        d, HumanAction.ACT, actor_user_id="usr_sales")
    session.flush()
    snap = session.scalar(select(models.OutcomeSnapshot))
    assert snap.horizon_days == 60


# ── evaluation: the horizon gates ────────────────────────────────────────────
def test_before_the_horizon_the_answer_is_pending_and_asserts_nothing(session):
    snap = _snapshot(session, category="MARGIN_DETERIORATION",
                     subject_type="PRODUCT", subject_id="prd_1",
                     baseline={"current_margin_pct": 0.22}, accepted=ACCEPTED)
    ev = outcome_tracker.evaluate(session, snap, as_of=date(2026, 6, 1))
    assert ev.status is OutcomeStatus.PENDING
    assert ev.realised == {} and ev.delta == {}
    assert ev.window_end == WINDOW_END


def test_a_book_not_observed_through_the_window_is_unknown_not_a_zero(session):
    """An empty window in an unsynced book is 'not looked', not 'no sales'."""
    snap = _snapshot(session, category="CUSTOMER_DECLINE",
                     subject_type="CUSTOMER", subject_id="cst_1",
                     baseline={"recent_revenue": 50000.0,
                               "baseline_revenue": 100000.0},
                     accepted=ACCEPTED)
    # Latest sale in the book predates the window end by weeks.
    _sale(session, product="prd_x", customer="cst_other",
          on=date(2026, 6, 15), qty=1, price=10)
    session.flush()
    ev = outcome_tracker.evaluate(session, snap, as_of=date(2026, 8, 16))
    assert ev.status is OutcomeStatus.UNKNOWN
    assert ev.realised == {} and ev.delta == {}
    assert any("observed through 2026-06-15" in m for m in ev.missing)


# ── evaluation: realised, hand-computed ──────────────────────────────────────
def test_margin_horizon_passed_with_evidence_yields_the_hand_computed_delta(session):
    """Window sales: 10 @ ₹100 + 10 @ ₹80 → qty-weighted price 1800/20 = 90.
    Cost basis as of the window end: ₹63. Realised margin (90−63)/90 = 0.30.
    Detection margin 0.35 → delta −0.05 pp; healthy baseline 0.40 → gap 0.10."""
    snap = _snapshot(session, category="MARGIN_DETERIORATION",
                     subject_type="PRODUCT", subject_id="prd_1",
                     baseline={"current_margin_pct": 0.35,
                               "baseline_margin_pct": 0.40},
                     accepted=ACCEPTED)
    _sale(session, product="prd_1", customer="cst_a", on=date(2026, 6, 1),
          qty=10, price=100)
    _sale(session, product="prd_1", customer="cst_b", on=date(2026, 7, 1),
          qty=10, price=80)
    _cost(session, product="prd_1", on=date(2026, 5, 15), unit_cost=63)
    # Coverage: the book must be observed through the window end. A different
    # product's sale on the closing day proves observation without entering the
    # subject's own window arithmetic.
    _sale(session, product="prd_other", customer="cst_z", on=WINDOW_END,
          qty=1, price=10)
    session.flush()

    ev = outcome_tracker.evaluate(session, snap, as_of=date(2026, 8, 16))
    assert ev.status is OutcomeStatus.REALISED
    assert ev.missing == ()
    assert ev.realised["window_avg_unit_price"] == 90.0
    assert ev.realised["window_unit_cost_basis"] == 63.0
    assert ev.realised["realised_margin_pct"] == pytest.approx(0.3)
    assert ev.delta["margin_change_pp_vs_detection"] == pytest.approx(-0.05)
    assert ev.delta["margin_gap_pp_vs_baseline"] == pytest.approx(0.10)


def test_missing_cost_rows_yield_unknown_naming_the_gap(session):
    """Same sales, no cost record at all: the one number that needs cost is
    refused by name — never computed from the rows that happen to exist."""
    snap = _snapshot(session, category="MARGIN_DETERIORATION",
                     subject_type="PRODUCT", subject_id="prd_1",
                     baseline={"current_margin_pct": 0.35}, accepted=ACCEPTED)
    _sale(session, product="prd_1", customer="cst_a", on=date(2026, 6, 1),
          qty=10, price=100)
    _sale(session, product="prd_other", customer="cst_z", on=WINDOW_END,
          qty=1, price=10)
    session.flush()

    ev = outcome_tracker.evaluate(session, snap, as_of=date(2026, 8, 16))
    assert ev.status is OutcomeStatus.UNKNOWN
    assert ev.realised == {} and ev.delta == {}
    assert any("No cost record on or before 2026-07-30" in m
               for m in ev.missing)


def test_a_placeholder_cost_is_refused_not_asserted(session):
    """A zero cost would manufacture a 100% realised margin. The §1 tell —
    `if unit_cost is not None` wrapped around the objection — is exactly what
    this asserts against: the objection fires, the number does not."""
    snap = _snapshot(session, category="MARGIN_DETERIORATION",
                     subject_type="PRODUCT", subject_id="prd_1",
                     baseline={"current_margin_pct": 0.35}, accepted=ACCEPTED)
    _sale(session, product="prd_1", customer="cst_a", on=date(2026, 6, 1),
          qty=10, price=100)
    _cost(session, product="prd_1", on=date(2026, 5, 15), unit_cost=0)
    _sale(session, product="prd_other", customer="cst_z", on=WINDOW_END,
          qty=1, price=10)
    session.flush()

    ev = outcome_tracker.evaluate(session, snap, as_of=date(2026, 8, 16))
    assert ev.status is OutcomeStatus.UNKNOWN
    assert any("unreliable" in m for m in ev.missing)


def test_decline_recovery_is_measured_against_the_frozen_baseline(session):
    """Window revenue: 30,000 + 40,000 = 70,000. Frozen recent 50,000 →
    +20,000; frozen healthy baseline 100,000 → −30,000; recovery 0.7."""
    snap = _snapshot(session, category="CUSTOMER_DECLINE",
                     subject_type="CUSTOMER", subject_id="cst_1",
                     baseline={"recent_revenue": 50000.0,
                               "baseline_revenue": 100000.0},
                     accepted=ACCEPTED)
    _sale(session, product="prd_a", customer="cst_1", on=date(2026, 5, 20),
          qty=30, price=1000, ref="inv_1:1")
    _sale(session, product="prd_b", customer="cst_1", on=date(2026, 7, 10),
          qty=40, price=1000, ref="inv_2:1")
    _sale(session, product="prd_other", customer="cst_z", on=WINDOW_END,
          qty=1, price=10)
    session.flush()

    ev = outcome_tracker.evaluate(session, snap, as_of=date(2026, 8, 16))
    assert ev.status is OutcomeStatus.REALISED
    assert ev.realised["window_revenue"] == "70000.00"
    assert ev.realised["window_orders"] == 2
    assert ev.delta["revenue_vs_recent"] == "20000.00"
    assert ev.delta["revenue_vs_baseline"] == "-30000.00"
    assert ev.delta["revenue_recovery_ratio"] == pytest.approx(0.7)


def test_dormancy_reads_reorder_or_its_absence_from_an_observed_window(session):
    snap = _snapshot(session, category="CUSTOMER_DORMANCY",
                     subject_type="CUSTOMER", subject_id="cst_1",
                     baseline={"actual_gap_days": 80}, accepted=ACCEPTED,
                     horizon=60)
    end = date(2026, 5, 1) + timedelta(days=60)  # 2026-06-30
    _sale(session, product="prd_a", customer="cst_1", on=date(2026, 5, 21),
          qty=1, price=500, ref="inv_9:1")
    _sale(session, product="prd_other", customer="cst_z", on=end,
          qty=1, price=10)
    session.flush()

    ev = outcome_tracker.evaluate(session, snap, as_of=date(2026, 8, 16))
    assert ev.status is OutcomeStatus.REALISED
    assert ev.realised["reordered"] is True
    assert ev.realised["first_order_date"] == "2026-05-21"
    assert ev.delta["days_to_first_order"] == 20


def test_a_category_without_an_evaluator_is_unknown_with_the_reason_named(session):
    snap = _snapshot(session, category="CI_MARGIN_EROSION",
                     subject_type="CUSTOMER_ITEM", subject_id="c::p",
                     baseline={}, accepted=ACCEPTED,
                     version="ci_abc123def4")
    _sale(session, product="prd_other", customer="cst_z", on=WINDOW_END,
          qty=1, price=10)
    session.flush()
    ev = outcome_tracker.evaluate(session, snap, as_of=date(2026, 8, 16))
    assert ev.status is OutcomeStatus.UNKNOWN
    assert any("No deterministic evaluator" in m for m in ev.missing)


# ── the role-scoped surface ──────────────────────────────────────────────────
@pytest.fixture()
def api():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base, get_session
    from app.routers import outcomes as outcomes_router
    from app.routers import platform_auth
    from app.seed import ensure_org_and_users

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(outcomes_router.router)

    def _override():
        sess = maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app), maker


def _login(client, email):
    from app.seed import SEED_PASSWORD
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _seed_accepted_pair(maker):
    """One restricted (margin) outcome and one unrestricted (decline) outcome
    assigned to the seeded salesperson, both accepted through the real hook."""
    s = maker()
    org = "org_pie"
    margin_sig = _signal(s, org=org, stype="MARGIN_DETERIORATION",
                         subject_type="PRODUCT", subject_id="prd_1",
                         metrics={"current_margin_pct": 0.22,
                                  "baseline_margin_pct": 0.35,
                                  "recent_unit_cost": 63.0})
    margin_dec = _decision(s, org=org, signal=margin_sig)
    decline_sig = _signal(s, org=org, stype="CUSTOMER_DECLINE",
                          subject_type="CUSTOMER", subject_id="cst_1",
                          metrics={"recent_revenue": 50000.0,
                                   "baseline_revenue": 100000.0})
    decline_dec = _decision(s, org=org, signal=decline_sig,
                            assigned_user_id="usr_sales")
    repo = DecisionRepository(s, org)
    repo.record_human_action(margin_dec, HumanAction.ACT,
                             actor_user_id="usr_owner")
    repo.record_human_action(decline_dec, HumanAction.ACT,
                             actor_user_id="usr_sales")
    s.commit()
    s.close()


def test_a_salesperson_response_contains_no_economics(api):
    """The §1 shape: cost and margin are absent from the body, not hidden in
    the component. Asserted over the serialized outcomes, so a field added
    anywhere in the payload cannot slip past a per-key assertion."""
    client, maker = api
    _seed_accepted_pair(maker)

    sales = _login(client, "r.nair@pie.example")
    body = client.get("/api/v1/outcomes", headers=sales).json()

    assert body["count"] == 1
    assert body["outcomes"][0]["category"] == "CUSTOMER_DECLINE"
    dumped = json.dumps(body["outcomes"]).lower()
    assert "margin" not in dumped
    assert "cost" not in dumped


def test_management_outcomes_carry_the_economics(api):
    client, maker = api
    _seed_accepted_pair(maker)

    mgr = _login(client, "m.rao@pie.example")
    body = client.get("/api/v1/outcomes", headers=mgr).json()
    assert body["count"] == 2
    by_cat = {o["category"]: o for o in body["outcomes"]}
    margin = by_cat["MARGIN_DETERIORATION"]
    assert margin["baseline_metrics"]["current_margin_pct"] == 0.22
    assert margin["thresholds_version"] == "th_testversion"
    # Accepted moments ago: the horizon has not elapsed, and PENDING asserts
    # nothing rather than showing a delta over an unclosed window.
    assert margin["evaluation"]["status"] == "PENDING"


def test_outcomes_require_a_session(api):
    client, _ = api
    assert client.get("/api/v1/outcomes").status_code == 401


def test_an_unknown_status_filter_is_refused_not_empty(api):
    client, maker = api
    _seed_accepted_pair(maker)
    mgr = _login(client, "m.rao@pie.example")
    r = client.get("/api/v1/outcomes?status_filter=REALIZED", headers=mgr)
    assert r.status_code == 400
    assert "REALISED" in r.json()["detail"]
