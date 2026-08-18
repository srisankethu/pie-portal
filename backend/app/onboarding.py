"""Self-serve onboarding: a company signs itself up, and is told what is left.

Until now a tenant could only be created by somebody with a shell on the box —
``python -m app.seed --provision`` or ``python -m app.provision_org``. That is
the correct arrangement for a deployment installed for one distributor, and it
is the reason the landing page's "Get started free" button led to a sign-in form
for an account nobody could have. This module is the other path: the owner of a
business creates their own organization, chooses their own password, and is
walked to the point where the platform has their books.

Two halves, because they answer different questions and only one of them writes:

``sign_up`` — create the tenant. Thin on purpose: it delegates the rows to
``seed.provision_organization`` (the existing tenant-creation function, which
already generates an unused org id from the company name, refuses a duplicate
email across tenants, and is deliberately not idempotent) and owns only what is
*different* about a self-serve signup — the plan it lands on and the fact that
the password was chosen rather than issued.

``checklist`` — what this organization still has to do. **Derived from rows on
every request, never a stored flag.** A "setup complete" column would be one
more thing that can disagree with reality: an owner who deletes their only
connection has not finished setting up, whatever a column says, and a step that
reports done because a flag was written is exactly the "absence of evidence is
not a pass" defect in a friendlier costume. So each step is a query, and a step
whose evidence is missing says what is missing rather than passing quietly.

What this deliberately does **not** do is grant anything. A self-serve tenant
lands on the free plan and gets its free month of Commercial Intelligence the
same way every other tenant does — from ``entitlements.begin_trial``, keyed to
the *connected books* rather than to the platform organization, when a
connection is added. Signing up twice with two email addresses therefore buys
nothing, which is the property that made the trial worth keying that way.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .domain import models
from .domain.enums import PlanTier
from .ingestion import connections as conn
from .passwords import password_problem

log = logging.getLogger("pie_portal.onboarding")

#: The plan a self-serve tenant lands on. Named here rather than left to
#: ``settings.DEFAULT_PLAN``, which defaults to *platform* so that existing
#: single-tenant deployments keep every feature they have — correct for them,
#: and the reason inheriting it here would hand the top tier to anyone who can
#: reach the sign-up form. The upgrade path is the operator CLI, as it is for
#: every other organization: there is deliberately no API that sets a plan.
SIGNUP_PLAN = PlanTier.FREE


class SignupDisabled(RuntimeError):
    """This deployment does not accept self-serve sign-ups."""


class SignupRefused(ValueError):
    """The details supplied cannot create a tenant. The message is for a person."""


def enabled() -> bool:
    return bool(settings.SELF_SERVE_SIGNUP)


def _clean_email(raw: str) -> str:
    """Shape only — the same rule ``admin.CreateUser`` applies, for the reason it
    gives: full RFC validation needs a dependency and rejects addresses that
    work, and the real question is whether this person can receive mail at it,
    which no regex answers."""
    email = (raw or "").strip().lower()
    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain or " " in email:
        raise SignupRefused("That does not look like an email address")
    return email


def parse_requested_plan(raw: Optional[str]) -> Optional[PlanTier]:
    """The plan a sign-up says it wants, or None for "did not say".

    Refuses an unknown value rather than resolving it to free the way
    ``entitlements.parse_plan`` does, and the difference is deliberate: that
    function reads config and a stored row, where degrading quietly to the
    narrowest plan is the safe direction. This reads a form somebody just filled
    in, where a value that is not one of the three means the client and the
    server disagree about what the plans are — and silently recording the wrong
    answer to the only question this endpoint asks is worse than saying so.

    It grants nothing either way. See ``Organization.requested_plan``.
    """
    if raw is None or not raw.strip():
        return None
    try:
        return PlanTier(raw.strip().lower())
    except ValueError:
        raise SignupRefused(
            "That is not one of the plans. Choose "
            + ", ".join(p.value for p in PlanTier) + ".") from None


def sign_up(session: Session, *, company: str, owner_name: str,
            owner_email: str, password: str,
            currency: str = "INR",
            wants_plan: Optional[str] = None) -> tuple[str, models.User]:
    """Create a tenant for somebody who is signing themselves up.

    Returns ``(organization_id, owner)``. Raises ``SignupDisabled`` when the
    deployment does not offer this at all, and ``SignupRefused`` with a sentence
    a person can act on for anything wrong with the details.

    ``wants_plan`` is recorded and **not granted**. Every sign-up lands on
    ``SIGNUP_PLAN`` whatever it says, which is what keeps the sign-up form from
    being the plan-setting API that deliberately does not exist. The value is
    kept so an operator can find out who asked (``python -m app.entitlements
    requests``) instead of the question being asked and then thrown away — a
    form that discards its own answer is worse than one that never asked.

    The caller commits. Nothing here sends mail or verifies the address: the
    owner is signed straight in, which is what makes this self-serve, and the
    address is proved by the first password reset rather than by a click-through
    nobody reads. That is a stated trade-off, not an oversight — an unverified
    address costs a tenant with a typo in it, and a verification wall costs
    every sign-up.
    """
    if not enabled():
        raise SignupDisabled(
            "This deployment does not accept sign-ups. Ask whoever runs it for "
            "an account.")

    company = (company or "").strip()
    owner_name = (owner_name or "").strip()
    if not company:
        raise SignupRefused("Your company name is required")
    if not owner_name:
        raise SignupRefused("Your name is required")
    email = _clean_email(owner_email)
    # Before anything is written, with the other refusals: a plan the server does
    # not recognise must not cost a tenant that then has to be deleted.
    wanted = parse_requested_plan(wants_plan)

    # Checked here as well as inside `provision_organization`, because the
    # password is the one field whose refusal has to arrive before anything is
    # written — and because the message is the only guidance the form gives.
    problem = password_problem(password)
    if problem:
        raise SignupRefused(problem)

    if session.scalar(select(models.User).where(models.User.email == email)):
        # Named rather than generalised to "could not sign up". This is a B2B
        # product where the person filling the form is the account holder, so
        # "you already have one, sign in" is the useful answer; the address is
        # one they just typed, so nothing is disclosed to them that they did not
        # bring. `admin.create_user` answers the same way, and consistency here
        # is worth more than an enumeration defence the sign-in form does not
        # share anyway.
        raise SignupRefused(
            f"An account already exists for {email}. Sign in instead, or use "
            "another address.")

    from .seed import provision_organization

    try:
        org_id, _ = provision_organization(
            session, name=company, owner_email=email, owner_name=owner_name,
            currency=currency, password=password,
            # Chosen, not issued — so no forced change on the way in. See the
            # parameter's docstring in `seed.py`.
            must_change_password=False,
            plan=SIGNUP_PLAN.value)
    except ValueError as e:
        # `provision_organization` raises ValueError for every refusal it owns
        # (blank name, duplicate email, a password that does not pass). They are
        # already sentences; re-typing them keeps one refusal class at the seam.
        raise SignupRefused(str(e)) from e

    owner = session.scalar(select(models.User).where(models.User.email == email))
    if owner is None:  # pragma: no cover — provision_organization just wrote it
        raise SignupRefused("The account could not be created")
    if wanted is not None:
        # A different column from `plan`, written here and read by no resolution
        # path. If these two were ever the same column this function would be
        # the API that lets a stranger pick their own tier.
        session.get(models.Organization, org_id).requested_plan = wanted.value
        session.flush()
    log.info("self-serve signup org=%s plan=%s wants=%s", org_id,
             SIGNUP_PLAN.value, wanted.value if wanted else "-")
    return org_id, owner


# ── what is left to do ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class Step:
    """One thing a new organization has or has not done.

    ``done`` is three-valued through its companion ``blocked``: a step is done,
    or it is not, and ``detail`` says which of those on the evidence actually
    present. Nothing here reports a step complete because it could not find a
    reason to object.
    """

    key: str
    title: str
    #: What is true right now — the sentence under the step. Always populated:
    #: "not done" without a reason is what sends somebody to the wrong screen.
    detail: str
    done: bool
    #: Whether the platform is unusable without it. The two optional steps are
    #: genuinely optional — a one-person distributor never adds a team, and the
    #: default margin policy is a working policy.
    required: bool
    #: Which client route completes it, as `platform/route.ts` names paths.
    route: str


def _connect_step(session: Session, org: str) -> Step:
    rows = conn.list_connections(session, org)
    enabled_rows = [r for r in rows if r.enabled]
    if not enabled_rows:
        detail = ("No Zoho Books company is connected yet. Everything this "
                  "platform reports is read from your books.")
        if rows:
            detail = (f"{len(rows)} connected " + ("company is" if len(rows) == 1
                                                   else "companies are")
                      + " switched off, so nothing is read from them.")
        return Step(key="connect", title="Connect your Zoho Books company",
                    detail=detail, done=False, required=True, route="/data")

    # A connection exists. Whether it *works* is a separate fact, and one that
    # is genuinely unknown until something has asked Zoho — so an unchecked
    # connection says so rather than counting as a working one.
    unchecked = [r for r in enabled_rows if r.last_checked_at is None]
    failing = [r for r in enabled_rows if r.last_checked_at is not None
               and not r.last_check_ok]
    if unchecked:
        detail = (f"Connected, but {len(unchecked)} of {len(enabled_rows)} has "
                  "not been checked against Zoho yet — press Check on the "
                  "Data & connection screen.")
    elif failing:
        detail = (failing[0].last_check_detail
                  or "The last check against Zoho did not succeed.")
    else:
        detail = (f"{len(enabled_rows)} compan"
                  + ("y is" if len(enabled_rows) == 1 else "ies are")
                  + " connected and reachable.")
    # Deliberately done once a connection exists and is enabled, even when the
    # check failed: the step is "connect", the detail carries the health, and a
    # step that un-completes itself on a transient Zoho blip is a checklist
    # people learn to ignore. `SyncStatus` is where a broken connection is
    # chased.
    return Step(key="connect", title="Connect your Zoho Books company",
                detail=detail, done=True, required=True, route="/data")


def _history_step(session: Session, org: str) -> Step:
    """Whether any pull has actually landed rows.

    ``OK`` and ``PARTIAL`` both count — a partial run is one that wrote what it
    reached, which is what makes the next attempt cheap and is enough to analyse.
    A run that is still going does not count and says so, because a checklist
    that ticks on QUEUED is describing an intention.
    """
    latest = session.scalars(
        select(models.SyncRun)
        .where(models.SyncRun.organization_id == org)
        .order_by(models.SyncRun.started_at.desc())
        .limit(1)).first()

    def step(detail: str, done: bool) -> Step:
        return Step(key="history", title="Pull your trading history",
                    detail=detail, done=done, required=True, route="/data")

    if latest is None:
        return step("Nothing has been pulled yet. The first pull reads about "
                    "18 months, which is what the analysis compares against.",
                    False)
    if latest.status in ("QUEUED", "RUNNING"):
        return step("A pull is running. This screen will fill in when it lands.",
                    False)
    if latest.status in ("OK", "PARTIAL"):
        rows = (latest.sales_txns or 0) + (latest.cost_records or 0)
        if rows == 0:
            # Finished and brought nothing: not a pass. Almost always a scope
            # the grant never had, or a window with no documents in it.
            return step("The last pull finished without reading any documents — "
                        "check the connection's permissions and the date it "
                        "started from.", False)
        return step(f"{rows:,} records read. Re-pull any time from "
                    "Data & connection.", True)
    return step(latest.error or "The last pull failed.", False)


def _policy_step(session: Session, org: str) -> Step:
    """Whether an owner has decided their own margin policy.

    Read from the override row rather than from the thresholds in force: every
    organization *has* a working policy (the environment defaults), so asking
    "is there a policy" would answer yes on day one and the step would be
    meaningless. The question worth asking is whether anybody has said what
    this business's floors actually are.
    """
    row = session.get(models.CommercialPolicy, org)
    n = len(row.overrides or {}) if row is not None else 0
    return Step(
        key="policy", title="Set your margin floors",
        detail=(f"{n} threshold{'' if n == 1 else 's'} set for this business."
                if n else
                "Running on the default thresholds. They work, but the floors "
                "that gate every quote should be yours."),
        done=n > 0, required=False, route="/settings")


def _team_step(session: Session, org: str) -> Step:
    others = session.scalars(
        select(models.User).where(models.User.organization_id == org,
                                  models.User.active.is_(True))).all()
    n = max(len(others) - 1, 0)
    return Step(
        key="team", title="Add your team",
        detail=(f"{n} other account{'' if n == 1 else 's'} — each one sees what "
                "its role allows."
                if n else
                "Only your account so far. A salesperson never sees cost or "
                "margin, so quoting can be handed over safely."),
        done=n > 0, required=False, route="/settings")


def checklist(session: Session, organization_id: str) -> dict:
    """Every setup step for this organization, and whether it is done.

    Ordered as they must actually happen: nothing can be pulled before a
    company is connected, and the two optional steps are worth nothing before
    there is data to apply them to.
    """
    steps = [
        _connect_step(session, organization_id),
        _history_step(session, organization_id),
        _policy_step(session, organization_id),
        _team_step(session, organization_id),
    ]
    required = [s for s in steps if s.required]
    return {
        "steps": [asdict(s) for s in steps],
        # What the screen keys off: the panel is for a tenant that has not
        # finished, and must disappear rather than become a permanent banner.
        "complete": all(s.done for s in required),
        "remaining": sum(1 for s in steps if not s.done),
        "remaining_required": sum(1 for s in required if not s.done),
    }
