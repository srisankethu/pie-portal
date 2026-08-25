"""Performance test for decisions endpoint — simulates the N+1 pattern from the frontend."""
import time
import pytest
from sqlalchemy.orm import sessionmaker
from fastapi import FastAPI
from fastapi.testclient import TestClient

import dbsupport
from app.domain import models
from app.seed import SEED_PASSWORD, ensure_org_and_users
from app.db import get_session
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
    print("\n✓ Frontend Step 1: GET /api/v1/decisions")
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
            print("  → ...")

    total_detail = sum(detail_times)
    avg_detail = total_detail / len(detail_times) if detail_times else 0

    print("\n📊 Summary:")
    print(f"  List endpoint:     {list_time:.0f}ms")
    print(f"  Detail endpoint:   {len(detail_times)} × {avg_detail:.0f}ms avg = {total_detail:.0f}ms total")
    print(f"  TOTAL FRONTEND:    {list_time + total_detail:.0f}ms")
    print(f"\n⚠️  If production has 50+ decisions, estimated time: {list_time + (50 * avg_detail):.0f}ms")


# ── the N+1 the include_detail flag was supposed to remove ──────────────────
#
# The first version of that flag said "in one pass to avoid N+1" and moved N
# HTTP round trips into one request that did strictly more database work than
# the N had: a fresh `Companies` per row — the exact thing that class's own
# docstring says it exists to be built once — plus a subject `get`, a
# `user_names` call, a snapshot query and an `Organization` fetch, each per
# card. Counted here rather than argued, because "avoids N+1" is a claim about
# query growth and nothing was measuring it.

def _count_queries(engine, fn):
    """Statements issued while `fn` runs. The measurement the claim needs."""
    from sqlalchemy import event

    seen = []

    def before(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", before)
    return len(seen)


def test_detail_for_ten_cards_does_not_cost_ten_times_detail_for_one(
        client_and_maker):
    """Query count must be near-flat in the number of decisions, not linear.

    Ten cards are allowed to cost more than one — each still resolves its own
    row. What they may not do is multiply the per-page work: with the prefetch
    removed this ratio was above 5, because five separate lookups ran per card.
    """
    client, maker = client_and_maker
    engine = maker.kw["bind"]
    tok = _login(client, "s.menon@pie.example")

    for i in range(1):
        _seed_decision(maker, dtype="MARGIN_EROSION", subject_id=f"one-{i}",
                       assigned_user_id=None)
    one = _count_queries(engine, lambda: client.get(
        "/api/v1/decisions?include_detail=true", headers=_hdr(tok)))

    for i in range(9):
        _seed_decision(maker, dtype="MARGIN_EROSION", subject_id=f"ten-{i}",
                       assigned_user_id=None)
    ten = _count_queries(engine, lambda: client.get(
        "/api/v1/decisions?include_detail=true", headers=_hdr(tok)))

    assert ten < one * 3, (
        f"ten cards cost {ten} queries against {one} for a single card — the "
        "per-page work is still being done per card")


def test_both_list_shapes_agree_about_the_company_badge(client_and_maker):
    """One badge, however the row was asked for.

    Written expecting a regression that turned out not to exist — worth saying,
    because the reasoning that predicted it was wrong in an instructive way. The
    `include_detail` branch does rebuild the summary from scratch and discard
    the one the loop above computed, but `_detail` *also* derives
    `subject_origin` and `sources_differ`, so the merge put them back. What that
    cost was a second derivation per card through its own `Companies` — a
    performance defect, not a correctness one, and the test above is the one
    that catches it.

    Kept because two derivations of one badge now exist in the file's history
    and only one does in its present: a queue pooled across three connected
    books lists the same customer name three times, and the two shapes must
    never start disagreeing about which book each is.
    """
    client, maker = client_and_maker
    tok = _login(client, "s.menon@pie.example")
    # A real master row, so the badge is actually computed. Without one both
    # paths answer None and the test passes on a coincidence.
    s = maker()
    s.add(models.Customer(customer_id="c-badge", organization_id=ORG,
                          external_id="c-badge", name="Pitti Engineering",
                          connector="zoho", connection_id="conn-1"))
    s.commit()
    s.close()
    _seed_decision(maker, dtype="MARGIN_EROSION", subject_id="c-badge",
                   assigned_user_id=None)

    plain = client.get("/api/v1/decisions", headers=_hdr(tok)).json()
    detailed = client.get("/api/v1/decisions?include_detail=true",
                          headers=_hdr(tok)).json()

    assert len(plain) == len(detailed) == 1
    assert plain[0]["subject_origin"] is not None, "the badge was not computed"
    for key in ("subject_origin", "sources_differ"):
        assert key in detailed[0], f"{key} was dropped by include_detail"
        assert detailed[0][key] == plain[0][key]
