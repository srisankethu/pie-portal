"""What an organization's plan lets it use — and the one place that decides.

``authz.py`` answers "who is this person and what may their *role* see";
this module answers the orthogonal question "what may this *organization*
use". The two compose: a route can require an owner *and* the intelligence
plan, and each check names its own refusal.

The pricing model this enforces, mechanically rather than contractually:

- **free** (Quote Desk): quoting, margin floors, approvals — and one
  connected company.
- **intelligence**: adds the decision layer — signals, decision cards, the
  insight screens. Still one connected company.
- **platform**: adds multi-company groups (several connections, one view).

Two rules with teeth, both from the same principle — *price the unit you can
enforce, never the one you would have to audit*:

- The free month of Commercial Intelligence belongs to the **connected books**
  (``IntelligenceTrial``, unique on ``zoho_organization_id``), not to the
  platform organization, which costs nothing to recreate.
- A second connected company is refused below the platform plan at the moment
  of connection, not discovered in an audit.

Resolution never widens on error: an unrecognised plan value — on the row or
in ``DEFAULT_PLAN`` — resolves to **free** and logs. A typo must degrade, not
grant (the same posture as a schema that is behind: refuse honestly rather
than succeed silently).

Plans are set by the *operator* (the CLI at the bottom: ``python -m
app.entitlements set-plan <org> <plan>``), never by a tenant through the API —
an owner who could set their own plan would not have one. Billing, when it
exists, will call ``set_plan`` the same way.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .authz import Principal, current_principal
from .config import settings
from .db import get_session
from .domain import models
from .domain.enums import PlanTier

log = logging.getLogger("pie_portal.entitlements")

#: Feature -> the plans that include it. The whole map, deliberately small:
#: a feature here is a *surface* the API refuses, not a marketing bullet.
FEATURES: dict[str, frozenset[PlanTier]] = {
    "intelligence": frozenset({PlanTier.INTELLIGENCE, PlanTier.PLATFORM}),
    "multi_company": frozenset({PlanTier.PLATFORM}),
}

#: What to tell a person, per plan, when naming what they are on.
PLAN_LABEL: dict[PlanTier, str] = {
    PlanTier.FREE: "Quote Desk (free)",
    PlanTier.INTELLIGENCE: "Commercial Intelligence",
    PlanTier.PLATFORM: "Platform",
}

#: What each plan *adds*, in one sentence, for a screen that has to show the
#: ladder. Here rather than in the browser for the reason ``loses_on_expiry``
#: gives below: a client-side copy of the plan map is a second copy, and the two
#: disagree the first time a feature moves between tiers. Prices are deliberately
#: **not** here — they are marketing copy and live in one place, the landing
#: page's pricing section. A second copy of a price is worse than a second copy
#: of a feature list.
PLAN_SUMMARY: dict[PlanTier, str] = {
    PlanTier.FREE: ("Quoting, RFQ reading, margin floors and approvals. "
                    "One connected company."),
    PlanTier.INTELLIGENCE: ("Adds the decision layer — the attention list, "
                            "customer health, the insight screens."),
    PlanTier.PLATFORM: ("Adds several connected companies under one view."),
}

#: Which plan a feature needs, for refusal messages — the *cheapest* that has it.
_NEEDS: dict[str, PlanTier] = {
    "intelligence": PlanTier.INTELLIGENCE,
    "multi_company": PlanTier.PLATFORM,
}


class PlanRefused(PermissionError):
    """The organization's plan does not include this feature.

    Typed so a router can map it to 403 with the message intact; the message
    always names what is needed and what the organization is on, because a
    refusal a person cannot act on is a bug report waiting to be filed.
    """

    def __init__(self, feature: str, plan: PlanTier) -> None:
        self.feature = feature
        self.plan = plan
        self.needs = _NEEDS[feature]
        super().__init__(
            f"This needs the {PLAN_LABEL[self.needs]} plan — this organization "
            f"is on {PLAN_LABEL[plan]}.")


def parse_plan(raw: Optional[str], *, source: str) -> Optional[PlanTier]:
    """A PlanTier from a stored/configured string, or None for empty.

    Unknown values are **free**, loudly — never the widest plan and never an
    exception: a typo in an env var must not take the API down, and it must
    not hand a tenant the platform plan either.
    """
    if raw is None or not raw.strip():
        return None
    try:
        return PlanTier(raw.strip().lower())
    except ValueError:
        log.warning("Unrecognised plan %r in %s — treating as 'free'. "
                    "Expected one of: %s", raw, source,
                    ", ".join(p.value for p in PlanTier))
        return PlanTier.FREE


def licensed_plan(org: Optional[models.Organization]) -> PlanTier:
    """The plan this organization is licensed on, before any trial.

    The row's own value wins; NULL falls back to ``DEFAULT_PLAN`` so existing
    deployments keep their behaviour without a backfill.
    """
    stored = parse_plan(org.plan if org is not None else None, source="organizations.plan")
    if stored is not None:
        return stored
    return parse_plan(settings.DEFAULT_PLAN, source="DEFAULT_PLAN") or PlanTier.FREE


def requested_plan(org: Optional[models.Organization]) -> Optional[PlanTier]:
    """The plan this organization asked for at sign-up, or None.

    Deliberately not consulted by ``licensed_plan``, ``effective_plan`` or
    anything else that decides what may be used. It is a question somebody
    answered on a form, and a form is not an entitlement — the whole reason
    there is no API that sets a plan is that a tenant must not be able to grant
    itself one, and reading this column anywhere in the resolution path would be
    exactly that API with an extra step.
    """
    return parse_plan(org.requested_plan if org is not None else None,
                      source="organizations.requested_plan")


def ladder() -> list[dict]:
    """Every plan, cheapest first, with what it adds. One source, no prices."""
    return [{"plan": p.value, "label": PLAN_LABEL[p], "summary": PLAN_SUMMARY[p]}
            for p in PlanTier]


#: Declaration order is the ladder, cheapest first. Used only to answer "is this
#: request for *more* than they have" — never to decide what a plan allows, which
#: is ``FEATURES`` and stays a set membership rather than a threshold.
_RANK: dict[PlanTier, int] = {p: i for i, p in enumerate(PlanTier)}


def wants_more(org: Optional[models.Organization]) -> Optional[PlanTier]:
    """The plan asked for, when it is above the one licensed. Otherwise None.

    A request for the plan they are already on, or for a cheaper one, is not
    something to show anybody: the first reads as a request that was ignored and
    the second is somebody changing their mind downward, which the operator CLI
    is the place for.
    """
    asked = requested_plan(org)
    if asked is None:
        return None
    return asked if _RANK[asked] > _RANK[licensed_plan(org)] else None


def trial_for(session: Session, organization_id: str) -> Optional[models.IntelligenceTrial]:
    """This organization's *running* trial, or None. Expired rows stay silent."""
    now = clock.now()
    for row in session.scalars(select(models.IntelligenceTrial).where(
            models.IntelligenceTrial.organization_id == organization_id)):
        ends = clock.aware(row.ends_at)
        if ends is not None and ends > now:
            return row
    return None


