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

from . import entitlements, memberships, tenancy
from .config import settings
from .domain import models
from .domain.enums import Role
from .passwords import generate_password, hash_password, password_problem

# Overridable so a real deployment never ships with the published default.
SEED_PASSWORD = os.environ.get("SEED_PASSWORD", "change-me-now")

# Demo users for the three roles.
DEMO_USERS = [
    {"user_id": "usr_owner", "email": "s.menon@pie.example", "name": "S. Menon",
     "role": Role.OWNER},
    {"user_id": "usr_manager", "email": "m.rao@pie.example", "name": "M. Rao",
     "role": Role.SALES_MANAGER},
    {"user_id": "usr_sales", "email": "r.nair@pie.example", "name": "R. Nair",
     "role": Role.SALESPERSON},
]


def ensure_org_and_users(session: Session) -> str:
    """Create the default org + demo users if absent. Returns the org id."""
    org = session.get(models.Organization, settings.DEFAULT_ORG_ID)
    if org is None:
        org = models.Organization(
            organization_id=settings.DEFAULT_ORG_ID, name=settings.DEFAULT_ORG_NAME,
            currency=settings.DEFAULT_CURRENCY, country="IN", config={})
        session.add(org)
    elif org.country is None:
        # The demo org is the Indian demo distributor, so its country is demo
        # data like its users are. Filled in here rather than in the migration
        # because only this org is *known* to be Indian — a real tenant's NULL
        # stays NULL, and the statutory screens refuse until somebody records
        # the fact rather than inheriting a guess.
        org.country = "IN"
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
    # The grant, without which none of the three could resolve a principal at
    # all: `authz.load_principal` reads a membership, not `users.role`.
    # `ensure_member` rather than `add_member` because this whole function is
    # re-run on every boot, and it deliberately does not correct a role
    # somebody has changed since.
    for u in DEMO_USERS:
        memberships.ensure_member(session, organization_id=settings.DEFAULT_ORG_ID,
                                  user_id=u["user_id"], role=u["role"])
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
    while _org_exists(session, candidate):
        candidate = f"org_{slug}_{n}"
        n += 1
    return candidate


def _org_exists(session: Session, organization_id: str) -> bool:
    """Does this id exist — for *any* tenant, not just the announced one.

    Under row-level security the plain ``session.get`` below sees only the
    caller's own organization, and a caller provisioning a new one has none. So
    the collision walk above would stop at the first candidate and the insert
    would fail on the primary key. ``tenancy.org_id_taken`` answers across
    tenants where there are policies and ``None`` where there are not, leaving
    the ordinary lookup as the authority on SQLite.
    """
    across = tenancy.org_id_taken(session, organization_id)
    if across is not None:
        return across
    return session.get(models.Organization, organization_id) is not None


def provision_organization(session: Session, *, name: str, owner_email: str,
                           owner_name: str, currency: str = "INR",
                           org_id: Optional[str] = None,
                           password: Optional[str] = None,
                           must_change_password: bool = True,
                           plan: Optional[str] = None) -> tuple[str, str]:
    """Create a new tenant: an Organization and its first owner account.

    The Tier-1 onboarding path — everything after this is self-serve through
    the existing screens: the owner signs in, is forced to change the password,
    adds the Zoho connection (``POST /connections``), and creates their own
    team (``POST /admin/users``). Deliberately **not** idempotent, unlike
    ``ensure_org_and_users``: provisioning the same customer twice is a mistake
    worth hearing about, not a state to converge on.

    Returns ``(organization_id, password)``. It is returned here and never
    again — only its hash is stored.

    Two parameters exist for the self-serve caller (``app/onboarding.py``) and
    default to the operator-provisioning behaviour that was here before them:

    ``must_change_password`` — true for a password *this* function invented and
    an operator will read out, false for one the account holder just chose. The
    forced change exists because an issued credential has been through a third
    party; a self-chosen one has not, and forcing a change to a second password
    thirty seconds after the first teaches people to pick worse ones. Note this
    is the same distinction ``settings.ISSUED_ACCOUNTS_MUST_CHANGE_PASSWORD``
    draws, not a way around it — that flag covers issued passwords, and this is
    the parameter that says whether this one was issued.

    ``plan`` — the licence to stamp on the row. ``None`` leaves it NULL, which
    resolves to ``settings.DEFAULT_PLAN``; a caller who must not inherit that
    (sign-up: ``DEFAULT_PLAN`` defaults to *platform*) names one explicitly.
    """
    name = name.strip()
    if not name:
        raise ValueError("Organization name is required")
    owner_email = owner_email.strip().lower()
    if "@" not in owner_email:
        raise ValueError(f"{owner_email!r} does not look like an email address")
    # Both refusals have to see across tenants: a caller provisioning a new
    # organization has none announced, and under a policy the ordinary queries
    # would answer "free" for an address and an id that are already taken.
    taken = tenancy.email_registered(session, owner_email)
    if taken is None:
        taken = session.scalar(
            select(models.User).where(
                models.User.email == owner_email)) is not None
    if taken:
        raise ValueError(f"An account already exists with email {owner_email!r}")
    if org_id is not None and _org_exists(session, org_id):
        raise ValueError(f"Organization {org_id!r} already exists")

    issued = password or generate_password()
    problem = password_problem(issued)
    if problem:
        raise ValueError(problem)

    org_id = org_id or _org_id_from_name(session, name)
    # Announced before the first write, not after: both rows below carry this
    # organization and a policy's WITH CHECK refuses a row whose tenant is not
    # the announced one. A no-op where there are no policies, and harmless on
    # the privileged connection the CLI callers use — it sets a setting nothing
    # there consults.
    tenancy.set_tenant(session, org_id)
    session.add(models.Organization(
        organization_id=org_id, name=name,
        currency=currency.strip().upper() or "INR", plan=plan, config={}))
    owner = models.User(
        organization_id=org_id, email=owner_email, name=owner_name.strip(),
        role=Role.OWNER.value, active=True,
        password_hash=hash_password(issued),
        must_change_password=must_change_password)
    session.add(owner)
    session.flush()

    # The founding membership. Nobody invited them, so `invited_by_user_id`
    # stays null — recording them as their own inviter would be a tidier lie
    # than a null. Nothing else about the organization refers to this user:
    # they can be removed later and Acme carries on, which is the property the
    # membership table exists for.
    memberships.add_member(session, organization_id=org_id,
                           user_id=owner.user_id, role=Role.OWNER)

    # **The organization gets its trial because it exists, not because somebody
    # connected books to it.** Here rather than at first connection so that the
    # days a buyer spends deciding are days they can use the product, and here
    # rather than anywhere near the user rows so that a second person joining
    # reads this one instead of starting another.
    entitlements.start_trial(session, org_id)
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
