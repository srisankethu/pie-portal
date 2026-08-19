"""Performance test for decisions endpoint — simulates the N+1 pattern from the frontend."""
import time
import pytest
from sqlalchemy.orm import sessionmaker
from fastapi import FastAPI
from fastapi.testclient import TestClient

from .conftest import *
import dbsupport
from app.domain import models
from app.seed import SEED_PASSWORD, ensure_org_and_users
from app.db import get_session, Base
from app.routers import platform_auth, internal, decisions


ORG = "org_pie"


@pytest.fixture()
def client_and_maker():
    """Test client with seeded users, and a Maker to seed decisions."""
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

    # seed org + users
    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(internal.router)
    app.include_router(decisions.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app), Maker


def _login(client, email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def _seed_decision(maker, *, dtype, subject_id, assigned_user_id, org=ORG, status="OPEN"):
    s = maker()
    d = models.Decision(
        organization_id=org, decision_type=dtype,
        decision_key=f"{org}:{dtype}:{subject_id}", subject_entity_type="CUSTOMER",
        subject_entity_id=subject_id, assigned_user_id=assigned_user_id,
        assigned_role="SALESPERSON" if assigned_user_id else "OWNER",
        priority_band="MEDIUM", priority_score=50, status=status, ai={"status": "PENDING"})
    s.add(d)
    s.commit()
    s.close()
    return d.decision_id


def test_decisions_n_plus_1_pattern(client_and_maker):
    """Frontend makes N+1 API calls: list then detail for each.

    Reproduces the timeout on Vercel: frontend calls papi.listDecisions() then for
    each in the list calls papi.getDetail(), creating N+1 requests.
    """
    client, maker = client_and_maker

    # Create several decisions
    n_decisions = 10
    print(f"\n\n✓ Creating {n_decisions} decisions...")
    for i in range(n_decisions):
        _seed_decision(maker, dtype="CUSTOMER_DORMANCY", subject_id=f"cust{i}",
                       assigned_user_id="usr_sales")

    sales = _hdr(_login(client, "r.nair@pie.example"))

    # Step 1: list endpoint
    print(f"\n✓ Frontend Step 1: GET /api/v1/decisions")
    t0 = time.time()
    list_resp = client.get("/api/v1/decisions", headers=sales)
    t1 = time.time()
    list_time = (t1 - t0) * 1000
    decisions = list_resp.json()
    print(f"  → {list_time:.0f}ms, {len(decisions)} decisions returned")

    # Step 2: detail endpoint for each (N+1)
    print(f"\n✓ Frontend Step 2: GET /api/v1/decisions/{{id}}/detail for each ({len(decisions)} calls)")
    detail_times = []
    for i, d in enumerate(decisions):
        t_s = time.time()
        resp = client.get(f"/api/v1/decisions/{d['decision_id']}/detail", headers=sales)
        t_e = time.time()
        elapsed = (t_e - t_s) * 1000
        detail_times.append(elapsed)
        if i < 3 or i == len(decisions) - 1:
            print(f"  → Detail {i+1}: {elapsed:.0f}ms")
        elif i == 3:
            print(f"  → ...")

    total_detail = sum(detail_times)
    avg_detail = total_detail / len(detail_times) if detail_times else 0

    print(f"\n📊 Summary:")
    print(f"  List endpoint:     {list_time:.0f}ms")
    print(f"  Detail endpoint:   {len(detail_times)} × {avg_detail:.0f}ms avg = {total_detail:.0f}ms total")
    print(f"  TOTAL FRONTEND:    {list_time + total_detail:.0f}ms")
    print(f"\n⚠️  If production has 50+ decisions, estimated time: {list_time + (50 * avg_detail):.0f}ms")
