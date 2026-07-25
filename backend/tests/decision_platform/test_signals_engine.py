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
from app.seed import ensure_org_and_users
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
                           json={"email": email, "password": "x"}).json()["token"]

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
