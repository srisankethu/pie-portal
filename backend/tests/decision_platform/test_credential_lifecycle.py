"""What happens to a *sign-in* when a company is disconnected.

Connections and credentials are separate rows, and disconnecting a company
deliberately leaves its credential behind (``clear_zoho_connection``): another
organization may be using it, and keeping it means reconnecting does not mean
re-entering a secret. That retention is right and is pinned in
``test_connections.py``.

This suite pins the two things that made the retention read as a bug on screen.
A grant left behind is offered in the sign-in picker for ever, so:

* re-entering credentials after Zoho re-issues a Self Client token must rotate
  the row for that app rather than mint a second one — otherwise one app
  registration accumulates a row per reconnect, each surviving the connection
  that prompted it; and
* an owner must be able to remove one that no longer reaches anything, or the
  pile is permanent.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import crypto
from app.db import get_session
from app.domain import models
from app.ingestion.connections import (
    CredentialNotUsable,
    clear_zoho_connection,
    delete_credential,
    list_connections,
    set_zoho_credentials,
    share_credential,
    usable_credentials,
)
from app.routers import data_status, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"
OWNER = "s.menon@pie.example"


def _org(session, org_id="org_a"):
    session.add(models.Organization(organization_id=org_id, name=org_id))
    session.flush()
    return org_id


def _creds(session, org):
    return session.scalars(select(models.ZohoCredential).where(
        models.ZohoCredential.owner_organization_id == org)).all()


# ── the duplicate that outlived its connection ───────────────────────────────

def test_a_regenerated_refresh_token_rotates_the_sign_in_rather_than_duplicating(session):
    """The screenshot that started this: two rows, one client id, both unused.

    Zoho issues a fresh refresh token every time a Self Client grant is
    generated, so "connect, disconnect, reconnect" re-enters the *same app*
    with a *different secret*. Matching on the whole secret triple missed that
    and created a second row per cycle.
    """
    org = _org(session)
    set_zoho_credentials(session, org, zoho_organization_id="AAA",
                         client_id="1000.UX014IF75", client_secret="s",
                         refresh_token="first")
    clear_zoho_connection(session, org)

    set_zoho_credentials(session, org, zoho_organization_id="AAA",
                         client_id="1000.UX014IF75", client_secret="s",
                         refresh_token="second-after-regenerating")

    rows = _creds(session, org)
    assert len(rows) == 1, "one app registration, one row"
    assert crypto.decrypt(rows[0].refresh_token_encrypted) == "second-after-regenerating"


def test_identical_secrets_still_attach_without_rotating(session):
    """The pre-existing behaviour, unchanged: nothing to rotate, nothing stamped."""
    org = _org(session)
    set_zoho_credentials(session, org, zoho_organization_id="AAA", client_id="cid",
                         client_secret="s", refresh_token="r")
    before = _creds(session, org)[0].rotated_at

    set_zoho_credentials(session, org, zoho_organization_id="BBB", client_id="cid",
                         client_secret="s", refresh_token="r")

    rows = _creds(session, org)
    assert len(rows) == 1
    assert rows[0].rotated_at == before
    assert len(list_connections(session, org)) == 2


def test_a_different_client_id_is_a_different_sign_in(session):
    """Two genuinely separate Zoho logins must not collapse into one row."""
    org = _org(session)
    set_zoho_credentials(session, org, zoho_organization_id="AAA", client_id="cid-one",
                         client_secret="s", refresh_token="r")
    set_zoho_credentials(session, org, zoho_organization_id="BBB", client_id="cid-two",
                         client_secret="s2", refresh_token="r2")
    assert len(_creds(session, org)) == 2


def test_an_already_duplicated_database_rotates_the_connected_row(session):
    """The rule arrived after the duplicate rows did.

    Rotating an orphan while a *connected* duplicate keeps the revoked token
    would leave a company that looks connected and cannot sync — so the row
    something is actually connected through is the one that gets the new
    secret.
    """
    from app.ingestion.connections import create_credential, connect_with_credential

    org = _org(session)
    orphan = create_credential(session, org, client_id="cid", client_secret="s",
                               refresh_token="orphan-token", label="left over")
    live = create_credential(session, org, client_id="cid", client_secret="s",
                             refresh_token="live-token", label="in use")
    connect_with_credential(session, org, credential_id=live.credential_id,
                            zoho_organization_id="AAA")

    set_zoho_credentials(session, org, zoho_organization_id="AAA", client_id="cid",
                         client_secret="s", refresh_token="regenerated")

    assert crypto.decrypt(live.refresh_token_encrypted) == "regenerated"
    assert crypto.decrypt(orphan.refresh_token_encrypted) == "orphan-token"
    # And the orphan is still removable, which is the other half of the fix.
    assert delete_credential(session, org, orphan.credential_id) is True


def test_re_entering_a_secret_never_rotates_another_organizations_grant(session):
    """Sharing grants *use*, never the right to change the key underneath.

    ``usable_credentials`` includes grants shared with this organization, so a
    rotate-on-match keyed off that list would let a borrower overwrite the
    lender's secret for every other tenant using it.
    """
    a, b = _org(session, "org_a"), _org(session, "org_b")
    set_zoho_credentials(session, a, zoho_organization_id="AAA", client_id="cid",
                         client_secret="s", refresh_token="lender")
    lent = _creds(session, a)[0]
    share_credential(session, a, lent.credential_id, with_organization_ids=[b])
    assert lent.credential_id in {c.credential_id for c in usable_credentials(session, b)}

    set_zoho_credentials(session, b, zoho_organization_id="BBB", client_id="cid",
                         client_secret="s", refresh_token="borrower-own-grant")

    assert crypto.decrypt(lent.refresh_token_encrypted) == "lender"
    assert len(_creds(session, b)) == 1, "the borrower got its own row"


def test_a_regenerated_token_for_another_data_centre_is_a_new_sign_in(session):
    """A code issued by accounts.zoho.com is not redeemable at accounts.zoho.in,
    so the estate is part of what identifies the grant."""
    org = _org(session)
    set_zoho_credentials(session, org, zoho_organization_id="AAA", client_id="cid",
                         client_secret="s", refresh_token="r",
                         accounts_base="https://accounts.zoho.in",
                         api_base="https://www.zohoapis.in/books/v3")
    set_zoho_credentials(session, org, zoho_organization_id="BBB", client_id="cid",
                         client_secret="s", refresh_token="r2",
                         accounts_base="https://accounts.zoho.com",
                         api_base="https://www.zohoapis.com/books/v3")
    assert len(_creds(session, org)) == 2


def test_a_refused_url_does_not_leave_the_secret_already_replaced(session):
    """Validation runs before the write on the rotate path too.

    A rotation that encrypts the new secret and *then* refuses the URL leaves
    the credential holding a half-applied change in exactly the case the guard
    exists to prevent.
    """
    from app.ingestion.url_safety import UnsafeSourceUrl

    org = _org(session)
    set_zoho_credentials(session, org, zoho_organization_id="AAA", client_id="cid",
                         client_secret="s", refresh_token="good")
    cred = _creds(session, org)[0]

    with pytest.raises(UnsafeSourceUrl):
        set_zoho_credentials(session, org, zoho_organization_id="AAA", client_id="cid",
                             client_secret="s", refresh_token="rotated",
                             api_base="http://169.254.169.254/latest/meta-data")

    assert crypto.decrypt(cred.refresh_token_encrypted) == "good"


# ── removing a sign-in that reaches nothing ──────────────────────────────────

@pytest.fixture()
def client(engine):
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    for r in (platform_auth.router, data_status.router):
        app.include_router(r)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app), Maker


def _hdr(c, email=OWNER):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_an_orphaned_sign_in_can_be_removed_over_http(client):
    c, Maker = client
    with Maker() as s:
        set_zoho_credentials(s, ORG, zoho_organization_id="AAA", client_id="cid",
                             client_secret="s", refresh_token="r")
        s.commit()
        cid = _creds(s, ORG)[0].credential_id

    # Still connected: refused, and the refusal says why rather than 500ing.
    r = c.delete(f"/api/v1/data/credentials/{cid}", headers=_hdr(c))
    assert r.status_code == 409, r.text
    assert "still use this credential" in r.json()["detail"]

    with Maker() as s:
        clear_zoho_connection(s, ORG)
        s.commit()

    r = c.delete(f"/api/v1/data/credentials/{cid}", headers=_hdr(c))
    assert r.status_code == 200, r.text
    assert r.json()["removed"] is True

    with Maker() as s:
        assert _creds(s, ORG) == []


def test_removing_a_sign_in_someone_else_owns_is_refused(client):
    """Owned-only, and the refusal cannot distinguish "not yours" from "no such
    credential" — otherwise this endpoint enumerates another tenant's ids."""
    c, Maker = client
    with Maker() as s:
        other = _org(s, "org_other")
        set_zoho_credentials(s, other, zoho_organization_id="ZZZ", client_id="cid",
                             client_secret="s", refresh_token="r")
        clear_zoho_connection(s, other)
        s.commit()
        cid = _creds(s, other)[0].credential_id

    r = c.delete(f"/api/v1/data/credentials/{cid}", headers=_hdr(c))
    assert r.status_code == 403, r.text

    with Maker() as s:
        assert len(_creds(s, "org_other")) == 1


def test_a_salesperson_cannot_remove_a_sign_in(client):
    c, Maker = client
    with Maker() as s:
        set_zoho_credentials(s, ORG, zoho_organization_id="AAA", client_id="cid",
                             client_secret="s", refresh_token="r")
        clear_zoho_connection(s, ORG)
        s.commit()
        cid = _creds(s, ORG)[0].credential_id

    r = c.delete(f"/api/v1/data/credentials/{cid}",
                 headers=_hdr(c, "r.nair@pie.example"))
    assert r.status_code in (401, 403), r.text
    with Maker() as s:
        assert len(_creds(s, ORG)) == 1


def test_service_level_delete_is_unchanged(session):
    """The guard this route leans on, still refusing and still owner-scoped."""
    a, b = _org(session, "org_a"), _org(session, "org_b")
    set_zoho_credentials(session, a, zoho_organization_id="AAA", client_id="cid",
                         client_secret="s", refresh_token="r")
    cid = _creds(session, a)[0].credential_id

    with pytest.raises(ValueError):
        delete_credential(session, a, cid)
    with pytest.raises(CredentialNotUsable):
        delete_credential(session, b, cid)

    clear_zoho_connection(session, a)
    assert delete_credential(session, a, cid) is True
