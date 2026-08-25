"""Plans, the organization's trial, and the boundaries they put teeth behind.

The pricing terms this enforces were prose until now, and prose is a suggestion
to a strategic customer. Four claims get pinned.

A plan resolves safely: NULL falls back to the deployment default, and an
unrecognised value degrades to free — a typo must never widen what a tenant may
use.

**The trial belongs to the organization.** It starts when the organization
does, it survives every user who comes and goes, and a second person joining
reads it rather than starting one. That is the change these tests are mostly
about: it used to belong to the *connected books* and to begin at first
connection, which meant a new customer had nothing during the days they were
most deciding whether to buy.

What survived from that shape is the duplicate-trial check, and only that: the
books still record a claim, and books already claimed by another organization
end the new organization's trial. One boundary, at one moment, and deliberately
not a fingerprinting scheme.

And a second connected company is refused below the platform plan at the moment
of connection — the licence unit is the one thing that can be enforced
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
from app.domain.enums import PlanTier, SubscriptionStatus
from app.ingestion import connections as conn
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_t"
ORG2 = "org_t2"


@pytest.fixture()
def orgs(session):
    """Two bare organizations, deliberately without subscriptions.

    Provisioning gives every new organization a trial (`start_trial`), and the
    tests that are about the trial call it explicitly so the moment it starts
    is visible in the test rather than in a fixture. What this fixture pins is
    the *other* case, which is real and has to keep working: an organization
    from before subscriptions existed, whose entitlement comes from the legacy
    ``organizations.plan`` column.
    """
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


# ── the trial belongs to the organization ────────────────────────────────────
def test_a_new_organization_gets_its_trial_when_it_is_created(session, orgs,
                                                              monkeypatch):
    """Not when it connects books. The days a buyer spends deciding are days
    they can use the product."""
    _free_default(monkeypatch)
    assert entitlements.effective_plan(session, ORG) is PlanTier.FREE
    entitlements.start_trial(session, ORG)
    assert entitlements.effective_plan(session, ORG) is PlanTier.INTELLIGENCE


def test_the_trial_runs_for_the_configured_number_of_days(session, orgs):
    from app.config import settings

    sub = entitlements.start_trial(session, ORG)
    span = clock.aware(sub.trial_ends_at) - clock.aware(sub.trial_started_at)
    assert round(span.total_seconds() / 86400) == settings.INTELLIGENCE_TRIAL_DAYS


def test_starting_a_trial_twice_does_not_extend_it(session, orgs):
    """The property that makes "a second person joined" cost nothing.

    Idempotent means the existing row comes back untouched — not re-dated, not
    re-statused. Otherwise re-running provisioning, or any future join path
    that reached for this, would walk an organization back to day one.
    """
    first = entitlements.start_trial(session, ORG)
    ends = first.trial_ends_at
    again = entitlements.start_trial(session, ORG)
    assert again.organization_id == first.organization_id
    assert again.trial_ends_at == ends


def test_a_second_user_joining_neither_starts_nor_extends_a_trial(session, orgs):
    """The requirement stated as the code path a joiner actually takes.

    `add_member` is the whole of adding somebody, and it touches no
    subscription — which is why this test can assert on the row rather than on
    a claim about intent.
    """
    from app import memberships
    from app.domain.enums import Role

    entitlements.start_trial(session, ORG)
    before = entitlements.subscription_for(session, ORG)
    ends, started = before.trial_ends_at, before.trial_started_at

    for i, role in enumerate((Role.SALES_MANAGER, Role.SALESPERSON)):
        user = models.User(user_id=f"u{i}", organization_id=ORG,
                           email=f"u{i}@t.example", name=f"U{i}", role=role.value)
        session.add(user)
        session.flush()
        memberships.add_member(session, organization_id=ORG, user_id=user.user_id,
                               role=role)

    after = entitlements.subscription_for(session, ORG)
    assert (after.trial_ends_at, after.trial_started_at) == (ends, started)
    assert session.query(models.OrganizationSubscription).count() == 1


def test_an_expired_trial_locks_intelligence_and_deletes_nothing(session, orgs,
                                                                 monkeypatch):
    _free_default(monkeypatch)
    sub = entitlements.start_trial(session, ORG)
    customer = models.Customer(customer_id="c_keep", organization_id=ORG,
                               external_id="c1", name="Acme")
    session.add(customer)
    sub.trial_ends_at = clock.now() - timedelta(days=1)
    session.flush()

    assert entitlements.effective_plan(session, ORG) is PlanTier.FREE
    with pytest.raises(entitlements.PlanRefused):
        entitlements.assert_feature(session, ORG, "intelligence")
    # And the organization's own data is exactly where it was. Ending a
    # subscription is not the same act as deleting a customer.
    assert session.get(models.Customer, "c_keep") is not None
    assert entitlements.subscription_for(session, ORG) is not None


def test_expiry_is_derived_from_the_date_not_written_by_a_job(session, orgs,
                                                              monkeypatch):
    """No sweep to miss. A stored status of TRIALING past its date is expired.

    The failure this prevents is the one CLAUDE.md §1 names: a nightly job that
    did not run would leave an organization entitled to something it stopped
    paying for, and the absence of the job would look exactly like the absence
    of a problem.
    """
    _free_default(monkeypatch)
    sub = entitlements.start_trial(session, ORG)
    sub.trial_ends_at = clock.now() - timedelta(seconds=1)
    session.flush()
    # The row still *says* TRIALING — nothing has rewritten it.
    assert sub.status == "TRIALING"
    ent = entitlements.resolve(session, ORG)
    assert ent.status is SubscriptionStatus.EXPIRED
    assert ent.effective is PlanTier.FREE


def test_upgrading_after_expiry_brings_intelligence_back(session, orgs, monkeypatch):
    _free_default(monkeypatch)
    sub = entitlements.start_trial(session, ORG)
    sub.trial_ends_at = clock.now() - timedelta(days=2)
    session.flush()
    assert entitlements.effective_plan(session, ORG) is PlanTier.FREE

    entitlements.set_plan(session, ORG, PlanTier.INTELLIGENCE)
    ent = entitlements.resolve(session, ORG)
    assert ent.status is SubscriptionStatus.ACTIVE
    assert ent.effective is PlanTier.INTELLIGENCE
    assert ent.trial_ends_at is not None, "the trial it had is still on record"


def test_the_paid_relationship_is_dated_and_survives_a_tier_change(session, orgs):
    entitlements.start_trial(session, ORG)
    entitlements.set_plan(session, ORG, PlanTier.INTELLIGENCE)
    began = entitlements.subscription_for(session, ORG).subscription_started_at
    assert began is not None
    entitlements.set_plan(session, ORG, PlanTier.PLATFORM)
    assert entitlements.subscription_for(session, ORG).subscription_started_at == began


def test_cancelling_does_not_hand_out_a_second_trial(session, orgs, monkeypatch):
    """The whole point of the trial belonging to the organization: it happened
    once, at the beginning."""
    _free_default(monkeypatch)
    sub = entitlements.start_trial(session, ORG)
    sub.trial_ends_at = clock.now() - timedelta(days=1)
    session.flush()
    entitlements.set_plan(session, ORG, PlanTier.INTELLIGENCE)
    entitlements.set_plan(session, ORG, PlanTier.FREE)

    ent = entitlements.resolve(session, ORG)
    assert ent.status is SubscriptionStatus.CANCELLED
    assert ent.effective is PlanTier.FREE


def test_a_trial_lifts_to_intelligence_never_to_platform(session, orgs, monkeypatch):
    _free_default(monkeypatch)
    entitlements.start_trial(session, ORG)
    entitlements.assert_feature(session, ORG, "intelligence")  # no raise
    with pytest.raises(entitlements.PlanRefused):
        entitlements.assert_feature(session, ORG, "multi_company")


# ── the books still hold the duplicate-trial check ───────────────────────────
def test_books_already_trialled_elsewhere_end_this_organizations_trial(
        session, orgs, monkeypatch):
    """The farming move, and the one boundary that answers it.

    A platform organization costs nothing to create, so signing up again with
    another address produces a second organization with a second trial. What it
    does not produce is a second free month over the same *books*: the claim is
    already held, and connecting them ends the new trial with a reason on the
    row.
    """
    _free_default(monkeypatch)
    entitlements.start_trial(session, ORG)
    entitlements.start_trial(session, ORG2)

    assert entitlements.claim_books(session, ORG, "60005555") is not None
    assert entitlements.effective_plan(session, ORG) is PlanTier.INTELLIGENCE

    # The fresh organization reconnects the same company.
    assert entitlements.claim_books(session, ORG2, "60005555") is None
    assert entitlements.effective_plan(session, ORG2) is PlanTier.FREE
    sub = entitlements.subscription_for(session, ORG2)
    assert sub.trial_ended_reason == entitlements.BOOKS_ALREADY_TRIALLED

    # Books nobody has claimed are claimable, and claiming them changes
    # nothing about the trial of the organization that claims them.
    assert entitlements.claim_books(session, ORG, "60007777") is not None
    assert entitlements.effective_plan(session, ORG) is PlanTier.INTELLIGENCE
    assert entitlements.subscription_for(session, ORG).trial_ended_reason == ""


def test_claiming_the_same_books_twice_from_one_organization_is_a_no_op(
        session, orgs, monkeypatch):
    """Reconnecting your own company must not end your own trial."""
    _free_default(monkeypatch)
    entitlements.start_trial(session, ORG)
    entitlements.claim_books(session, ORG, "60005555")
    assert entitlements.claim_books(session, ORG, "60005555") is None
    assert entitlements.effective_plan(session, ORG) is PlanTier.INTELLIGENCE


def test_a_books_collision_never_knocks_down_a_paying_organization(session, orgs,
                                                                   monkeypatch):
    _free_default(monkeypatch)
    entitlements.start_trial(session, ORG)
    entitlements.claim_books(session, ORG, "60005555")
    entitlements.start_trial(session, ORG2)
    entitlements.set_plan(session, ORG2, PlanTier.INTELLIGENCE)

    entitlements.claim_books(session, ORG2, "60005555")
    assert entitlements.effective_plan(session, ORG2) is PlanTier.INTELLIGENCE


def test_a_claim_is_spent_not_deleted(session, orgs, monkeypatch):
    _free_default(monkeypatch)
    entitlements.start_trial(session, ORG)
    claim = entitlements.claim_books(session, ORG, "60005555")
    claim.ends_at = clock.now() - timedelta(days=1)
    session.flush()
    # The record remains, which is exactly what the next attempt must find.
    assert entitlements.claim_books(session, ORG2, "60005555") is None


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
    sub = entitlements.start_trial(session, ORG)
    sub.trial_ends_at = clock.now() + timedelta(days=6, hours=2)
    session.flush()

    view = entitlements.describe(session, ORG)
    assert view["effective_plan"] == "intelligence"
    assert view["plan"] == "free"                 # the licence underneath
    assert view["status"] == "TRIALING"
    assert view["trial"]["active"] is True
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
    sub = entitlements.start_trial(session, ORG)
    sub.trial_ends_at = clock.now() + timedelta(hours=12)
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


def test_an_ended_trial_is_still_described_so_a_screen_can_say_so(session, orgs,
                                                                  monkeypatch):
    """No negative countdown, nothing claimed to be at stake — and not silence.

    Going quiet at expiry is what made the decision layer vanish overnight with
    nothing on screen explaining it. The payload has to be able to tell "ended
    on the 3rd" from "never had one", so an ended trial is described with
    ``active: false`` rather than replaced by ``None``.
    """
    _free_default(monkeypatch)
    sub = entitlements.start_trial(session, ORG)
    sub.trial_ends_at = clock.now() - timedelta(days=3)
    session.flush()

    view = entitlements.describe(session, ORG)
    assert view["trial"] is not None
    assert view["trial"]["active"] is False
    assert view["trial"]["days_remaining"] == 0
    assert view["status"] == "EXPIRED"
    assert view["effective_plan"] == "free"
    assert view["loses_on_expiry"] == []
    assert "intelligence" in view["locked"]


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


def test_connecting_claims_the_books_and_reconnecting_elsewhere_spends_a_trial(
        session, orgs, monkeypatch):
    """The whole thing through the connection path, which is where it happens.

    Connecting no longer *starts* anything — the organization's trial began
    when the organization did — so the first assertion is about the trial being
    untouched by the connection. The second is the boundary: the same books
    under a second organization end that organization's trial.
    """
    _free_default(monkeypatch)
    entitlements.start_trial(session, ORG)
    entitlements.start_trial(session, ORG2)

    _connect(session, ORG, "60001111")
    assert entitlements.effective_plan(session, ORG) is PlanTier.INTELLIGENCE
    assert entitlements.subscription_for(session, ORG).trial_ended_reason == ""

    _connect(session, ORG2, "60001111")
    assert entitlements.effective_plan(session, ORG2) is PlanTier.FREE
    assert (entitlements.subscription_for(session, ORG2).trial_ended_reason
            == entitlements.BOOKS_ALREADY_TRIALLED)


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
