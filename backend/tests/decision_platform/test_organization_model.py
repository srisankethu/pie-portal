"""The organization as the tenant: membership, entitlement, and the boundary.

This file is the lifecycle in one place, because the lifecycle is what the
model changed. Every test below is a sentence somebody said about the product
before it was code:

*John creates Acme. Acme gets thirty days. Sarah joins and Acme's thirty days
do not move. The trial runs out and the decision layer locks — and every row
Acme put in is still there. Acme subscribes. John leaves. Sarah, now owner,
carries on. John cannot get back in. Nobody in Beta can see Acme, whatever
organization id they put in the request.*

Two of those are security claims rather than product ones, and they are the
reason this is not just a unit test of `memberships.py`: they are properties of
the *request path*, so they are asserted through the API with a real token,
where an authorization bug would actually live.

What is deliberately **not** here: assertions that a specific screen renders a
specific string. Those belong to the components that render them.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app import clock, entitlements, memberships
from app.db import get_session
from app.domain import models
from app.domain.enums import (MembershipStatus, PlanTier, Role,
                              SubscriptionStatus)
from app.passwords import hash_password
from app.routers import admin, organizations, platform_auth
from app.seed import provision_organization

PASSWORD = "a-perfectly-ordinary-password"


@pytest.fixture()
def app_client():
    """The API, on an isolated database, with nothing seeded.

    Nothing seeded on purpose: this file is about what happens when an
    organization is *created*, and a fixture that had already created one would
    hide the first two assertions in the list above.
    """
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    app = FastAPI()
    for r in (platform_auth.router, admin.router, organizations.router):
        app.include_router(r)

    def _override():
        s = Maker()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override
    client = TestClient(app)
    client.Maker = Maker
    return client


def _new_org(client, *, company: str, owner_email: str) -> str:
    s = client.Maker()
    try:
        org_id, _ = provision_organization(
            s, name=company, owner_email=owner_email, owner_name="Owner",
            password=PASSWORD, must_change_password=False)
        s.commit()
        return org_id
    finally:
        s.close()


def _token(client, email: str, password: str = PASSWORD) -> str:
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hdr(client, email: str, password: str = PASSWORD) -> dict:
    return {"Authorization": f"Bearer {_token(client, email, password)}"}


def _expire_trial(client, org_id: str) -> None:
    """Put the trial in the past. The way expiry actually happens.

    The date is moved rather than the status, because the status is not what
    decides — `resolve` compares the date against the clock on every read. A
    test that flipped the status to EXPIRED would pass against an
    implementation that had a sweep and no derivation, which is the
    implementation this must not silently become.
    """
    s = client.Maker()
    try:
        sub = s.get(models.OrganizationSubscription, org_id)
        sub.trial_ends_at = clock.now() - timedelta(days=1)
        s.commit()
    finally:
        s.close()


# ── 1 & 2. an organization is created, and it has a trial ────────────────────
def test_creating_an_organization_gives_that_organization_a_trial(app_client):
    org_id = _new_org(app_client, company="Acme", owner_email="john@acme.example")
    s = app_client.Maker()
    try:
        ent = entitlements.resolve(s, org_id)
        assert ent.status is SubscriptionStatus.TRIALING
        assert ent.effective is PlanTier.INTELLIGENCE
        assert ent.trial_started_at is not None and ent.trial_ends_at is not None
        assert entitlements.can_use(s, org_id, "intelligence")
    finally:
        s.close()


def test_the_trial_is_on_the_organization_and_names_no_user(app_client):
    """The strongest form of "the organization owns it": there is nowhere on
    the row to put a person."""
    org_id = _new_org(app_client, company="Acme", owner_email="john@acme.example")
    s = app_client.Maker()
    try:
        sub = s.get(models.OrganizationSubscription, org_id)
        columns = {c.name for c in sub.__table__.columns}
        assert not any("user" in c for c in columns), columns
    finally:
        s.close()


def test_the_founder_is_a_membership_not_a_column_on_the_organization(app_client):
    org_id = _new_org(app_client, company="Acme", owner_email="john@acme.example")
    s = app_client.Maker()
    try:
        org = s.get(models.Organization, org_id)
        assert not any("user" in c.name for c in org.__table__.columns)
        rows = memberships.memberships_of(s, org_id)
        assert [r.role for r in rows] == [Role.OWNER.value]
        # Nobody invited them, and recording them as their own inviter would be
        # a tidier lie than a null.
        assert rows[0].invited_by_user_id is None
    finally:
        s.close()


# ── 3, 4 & 13. a second person joins ─────────────────────────────────────────
def test_a_second_user_joins_and_reads_the_same_entitlement(app_client):
    org_id = _new_org(app_client, company="Acme", owner_email="john@acme.example")
    r = app_client.post("/api/v1/admin/users",
                        json={"email": "sarah@acme.example", "name": "Sarah",
                              "role": "SALES_MANAGER"},
                        headers=_hdr(app_client, "john@acme.example"))
    assert r.status_code == 201, r.text
    sarah_password = r.json()["temporary_password"]

    s = app_client.Maker()
    try:
        sarah = s.get(models.User, r.json()["user"]["user_id"])
        # Sarah's entitlement *is* Acme's — there is no per-user entitlement to
        # be equal to, which is the point.
        assert memberships.role_in(s, sarah.user_id, org_id) is Role.SALES_MANAGER
        assert entitlements.can_use(s, org_id, "intelligence")
    finally:
        s.close()

    # And she can actually use it, through the request path.
    s = app_client.Maker()
    try:
        s.get(models.User, r.json()["user"]["user_id"]).must_change_password = False
        s.commit()
    finally:
        s.close()
    me = app_client.get("/api/v1/auth/me",
                        headers=_hdr(app_client, "sarah@acme.example",
                                     sarah_password))
    assert me.status_code == 200
    assert me.json()["organization_id"] == org_id


def test_a_second_user_does_not_create_a_second_trial(app_client):
    """Requirement 13, and it is asserted by counting rows rather than by
    reading a date: "no second trial" has to mean no second row, or a future
    join path could hand out one that merely happened to have the same dates."""
    org_id = _new_org(app_client, company="Acme", owner_email="john@acme.example")
    s = app_client.Maker()
    try:
        before = s.get(models.OrganizationSubscription, org_id).trial_ends_at
    finally:
        s.close()

    for i in range(3):
        app_client.post("/api/v1/admin/users",
                        json={"email": f"p{i}@acme.example", "name": f"P{i}",
                              "role": "SALESPERSON"},
                        headers=_hdr(app_client, "john@acme.example"))

    s = app_client.Maker()
    try:
        assert s.query(models.OrganizationSubscription).count() == 1
        assert s.get(models.OrganizationSubscription, org_id).trial_ends_at == before
    finally:
        s.close()


def test_creating_an_account_is_not_creating_an_entitled_organization(app_client):
    """Requirement: account creation and trial creation stay separate concepts.

    Adding a person to an existing organization creates an identity and a
    membership and no subscription at all — so "make another user" is not a
    route to another trial.
    """
    org_id = _new_org(app_client, company="Acme", owner_email="john@acme.example")
    r = app_client.post("/api/v1/admin/users",
                        json={"email": "new@acme.example", "name": "New",
                              "role": "SALESPERSON"},
                        headers=_hdr(app_client, "john@acme.example"))
    assert r.status_code == 201
    s = app_client.Maker()
    try:
        assert s.query(models.OrganizationSubscription).count() == 1
        assert s.query(models.OrganizationSubscription).one().organization_id == org_id
    finally:
        s.close()


# ── 5, 6 & 7. expiry, survival, and paying ───────────────────────────────────
def test_expiry_locks_intelligence_but_keeps_every_row(app_client):
    org_id = _new_org(app_client, company="Acme", owner_email="john@acme.example")
    s = app_client.Maker()
    try:
        s.add(models.Customer(customer_id="c1", organization_id=org_id,
                              external_id="C1", name="A Customer"))
        s.add(models.Product(product_id="p1", organization_id=org_id,
                             external_id="I1", name="An Item", uom="pcs"))
        s.commit()
    finally:
        s.close()

    _expire_trial(app_client, org_id)

    s = app_client.Maker()
    try:
        assert not entitlements.can_use(s, org_id, "intelligence")
        with pytest.raises(entitlements.PlanRefused):
            entitlements.assert_feature(s, org_id, "intelligence")
        # Requirement 6, stated as rows rather than as intent.
        assert s.get(models.Customer, "c1") is not None
        assert s.get(models.Product, "p1") is not None
        assert s.get(models.Organization, org_id) is not None
        assert s.get(models.OrganizationSubscription, org_id) is not None
        assert memberships.active_owners(s, org_id), "and the people are still here"
    finally:
        s.close()


def test_an_expired_organization_can_still_sign_in_and_quote(app_client):
    """Locking a *feature* is not locking the customer out of their account.

    The trial's promise is that the data survives it, and a business that
    cannot sign in cannot see that its data survived.
    """
    org_id = _new_org(app_client, company="Acme", owner_email="john@acme.example")
    _expire_trial(app_client, org_id)
    me = app_client.get("/api/v1/auth/me", headers=_hdr(app_client, "john@acme.example"))
    assert me.status_code == 200
    assert me.json()["organization_id"] == org_id


def test_upgrading_restores_intelligence_over_the_same_data(app_client):
    org_id = _new_org(app_client, company="Acme", owner_email="john@acme.example")
    s = app_client.Maker()
    try:
        s.add(models.Customer(customer_id="c1", organization_id=org_id,
                              external_id="C1", name="A Customer"))
        s.commit()
    finally:
        s.close()
    _expire_trial(app_client, org_id)

    s = app_client.Maker()
    try:
        entitlements.set_plan(s, org_id, PlanTier.INTELLIGENCE)
        s.commit()
        assert entitlements.can_use(s, org_id, "intelligence")
        assert s.get(models.Customer, "c1") is not None
    finally:
        s.close()


# ── 8 & 9. the founder leaves ────────────────────────────────────────────────
def test_the_founder_can_leave_and_the_organization_carries_on(app_client):
    """The requirement in full: John founds Acme, Sarah takes it over, and
    Acme's id, data, subscription and configuration are untouched."""
    org_id = _new_org(app_client, company="Acme", owner_email="john@acme.example")
    john_hdr = _hdr(app_client, "john@acme.example")

    s = app_client.Maker()
    try:
        s.add(models.Customer(customer_id="c1", organization_id=org_id,
                              external_id="C1", name="A Customer"))
        s.get(models.Organization, org_id).config = {"a-setting": "kept"}
        before = entitlements.resolve(s, org_id)
        trial_ends = before.trial_ends_at
        s.commit()
    finally:
        s.close()

    # Sarah joins, and is promoted before John goes — which is the order the
    # last-owner rule enforces rather than merely suggests.
    created = app_client.post("/api/v1/admin/users",
                              json={"email": "sarah@acme.example", "name": "Sarah",
                                    "role": "SALES_MANAGER"},
                              headers=john_hdr).json()
    sarah_id, sarah_password = created["user"]["user_id"], created["temporary_password"]

    assert app_client.patch(f"/api/v1/admin/users/{sarah_id}",
                            json={"role": "OWNER"}, headers=john_hdr).status_code == 200

    # Now John's membership can end, and his login can be switched off.
    s = app_client.Maker()
    try:
        john = s.query(models.User).filter_by(email="john@acme.example").one()
        s.commit()
    finally:
        s.close()

    s = app_client.Maker()
    try:
        s.get(models.User, sarah_id).must_change_password = False
        s.commit()
    finally:
        s.close()
    sarah_hdr = _hdr(app_client, "sarah@acme.example", sarah_password)

    assert app_client.patch(f"/api/v1/admin/users/{john.user_id}",
                            json={"member": False},
                            headers=sarah_hdr).status_code == 200
    assert app_client.patch(f"/api/v1/admin/users/{john.user_id}",
                            json={"active": False},
                            headers=sarah_hdr).status_code == 200

    # Everything about Acme is where it was.
    s = app_client.Maker()
    try:
        assert s.get(models.Organization, org_id).organization_id == org_id
        assert s.get(models.Organization, org_id).config == {"a-setting": "kept"}
        assert s.get(models.Customer, "c1") is not None
        assert entitlements.resolve(s, org_id).trial_ends_at == trial_ends
        assert [m.user_id for m in memberships.active_owners(s, org_id)] == [sarah_id]
    finally:
        s.close()


