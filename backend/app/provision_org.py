"""Provision a new tenant organization.

Each Zoho connection is a fully separate platform tenant (its own users, its
own decision queue — see ``ZohoConnection`` and ``ingestion/connections.py``).
The platform's single default organization is created automatically at
bootstrap; every additional one (a second, third... legal entity, each with
its own Zoho Books account) is provisioned explicitly with this module, the
same way ``python -m app.seed`` seeds the first one.

This does not connect Zoho — provisioning an organization and linking its
Zoho account are separate steps, matching who typically does each: an admin
provisions the tenant and its first owner account; that owner then signs in
and connects their own Zoho Books account from Data & connection (or an admin
does it for them via ``PUT /api/v1/data/connection``, using that owner's
token).
"""
from __future__ import annotations

import argparse
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import models
from .domain.enums import Role
from .passwords import generate_password, hash_password, password_problem

# Temporary passwords issued during this process, by email. Written here rather
# than returned because the callers below want the org/user object; the caller
# that needs the credential reads it out immediately and it is never persisted
# in plaintext.
provisioned_password: dict[str, str] = {}


def provision_organization(
    session: Session, *, organization_id: str, name: str,
    owner_email: str, owner_name: str, currency: str = "INR",
    owner_password: Optional[str] = None,
) -> models.Organization:
    """Create the organization and its first (owner) user, idempotently.

    A pre-existing organization_id is left as-is (name/currency are not
    overwritten by a re-run) — provisioning is a one-time step, not a sync.

    The owner is given a temporary password, readable once from
    ``provisioned_password``. An owner without one could not sign in at all,
    which would leave the tenant unreachable by anyone: the platform has no
    self-service sign-up, so this account is the only way in.
    """
    owner_email = owner_email.strip().lower()
    existing_email = session.scalar(select(models.User).where(models.User.email == owner_email))
    if existing_email is not None and existing_email.organization_id != organization_id:
        raise ValueError(
            f"{owner_email!r} already belongs to organization "
            f"{existing_email.organization_id!r} — emails are unique across every "
            "tenant, so this owner cannot also head a second one with this address.")

    org = session.get(models.Organization, organization_id)
    if org is None:
        org = models.Organization(organization_id=organization_id, name=name,
                                  erp="zoho", currency=currency, config={})
        session.add(org)

    if existing_email is None:
        password = owner_password or generate_password()
        problem = password_problem(password)
        if problem:
            raise ValueError(problem)
        owner = models.User(organization_id=organization_id, email=owner_email,
                            name=owner_name, role=Role.OWNER.value, active=True,
                            password_hash=hash_password(password),
                            must_change_password=True)
        session.add(owner)
        provisioned_password[owner_email] = password
    session.flush()
    return org


def add_user(session: Session, *, organization_id: str, email: str, name: str,
            role: Role, password: Optional[str] = None) -> models.User:
    """Add another user (manager or salesperson) to an already-provisioned org.

    Like the owner above, they get a temporary password recorded in
    ``provisioned_password`` — an account that cannot sign in is not an account.
    """
    email = email.strip().lower()
    existing = session.scalar(select(models.User).where(models.User.email == email))
    if existing is not None:
        if existing.organization_id != organization_id:
            raise ValueError(
                f"{email!r} already belongs to a different organization "
                f"({existing.organization_id!r}) — emails are unique across every tenant.")
        return existing
    issued = password or generate_password()
    problem = password_problem(issued)
    if problem:
        raise ValueError(problem)
    user = models.User(organization_id=organization_id, email=email, name=name,
                       role=role.value, active=True,
                       password_hash=hash_password(issued),
                       must_change_password=True)
    session.add(user)
    session.flush()
    provisioned_password[email] = issued
    return user


def main() -> None:
    """CLI: ``python -m app.provision_org --org-id org_sls --name "SLS Engineers" \\
    --owner-email owner@sls.example --owner-name "..."``

    Run after ``alembic upgrade head``. Idempotent for the organization and the
    owner; does not touch a Zoho connection — that owner connects their own
    from the app afterward.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", required=True, help="A short, stable slug, e.g. org_sls")
    parser.add_argument("--name", required=True, help="Display name, e.g. 'SLS Engineers'")
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--owner-name", required=True)
    parser.add_argument("--currency", default="INR")
    args = parser.parse_args()

    from .db import SessionLocal

    session = SessionLocal()
    try:
        org = provision_organization(
            session, organization_id=args.org_id, name=args.name,
            owner_email=args.owner_email, owner_name=args.owner_name,
            currency=args.currency)
        session.commit()
        email = args.owner_email.strip().lower()
        password = provisioned_password.get(email)
        print(f"Provisioned organization {org.organization_id!r} ({org.name}).")
        if password:
            print(f"Owner {email} temporary password: {password}")
            print("Shown once. They are prompted to change it at first sign-in.")
        else:
            print(f"Owner {email} already existed — their password is unchanged.")
        print("They can sign in now and connect Zoho from Data & connection.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
