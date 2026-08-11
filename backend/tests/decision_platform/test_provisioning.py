"""Provisioning: creating a new tenant with its first owner.

The Tier-1 onboarding path (`seed.provision_organization`). The properties that
matter: the new org is fully isolated, its owner can actually sign in (hash
stored, change-password flag set), and provisioning the same thing twice fails
loudly instead of converging — a duplicated customer is a mistake, not a state.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.domain import models
from app.domain.enums import Role
from app.passwords import verify_password
from app.seed import provision_organization


def test_provisions_org_with_a_sign_in_capable_owner(session):
    org_id, password = provision_organization(
        session, name="Acme Distributors", owner_email="owner@acme.in",
        owner_name="A. Owner")

    org = session.get(models.Organization, org_id)
    assert org is not None and org.name == "Acme Distributors"
    assert org.erp == "zoho" and org.currency == "INR"

    user = session.scalar(select(models.User).where(
        models.User.email == "owner@acme.in"))
    assert user is not None
    assert user.organization_id == org_id
    assert user.role == Role.OWNER.value and user.active
    assert verify_password(password, user.password_hash)
    assert user.must_change_password  # issued credentials are always flagged


def test_org_id_derives_from_name_and_avoids_collisions(session):
    first, _ = provision_organization(
        session, name="Acme Distributors", owner_email="a@acme.in",
        owner_name="A")
    second, _ = provision_organization(
        session, name="Acme Distributors", owner_email="b@acme.in",
        owner_name="B")
    assert first == "org_acme_distributors"
    assert second == "org_acme_distributors_2"


def test_explicit_org_id_and_currency_are_honoured(session):
    org_id, _ = provision_organization(
        session, name="Gulf Tools", owner_email="o@gulf.ae",
        owner_name="O", currency="aed", org_id="org_gulf")
    org = session.get(models.Organization, org_id)
    assert org_id == "org_gulf"
    assert org.currency == "AED"


def test_duplicate_email_is_refused(session):
    provision_organization(session, name="First", owner_email="dup@x.in",
                           owner_name="F")
    with pytest.raises(ValueError, match="already exists with email"):
        provision_organization(session, name="Second", owner_email="dup@x.in",
                               owner_name="S")


def test_duplicate_explicit_org_id_is_refused(session):
    provision_organization(session, name="First", owner_email="f@x.in",
                           owner_name="F", org_id="org_taken")
    with pytest.raises(ValueError, match="org_taken.*already exists"):
        provision_organization(session, name="Second", owner_email="s@x.in",
                               owner_name="S", org_id="org_taken")


def test_rejects_blank_name_and_bad_email(session):
    with pytest.raises(ValueError, match="name is required"):
        provision_organization(session, name="  ", owner_email="o@x.in",
                               owner_name="O")
    with pytest.raises(ValueError, match="does not look like an email"):
        provision_organization(session, name="X", owner_email="not-an-email",
                               owner_name="O")


def test_weak_explicit_password_is_refused(session):
    with pytest.raises(ValueError):
        provision_organization(session, name="X", owner_email="o2@x.in",
                               owner_name="O", password="short")


def test_new_tenant_is_isolated_from_the_default_org(session):
    """The provisioned org shares nothing with org_sanketh — the property the
    whole multi-tenant claim rests on."""
    from app.seed import ensure_org_and_users

    default_org = ensure_org_and_users(session)
    new_org, _ = provision_organization(
        session, name="Stranger Co", owner_email="o@stranger.in",
        owner_name="O")
    assert new_org != default_org

    new_org_users = session.scalars(select(models.User).where(
        models.User.organization_id == new_org)).all()
    assert [u.email for u in new_org_users] == ["o@stranger.in"]