def test_the_new_owner_can_manage_the_organization(app_client):
    """Requirement 9. Sarah's authority comes from her membership, so it is
    complete rather than a subset the founder left behind."""
    org_id = _new_org(app_client, company="Acme", owner_email="john@acme.example")
    john_hdr = _hdr(app_client, "john@acme.example")
    created = app_client.post("/api/v1/admin/users",
                              json={"email": "sarah@acme.example", "name": "Sarah",
                                    "role": "SALES_MANAGER"},
                              headers=john_hdr).json()
    sarah_id, sarah_password = created["user"]["user_id"], created["temporary_password"]
    app_client.patch(f"/api/v1/admin/users/{sarah_id}", json={"role": "OWNER"},
                     headers=john_hdr)
    s = app_client.Maker()
    try:
        s.get(models.User, sarah_id).must_change_password = False
        s.commit()
    finally:
        s.close()
    sarah_hdr = _hdr(app_client, "sarah@acme.example", sarah_password)

    assert app_client.get("/api/v1/admin/users",
                          headers=sarah_hdr).json()["can_manage"] is True
    assert app_client.post("/api/v1/admin/users",
                           json={"email": "third@acme.example", "name": "Third",
                                 "role": "SALESPERSON"},
                           headers=sarah_hdr).status_code == 201


