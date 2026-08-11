"""Bootstrap the single V1 organization and its demo users.

Users are the platform's own (role is a property of User, §2); they are seeded
rather than pulled from Zoho. Idempotent — safe to call on every startup.

Seeded accounts get a known development password and are flagged to change it.
That is worse than no default in a real deployment and better than the previous
arrangement, where the login endpoint accepted anything: a default password can
be rotated, and ``SEED_PASSWORD`` overrides it. What must never come back is a
sign-in that does not check.
"""
from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .domain import models
from .domain.enums import Role
from .passwords import generate_password, hash_password, password_problem

# Overridable so a real deployment never ships with the published default.
SEED_PASSWORD = os.environ.get("SEED_PASSWORD", "change-me-now")

# Demo users for the three roles.
DEMO_USERS = [
    {"user_id": "usr_owner", "email": "s.menon@sanketh.in", "name": "S. Menon",
     "role": Role.OWNER},
    {"user_id": "usr_manager", "email": "m.rao@sanketh.in", "name": "M. Rao",
     "role": Role.SALES_MANAGER},
    {"user_id": "usr_sales", "email": "r.nair@sanketh.in", "name": "R. Nair",
     "role": Role.SALESPERSON},
]


def ensure_org_and_users(session: Session) -> str:
    """Create the default org + demo users if absent. Returns the org id."""
    org = session.get(models.Organization, settings.DEFAULT_ORG_ID)
    if org is None:
        org = models.Organization(
            organization_id=settings.DEFAULT_ORG_ID, name=settings.DEFAULT_ORG_NAME,
            erp="zoho", currency=settings.DEFAULT_CURRENCY, config={})
        session.add(org)
    for u in DEMO_USERS:
        existing = session.get(models.User, u["user_id"])
        if existing is None:
            session.add(models.User(
                user_id=u["user_id"], organization_id=settings.DEFAULT_ORG_ID,
                email=u["email"], name=u["name"], role=u["role"].value, active=True,
                password_hash=hash_password(SEED_PASSWORD),
                must_change_password=settings.ISSUED_ACCOUNTS_MUST_CHANGE_PASSWORD))
        elif not existing.password_hash:
            # An account seeded before passwords existed would otherwise be
            # locked out entirely by the new login check.
            existing.password_hash = hash_password(SEED_PASSWORD)
            existing.must_change_password = settings.ISSUED_ACCOUNTS_MUST_CHANGE_PASSWORD
    session.flush()
    return settings.DEFAULT_ORG_ID


def set_password(session: Session, email: str,
                 password: Optional[str] = None) -> str:
    """Set one user's password out-of-band. Returns the password that was set.

    The recovery path, and the reason making login strict is safe to ship.
    Sign-in now requires a stored hash, and a database that predates hashes has
    none — so without this, upgrading would lock out every account created
    before the change, including the only owner of a tenant. That is not a
    migration, it is an outage.
    """
    email = (email or "").strip().lower()
    user = session.scalar(select(models.User).where(models.User.email == email))
    if user is None:
        raise ValueError(f"No account with email {email!r}")
    issued = password or generate_password()
    problem = password_problem(issued)
    if problem:
        raise ValueError(problem)
    user.password_hash = hash_password(issued)
    user.must_change_password = True
    session.flush()
    return issued


def _org_id_from_name(session: Session, name: str) -> str:
    """Derive an unused ``org_<slug>`` id from the organization's name."""
    slug = "".join(c if c.isalnum() else "_" for c in name.strip().lower())
    slug = "_".join(filter(None, slug.split("_")))[:40] or "tenant"
    candidate = f"org_{slug}"
    n = 2
    while session.get(models.Organization, candidate) is not None:
        candidate = f"org_{slug}_{n}"
        n += 1
    return candidate


