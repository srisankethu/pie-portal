"""Self-serve sign-up, and the checklist that says what is left.

Four claims get pinned, and three of them are about refusing rather than about
creating — which is the right ratio for the only unauthenticated write in this
application.

**Sign-up is off unless a deployment turns it on**, and when it is off the
endpoint is not a door somebody lacks a key to: it is absent.

**A stranger who signs up lands on the free plan**, whatever ``DEFAULT_PLAN``
says. That setting defaults to *platform* so an existing single-tenant install
keeps every feature it has, so a sign-up that inherited it would hand the top
tier to anyone who can reach the form. This is the test that would catch it.

**A password the account holder chose is not a password they must change.**
The forced-change gate is unconditional in `authz.current_principal`, so getting
this wrong does not produce a cosmetic nag — it produces an account whose fresh
token 403s on every request but one, thirty seconds after signing up. The test
therefore uses the token rather than reading the flag.

**The checklist is derived, and absence of evidence is not a pass.** A pull that
finished having read nothing is the case that matters: it is the one that looks
like success from every angle except the only one that counts.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import clock, entitlements, onboarding
import dbsupport
from app.config import settings
from app.db import get_session
from app.domain import models
from app.domain.enums import PlanTier, Role
from app.passwords import verify_password

GOOD = {"company": "Acme Distributors", "name": "A. Owner",
        "email": "owner@acme.in", "password": "a-long-enough-password"}


@pytest.fixture()
def on(monkeypatch):
    """Sign-up offered, and the rate limit out of the way of the other tests."""
    monkeypatch.setattr(settings, "SELF_SERVE_SIGNUP", True)
    monkeypatch.setattr(settings, "SIGNUP_RATE_LIMIT_PER_HOUR", 0)
    return True


@pytest.fixture()
def client(monkeypatch):
    """The sign-up and onboarding routes over HTTP, on an isolated database.

    `admin` is mounted too, because one test needs a route that is *not* the
    password change to prove a fresh token actually works — reading the flag
    would only prove the flag.
    """
    from app.routers import admin, onboarding as onboarding_router, platform_auth

    # Each client gets its own limiter state; the counter is process-wide by
    # design (see the router), so a test that fills it would otherwise leak into
    # whichever test ran next.
    monkeypatch.setattr(onboarding_router, "_RECENT", {})

    eng = dbsupport.fresh_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False,
                         future=True)

    app = FastAPI()
    app.include_router(onboarding_router.router)
    app.include_router(platform_auth.router)
    app.include_router(admin.router)

    def _override():
        sess = maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    c = TestClient(app)
    c.maker = maker          # for the tests that assert on rows
    return c


# ── the door is only there when the deployment opens it ──────────────────────
def test_signup_is_absent_unless_the_deployment_offers_it(client, monkeypatch):
    monkeypatch.setattr(settings, "SELF_SERVE_SIGNUP", False)

    assert client.get("/api/v1/signup").json()["enabled"] is False
    r = client.post("/api/v1/signup", json=GOOD)
    assert r.status_code == 404
    with client.maker() as s:
        assert s.scalars(select(models.User)).all() == []


def test_the_offer_says_what_a_signup_gets(client, on):
    body = client.get("/api/v1/signup").json()
    assert body["enabled"] is True
    assert body["plan"] == "free"
    assert body["trial_days"] == settings.INTELLIGENCE_TRIAL_DAYS


def test_the_offer_carries_the_plan_ladder(client, on):
    """So the form can ask which plan a business wants without holding its own
    copy of what the plans are — the copy goes stale the first time a feature
    moves between tiers."""
    plans = client.get("/api/v1/signup").json()["plans"]
    assert [p["plan"] for p in plans] == [p.value for p in PlanTier]
    assert all(p["label"] and p["summary"] for p in plans)
    # No prices. They are marketing copy and live on the landing page only;
    # a second copy of a price is worse than a second copy of a feature list.
    assert not any("₹" in p["summary"] for p in plans)


# ── the plan question, which is a question and not a purchase ────────────────
def test_asking_for_the_top_plan_still_creates_a_free_account(client, on):
    """The whole safety property of putting a plan picker on an unauthenticated
    form. `entitlements` is explicit that plans are set by the operator and
    never by a tenant, so the form must record the answer and grant nothing —
    a sign-up that honoured this field would be the plan-setting API that
    deliberately does not exist."""
    org_id = client.post(
        "/api/v1/signup",
        json={**GOOD, "plan": "platform"}).json()["organization_id"]

    with client.maker() as s:
        org = s.get(models.Organization, org_id)
        assert org.plan == PlanTier.FREE.value
        assert org.requested_plan == PlanTier.PLATFORM.value
        # And the resolution path has not heard of the request.
        assert entitlements.effective_plan(s, org_id) is PlanTier.FREE
        assert entitlements.allows(
            entitlements.effective_plan(s, org_id), "multi_company") is False
        assert entitlements.licensed_plan(org) is PlanTier.FREE


def test_a_request_is_findable_by_the_operator_who_can_act_on_it(client, on):
    """Recorded so somebody can answer "who asked for Commercial Intelligence".
    A form that discards its own answer is worse than one that never asked."""
    org_id = client.post(
        "/api/v1/signup",
        json={**GOOD, "plan": "intelligence"}).json()["organization_id"]

    with client.maker() as s:
        org = s.get(models.Organization, org_id)
        assert entitlements.wants_more(org) is PlanTier.INTELLIGENCE
        # Once granted, the request stops being reported: a standing "you asked
        # for this" on a plan you already have reads as a request ignored.
        entitlements.set_plan(s, org_id, PlanTier.INTELLIGENCE)
        assert entitlements.wants_more(s.get(models.Organization, org_id)) is None


def test_not_choosing_a_plan_records_nothing(client, on):
    org_id = client.post("/api/v1/signup", json=GOOD).json()["organization_id"]
    with client.maker() as s:
        org = s.get(models.Organization, org_id)
        assert org.requested_plan is None
        assert entitlements.wants_more(org) is None


def test_a_plan_the_server_does_not_know_is_refused_before_anything_is_written(
        client, on):
    """Unlike `entitlements.parse_plan`, which reads config and degrades to free.
    This reads a form: an unrecognised value means the client and the server
    disagree about what the plans are, and recording the wrong answer to the one
    question this endpoint asks is worse than saying so."""
    r = client.post("/api/v1/signup", json={**GOOD, "plan": "enterprise"})
    assert r.status_code == 400, r.text
    assert "not one of the plans" in r.json()["detail"]
    with client.maker() as s:
        assert s.scalars(select(models.Organization)).all() == []
        assert s.scalars(select(models.User)).all() == []


# ── what a sign-up creates ───────────────────────────────────────────────────
def test_signup_creates_a_tenant_and_signs_its_owner_in(client, on):
    r = client.post("/api/v1/signup", json=GOOD)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == Role.OWNER.value
    assert body["organization_id"] == "org_acme_distributors"
    assert body["email"] == "owner@acme.in"
    assert body["token"]

    with client.maker() as s:
        org = s.get(models.Organization, body["organization_id"])
        assert org is not None and org.name == "Acme Distributors"
        user = s.scalar(select(models.User).where(
            models.User.email == "owner@acme.in"))
        assert user.role == Role.OWNER.value and user.active
        # Stored as a hash, and it is the password they chose.
        assert user.password_hash != GOOD["password"]
        assert verify_password(GOOD["password"], user.password_hash)


def test_the_owner_can_sign_in_again_with_the_password_they_chose(client, on):
    client.post("/api/v1/signup", json=GOOD)
    r = client.post("/api/v1/auth/login",
                    json={"email": GOOD["email"], "password": GOOD["password"]})
    assert r.status_code == 200
    assert r.json()["organization_id"] == "org_acme_distributors"


def test_a_chosen_password_does_not_force_a_change(client, on):
    """Not read off the flag — spent on a route the gate would refuse.

    `current_principal` 403s every path but the password change while the flag
    is set, so a wrong answer here is an account that cannot use the product it
    just signed up for.
    """
    token = client.post("/api/v1/signup", json=GOOD).json()["token"]
    r = client.get("/api/v1/admin/users",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert [u["email"] for u in r.json()["users"]] == ["owner@acme.in"]


def test_a_signup_lands_on_free_whatever_the_deployment_default_is(client, on,
                                                                   monkeypatch):
    """The one that matters. DEFAULT_PLAN is *platform* out of the box."""
    monkeypatch.setattr(settings, "DEFAULT_PLAN", "platform")
    org_id = client.post("/api/v1/signup", json=GOOD).json()["organization_id"]

    with client.maker() as s:
        org = s.get(models.Organization, org_id)
        # Stamped on the row, not left NULL to inherit the default.
        assert org.plan == PlanTier.FREE.value
        assert entitlements.effective_plan(s, org_id) is PlanTier.FREE
        assert entitlements.allows(
            entitlements.effective_plan(s, org_id), "multi_company") is False


def test_a_signup_does_not_start_its_own_trial(client, on):
    """The free month is keyed to the connected books, so signing up buys
    nothing until a Zoho company is connected — which is what stops a second
    email address from being a second free month."""
    org_id = client.post("/api/v1/signup", json=GOOD).json()["organization_id"]
    with client.maker() as s:
        assert entitlements.trial_for(s, org_id) is None


# ── refusals ─────────────────────────────────────────────────────────────────
def test_a_second_signup_with_the_same_address_is_refused(client, on):
    assert client.post("/api/v1/signup", json=GOOD).status_code == 201
    r = client.post("/api/v1/signup",
                    json={**GOOD, "company": "Somebody Else"})
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]
    with client.maker() as s:
        assert len(s.scalars(select(models.Organization)).all()) == 1


@pytest.mark.parametrize("field,value,expected", [
    ("password", "short", "at least"),
    ("email", "not-an-email", "email address"),
    ("company", "   ", "company name is required"),
    ("name", "   ", "Your name is required"),
])
def test_bad_details_are_refused_with_a_sentence(client, on, field, value,
                                                 expected):
    r = client.post("/api/v1/signup", json={**GOOD, field: value})
    assert r.status_code == 400, r.text
    assert expected in r.json()["detail"]
    with client.maker() as s:
        assert s.scalars(select(models.Organization)).all() == []


def test_the_rate_limit_stops_a_loop(client, monkeypatch):
    monkeypatch.setattr(settings, "SELF_SERVE_SIGNUP", True)
    monkeypatch.setattr(settings, "SIGNUP_RATE_LIMIT_PER_HOUR", 2)

    for n in range(2):
        r = client.post("/api/v1/signup",
                        json={**GOOD, "email": f"o{n}@acme.in",
                              "company": f"Acme {n}"})
        assert r.status_code == 201, r.text
    blocked = client.post("/api/v1/signup",
                          json={**GOOD, "email": "o3@acme.in", "company": "Acme 3"})
    assert blocked.status_code == 429


# ── the checklist is derived, never a stored flag ────────────────────────────
@pytest.fixture()
def org(session):
    session.add(models.Organization(organization_id="org_c", name="C"))
    session.add(models.User(organization_id="org_c", email="o@c.in", name="O",
                            role=Role.OWNER.value, active=True,
                            password_hash="x"))
    session.flush()
    return "org_c"


def _steps(session, org) -> dict:
    return {s["key"]: s for s in onboarding.checklist(session, org)["steps"]}


def _connection(session, org, **kw):
    row = models.ZohoConnection(organization_id=org, zoho_organization_id="z1",
                                label="Books", enabled=True, **kw)
    session.add(row)
    session.flush()
    return row


def _run(session, org, **kw):
    row = models.SyncRun(organization_id=org, source="fixture",
                         started_at=clock.now(), **kw)
    session.add(row)
    session.flush()
    return row


def test_a_fresh_organization_has_done_nothing(session, org):
    view = onboarding.checklist(session, org)
    assert view["complete"] is False
    assert view["remaining"] == 4
    assert view["remaining_required"] == 2
    assert [s["key"] for s in view["steps"]] == ["connect", "history", "policy",
                                                 "team"]
    assert all(s["detail"] for s in view["steps"])   # never "not done" bare


def test_connecting_completes_the_step_but_says_it_is_unchecked(session, org):
    _connection(session, org)
    step = _steps(session, org)["connect"]
    assert step["done"] is True
    assert "not been checked" in step["detail"]


def test_a_failing_check_reports_zohos_own_reason(session, org):
    _connection(session, org, last_checked_at=clock.now(), last_check_ok=False,
                last_check_detail="Refresh token was revoked.")
    assert _steps(session, org)["connect"]["detail"] == "Refresh token was revoked."


def test_a_disabled_connection_is_not_a_connection(session, org):
    row = _connection(session, org)
    row.enabled = False
    session.flush()
    step = _steps(session, org)["connect"]
    assert step["done"] is False
    assert "switched off" in step["detail"]


def test_a_pull_that_read_nothing_is_not_a_pull(session, org):
    """Absence of evidence is not a pass. The run says OK and wrote no rows —
    a granted-but-scopeless connection looks exactly like this."""
    _run(session, org, status="OK", sales_txns=0, cost_records=0)
    step = _steps(session, org)["history"]
    assert step["done"] is False
    assert "without reading any documents" in step["detail"]


def test_a_running_pull_is_not_a_finished_one(session, org):
    _run(session, org, status="RUNNING")
    assert _steps(session, org)["history"]["done"] is False


def test_a_partial_pull_that_landed_rows_counts(session, org):
    _run(session, org, status="PARTIAL", sales_txns=120, cost_records=30)
    step = _steps(session, org)["history"]
    assert step["done"] is True
    assert "150" in step["detail"]


def test_a_failed_pull_carries_its_error(session, org):
    _run(session, org, status="FAILED", error="Zoho returned 401.")
    step = _steps(session, org)["history"]
    assert step["done"] is False and step["detail"] == "Zoho returned 401."


def test_the_optional_steps_do_not_hold_completion_back(session, org):
    """Connected and pulled is a working platform. Floors and a team are worth
    doing and must not make the panel permanent."""
    _connection(session, org, last_checked_at=clock.now(), last_check_ok=True)
    _run(session, org, status="OK", sales_txns=10, cost_records=5)

    view = onboarding.checklist(session, org)
    assert view["complete"] is True
    assert view["remaining"] == 2            # policy and team still undone
    assert view["remaining_required"] == 0


def test_policy_and_team_complete_on_real_evidence(session, org):
    session.add(models.CommercialPolicy(organization_id=org,
                                        overrides={"min_margin": 0.18}))
    session.add(models.User(organization_id=org, email="s@c.in", name="S",
                            role=Role.SALESPERSON.value, active=True,
                            password_hash="x"))
    session.flush()
    steps = _steps(session, org)
    assert steps["policy"]["done"] is True
    assert steps["team"]["done"] is True


def test_the_checklist_is_scoped_to_one_organization(session, org):
    """Another tenant's connection must not tick this tenant's box."""
    session.add(models.Organization(organization_id="org_other", name="Other"))
    session.flush()
    _connection(session, "org_other")
    assert _steps(session, org)["connect"]["done"] is False