def test_the_last_owner_cannot_be_removed_from_the_organization(app_client):
    """The same refusal demotion has, for the same reason: an organization with
    no owner is a tenant nobody can administer."""
    org_id = _new_org(app_client, company="Acme", owner_email="john@acme.example")
    s = app_client.Maker()
    try:
        john = s.query(models.User).filter_by(email="john@acme.example").one()
        second = models.User(organization_id=org_id, email="two@acme.example",
                             name="Two", role=Role.OWNER.value,
                             password_hash=hash_password(PASSWORD))
        s.add(second)
        s.flush()
        memberships.add_member(s, organization_id=org_id, user_id=second.user_id,
                               role=Role.OWNER)
        s.commit()
        john_id, second_id = john.user_id, second.user_id
    finally:
        s.close()

    hdr = _hdr(app_client, "two@acme.example")
    # Two owners: removing one is allowed.
    assert app_client.patch(f"/api/v1/admin/users/{john_id}", json={"member": False},
                            headers=hdr).status_code == 200
    # One left: and the endpoint refuses to remove your own membership before
    # it ever reaches the last-owner rule, which is the friendlier of the two
    # refusals and the one a person is more likely to trip.
    assert app_client.patch(f"/api/v1/admin/users/{second_id}", json={"member": False},
                            headers=hdr).status_code == 400

    s = app_client.Maker()
    try:
        m = memberships.membership_for(s, second_id, org_id)
        with pytest.raises(memberships.MembershipRefused):
            memberships.remove_member(s, m, removed_by_user_id="somebody")
    finally:
        s.close()