def effective_plan(session: Session, organization_id: str) -> PlanTier:
    """What this organization may use right now: its licence, or its trial.

    A running trial lifts a free organization to INTELLIGENCE — never to
    PLATFORM: the trial exists to demonstrate the decision layer, not to
    waive the multi-company boundary.
    """
    plan = licensed_plan(session.get(models.Organization, organization_id))
    if plan is PlanTier.FREE and trial_for(session, organization_id) is not None:
        return PlanTier.INTELLIGENCE
    return plan


def allows(plan: PlanTier, feature: str) -> bool:
    return plan in FEATURES[feature]


def assert_feature(session: Session, organization_id: str, feature: str) -> None:
    """Raise PlanRefused unless this organization's effective plan has the feature."""
    plan = effective_plan(session, organization_id)
    if not allows(plan, feature):
        raise PlanRefused(feature, plan)


# ── the free month, keyed to the books ───────────────────────────────────────
def begin_trial(session: Session, organization_id: str,
                zoho_organization_id: str) -> Optional[models.IntelligenceTrial]:
    """Start the free intelligence month for these books, if they never had one.

    Returns the new trial, or None when these books already used theirs —
    including under a *different* platform organization, which is the entire
    point of keying on the books. Never raises: a connection must not fail
    because its trial cannot start.
    """
    zoho_organization_id = zoho_organization_id.strip()
    if not zoho_organization_id:
        return None
    existing = session.scalar(select(models.IntelligenceTrial).where(
        models.IntelligenceTrial.zoho_organization_id == zoho_organization_id))
    if existing is not None:
        return None
    now = clock.now()
    row = models.IntelligenceTrial(
        organization_id=organization_id,
        zoho_organization_id=zoho_organization_id,
        started_at=now,
        ends_at=now + timedelta(days=settings.INTELLIGENCE_TRIAL_DAYS))
    session.add(row)
    session.flush()
    log.info("intelligence trial started org=%s books=%s ends=%s",
             organization_id, zoho_organization_id, row.ends_at.date())
    return row


