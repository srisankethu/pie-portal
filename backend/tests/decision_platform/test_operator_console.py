"""The vendor's console, tested as the boundary it is.

The console is the one surface in this application whose caller is not a
tenant, so the tests that matter are not "does the queue render" — they are the
two questions a cross-tenant reader has to keep answering:

  * **Can anything but a live operator key reach it?** A tenant session, a
    tenant API key, an expired key, a revoked one, a malformed one. Every one
    of those must be refused at the door, and refused the same way, so that a
    caller cannot learn from the message which half of a credential was right.

  * **Can it read inside a tenant without a break-glass grant?** Not "does it
    check" — whether the rows come back. The 58 policied tables are fail-closed
    under row-level security, and `reach_into` is the only thing that announces
    a tenant; a test that asserted the check was called would pass just as well
    if the check were called after the read.

The vendor-scope panels are tested for what they do, and one thing they must
not do: carry a number out of a customer's book.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app import contact, operator
from app.db import get_session
from app.domain import models
from app.domain.enums import PlanTier
from app.trust import access

ORG = "org_console"
OTHER = "org_elsewhere"


@pytest.fixture()
def client_and_maker():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    for oid, name in ((ORG, "Console Customer"), (OTHER, "Somebody Else")):
        s.add(models.Organization(organization_id=oid, name=name,
                                  currency="INR", config={}))
    s.commit()
    s.close()

    from app.routers import operator as operator_router

    app = FastAPI()
    app.include_router(operator_router.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app), Maker


@pytest.fixture()
def key(client_and_maker):
    """A live operator key, and the secret to present it with."""
    _, Maker = client_and_maker
    s = Maker()
    issued = operator.issue(s, operator_id="sanketh", name="laptop")
    s.commit()
    secret = issued.secret
    s.close()
    return secret


def auth(secret: str) -> dict:
    return {"Authorization": f"Bearer {secret}"}


# ── the door ─────────────────────────────────────────────────────────────────
def test_the_console_refuses_a_caller_with_no_credential(client_and_maker):
    client, _ = client_and_maker
    assert client.get("/api/v1/operator/enquiries").status_code == 401


@pytest.mark.parametrize("presented", [
    "",
    "nonsense",
    "pieop_only_two",                     # right prefix, wrong shape
    "pie_abcdef_secret",                  # a *tenant* API key's prefix
    "Bearer pieop_abcdef_secret",         # double-prefixed by a confused client
])
def test_the_console_refuses_anything_that_is_not_a_live_operator_key(
        client_and_maker, presented):
    client, _ = client_and_maker
    r = client.get("/api/v1/operator/enquiries", headers=auth(presented))
    assert r.status_code == 401


def test_a_tenant_api_key_is_not_an_operator_key(client_and_maker):
    """The whole reason `operator_keys` is its own table.

    A tenant's API key is a live credential against the same database, and if
    the two verifiers shared a lookup this would be the request that got in.
    """
    from app import api_keys

    client, Maker = client_and_maker
    s = Maker()
    issued = api_keys.issue(s, ORG, name="a partner's integration")
    s.commit()
    tenant_key = issued.secret
    s.close()

    r = client.get("/api/v1/operator/enquiries", headers=auth(tenant_key))
    assert r.status_code == 401


def test_a_revoked_key_stops_working(client_and_maker, key):
    client, Maker = client_and_maker
    assert client.get("/api/v1/operator/whoami",
                      headers=auth(key)).status_code == 200

    s = Maker()
    row = operator.keys(s)[0]
    operator.revoke(s, row.key_id)
    s.commit()
    s.close()

    assert client.get("/api/v1/operator/whoami",
                      headers=auth(key)).status_code == 401


def test_every_refusal_reads_the_same(client_and_maker, key):
    """A caller must not learn which half of a credential was wrong.

    An unknown key id and a real key id with a bad secret are different facts
    internally and the same 401 on the wire.
    """
    client, _ = client_and_maker
    key_id, secret = operator.split(key)
    unknown = client.get("/api/v1/operator/whoami",
                         headers=auth(f"pieop_{'0' * 24}_{secret}"))
    bad_secret = client.get("/api/v1/operator/whoami",
                            headers=auth(f"pieop_{key_id}_wrong"))
    assert unknown.status_code == bad_secret.status_code == 401
    assert unknown.json()["detail"] == bad_secret.json()["detail"]


def test_whoami_names_the_operator(client_and_maker, key):
    client, _ = client_and_maker
    body = client.get("/api/v1/operator/whoami", headers=auth(key)).json()
    assert body["operator_id"] == "sanketh"
    assert body["name"] == "laptop"


# ── the enquiry queue ────────────────────────────────────────────────────────
def test_the_queue_shows_what_is_waiting_and_then_what_was_answered(
        client_and_maker, key):
    client, Maker = client_and_maker
    s = Maker()
    contact.capture(s, company="Acme Tools", name="A Buyer",
                    email="buyer@acme.example", message="Four companies.")
    s.commit()
    s.close()

    waiting = client.get("/api/v1/operator/enquiries", headers=auth(key)).json()
    assert len(waiting["enquiries"]) == 1
    row = waiting["enquiries"][0]
    assert row["company"] == "Acme Tools"
    assert row["status"] == contact.NEW

    done = client.post(f"/api/v1/operator/enquiries/{row['id']}/handled",
                       headers=auth(key))
    assert done.status_code == 200
    # Stamped with the operator, never with a value the caller supplied.
    assert done.json()["handled_by"] == "sanketh"

    assert client.get("/api/v1/operator/enquiries",
                      headers=auth(key)).json()["enquiries"] == []
    handled = client.get("/api/v1/operator/enquiries?handled=true",
                         headers=auth(key)).json()
    assert [r["id"] for r in handled["enquiries"]] == [row["id"]]


def test_answering_the_same_enquiry_twice_is_refused(client_and_maker, key):
    """The stamp is the only audit the row has, so a second one must not
    overwrite the first."""
    client, Maker = client_and_maker
    s = Maker()
    row = contact.capture(s, company="Acme", name="A", email="a@b.example")
    s.commit()
    rid = row.contact_request_id
    s.close()

    assert client.post(f"/api/v1/operator/enquiries/{rid}/handled",
                       headers=auth(key)).status_code == 200
    again = client.post(f"/api/v1/operator/enquiries/{rid}/handled",
                        headers=auth(key))
    assert again.status_code == 400
    assert "Already handled" in again.json()["detail"]


# ── plans ────────────────────────────────────────────────────────────────────
def test_the_console_lists_tenants_with_their_plan_and_nothing_from_the_book(
        client_and_maker, key):
    client, _ = client_and_maker
    body = client.get("/api/v1/operator/organizations", headers=auth(key)).json()
    assert {o["organization_id"] for o in body["organizations"]} == {ORG, OTHER}
    # Vendor metadata only. If a field naming money or a customer ever appears
    # here, it came from inside a tenant and this console is not the place.
    assert set(body["organizations"][0]) == {
        "organization_id", "name", "plan", "wants", "currency", "created_at"}


def test_putting_a_tenant_on_a_plan_goes_through_the_one_granting_function(
        client_and_maker, key):
    client, Maker = client_and_maker
    r = client.post(f"/api/v1/operator/organizations/{ORG}/plan",
                    json={"plan": PlanTier.PLATFORM.value}, headers=auth(key))
    assert r.status_code == 200

    from app import entitlements
    s = Maker()
    assert entitlements.resolve(s, ORG).licensed is PlanTier.PLATFORM
    s.close()


def test_a_plan_for_an_organization_that_does_not_exist_is_a_404(
        client_and_maker, key):
    client, _ = client_and_maker
    r = client.post("/api/v1/operator/organizations/org_nope/plan",
                    json={"plan": PlanTier.PLATFORM.value}, headers=auth(key))
    assert r.status_code == 404


# ── break-glass ──────────────────────────────────────────────────────────────
def test_reaching_inside_a_tenant_without_a_grant_is_refused(
        client_and_maker, key):
    """The console holds a live credential and still cannot read the tenant."""
    client, _ = client_and_maker
    r = client.get(f"/api/v1/operator/organizations/{ORG}/support",
                   headers=auth(key))
    assert r.status_code == 403
    assert "break-glass" in r.json()["detail"]


def test_a_grant_needs_a_reason_a_person_could_be_asked_about(
        client_and_maker, key):
    client, _ = client_and_maker
    r = client.post(f"/api/v1/operator/organizations/{ORG}/access",
                    json={"justification": "because"}, headers=auth(key))
    assert r.status_code == 422           # refused by the model's own minimum


def test_a_grant_opens_the_door_and_every_reach_is_recorded(
        client_and_maker, key):
    client, Maker = client_and_maker
    opened = client.post(
        f"/api/v1/operator/organizations/{ORG}/access",
        json={"justification": "PIE-114 — customer says their sync is stuck"},
        headers=auth(key))
    assert opened.status_code == 200

    r = client.get(f"/api/v1/operator/organizations/{ORG}/support",
                   headers=auth(key))
    assert r.status_code == 200
    assert "people" in r.json()

    s = Maker()
    events = s.query(models.AccessEvent).filter_by(organization_id=ORG).all()
    actions = [e.action for e in events]
    s.close()
    # The grant, and the individual reach. Per-use rather than per-grant is the
    # property `trust/access` exists for.
    assert "GRANTED" in actions
    assert "ACCESSED" in actions


def test_a_grant_over_one_tenant_does_not_open_another(client_and_maker, key):
    client, _ = client_and_maker
    client.post(f"/api/v1/operator/organizations/{ORG}/access",
                json={"justification": "PIE-114 — looking at their sync"},
                headers=auth(key))
    r = client.get(f"/api/v1/operator/organizations/{OTHER}/support",
                   headers=auth(key))
    assert r.status_code == 403


def test_handing_the_key_back_closes_the_door_again(client_and_maker, key):
    client, _ = client_and_maker
    opened = client.post(
        f"/api/v1/operator/organizations/{ORG}/access",
        json={"justification": "PIE-114 — checking their last sync"},
        headers=auth(key)).json()
    assert client.get(f"/api/v1/operator/organizations/{ORG}/support",
                      headers=auth(key)).status_code == 200

    client.delete(f"/api/v1/operator/access/{opened['grant_id']}",
                  headers=auth(key))
    assert client.get(f"/api/v1/operator/organizations/{ORG}/support",
                      headers=auth(key)).status_code == 403


def test_the_access_log_is_readable_without_a_grant(client_and_maker, key):
    """Needing a grant to see whether you hold one would be circular, and this
    is the record *of* break-glass rather than tenant data."""
    client, _ = client_and_maker
    r = client.get(f"/api/v1/operator/organizations/{ORG}/access",
                   headers=auth(key))
    assert r.status_code == 200
    assert r.json()["active_grant"] is None


def test_the_operator_is_the_staff_id_a_customer_reads(client_and_maker, key):
    """`operator_id` and not `key_id`: rotating a laptop's key must not make
    last month's access log name somebody who does not exist."""
    client, Maker = client_and_maker
    client.post(f"/api/v1/operator/organizations/{ORG}/access",
                json={"justification": "PIE-114 — support request from them"},
                headers=auth(key))
    s = Maker()
    grant = s.query(models.AccessGrant).filter_by(organization_id=ORG).one()
    s.close()
    assert grant.staff_user_id == "sanketh"


# ── the module, without HTTP ─────────────────────────────────────────────────
def test_reach_into_records_before_it_announces(session):
    """Order is the control: a refusal must leave the connection as
    fail-closed as it was, so the grant is asserted before the tenant is set."""
    session.add(models.Organization(organization_id=ORG, name="x",
                                    currency="INR", config={}))
    session.flush()
    who = operator.Operator(operator_id="sanketh", key_id="k", name="")
    with pytest.raises(access.AccessDenied):
        operator.reach_into(session, who, ORG, resource="test")
    # Nothing was announced, and nothing was written.
    assert session.query(models.AccessEvent).count() == 0


def test_a_key_needs_an_operator_to_name(session):
    with pytest.raises(ValueError):
        operator.issue(session, operator_id="  ")