def provision_organization(session: Session, *, name: str, owner_email: str,
                           owner_name: str, currency: str = "INR",
                           org_id: Optional[str] = None,
                           password: Optional[str] = None) -> tuple[str, str]:
    """Create a new tenant: an Organization and its first owner account.

    The Tier-1 onboarding path — everything after this is self-serve through
    the existing screens: the owner signs in, is forced to change the password,
    adds the Zoho connection (``POST /connections``), and creates their own
    team (``POST /admin/users``). Deliberately **not** idempotent, unlike
    ``ensure_org_and_users``: provisioning the same customer twice is a mistake
    worth hearing about, not a state to converge on.

    Returns ``(organization_id, temporary_password)``. The password is returned
    here and never again — only its hash is stored, and the account is flagged
    to change it at first sign-in.
    """
    name = name.strip()
    if not name:
        raise ValueError("Organization name is required")
    owner_email = owner_email.strip().lower()
    if "@" not in owner_email:
        raise ValueError(f"{owner_email!r} does not look like an email address")
    if session.scalar(select(models.User).where(models.User.email == owner_email)):
        raise ValueError(f"An account already exists with email {owner_email!r}")
    if org_id is not None and session.get(models.Organization, org_id) is not None:
        raise ValueError(f"Organization {org_id!r} already exists")

    issued = password or generate_password()
    problem = password_problem(issued)
    if problem:
        raise ValueError(problem)

    org_id = org_id or _org_id_from_name(session, name)
    session.add(models.Organization(
        organization_id=org_id, name=name, erp="zoho",
        currency=currency.strip().upper() or "INR", config={}))
    session.add(models.User(
        organization_id=org_id, email=owner_email, name=owner_name.strip(),
        role=Role.OWNER.value, active=True,
        password_hash=hash_password(issued), must_change_password=True))
    session.flush()
    return org_id, issued


def main() -> None:
    """CLI: seed the default org + demo users, set a password, or provision.

    ``python -m app.seed`` — seed (idempotent), run after ``alembic upgrade head``
    ``python -m app.seed --set-password someone@example.com`` — issue a new one
    ``python -m app.seed --provision "Acme Distributors" --owner-email o@acme.in
    --owner-name "A. Owner"`` — create a new tenant with its first owner
    """
    import argparse

    from .db import SessionLocal

    parser = argparse.ArgumentParser(description="Seed, recover, or provision.")
    parser.add_argument("--set-password", metavar="EMAIL",
                        help="Issue a new temporary password for this account")
    parser.add_argument("--password", help="Use this instead of a generated one")
    parser.add_argument("--provision", metavar="ORG_NAME",
                        help="Create a new organization with its first owner")
    parser.add_argument("--owner-email", help="Owner's email (with --provision)")
    parser.add_argument("--owner-name", help="Owner's name (with --provision)")
    parser.add_argument("--currency", default="INR",
                        help="Organization currency (default INR)")
    parser.add_argument("--org-id", help="Explicit organization id (optional)")
    args = parser.parse_args()

    session = SessionLocal()
    try:
        if args.set_password:
            issued = set_password(session, args.set_password, args.password)
            session.commit()
            print(f"Temporary password for {args.set_password}: {issued}")
            print("Shown once. They are prompted to change it at next sign-in.")
            return
        if args.provision:
            if not args.owner_email or not args.owner_name:
                parser.error("--provision requires --owner-email and --owner-name")
            org_id, issued = provision_organization(
                session, name=args.provision, owner_email=args.owner_email,
                owner_name=args.owner_name, currency=args.currency,
                org_id=args.org_id, password=args.password)
            session.commit()
            print(f"Provisioned organization {org_id}.")
            print(f"Owner sign-in: {args.owner_email} / {issued}")
            print("Password shown once; they must change it at first sign-in.")
            print("Next: sign in and add the Zoho connection under Settings.")
            return
        org_id = ensure_org_and_users(session)
        session.commit()
        print(f"Seeded organization {org_id} with {len(DEMO_USERS)} demo users.")
        print("Sign in with the seed password, then change it immediately.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
