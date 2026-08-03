"""Per-organization Zoho connections — the multi-tenant credential store.

Each organization is a fully separate tenant, so each has at most one Zoho
connection of its own. The one exception is the platform's original default
organization, which falls back to the ZOHO_* environment variables when it has
no stored connection — so an existing single-tenant deployment is unaffected.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.domain import models
from app.ingestion.connections import (
    CredentialNotUsable,
    clear_zoho_connection,
    connect_with_credential,
    list_connections,
    create_credential,
    delete_credential,
    get_zoho_credentials,
    has_zoho_connection,
    rotate_credential,
    set_zoho_credentials,
    share_credential,
)
from app.ingestion.zoho_client import ZohoCredentials


def _org(session, org_id="org_a"):
    session.add(models.Organization(organization_id=org_id, name=org_id))
    session.flush()
    return org_id


def test_a_new_organization_has_no_connection_by_default(session):
    org = _org(session)
    assert get_zoho_credentials(session, org) is None
    assert has_zoho_connection(session, org) is False


def test_set_then_get_round_trips_the_credentials(session):
    org = _org(session)
    set_zoho_credentials(
        session, org, zoho_organization_id="60036630626", client_id="1000.CID",
        client_secret="s3cr3t", refresh_token="1000.rtok.abc",
        accounts_base="https://accounts.zoho.in", api_base="https://www.zohoapis.in/books/v3")

    creds = get_zoho_credentials(session, org)
    assert creds == ZohoCredentials(
        organization_id="60036630626", client_id="1000.CID", client_secret="s3cr3t",
        refresh_token="1000.rtok.abc", accounts_base="https://accounts.zoho.in",
        api_base="https://www.zohoapis.in/books/v3")


def test_secrets_are_encrypted_at_rest(session):
    org = _org(session)
    set_zoho_credentials(session, org, zoho_organization_id="1", client_id="cid",
                         client_secret="the-client-secret", refresh_token="the-refresh-token")

    row = list_connections(session, org)[0]
    cred = row.credential
    assert cred is not None, "secrets live on the credential, not the connection"
    assert "the-client-secret" not in cred.client_secret_encrypted
    assert "the-refresh-token" not in cred.refresh_token_encrypted
    # and no plaintext copy is left behind on the connection itself
    assert row.client_secret_encrypted is None
    assert row.refresh_token_encrypted is None


def test_a_second_company_is_a_second_connection_not_a_replacement(session):
    """The restriction that was removed. One organization reads as many sets of
    books as it has; adding the second must not silently drop the first."""
    org = _org(session)
    set_zoho_credentials(session, org, zoho_organization_id="1", client_id="cid-old",
                         client_secret="s1", refresh_token="r1")
    set_zoho_credentials(session, org, zoho_organization_id="2", client_id="cid-new",
                         client_secret="s2", refresh_token="r2")

    rows = list_connections(session, org)
    assert [r.zoho_organization_id for r in rows] == ["1", "2"]


def test_re_adding_the_same_company_updates_it_rather_than_duplicating(session):
    """Two rows for one Zoho company would sync it twice and double every
    figure the platform reports."""
    org = _org(session)
    set_zoho_credentials(session, org, zoho_organization_id="1", client_id="cid-old",
                         client_secret="s1", refresh_token="r1")
    set_zoho_credentials(session, org, zoho_organization_id="1", client_id="cid-new",
                         client_secret="s2", refresh_token="r2")

    rows = list_connections(session, org)
    assert len(rows) == 1
    assert get_zoho_credentials(session, org).client_id == "cid-new"


def test_clear_removes_the_connection_and_reports_whether_one_existed(session):
    org = _org(session)
    assert clear_zoho_connection(session, org) is False   # nothing to remove yet

    set_zoho_credentials(session, org, zoho_organization_id="1", client_id="cid",
                         client_secret="s", refresh_token="r")
    assert clear_zoho_connection(session, org) is True
    assert get_zoho_credentials(session, org) is None


def test_two_organizations_do_not_see_each_other_s_connection(session):
    """The core multi-tenant guarantee: connecting org_a's Zoho account must
    never be visible to, or usable from, org_b."""
    a, b = _org(session, "org_a"), _org(session, "org_b")
    set_zoho_credentials(session, a, zoho_organization_id="AAA", client_id="cid-a",
                         client_secret="sa", refresh_token="ra")
    set_zoho_credentials(session, b, zoho_organization_id="BBB", client_id="cid-b",
                         client_secret="sb", refresh_token="rb")

    assert get_zoho_credentials(session, a).organization_id == "AAA"
    assert get_zoho_credentials(session, b).organization_id == "BBB"

    clear_zoho_connection(session, a)
    assert get_zoho_credentials(session, a) is None
    assert get_zoho_credentials(session, b).organization_id == "BBB", \
        "clearing one org's connection must not touch the other's"


# ── the default-organization fallback ───────────────────────────────────────
def test_the_default_org_falls_back_to_settings_when_unconfigured(session, monkeypatch):
    """Backward compatibility: an existing single-tenant deployment configured
    entirely via ZOHO_* environment variables must keep working with no DB
    migration step required of it."""
    monkeypatch.setattr(settings, "ZOHO_ORGANIZATION_ID", "60036630487")
    monkeypatch.setattr(settings, "ZOHO_CLIENT_ID", "env-cid")
    monkeypatch.setattr(settings, "ZOHO_CLIENT_SECRET", "env-secret")
    monkeypatch.setattr(settings, "ZOHO_REFRESH_TOKEN", "env-rtok")
    _org(session, settings.DEFAULT_ORG_ID)

    creds = get_zoho_credentials(session, settings.DEFAULT_ORG_ID)
    assert creds == ZohoCredentials.from_settings()
    assert creds.organization_id == "60036630487"


def test_a_stored_connection_wins_over_settings_even_for_the_default_org(session, monkeypatch):
    """Once the default org explicitly connects its own account, that — not
    whatever is left in the environment — is authoritative."""
    monkeypatch.setattr(settings, "ZOHO_ORGANIZATION_ID", "60036630487")
    org = _org(session, settings.DEFAULT_ORG_ID)
    set_zoho_credentials(session, org, zoho_organization_id="99999999", client_id="cid",
                         client_secret="s", refresh_token="r")

    assert get_zoho_credentials(session, org).organization_id == "99999999"


def test_a_non_default_org_with_no_connection_gets_none_not_the_environment(session, monkeypatch):
    """The settings fallback is scoped to exactly one organization. Any other
    org with no connection of its own must never silently inherit whatever
    happens to be sitting in the process environment."""
    monkeypatch.setattr(settings, "ZOHO_ORGANIZATION_ID", "60036630487")
    monkeypatch.setattr(settings, "ZOHO_CLIENT_ID", "env-cid")
    monkeypatch.setattr(settings, "ZOHO_CLIENT_SECRET", "env-secret")
    monkeypatch.setattr(settings, "ZOHO_REFRESH_TOKEN", "env-rtok")
    org = _org(session, "org_some_other_tenant")

    assert get_zoho_credentials(session, org) is None


# ── one grant, several companies ────────────────────────────────────────────
# A Zoho refresh token belongs to a user, not a company: organization_id is a
# request parameter, and one grant already reaches every company that user can
# see. Storing the secret per connection forced it to be entered — and rotated —
# once per legal entity, for no security benefit, since it was the same secret.

def test_the_same_grant_entered_twice_does_not_make_two_credentials(session):
    """Two entities under one Zoho login must not become two copies of one
    secret — that is the state a later rotation misses half of."""
    a, b = _org(session, "org_sls"), _org(session, "org_4u")
    set_zoho_credentials(session, a, zoho_organization_id="AAA", client_id="cid",
                         client_secret="s", refresh_token="r")
    share_credential(session, a,
                     list_connections(session, a)[0].credential_id,
                     with_organization_ids=[b])
    set_zoho_credentials(session, b, zoho_organization_id="BBB", client_id="cid",
                         client_secret="s", refresh_token="r")

    assert session.query(models.ZohoCredential).count() == 1
    assert (list_connections(session, a)[0].credential_id
            == list_connections(session, b)[0].credential_id)


def test_a_second_company_connects_without_re_entering_any_secret(session):
    a, b = _org(session, "org_sls"), _org(session, "org_4u")
    set_zoho_credentials(session, a, zoho_organization_id="AAA", client_id="cid",
                         client_secret="s", refresh_token="r")
    cid = list_connections(session, a)[0].credential_id
    share_credential(session, a, cid, with_organization_ids=[b])

    connect_with_credential(session, b, credential_id=cid, zoho_organization_id="BBB")

    assert get_zoho_credentials(session, b) == ZohoCredentials(
        organization_id="BBB", client_id="cid", client_secret="s", refresh_token="r",
        accounts_base="https://accounts.zoho.in",
        api_base="https://www.zohoapis.in/books/v3")


def test_one_rotation_covers_every_company_on_that_grant(session):
    """The entire point. Three entities, one new refresh token, one operation."""
    orgs = [_org(session, f"org_{n}") for n in ("sls", "4u", "ups")]
    set_zoho_credentials(session, orgs[0], zoho_organization_id="A", client_id="cid",
                         client_secret="s", refresh_token="old-token")
    cid = list_connections(session, orgs[0])[0].credential_id
    share_credential(session, orgs[0], cid, with_organization_ids=orgs[1:])
    for org, zoho_id in zip(orgs[1:], ("B", "C")):
        connect_with_credential(session, org, credential_id=cid, zoho_organization_id=zoho_id)

    rotate_credential(session, orgs[0], cid, refresh_token="new-token")

    for org, zoho_id in zip(orgs, ("A", "B", "C")):
        creds = get_zoho_credentials(session, org)
        assert creds.refresh_token == "new-token"
        assert creds.organization_id == zoho_id, "each still pulls its own company"


def test_sharing_a_key_does_not_share_a_company(session):
    """Two organizations on one credential must still pull different books."""
    a, b = _org(session, "org_a"), _org(session, "org_b")
    set_zoho_credentials(session, a, zoho_organization_id="AAA", client_id="cid",
                         client_secret="s", refresh_token="r")
    cid = list_connections(session, a)[0].credential_id
    share_credential(session, a, cid, with_organization_ids=[b])
    connect_with_credential(session, b, credential_id=cid, zoho_organization_id="BBB")

    assert get_zoho_credentials(session, a).organization_id == "AAA"
    assert get_zoho_credentials(session, b).organization_id == "BBB"


def test_an_unshared_credential_cannot_be_used_by_another_organization(session):
    a, b = _org(session, "org_a"), _org(session, "org_b")
    set_zoho_credentials(session, a, zoho_organization_id="AAA", client_id="cid",
                         client_secret="s", refresh_token="r")
    cid = list_connections(session, a)[0].credential_id

    with pytest.raises(CredentialNotUsable):
        connect_with_credential(session, b, credential_id=cid, zoho_organization_id="BBB")


def test_revoking_a_share_stops_the_sync_immediately(session):
    """Checked at use, not only at attach: an organization removed from the
    share list must stop pulling now, not whenever someone next edits it."""
    a, b = _org(session, "org_a"), _org(session, "org_b")
    set_zoho_credentials(session, a, zoho_organization_id="AAA", client_id="cid",
                         client_secret="s", refresh_token="r")
    cid = list_connections(session, a)[0].credential_id
    share_credential(session, a, cid, with_organization_ids=[b])
    connect_with_credential(session, b, credential_id=cid, zoho_organization_id="BBB")
    assert get_zoho_credentials(session, b) is not None

    share_credential(session, a, cid, with_organization_ids=[])
    with pytest.raises(CredentialNotUsable):
        get_zoho_credentials(session, b)


def test_only_the_owning_organization_may_rotate_or_share(session):
    a, b = _org(session, "org_a"), _org(session, "org_b")
    set_zoho_credentials(session, a, zoho_organization_id="AAA", client_id="cid",
                         client_secret="s", refresh_token="r")
    cid = list_connections(session, a)[0].credential_id
    share_credential(session, a, cid, with_organization_ids=[b])

    with pytest.raises(CredentialNotUsable):
        rotate_credential(session, b, cid, refresh_token="hijacked")
    with pytest.raises(CredentialNotUsable):
        share_credential(session, b, cid, with_organization_ids=[b])


def test_a_credential_in_use_cannot_be_deleted(session):
    """Deleting under a live connection leaves an org that looks connected and
    silently cannot sync."""
    a = _org(session, "org_a")
    set_zoho_credentials(session, a, zoho_organization_id="AAA", client_id="cid",
                         client_secret="s", refresh_token="r")
    cid = list_connections(session, a)[0].credential_id

    with pytest.raises(ValueError):
        delete_credential(session, a, cid)

    clear_zoho_connection(session, a)
    assert delete_credential(session, a, cid) is True


def test_disconnecting_keeps_the_credential_for_the_others(session):
    a, b = _org(session, "org_a"), _org(session, "org_b")
    set_zoho_credentials(session, a, zoho_organization_id="AAA", client_id="cid",
                         client_secret="s", refresh_token="r")
    cid = list_connections(session, a)[0].credential_id
    share_credential(session, a, cid, with_organization_ids=[b])
    connect_with_credential(session, b, credential_id=cid, zoho_organization_id="BBB")

    clear_zoho_connection(session, a)
    assert get_zoho_credentials(session, b) is not None, \
        "one tenant disconnecting must not break the others on the same grant"


# ── merging what the old schema already duplicated ──────────────────────────
def test_merge_collapses_duplicate_grants_and_repoints_connections(session):
    """The one-off cleanup for a deployment that entered one app three times."""
    from app.ingestion.merge_credentials import merge

    orgs = [_org(session, f"org_{n}") for n in ("sls", "4u", "ups")]
    for org, zoho_id in zip(orgs, ("A", "B", "C")):
        # each connects independently with the same secret, as the old schema
        # forced — bypassing dedupe by creating the credential directly
        cred = create_credential(session, org, client_id="cid", client_secret="s",
                                 refresh_token="r")
        connect_with_credential(session, org, credential_id=cred.credential_id,
                                zoho_organization_id=zoho_id)
    assert session.query(models.ZohoCredential).count() == 3

    report = merge(session, dry_run=True)
    assert report["credentials_removed"] == 2 and session.query(
        models.ZohoCredential).count() == 3, "a dry run changes nothing"

    merge(session)
    assert session.query(models.ZohoCredential).count() == 1
    for org, zoho_id in zip(orgs, ("A", "B", "C")):
        creds = get_zoho_credentials(session, org)
        assert creds.refresh_token == "r"
        assert creds.organization_id == zoho_id, "each keeps its own company"


def test_merge_leaves_genuinely_different_grants_alone(session):
    """Two entities under separate Zoho logins legitimately need two."""
    from app.ingestion.merge_credentials import merge

    a, b = _org(session, "org_a"), _org(session, "org_b")
    set_zoho_credentials(session, a, zoho_organization_id="AAA", client_id="cid-a",
                         client_secret="sa", refresh_token="ra")
    set_zoho_credentials(session, b, zoho_organization_id="BBB", client_id="cid-b",
                         client_secret="sb", refresh_token="rb")

    assert merge(session)["groups"] == 0
    assert session.query(models.ZohoCredential).count() == 2