# ── HTTP surface ─────────────────────────────────────────────────────────────
def require_feature(feature: str):
    """A FastAPI dependency: 403 with a plan-shaped message unless entitled.

    Applied at ``include_router`` time in ``main.py`` for whole surfaces
    (decisions, insight), so the plan boundary is declared in one visible
    place rather than sprinkled per-route.
    """
    if feature not in FEATURES:
        raise ValueError(f"Unknown feature {feature!r}")

    def _check(principal: Principal = Depends(current_principal),
               session: Session = Depends(get_session)) -> Principal:
        try:
            assert_feature(session, principal.organization_id, feature)
        except PlanRefused as e:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
        return principal

    return _check


def _trial_view(org: Optional[models.Organization],
                trial: Optional[models.IntelligenceTrial]) -> Optional[dict]:
    """A running trial, with how long is left, or None.

    ``days_remaining`` is counted **here** rather than in the browser, and that
    is not fussiness. It is the number of times the business's own date has to
    turn over before the decision layer switches off, and a browser computing it
    would use the reader's zone — an owner travelling, or a server in UTC, would
    see a day more or less than the tenant actually has. ``clock`` exists for
    exactly this, and says so: "``date.today()`` is the server's idea of the day
    and is UTC in every container this runs in".

    Zero means it ends today and the tenant still has it; ``trial_for`` only
    returns trials that have not expired, so this is never negative.
    """
    if trial is None:
        return None
    tz = getattr(org, "timezone", None)
    ends_local = clock.to_local(trial.ends_at, tz)
    days = (ends_local.date() - clock.today(tz)).days if ends_local else 0
    return {
        "ends_at": clock.iso(trial.ends_at),
        # The date a person would write down, in their own zone.
        "ends_on": ends_local.date().isoformat() if ends_local else None,
        "days_remaining": max(days, 0),
    }


def describe(session: Session, organization_id: str) -> dict:
    """Everything a screen needs to say what this organization may use."""
    org = session.get(models.Organization, organization_id)
    licensed = licensed_plan(org)
    trial = trial_for(session, organization_id)
    effective = effective_plan(session, organization_id)
    # A plan *request* (``wants_more``) is deliberately not in this payload. It
    # has one consumer — the operator who can act on it, through the CLI — and
    # no screen shows it, because the screen that would is the plan-status panel
    # this product does not have yet. A field on the wire that nothing reads is
    # the defect ``GET /api/v1/entitlements`` itself was for two releases; it
    # belongs here on the day something renders it.
    return {
        "plan": licensed.value,
        "plan_label": PLAN_LABEL[licensed],
        "effective_plan": effective.value,
        "effective_label": PLAN_LABEL[effective],
        "trial": _trial_view(org, trial),
        "features": {name: allows(effective, name) for name in FEATURES},
        # What actually goes away when the trial does. Named rather than left to
        # the client to hardcode: the client would then hold a second copy of
        # the plan map, and the two would disagree the first time a feature moved
        # between tiers.
        "loses_on_expiry": (
            sorted(name for name in FEATURES
                   if allows(effective, name) and not allows(licensed, name))
            if trial is not None else []),
    }


# ── operator CLI ─────────────────────────────────────────────────────────────
def set_plan(session: Session, organization_id: str, plan: PlanTier) -> models.Organization:
    org = session.get(models.Organization, organization_id)
    if org is None:
        raise LookupError(f"No such organization {organization_id!r}")
    org.plan = plan.value
    session.flush()
    log.info("plan set org=%s plan=%s", organization_id, plan.value)
    return org


def _main() -> int:
    import argparse

    from .db import SessionLocal

    parser = argparse.ArgumentParser(
        prog="python -m app.entitlements",
        description="Operator plan management (there is deliberately no API for this).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_set = sub.add_parser("set-plan", help="Put an organization on a plan")
    p_set.add_argument("organization_id")
    p_set.add_argument("plan", choices=[p.value for p in PlanTier])
    p_show = sub.add_parser("show", help="Show an organization's entitlements")
    p_show.add_argument("organization_id")
    sub.add_parser(
        "requests",
        help="Organizations asking for more than they are licensed on")
    args = parser.parse_args()

    with SessionLocal() as session:
        # The sign-up form asks which plan a business wants, and the answer is
        # only worth recording if somebody can find it again. This is that: the
        # queue between "a stranger asked for Commercial Intelligence" and
        # `set-plan`, which is still the only thing that grants it.
        if args.cmd == "requests":
            asked = [(org, want) for org in session.scalars(
                        select(models.Organization).order_by(
                            models.Organization.created_at))
                     if (want := wants_more(org)) is not None]
            if not asked:
                print("No organization is asking for more than it has.")
                return 0
            for org, want in asked:
                print(f"{org.organization_id}\t{org.name}\t"
                      f"on {licensed_plan(org).value}\twants {want.value}")
            return 0
        if args.cmd == "set-plan":
            set_plan(session, args.organization_id, PlanTier(args.plan))
            session.commit()
        print(describe(session, args.organization_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