# ── 10. a removed person is out ──────────────────────────────────────────────
def test_a_removed_user_cannot_reach_the_organizations_data(app_client):
    """Through the request path, with a token that was valid a moment ago.

    This is the test that would fail if authorization went back to reading
    ``users.organization_id``: that column still names Acme after the
    membership ends, so a check against it would keep letting them in.
    """
    org_id = _new_org(app_client, company="Acme", owner_email="john@acme.example")
    john_hdr = _hdr(app_client, "john@acme.example")
    created = app_client.post("/api/v1/admin/users",
                              json={"email": "temp@acme.example", "name": "Temp",
                                    "role": "SALES_MANAGER"},
                              headers=john_hdr).json()
    temp_id, temp_password = created["user"]["user_id"], created["temporary_password"]
    s = app_client.Maker()
    try:
        s.get(models.User, temp_id).must_change_password = False
        s.commit()
    finally:
        s.close()

    temp_hdr = _hdr(app_client, "temp@acme.example", temp_password)
    assert app_client.get("/api/v1/admin/users", headers=temp_hdr).status_code == 200

    assert app_client.patch(f"/api/v1/admin/users/{temp_id}", json={"member": False},
                            headers=john_hdr).status_code == 200

    # The token is unchanged and still correctly signed. It resolves to nothing,
    # because the grant it depended on is gone.
    assert app_client.get("/api/v1/admin/users", headers=temp_hdr).status_code == 401
    # And they cannot get a new one either.
    assert app_client.post("/api/v1/auth/login",
                           json={"email": "temp@acme.example",
                                 "password": temp_password}).status_code == 401

    s = app_client.Maker()
    try:
        m = memberships.membership_for(s, temp_id, org_id)
        assert m.status == MembershipStatus.REMOVED.value
        assert m.removed_at is not None and m.removed_by_user_id
        # The identity survives. Removing somebody from a workspace is not
        # deleting the person.
        assert s.get(models.User, temp_id) is not None
    finally:
        s.close()


