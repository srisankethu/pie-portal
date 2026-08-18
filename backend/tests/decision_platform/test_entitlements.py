"""Plans, the one-per-books trial, and the boundaries they put teeth behind.

The pricing terms this enforces were prose until now, and prose is a
suggestion to a strategic customer. Three claims get pinned. A plan resolves
safely: NULL falls back to the deployment default, and an unrecognised value
degrades to free — a typo must never widen what a tenant may use. The free
intelligence month belongs to the *connected books*: reconnecting the same
Zoho company under a fresh organization finds the trial already spent. And a
second connected company is refused below the platform plan at the moment of
connection — the licence unit is the one thing that can be enforced
mechanically.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app import clock, entitlements
import dbsupport
from app.db import get_session
from app.domain import models
from app.domain.enums import PlanTier
from app.ingestion import connections as conn
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_t"
ORG2 = "org_t2"


@pytest.fixture()
def orgs(session):
    session.add(models.Organization(organization_id=ORG, name="T"))
    session.add(models.Organization(organization_id=ORG2, name="T2"))
    session.flush()


def _free_default(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DEFAULT_PLAN", "free")


# ── plan resolution ──────────────────────────────────────────────────────────
def test_null_plan_resolves_to_the_deployment_default(session, orgs, monkeypatch):
    from app.config import settings

    assert entitlements.effective_plan(session, ORG) is PlanTier(settings.DEFAULT_PLAN)
    _free_default(monkeypatch)
    assert entitlements.effective_plan(session, ORG) is PlanTier.FREE


def test_the_rows_own_plan_beats_the_default(session, orgs, monkeypatch):
    _free_default(monkeypatch)
    entitlements.set_plan(session, ORG, PlanTier.INTELLIGENCE)
    assert entitlements.effective_plan(session, ORG) is PlanTier.INTELLIGENCE


def test_an_unrecognised_plan_degrades_to_free_never_wider(session, orgs, monkeypatch):
    """A typo in the row or the env must not hand a tenant the platform plan."""
    org = session.get(models.Organization, ORG)
    org.plan = "platinum"  # not a plan
    session.flush()
    assert entitlements.effective_plan(session, ORG) is PlanTier.FREE

    from app.config import settings

    org.plan = None
    monkeypatch.setattr(settings, "DEFAULT_PLAN", "unlimited")  # not a plan
    assert entitlements.effective_plan(session, ORG) is PlanTier.FREE


# ── the trial belongs to the books ───────────────────────────────────────────
def test_the_free_month_is_per_books_not_per_signup(session, orgs, monkeypatch):
    _free_default(monkeypatch)
    trial = entitlements.begin_trial(session, ORG, "60005555")
    assert trial is not None
    assert entitlements.effective_plan(session, ORG) is PlanTier.INTELLIGENCE

    # The farming move: a fresh organization reconnects the same company.
    assert entitlements.begin_trial(session, ORG2, "60005555") is None
    assert entitlements.effective_plan(session, ORG2) is PlanTier.FREE

    # Different books are a different business — they get their own month.
    assert entitlements.begin_trial(session, ORG2, "60007777") is not None


def test_an_expired_trial_is_spent_not_deleted(session, orgs, monkeypatch):
    _free_default(monkeypatch)
    trial = entitlements.begin_trial(session, ORG, "60005555")
    trial.ends_at = clock.now() - timedelta(days=1)
    session.flush()
    assert entitlements.effective_plan(session, ORG) is PlanTier.FREE
    # The record remains, which is exactly what the next attempt must find.
    assert entitlements.begin_trial(session, ORG, "60005555") is None


def test_a_trial_lifts_to_intelligence_never_to_platform(session, orgs, monkeypatch):
    _free_default(monkeypatch)
    entitlements.begin_trial(session, ORG, "60005555")
    entitlements.assert_feature(session, ORG, "intelligence")  # no raise
    with pytest.raises(entitlements.PlanRefused):
        entitlements.assert_feature(session, ORG, "multi_company")


# ── what a screen is told about the trial ────────────────────────────────────
def test_describe_counts_the_days_left_and_names_what_expires(session, orgs,
                                                              monkeypatch):
    """The countdown a screen renders, and the list of what it costs.

    `loses_on_expiry` is derived from the plan map rather than hardcoded in the
    client, so a feature moving between tiers cannot leave the two disagreeing
    about what a tenant is about to lose.
    """
    _free_default(monkeypatch)
    # Freeze the clock at local mid-day so the day count is deterministic.
    # `days_remaining` is `(ends_at.date() - today).days` in the org's zone, and
    # with the real wall clock `now + 6d2h` crosses midnight in that zone whenever
    # the current local time is within 2h of it — days_remaining then comes back 7.
    # 06:30 UTC is noon in Asia/Kolkata (the default org zone), the furthest point
    # from any midnight boundary. This is a test-only flake: the production count
    # is correct; only this fixed-instant assertion needed pinning.
    monkeypatch.setattr(clock, "now",
                        lambda: datetime(2026, 6, 15, 6, 30, tzinfo=timezone.utc))
    trial = entitlements.begin_trial(session, ORG, "60005555")
    trial.ends_at = clock.now() + timedelta(days=6, hours=2)
    session.flush()

    view = entitlements.describe(session, ORG)
    assert view["effective_plan"] == "intelligence"
    assert view["plan"] == "free"                 # the licence underneath
    assert view["trial"]["days_remaining"] == 6
    assert view["trial"]["ends_on"]               # a date a person would write
    assert view["loses_on_expiry"] == ["intelligence"]


def test_the_countdown_is_measured_in_the_businesss_own_day(session, orgs,
                                                            monkeypatch):
    """Not the server's, and not the reader's browser's.

    This is the reason the number is computed server-side at all. The container
    runs in UTC; a trial ending just after midnight in Kolkata is still *today*
    in UTC for five and a half hours, and a browser in yet another zone would
    give a third answer. `clock` says plainly that `date.today()` is the wrong
    call here, so the count goes through the organization's timezone.
    """
    _free_default(monkeypatch)
    org = session.get(models.Organization, ORG)
    org.timezone = "Pacific/Kiritimati"           # UTC+14, the furthest ahead
    session.flush()
    trial = entitlements.begin_trial(session, ORG, "60005555")
    trial.ends_at = clock.now() + timedelta(hours=12)
    session.flush()

    ahead = entitlements.describe(session, ORG)["trial"]

    org.timezone = "Pacific/Midway"               # UTC-11, the furthest behind
    session.flush()
    behind = entitlements.describe(session, ORG)["trial"]

    # Same instant, two zones: the local date it falls on can differ, which is
    # exactly what a browser-side subtraction would get wrong.
    assert ahead["ends_on"] != behind["ends_on"]
    assert ahead["days_remaining"] >= 0 and behind["days_remaining"] >= 0


def test_no_trial_means_no_countdown_and_nothing_to_lose(session, orgs, monkeypatch):
    _free_default(monkeypatch)
    view = entitlements.describe(session, ORG)
    assert view["trial"] is None
    assert view["loses_on_expiry"] == []


def test_an_expired_trial_stops_being_counted(session, orgs, monkeypatch):
    """No negative countdown, and nothing claimed to be at stake."""
    _free_default(monkeypatch)
    trial = entitlements.begin_trial(session, ORG, "60005555")
    trial.ends_at = clock.now() - timedelta(days=3)
    session.flush()

    view = entitlements.describe(session, ORG)
    assert view["trial"] is None
    assert view["effective_plan"] == "free"
    assert view["loses_on_expiry"] == []


# ── the connection gate ──────────────────────────────────────────────────────
def _connect(session, org, zoho_org):
    return conn.set_zoho_credentials(
        session, org, zoho_organization_id=zoho_org,
        client_id="cid", client_secret="sec", refresh_token="tok")


def test_a_second_company_needs_the_platform_plan(session, orgs, monkeypatch):
    _free_default(monkeypatch)
    _connect(session, ORG, "60001111")
    with pytest.raises(entitlements.PlanRefused) as e:
        _connect(session, ORG, "60002222")
    assert "Platform" in str(e.value)

    # Reconnecting the SAME company is an update, never a second company.
    _connect(session, ORG, "60001111")

    entitlements.set_plan(session, ORG, PlanTier.PLATFORM)
    _connect(session, ORG, "60002222")
    assert len(conn.list_connections(session, ORG)) == 2


def test_connecting_starts_the_trial_and_reconnecting_elsewhere_does_not(
        session, orgs, monkeypatch):
    _free_default(monkeypatch)
    _connect(session, ORG, "60001111")
    assert entitlements.effective_plan(session, ORG) is PlanTier.INTELLIGENCE

    _connect(session, ORG2, "60001111")
    assert entitlements.effective_plan(session, ORG2) is PlanTier.FREE


# ── over HTTP: the gate answers in plan language ─────────────────────────────
@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)

    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    from app.routers import decisions as decisions_router
    from app.routers import entitlements as entitlements_router
    from app.routers import platform_auth

    # Mirrors main.py: the decisions surface is gated at inclusion.
    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(entitlements_router.router)
    app.include_router(
        decisions_router.router,
        dependencies=[Depends(entitlements.require_feature("intelligence"))])

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app), Maker


def _hdr(client, email="s.menon@pie.example"):
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_a_free_org_is_refused_in_plan_language_and_a_paid_one_passes(
        client, monkeypatch):
    tc, Maker = client
    _free_default(monkeypatch)
    hdr = _hdr(tc)

    r = tc.get("/api/v1/decisions", headers=hdr)
    assert r.status_code == 403
    assert "Commercial Intelligence" in r.json()["detail"]
    assert "Quote Desk" in r.json()["detail"]

    s = Maker()
    entitlements.set_plan(s, "org_pie", PlanTier.INTELLIGENCE)
    s.commit()
    s.close()
    assert tc.get("/api/v1/decisions", headers=hdr).status_code == 200


def test_the_entitlements_read_says_what_a_screen_needs(client, monkeypatch):
    tc, _ = client
    _free_default(monkeypatch)
    body = tc.get("/api/v1/entitlements", headers=_hdr(tc)).json()
    assert body["plan"] == "free"
    assert body["effective_plan"] == "free"
    assert body["trial"] is None
    assert body["features"] == {"intelligence": False, "multi_company": False}


# ── asking, which is not granting ────────────────────────────────────────────
# The platform sold three tiers and offered no way to buy the upper two:
# `set_plan` is an operator command with deliberately no API, and the trial
# notice said so out loud — "a button that opened a checkout nobody built would
# be worse than no button". Splitting the ask from the grant is what lets the
# button exist. The first test is the one that matters.
def test_asking_for_a_plan_does_not_grant_it(session, orgs, monkeypatch):
    """The whole property. If this ever passes for the wrong reason, an owner
    can hand themselves the top tier, which is what the missing API prevented."""
    _free_default(monkeypatch)
    entitlements.request_plan_change(
        session, ORG, requested_plan=PlanTier.PLATFORM, requested_by="usr_x")

    assert entitlements.effective_plan(session, ORG) is PlanTier.FREE
    assert entitlements.licensed_plan(session.get(models.Organization, ORG)) \
        is PlanTier.FREE


def test_a_request_is_refused_for_the_plan_they_are_already_on(session, orgs,
                                                               monkeypatch):
    """An 'upgrade' that changes nothing wastes the attention of a queue whose
    whole value is that every row in it is real."""
    _free_default(monkeypatch)
    with pytest.raises(entitlements.PlanRequestRefused):
        entitlements.request_plan_change(
            session, ORG, requested_plan=PlanTier.FREE, requested_by="usr_x")


def test_a_second_open_request_is_refused(session, orgs, monkeypatch):
    _free_default(monkeypatch)
    entitlements.request_plan_change(
        session, ORG, requested_plan=PlanTier.INTELLIGENCE, requested_by="usr_x")
    with pytest.raises(entitlements.PlanRequestRefused):
        entitlements.request_plan_change(
            session, ORG, requested_plan=PlanTier.PLATFORM, requested_by="usr_x")


def test_applying_a_request_is_the_only_thing_that_moves_the_plan(session, orgs,
                                                                  monkeypatch):
    _free_default(monkeypatch)
    row = entitlements.request_plan_change(
        session, ORG, requested_plan=PlanTier.INTELLIGENCE, requested_by="usr_x")

    entitlements.decide_request(session, row.request_id, apply=True,
                                decided_by="operator")

    assert entitlements.effective_plan(session, ORG) is PlanTier.INTELLIGENCE
    assert row.status == entitlements.APPLIED
    assert row.decided_by == "operator" and row.decided_at is not None
    # What was asked for stays what was asked for.
    assert row.requested_plan == PlanTier.INTELLIGENCE.value
    assert row.plan_at_request == PlanTier.FREE.value


def test_declining_leaves_the_plan_alone_and_closes_the_request(session, orgs,
                                                                monkeypatch):
    _free_default(monkeypatch)
    row = entitlements.request_plan_change(
        session, ORG, requested_plan=PlanTier.PLATFORM, requested_by="usr_x")

    entitlements.decide_request(session, row.request_id, apply=False,
                                decided_by="operator")

    assert entitlements.effective_plan(session, ORG) is PlanTier.FREE
    assert row.status == entitlements.DECLINED
    assert entitlements.pending_request(session, ORG) is None


def test_a_decided_request_cannot_be_decided_again(session, orgs, monkeypatch):
    """Never back to REQUESTED: a second ask is a second row."""
    _free_default(monkeypatch)
    row = entitlements.request_plan_change(
        session, ORG, requested_plan=PlanTier.INTELLIGENCE, requested_by="usr_x")
    entitlements.decide_request(session, row.request_id, apply=True,
                                decided_by="operator")
    with pytest.raises(entitlements.PlanRequestRefused):
        entitlements.decide_request(session, row.request_id, apply=False,
                                    decided_by="operator")


# ── over HTTP ────────────────────────────────────────────────────────────────
def test_an_owner_can_ask_and_the_answer_says_they_asked(client, monkeypatch):
    tc, _ = client
    _free_default(monkeypatch)
    hdr = _hdr(tc)

    assert tc.get("/api/v1/entitlements", headers=hdr).json()["pending_request"] is None

    r = tc.post("/api/v1/entitlements",
                json={"plan": "intelligence", "note": "three companies"},
                headers=hdr)
    assert r.status_code == 201, r.text
    body = r.json()
    # Still free — asking is not granting, over HTTP as much as anywhere.
    assert body["plan"] == "free"
    assert body["pending_request"]["requested_plan"] == "intelligence"
    # And the button has something to render instead of offering itself again.
    assert body["pending_request"]["requested_at"]


def test_a_manager_cannot_commit_the_business_to_a_subscription(client,
                                                                monkeypatch):
    tc, _ = client
    _free_default(monkeypatch)
    r = tc.post("/api/v1/entitlements", json={"plan": "intelligence"},
                headers=_hdr(tc, "m.rao@pie.example"))
    assert r.status_code == 403


def test_a_typo_is_refused_rather_than_recorded_as_a_request_for_free(
        client, monkeypatch):
    """`parse_plan` degrades an unknown value to free and logs, which is right
    where a *stored* plan is resolved and wrong here — it would file a request
    nobody made."""
    tc, _ = client
    _free_default(monkeypatch)
    r = tc.post("/api/v1/entitlements", json={"plan": "platfrom"},
                headers=_hdr(tc))
    assert r.status_code == 400
    assert "platfrom" in r.json()["detail"]
