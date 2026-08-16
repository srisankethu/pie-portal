"""The `/insight/daily` endpoint answers at all.

`test_daily.py` covers `daily.assemble` thoroughly — 22 tests over the bands,
the windows and the freshness headline. What nothing covered was the router
calling it, and that is where it broke: the `_envelope(currency=…)` →
`_envelope(th=…)` change was applied one level too deep and rewrote the inner
`assemble(…)` call too, which does not take a `th`. The landing screen answered
500 while the whole suite stayed green.

So this asserts the wiring rather than the arithmetic: the request reaches the
builder with arguments it accepts, and comes back as an envelope. It is
deliberately thin — `assemble` already has the unit tests, and duplicating them
through HTTP would be a second copy that drifts.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base, get_session
    from app.routers import insight, platform_auth
    from app.seed import ensure_org_and_users

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight.router)

    def _session():
        db = Maker()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _session
    return TestClient(app)


def _head(client):
    from app.seed import SEED_PASSWORD

    r = client.post("/api/v1/auth/login",
                    json={"email": "s.menon@pie.example", "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_the_morning_read_answers_on_an_empty_book(client):
    """A book with nothing synced is the state a fresh deployment is in, so it
    is the one the landing screen has to survive. Empty is a valid answer; 500
    is not."""
    r = client.get("/api/v1/insight/daily", headers=_head(client))
    assert r.status_code == 200, r.text
    body = r.json()
    # The envelope every insight response carries — and `thresholds_version` in
    # particular, which is the reason `_envelope` takes the thresholds object.
    assert body["currency"]
    assert body["thresholds_version"]
    assert "freshness" in body


def test_choosing_today_counts_document_dates_not_the_sync_that_just_ran(client):
    """The morning after a first sync, **Today** showed the entire book: every
    row the platform held had been "first seen" that day, so the ingest
    calendar answered a question nobody asked. A person choosing a day means
    invoices *dated* that day — the ingest reading is only right when no day
    was chosen, where "what the last sync brought in" is genuinely the
    question."""
    from datetime import date, timedelta

    from app.db import get_session
    from app.domain import models

    today = date.today()
    old_day = today - timedelta(days=40)

    session_gen = client.app.dependency_overrides[get_session]()
    session = next(session_gen)
    try:
        # The whole book arrives in one sync "now": an old invoice and one
        # genuinely dated today. Both have created_at == now.
        for n, d in (("INV-OLD", old_day), ("INV-TODAY", today)):
            session.add(models.InvoiceDoc(
                organization_id="org_sanketh", external_ref=n, number=n,
                date=d, total=1000, balance=1000, status="sent"))
        session.commit()
    finally:
        session_gen.close()

    def invoices(params: str) -> dict:
        r = client.get(f"/api/v1/insight/daily{params}", headers=_head(client))
        assert r.status_code == 200, r.text
        moved = next(b for b in r.json()["bands"] if b["key"] == "MOVED")
        return {"band": moved,
                "tile": next(t for t in moved["tiles"] if t["key"] == "invoices")}

    picked = invoices(f"?moved_from={today.isoformat()}&moved_to={today.isoformat()}")
    assert picked["tile"]["count"] == 1, (
        "Today means invoices dated today — not the whole book the sync "
        "happened to write today")
    assert picked["band"]["question"] == f"Documents dated {today.isoformat()}"

    # And the default view keeps the other calendar: with no day chosen the
    # band is "what the last sync brought in", where both rows belong.
    assert invoices("")["tile"]["count"] == 2

    # A chosen day that predates the book answers zero, not an error.
    lone = today - timedelta(days=400)
    assert invoices(f"?moved_from={lone.isoformat()}&moved_to={lone.isoformat()}"
                    )["tile"]["count"] == 0
