"""Bootstrap the single V1 organization and its demo users.

Users are the platform's own (role is a property of User, §2); they are seeded
rather than pulled from Zoho. Idempotent — safe to call on every startup.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .config import settings
from .domain import models
from .domain.enums import Role

# Demo users for the three roles (any password in the dev login).
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
        if session.get(models.User, u["user_id"]) is None:
            session.add(models.User(
                user_id=u["user_id"], organization_id=settings.DEFAULT_ORG_ID,
                email=u["email"], name=u["name"], role=u["role"].value, active=True))
    session.flush()
    return settings.DEFAULT_ORG_ID


def main() -> None:
    """CLI: seed the default org + demo users into the configured database.

    Run after ``alembic upgrade head``. Idempotent.
    """
    from .db import SessionLocal

    session = SessionLocal()
    try:
        org_id = ensure_org_and_users(session)
        session.commit()
        print(f"Seeded organization {org_id} with {len(DEMO_USERS)} demo users.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
