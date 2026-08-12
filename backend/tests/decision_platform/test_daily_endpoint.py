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
