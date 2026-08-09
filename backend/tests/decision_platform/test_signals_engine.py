"""Signal Engine end to end: DB persistence, provenance, write-once, and the
owner-only internal endpoint."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.domain import models
from app.routers import internal, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users
from app.signals.engine import run_detectors

from .signal_fixtures import (
    cost_pass_through_product,
    declining_customer,
    dormant_customer,
    margin_deterioration_product,
)

ORG = "org_sanketh"


def _insert(session, org, sales=(), costs=()):
    for s in sales:
        session.add(models.SalesTxn(
            organization_id=org, external_ref=s.external_ref, customer_id=s.customer_id,
            product_id=s.product_id, date=s.date, qty=s.qty, unit_price=s.unit_price,
            line_revenue=s.line_revenue, source_ref=s.source_ref))
    for c in costs:
        session.add(models.CostRecord(
            organization_id=org, external_ref=c.external_ref, product_id=c.product_id,
            date=c.date, qty=c.qty, unit_cost=c.unit_cost, source_ref=c.source_ref))
    session.flush()


def _load_all(session, org):
    m_sales, m_costs = margin_deterioration_product()
    c_sales, c_costs = cost_pass_through_product()
    _insert(session, org,
            sales=declining_customer() + dormant_customer() + m_sales + c_sales,
            costs=m_costs + c_costs)


def test_run_detectors_persists_with_provenance(session):
    _load_all(session, ORG)
    session.commit()
    result = run_detectors(session, ORG)
    session.commit()

    assert result["signals_emitted"] >= 4
    assert set(result["by_type"]) == {
        "CUSTOMER_DECLINE", "CUSTOMER_DORMANCY", "MARGIN_DETERIORATION", "COST_PASS_THROUGH"}

    rows = session.query(models.Signal).all()
    assert len(rows) == result["signals_emitted"]
    for sig in rows:
        assert sig.organization_id == ORG
        assert sig.detector_version and sig.threshold_config_version.startswith("th_")
        assert sig.evidence_refs                     # every signal cites source records
        assert "level" in sig.sufficiency
        assert isinstance(sig.severity_base, int)


def test_the_owner_editable_threshold_governs_what_reaches_the_queue(session):
    """Raising the erosion threshold in Settings must quieten the queue too.

    It used to quieten only the commercial screens. The queue ran on
    `SignalThresholds.margin_drop_points`, reachable solely by redeploying with
    `SIG_MARGIN_DROP_POINTS`, so an owner turning the knob watched the screens go
    silent while the decisions kept arriving — a control that did half of what it
    said, with no way to tell from the outside which half.
    """
    from app.commercial.policy import save_for_org

    _load_all(session, ORG)
    session.commit()

    before = run_detectors(session, ORG)
    assert "MARGIN_DETERIORATION" in before["by_type"], (
        "the fixture must deteriorate at the default threshold, or this proves nothing")

    # 20 pp: wider than the fixture's drop, so the queue should fall silent.
    save_for_org(session, ORG, {"queue_margin_drop_pp": 0.20})
    session.commit()
    after = run_detectors(session, ORG)
    assert "MARGIN_DETERIORATION" not in after["by_type"]

    # The stamp moves with the threshold, so a signal says which one judged it.
    # Without this, two runs under different policies would be indistinguishable
    # after the fact — which is the whole point of stamping a version at all.
    assert before["threshold_config_version"] != after["threshold_config_version"]

    # And back: the threshold is the only thing that changed.
    save_for_org(session, ORG, {"queue_margin_drop_pp": 0.05})
    session.commit()
    assert "MARGIN_DETERIORATION" in run_detectors(session, ORG)["by_type"]


def test_an_explicitly_passed_threshold_set_is_left_alone(session):
    """Passing thresholds is a deliberate act; the org's policy must not override it."""
    from dataclasses import replace

    from app.commercial.policy import save_for_org
    from app.signals.config import load_thresholds

    _load_all(session, ORG)
    save_for_org(session, ORG, {"queue_margin_drop_pp": 0.20})
    session.commit()

    explicit = replace(load_thresholds(), margin_drop_points=0.01)
    assert "MARGIN_DETERIORATION" in run_detectors(session, ORG, explicit)["by_type"]


def test_signals_are_write_once_rerun_adds_new(session):
    _load_all(session, ORG)
    session.commit()
    first = run_detectors(session, ORG)
    session.commit()
    second = run_detectors(session, ORG)          # re-run
    session.commit()
    total = session.query(models.Signal).count()
    assert total == first["signals_emitted"] + second["signals_emitted"]


def test_no_data_no_signals(session):
    assert run_detectors(session, ORG)["signals_emitted"] == 0


# ── endpoint ──────────────────────────────────────────────────────────────────
@pytest.fixture()
def client_and_maker():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    s = Maker()
    ensure_org_and_users(s)
    _load_all(s, ORG)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(internal.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app), Maker


def test_detectors_run_endpoint_owner_only(client_and_maker):
    client, Maker = client_and_maker

    def tok(email):
        return client.post("/api/v1/auth/login",
                           json={"email": email, "password": SEED_PASSWORD}).json()["token"]

    # salesperson forbidden
    r = client.post("/api/v1/internal/detectors/run",
                    headers={"Authorization": f"Bearer {tok('r.nair@sanketh.in')}"})
    assert r.status_code == 403

    # owner runs the engine
    r = client.post("/api/v1/internal/detectors/run",
                    headers={"Authorization": f"Bearer {tok('s.menon@sanketh.in')}"})
    assert r.status_code == 200
    body = r.json()
    assert body["signals_emitted"] >= 4
    with Maker() as s:
        assert s.query(models.Signal).count() == body["signals_emitted"]
