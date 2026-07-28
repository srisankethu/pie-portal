"""Provisioning a new tenant organization.

Each Zoho connection is a fully separate platform tenant — this is how a
second, third, ... legal entity gets its own organization and owner account,
the same way the default one is seeded at bootstrap.
"""
from __future__ import annotations

import pytest

from app.domain import models
from app.domain.enums import Role
from app.provision_org import add_user, provision_organization


def test_provisions_the_organization_and_its_owner(session):
    org = provision_organization(
        session, organization_id="org_sls", name="SLS Engineers",
        owner_email="Owner@SLS.example", owner_name="S. Owner")
    session.commit()

    assert org.organization_id == "org_sls" and org.name == "SLS Engineers"
    owner = session.query(models.User).filter_by(organization_id="org_sls").one()
    assert owner.email == "owner@sls.example"          # lowercased
    assert owner.role == Role.OWNER.value
    assert owner.active is True


def test_is_idempotent(session):
    provision_organization(session, organization_id="org_sls", name="SLS Engineers",
                           owner_email="owner@sls.example", owner_name="S. Owner")
    session.commit()
    provision_organization(session, organization_id="org_sls", name="SLS Engineers",
                           owner_email="owner@sls.example", owner_name="S. Owner")
    session.commit()

    assert session.query(models.Organization).filter_by(organization_id="org_sls").count() == 1
    assert session.query(models.User).filter_by(organization_id="org_sls").count() == 1


def test_a_re_run_does_not_overwrite_the_organization_name(session):
    """Provisioning is a one-time step, not a sync — a re-run with a typo'd
    name argument must not silently rename an already-live tenant."""
    provision_organization(session, organization_id="org_sls", name="SLS Engineers",
                           owner_email="owner@sls.example", owner_name="S. Owner")
    session.commit()
    provision_organization(session, organization_id="org_sls", name="Something Else",
                           owner_email="owner@sls.example", owner_name="S. Owner")
    session.commit()

    assert session.get(models.Organization, "org_sls").name == "SLS Engineers"


def test_the_same_email_cannot_head_two_different_organizations(session):
    """Email is the platform's global login key — reusing one across tenants
    would make login ambiguous about which organization it resolves to."""
    provision_organization(session, organization_id="org_sls", name="SLS Engineers",
                           owner_email="owner@example.com", owner_name="Owner")
    session.commit()

    with pytest.raises(ValueError, match="already belongs to organization"):
        provision_organization(session, organization_id="org_4u", name="4U Precision",
                               owner_email="owner@example.com", owner_name="Owner")


def test_add_user_adds_a_manager_to_an_existing_org(session):
    provision_organization(session, organization_id="org_sls", name="SLS Engineers",
                           owner_email="owner@sls.example", owner_name="Owner")
    session.commit()

    user = add_user(session, organization_id="org_sls", email="manager@sls.example",
                    name="M. Rao", role=Role.SALES_MANAGER)
    session.commit()

    assert user.role == Role.SALES_MANAGER.value
    assert session.query(models.User).filter_by(organization_id="org_sls").count() == 2


def test_add_user_is_idempotent_for_the_same_email(session):
    provision_organization(session, organization_id="org_sls", name="SLS Engineers",
                           owner_email="owner@sls.example", owner_name="Owner")
    session.commit()

    add_user(session, organization_id="org_sls", email="m@sls.example", name="M",
            role=Role.SALES_MANAGER)
    add_user(session, organization_id="org_sls", email="m@sls.example", name="M",
            role=Role.SALES_MANAGER)
    session.commit()

    assert session.query(models.User).filter_by(email="m@sls.example").count() == 1


def test_add_user_refuses_an_email_from_another_tenant(session):
    provision_organization(session, organization_id="org_sls", name="SLS Engineers",
                           owner_email="owner@sls.example", owner_name="Owner")
    provision_organization(session, organization_id="org_4u", name="4U Precision",
                           owner_email="owner@4u.example", owner_name="Owner")
    session.commit()

    with pytest.raises(ValueError, match="different organization"):
        add_user(session, organization_id="org_sls", email="owner@4u.example",
                name="Someone", role=Role.SALESPERSON)