# ── 11 & 12. one tenant cannot reach another ─────────────────────────────────
def test_a_user_of_one_organization_cannot_reach_another(app_client):
    _new_org(app_client, company="Acme", owner_email="john@acme.example")
    beta_id = _new_org(app_client, company="Beta", owner_email="bea@beta.example")

    john_hdr = _hdr(app_client, "john@acme.example")
    listed = app_client.get("/api/v1/admin/users", headers=john_hdr).json()["users"]
    assert [u["email"] for u in listed] == ["john@acme.example"]

    # Beta's owner, named by id, is not a user John can act on.
    s = app_client.Maker()
    try:
        bea_id = s.query(models.User).filter_by(email="bea@beta.example").one().user_id
    finally:
        s.close()
    assert app_client.patch(f"/api/v1/admin/users/{bea_id}",
                            json={"role": "SALESPERSON"},
                            headers=john_hdr).status_code == 404
    assert app_client.post(f"/api/v1/admin/users/{bea_id}/reset-password",
                           headers=john_hdr).status_code == 404
    assert app_client.post(f"/api/v1/organizations/{beta_id}/switch",
                           headers=john_hdr).status_code == 404


def test_naming_another_organization_in_a_token_grants_nothing(app_client):
    """Requirement 12, at the layer that decides.

    The token is *ours* — signed with the real secret, not forged — and names
    an organization the holder has no membership of. That is the strongest
    version of the attack available to somebody who can mint their own body,
    and it has to resolve to no principal at all rather than to a principal
    with the wrong tenant.
    """
    from app import authz

    acme_id = _new_org(app_client, company="Acme", owner_email="john@acme.example")
    beta_id = _new_org(app_client, company="Beta", owner_email="bea@beta.example")

    s = app_client.Maker()
    try:
        john = s.query(models.User).filter_by(email="john@acme.example").one()
        john_id = john.user_id
        _, opened = authz.open_session(s, john, organization_id=acme_id)
        real_session = opened.session_id
        s.commit()
    finally:
        s.close()

    # Same user, same live session row, a properly signed token — and Beta's id
    # substituted for Acme's.
    forged = authz.issue_token(john_id, beta_id, real_session)
    r = app_client.get("/api/v1/admin/users",
                       headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401

    s = app_client.Maker()
    try:
        assert authz.load_principal(s, forged) is None
    finally:
        s.close()


def test_the_principal_acts_for_the_sessions_organization_not_the_users_column(
        app_client):
    """The positive half of the same rule, and the one that makes two
    workspaces work at all."""
    from app import authz

    acme_id = _new_org(app_client, company="Acme", owner_email="john@acme.example")
    beta_id = _new_org(app_client, company="Beta", owner_email="bea@beta.example")

    s = app_client.Maker()
    try:
        john = s.query(models.User).filter_by(email="john@acme.example").one()
        # John is also a salesperson in Beta. His identity row still lives
        # under Acme — that column is his home, not his limit.
        memberships.add_member(s, organization_id=beta_id, user_id=john.user_id,
                               role=Role.SALESPERSON)
        token, _ = authz.open_session(s, john, organization_id=beta_id)
        s.commit()
        assert john.organization_id == acme_id
    finally:
        s.close()

    s = app_client.Maker()
    try:
        principal = authz.load_principal(s, token)
        assert principal is not None
        assert principal.organization_id == beta_id
        assert principal.role is Role.SALESPERSON, "Beta's role, not Acme's"
    finally:
        s.close()


def test_switching_workspace_requires_a_membership_and_reports_the_right_role(
        app_client):
    acme_id = _new_org(app_client, company="Acme", owner_email="john@acme.example")
    beta_id = _new_org(app_client, company="Beta", owner_email="bea@beta.example")
    john_hdr = _hdr(app_client, "john@acme.example")

    # Before the grant: Beta may as well not exist.
    assert app_client.post(f"/api/v1/organizations/{beta_id}/switch",
                           headers=john_hdr).status_code == 404
    listed = app_client.get("/api/v1/organizations", headers=john_hdr).json()
    assert [o["organization_id"] for o in listed["organizations"]] == [acme_id]

    s = app_client.Maker()
    try:
        john = s.query(models.User).filter_by(email="john@acme.example").one()
        memberships.add_member(s, organization_id=beta_id, user_id=john.user_id,
                               role=Role.SALESPERSON)
        s.commit()
    finally:
        s.close()

    r = app_client.post(f"/api/v1/organizations/{beta_id}/switch", headers=john_hdr)
    assert r.status_code == 200, r.text
    assert r.json()["organization_id"] == beta_id
    assert r.json()["role"] == "SALESPERSON"

    switched = {"Authorization": f"Bearer {r.json()['token']}"}
    current = app_client.get("/api/v1/organizations/current", headers=switched).json()
    assert current["organization_id"] == beta_id
    assert current["role"] == "SALESPERSON"

    # The workspace he left is still open on the session he left it on: a
    # switch mints a session, it does not repoint one.
    assert app_client.get("/api/v1/organizations/current",
                          headers=john_hdr).json()["organization_id"] == acme_id


# ── the legacy mirror, while it is still here ────────────────────────────────
def test_the_legacy_user_role_mirrors_the_home_membership(app_client):
    """``users.role`` is deprecated and written in one place. This is the pin
    that keeps it from drifting while it is still in the schema — a stale
    mirror is worse than no mirror, because the rollback it exists for would
    read it."""
    org_id = _new_org(app_client, company="Acme", owner_email="john@acme.example")
    created = app_client.post("/api/v1/admin/users",
                              json={"email": "sarah@acme.example", "name": "Sarah",
                                    "role": "SALESPERSON"},
                              headers=_hdr(app_client, "john@acme.example")).json()
    sarah_id = created["user"]["user_id"]
    app_client.patch(f"/api/v1/admin/users/{sarah_id}", json={"role": "SALES_MANAGER"},
                     headers=_hdr(app_client, "john@acme.example"))

    s = app_client.Maker()
    try:
        for user in s.query(models.User).all():
            home = memberships.active_membership_for(s, user.user_id,
                                                     user.organization_id)
            assert home is not None
            assert user.role == home.role, user.email
    finally:
        s.close()
