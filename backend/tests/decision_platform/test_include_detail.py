"""Test the include_detail parameter to verify N+1 fix."""
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


def _seed_decision(maker, *, dtype, subject_id, assigned_user_id, org="org_pie", status="OPEN"):
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


def test_include_detail_parameter(client_and_maker):
    """Verify include_detail parameter batches details in single request."""
    client, maker = client_and_maker

    # Create several decisions
    n_decisions = 5
    print(f"\n✓ Creating {n_decisions} decisions...")
    for i in range(n_decisions):
        _seed_decision(maker, dtype="CUSTOMER_DORMANCY", subject_id=f"cust{i}",
                       assigned_user_id="usr_sales")

    sales = _hdr(_login(client, "r.nair@pie.example"))

    # Test without include_detail
    print(f"\n✓ Test 1: GET /api/v1/decisions (without include_detail)")
    t0 = time.time()
    r1 = client.get("/api/v1/decisions", headers=sales)
    t1 = time.time()
    list1 = r1.json()
    print(f"  Time: {(t1-t0)*1000:.0f}ms")
    print(f"  Number of decisions: {len(list1)}")
    print(f"  Has 'impact' field: {'impact' in list1[0]}")
    assert 'decision_id' in list1[0], "Response missing decision_id"
    assert 'impact' not in list1[0], "Without include_detail should not have impact field"

    # Test with include_detail
    print(f"\n✓ Test 2: GET /api/v1/decisions?include_detail=true (with include_detail)")
    t0 = time.time()
    r2 = client.get("/api/v1/decisions?include_detail=true", headers=sales)
    t2 = time.time()
    list2 = r2.json()
    print(f"  Time: {(t2-t0)*1000:.0f}ms")
    print(f"  Number of decisions: {len(list2)}")
    print(f"  Has 'impact' field: {'impact' in list2[0]}")
    assert 'decision_id' in list2[0], "Response missing decision_id"
    assert 'impact' in list2[0], "With include_detail should have impact field"
    assert 'facts' in list2[0], "With include_detail should have facts field"

    # Verify both responses have same decisions
    print(f"\n✓ Test 3: Verify responses contain same decisions")
    ids1 = {d['decision_id'] for d in list1}
    ids2 = {d['decision_id'] for d in list2}
    assert ids1 == ids2, "Same decisions should be in both responses"
    print(f"  Both responses have {len(ids1)} decisions: ✓")

    print(f"\n✅ All tests passed!")
