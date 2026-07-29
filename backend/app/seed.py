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
                must_change_password=True))
        elif not existing.password_hash:
            # An account seeded before passwords existed would otherwise be
            # locked out entirely by the new login check.
            existing.password_hash = hash_password(SEED_PASSWORD)
            existing.must_change_password = True
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


def main() -> None:
    """CLI: seed the default org + demo users, or set one user's password.

    ``python -m app.seed`` — seed (idempotent), run after ``alembic upgrade head``
    ``python -m app.seed --set-password someone@example.com`` — issue a new one
    """
    import argparse

    from .db import SessionLocal

    parser = argparse.ArgumentParser(description="Seed, or recover an account.")
    parser.add_argument("--set-password", metavar="EMAIL",
                        help="Issue a new temporary password for this account")
    parser.add_argument("--password", help="Use this instead of a generated one")
    args = parser.parse_args()

    session = SessionLocal()
    try:
        if args.set_password:
            issued = set_password(session, args.set_password, args.password)
            session.commit()
            print(f"Temporary password for {args.set_password}: {issued}")
            print("Shown once. They are prompted to change it at next sign-in.")
            return
        org_id = ensure_org_and_users(session)
        session.commit()
        print(f"Seeded organization {org_id} with {len(DEMO_USERS)} demo users.")
        print("Sign in with the seed password, then change it immediately.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
