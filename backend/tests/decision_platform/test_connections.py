"""Per-organization Zoho connections — the multi-tenant credential store.

Each organization is a fully separate tenant, so each has at most one Zoho
connection of its own. The one exception is the platform's original default
organization, which falls back to the ZOHO_* environment variables when it has
no stored connection — so an existing single-tenant deployment is unaffected.
"""
from __future__ import annotations

from app.config import settings
from app.domain import models
from app.ingestion.connections import (
    clear_zoho_connection,
    get_zoho_credentials,
    has_zoho_connection,
    set_zoho_credentials,
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

    row = session.get(models.ZohoConnection, org)
    assert "the-client-secret" not in row.client_secret_encrypted
    assert "the-refresh-token" not in row.refresh_token_encrypted


def test_setting_again_replaces_the_connection_in_place(session):
    org = _org(session)
    set_zoho_credentials(session, org, zoho_organization_id="1", client_id="cid-old",
                         client_secret="s1", refresh_token="r1")
    set_zoho_credentials(session, org, zoho_organization_id="2", client_id="cid-new",
                         client_secret="s2", refresh_token="r2")

    assert session.query(models.ZohoConnection).filter_by(organization_id=org).count() == 1
    creds = get_zoho_credentials(session, org)
    assert creds.organization_id == "2" and creds.client_id == "cid-new"


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
