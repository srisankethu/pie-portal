"""What an organization may use — and the one place that decides.

``authz.py`` answers "who is this person"; ``memberships.py`` answers "which
organizations may they open, and as what"; this module answers the third,
orthogonal question: **what is this organization commercially entitled to**.
The three compose, and each check names its own refusal.

The pricing model this enforces, mechanically rather than contractually:

- A new organization gets a **30-day trial of Commercial Intelligence**, in
  full, starting the moment the organization exists.
- When it ends, the decision layer locks. Nothing is deleted, nothing is
  anonymised and no screen the business had its working week in goes away —
  the quote desk, margin floors and approvals keep working, because ending a
  subscription is not the same act as deleting a customer.
- From there the organization subscribes, and the entitlement comes back.

**There is no always-free plan.** There used to be: FREE was marketed as "the
Quote Desk, free forever", something a business could choose and stay on, and
the trial was a month of *extra* on top of it. That is gone. FREE is now the
floor an organization sits on when it is paying for nothing — before a trial
and after one — and the ladder no longer offers it as a destination.

**The trial belongs to the organization, not to a person and not to a set of
books.** That is the change with the most consequences and each is deliberate:

- It starts at sign-up rather than at first Zoho connection, so the days a
  buyer spends deciding are days they can actually use the product.
- A second person joining reads the organization's row. They do not get one of
  their own, and nothing about the organization's dates moves.
- The founder leaving does not touch it. ``organization_subscriptions`` has no
  user column at all, which is the strongest form of that guarantee.

What survives from the old shape is the *duplicate-trial* check, and only that.
``IntelligenceTrial`` still records that one set of books has claimed a trial;
``claim_books`` writes the claim at first connection and, finding one already
held by another organization, ends the new organization's trial and says so.
One boundary, enforced at one moment, and deliberately not a fingerprinting
scheme: an account can be recreated, and the answer to that is that it buys
nothing once its books are connected — not a device graph.

**Expiry is derived, never swept.** ``resolve`` compares ``trial_ends_at``
against the clock on every read. A nightly job that flipped rows to EXPIRED
would mean a missed run reads exactly like a healthy one, which is the
"absence of evidence is not a pass" failure CLAUDE.md §1 names.

Resolution never widens on error: an unrecognised plan value — on the row or in
``DEFAULT_PLAN`` — resolves to **free** and logs. A typo must degrade, not
grant.

Plans are set by the *operator* (the CLI at the bottom: ``python -m
app.entitlements set-plan <org> <plan>``), never by a tenant through the API —
an owner who could set their own plan would not have one.

What an owner *can* do is ask. ``request_plan_change`` records a
``PlanChangeRequest`` and grants nothing; ``decide_request`` is the only path
that both reads one and moves a plan, and it goes through ``set_plan`` rather
than writing the columns itself, so there stays exactly one function that
changes what an organization may use.

Billing, when it exists, calls ``decide_request`` — it is the same shape a
payment confirmation has, and the queue is what it will drain.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .authz import Principal, current_principal
from .config import settings
from .db import get_session
from .domain import models
from .domain.enums import PlanTier, SubscriptionStatus

log = logging.getLogger("pie_portal.entitlements")

#: Feature -> the plans that include it. The whole map, deliberately small:
#: a feature here is a *surface* the API refuses, not a marketing bullet.
FEATURES: dict[str, frozenset[PlanTier]] = {
    "intelligence": frozenset({PlanTier.INTELLIGENCE, PlanTier.PLATFORM}),
    "multi_company": frozenset({PlanTier.PLATFORM}),
}

#: What to tell a person, per plan, when naming what they are on. FREE is
#: named for what it *is* — the state of paying for nothing — rather than as a
#: product, because "Quote Desk (free)" in a refusal message read as though the
#: reader had chosen something, and nobody chooses this.
PLAN_LABEL: dict[PlanTier, str] = {
    PlanTier.FREE: "Quote Desk (no subscription)",
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
    PlanTier.FREE: ("Quoting, RFQ reading, margin floors and approvals — what "
                    "keeps working when nothing is being paid for. One "
                    "connected company."),
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
    """The plan on the **legacy** ``organizations.plan`` column, before any trial.

    Not the authority any more — `resolve` is, reading
    ``organization_subscriptions`` — and there are exactly two callers left,
    both of which want this column specifically. `resolve` itself falls back
    here for an organization that has no subscription row (one provisioned
    before they existed, or a fixture that only needed an id), and `wants_more`
    compares the sign-up form's answer against it.

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


#: The plans an organization can actually move to. FREE is absent because it is
#: not a destination any more — it is where an organization lands when it stops
#: paying, and offering it as a choice is what "always free" was.
PURCHASABLE: tuple[PlanTier, ...] = (PlanTier.INTELLIGENCE, PlanTier.PLATFORM)


def ladder() -> list[dict]:
    """Every plan, cheapest first, with what it adds. One source, no prices.

    FREE stays *in* the list and is marked ``purchasable: False`` rather than
    filtered out of it. A screen has to be able to say what an organization on
    it currently has — the list is read by the plan panel as much as by the
    upgrade control — and a tier the reader is standing on that does not appear
    in the ladder reads as a fault in the page.
    """
    return [{"plan": p.value, "label": PLAN_LABEL[p], "summary": PLAN_SUMMARY[p],
             "purchasable": p in PURCHASABLE}
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


@dataclass(frozen=True)
class Entitlement:
    """What one organization may use right now, and why. The whole answer.

    A frozen record rather than four functions each re-deriving the same thing.
    ``effective_plan``, ``describe`` and ``assert_feature`` all call `resolve`
    and read this; that is what makes "a single authoritative entitlement
    mechanism" a property of the code rather than a claim about it.
    """

    organization_id: str
    #: What is being paid for. FREE means nothing is.
    licensed: PlanTier
    #: What may be used right now — the licence, or the trial lifting it.
    effective: PlanTier
    #: The commercial state, with expiry already applied.
    status: SubscriptionStatus
    trial_started_at: Optional[datetime] = None
    trial_ends_at: Optional[datetime] = None
    #: Why a trial ended before its date, when one did.
    trial_ended_reason: str = ""

    @property
    def on_trial(self) -> bool:
        return self.status is SubscriptionStatus.TRIALING

    @property
    def trial_expired(self) -> bool:
        """The trial ran and is over. Distinct from "never had one".

        The screen that says "your trial ended — subscribe to bring the
        decision layer back" must not say it to an organization that never
        started one, which is why this asks for the dates rather than for the
        status alone.
        """
        return (self.status is SubscriptionStatus.EXPIRED
                and self.trial_ends_at is not None)


def subscription_for(session: Session,
                     organization_id: str) -> Optional[models.OrganizationSubscription]:
    """This organization's subscription row, or None. Creates nothing.

    None is a real answer and not a hole: an organization provisioned before
    subscriptions existed and not yet migrated has one, and so does a tenant
    created by a fixture that only needed an id. `resolve` degrades to the
    ``organizations.plan`` column for those rather than inventing a trial for a
    row somebody made in a test three years ago.
    """
    return session.get(models.OrganizationSubscription, organization_id)


def resolve(session: Session, organization_id: str) -> Entitlement:
    """**The one function that decides what an organization may use.**

    Everything else in this module — the feature gate, the HTTP dependency, the
    screen payload, the CLI — reads this. A second path that derived a plan its
    own way is the semantic duplication CLAUDE.md §2 is about, and here it
    would be a duplication with money on it: two answers to "is this tenant
    entitled" eventually disagree, and the one that disagrees generously is the
    one nobody reports.

    Expiry is applied *here*, at read time, by comparing ``trial_ends_at``
    against the clock. There is no sweep to miss.
    """
    org = session.get(models.Organization, organization_id)
    sub = subscription_for(session, organization_id)

    if sub is None:
        # Pre-subscription rows: the plan column is all there is, and there is
        # no trial to speak of. Deliberately not "start one now" — a read must
        # not grant, and a lazily-created trial would hand a fresh 30 days to
        # every organization that had already used theirs.
        licensed = licensed_plan(org)
        return Entitlement(
            organization_id=organization_id, licensed=licensed,
            effective=licensed,
            status=(SubscriptionStatus.ACTIVE if licensed is not PlanTier.FREE
                    else SubscriptionStatus.EXPIRED))

    licensed = parse_plan(sub.plan, source="organization_subscriptions.plan")
    if licensed is None:
        # Nothing paid for. Not ``DEFAULT_PLAN``: that setting exists so a
        # single-tenant deployment can run every screen without an operator
        # command, and it is read where a *licence* is missing entirely
        # (`licensed_plan`), not where a subscription row says in as many words
        # that this organization is not paying for anything.
        licensed = PlanTier.FREE

    status = _stored_status(sub)
    ends = clock.aware(sub.trial_ends_at)
    if status is SubscriptionStatus.TRIALING and (
            ends is None or ends <= clock.now()):
        status = SubscriptionStatus.EXPIRED

    effective = licensed
    if status is SubscriptionStatus.TRIALING and licensed is PlanTier.FREE:
        # A running trial lifts an unpaid organization to INTELLIGENCE — never
        # to PLATFORM: the trial exists to demonstrate the decision layer, not
        # to waive the multi-company boundary.
        effective = PlanTier.INTELLIGENCE

    return Entitlement(
        organization_id=organization_id, licensed=licensed, effective=effective,
        status=status,
        trial_started_at=clock.aware(sub.trial_started_at),
        trial_ends_at=ends,
        trial_ended_reason=sub.trial_ended_reason or "")


def _stored_status(sub: models.OrganizationSubscription) -> SubscriptionStatus:
    """The status as stored, degrading an unrecognised one to EXPIRED.

    The same posture as `parse_plan`, pointed the same way: a value nobody can
    parse must not be read as an entitlement. EXPIRED is the narrow answer —
    the organization keeps its data and its quote desk and loses the decision
    layer until somebody looks at the row.
    """
    try:
        return SubscriptionStatus(sub.status)
    except ValueError:
        log.warning("Unrecognised subscription status %r on org=%s — treating "
                    "as expired", sub.status, sub.organization_id)
        return SubscriptionStatus.EXPIRED


def effective_plan(session: Session, organization_id: str) -> PlanTier:
    """What this organization may use right now: its licence, or its trial."""
    return resolve(session, organization_id).effective


def allows(plan: PlanTier, feature: str) -> bool:
    return plan in FEATURES[feature]


def can_use(session: Session, organization_id: str, feature: str) -> bool:
    """Whether this **organization** may use this feature. The boolean form.

    Named for the question rather than for the plan, because the plan is an
    implementation of the question and the callers should not have to know
    which tier holds what. `assert_feature` is the same check with a refusal
    attached; `require_feature` is the same check as a dependency.
    """
    if feature not in FEATURES:
        raise ValueError(f"Unknown feature {feature!r}")
    return allows(effective_plan(session, organization_id), feature)


def assert_feature(session: Session, organization_id: str, feature: str) -> None:
    """Raise PlanRefused unless this organization's effective plan has the feature."""
    plan = effective_plan(session, organization_id)
    if not allows(plan, feature):
        raise PlanRefused(feature, plan)


# ── the trial, which belongs to the organization ─────────────────────────────
def start_trial(session: Session,
                organization_id: str) -> models.OrganizationSubscription:
    """Give a new organization its 30 days. Idempotent, and it is the *only*
    thing that hands out a trial.

    Called once, from tenant provisioning, so that "an organization exists" and
    "an organization has a trial" are the same event. Two properties follow
    from that and both are requirements rather than conveniences:

    - **A second user joining creates nothing.** Membership is a different act
      in a different module and never reaches this function; the joiner reads
      the row that is already here.
    - **A second sign-up does not extend the first.** Idempotent means the
      existing row is returned untouched — not re-dated, not re-statused — so
      an organization cannot be walked back to day one by re-running
      provisioning.
    """
    existing = subscription_for(session, organization_id)
    if existing is not None:
        return existing
    now = clock.now()
    row = models.OrganizationSubscription(
        organization_id=organization_id,
        plan=None,
        status=SubscriptionStatus.TRIALING.value,
        trial_started_at=now,
        trial_ends_at=now + timedelta(days=settings.INTELLIGENCE_TRIAL_DAYS),
        created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    log.info("trial started org=%s ends=%s", organization_id,
             row.trial_ends_at.date())
    return row


def end_trial(session: Session, organization_id: str, *,
              reason: str) -> Optional[models.OrganizationSubscription]:
    """End a running trial now, recording why. Returns the row, or None.

    One caller today — `claim_books`, when the books were already used
    elsewhere. It is a named function rather than two lines inline because
    ending somebody's trial early is the kind of thing that should be greppable
    and should always leave a reason on the row.

    A subscription that is not TRIALING is left exactly as it is: a paying
    organization must not be knocked down by a books collision, and an already
    expired one has nothing to end.
    """
    sub = subscription_for(session, organization_id)
    if sub is None or _stored_status(sub) is not SubscriptionStatus.TRIALING:
        return None
    sub.status = SubscriptionStatus.EXPIRED.value
    sub.trial_ends_at = clock.now()
    sub.trial_ended_reason = reason
    session.flush()
    log.info("trial ended early org=%s reason=%s", organization_id, reason)
    return sub


#: What an organization is told when its books turn out to have been somewhere
#: before. Stored on the row and shown on the trial notice, because a decision
#: layer that switches off on the day somebody connects their books is exactly
#: the kind of thing that reads as a fault unless it says otherwise.
BOOKS_ALREADY_TRIALLED = (
    "These books have already had their trial of Commercial Intelligence "
    "under another organization.")


def claim_books(session: Session, organization_id: str,
                books_key: str) -> Optional[models.IntelligenceTrial]:
    """Record that these books belong to this organization's trial. **The
    duplicate-trial check, and the only anti-abuse mechanism there is.**

    Returns the new claim when this organization is the first to connect these
    books, and None when somebody already has — including under a *different*
    platform organization, which is the case worth catching. A platform
    organization costs nothing to create, so a trial that keyed only on one
    would reset with every fresh address; the connected books are the thing a
    business cannot mint a second copy of.

    Finding a claim held elsewhere **ends this organization's trial**, with the
    reason on the row. That is a deliberate commercial boundary and a
    deliberately shallow one: recreating an account is still possible and buys
    nothing the moment the books are connected. There is no device
    fingerprinting here, no identity graph and none is wanted — see the module
    docstring.

    Never raises. A connection must not fail because a trial bookkeeping write
    could not be made.
    """
    books_key = books_key.strip()
    if not books_key:
        return None
    existing = session.scalar(select(models.IntelligenceTrial).where(
        models.IntelligenceTrial.zoho_organization_id == books_key))
    if existing is not None:
        if existing.organization_id != organization_id:
            log.info("books already trialled org=%s books=%s first_claimed_by=%s",
                     organization_id, books_key, existing.organization_id)
            end_trial(session, organization_id, reason=BOOKS_ALREADY_TRIALLED)
        return None

    sub = subscription_for(session, organization_id)
    now = clock.now()
    row = models.IntelligenceTrial(
        organization_id=organization_id,
        zoho_organization_id=books_key,
        # The claim is stamped with the *organization's* trial window when it
        # has one, so the two records agree about the same month rather than
        # each holding its own dates. An organization that connects after its
        # trial has run gets a claim stamped now, which is still the truthful
        # answer to "when did these books first appear here".
        started_at=clock.aware(sub.trial_started_at) if sub is not None and sub.trial_started_at else now,
        ends_at=clock.aware(sub.trial_ends_at) if sub is not None and sub.trial_ends_at else now)
    session.add(row)
    session.flush()
    log.info("books claimed org=%s books=%s", organization_id, books_key)
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
                ent: Entitlement) -> Optional[dict]:
    """The trial as a screen should read it, or None when there is nothing to say.

    ``days_remaining`` is counted **here** rather than in the browser, and that
    is not fussiness. It is the number of times the business's own date has to
    turn over before the decision layer switches off, and a browser computing
    it would use the reader's zone — an owner travelling, or a server in UTC,
    would see a day more or less than the tenant actually has. ``clock`` exists
    for exactly this, and says so: "``date.today()`` is the server's idea of
    the day and is UTC in every container this runs in".

    An **ended** trial is described too, with ``active: false``. It used to
    return None the moment the date passed, which is how the decision layer
    came to vanish overnight with nothing on screen explaining it — the notice
    could not say "your trial ended on the 3rd" because by then the server had
    stopped mentioning that there had been one.
    """
    if ent.trial_started_at is None or ent.trial_ends_at is None:
        return None
    tz = getattr(org, "timezone", None)
    ends_local = clock.to_local(ent.trial_ends_at, tz)
    days = (ends_local.date() - clock.today(tz)).days if ends_local else 0
    return {
        "active": ent.on_trial,
        "started_at": clock.iso(ent.trial_started_at),
        "ends_at": clock.iso(ent.trial_ends_at),
        # The date a person would write down, in their own zone.
        "ends_on": ends_local.date().isoformat() if ends_local else None,
        # Zero means it ends today and the tenant still has it. Negative is not
        # returned: an ended trial says so through ``active`` rather than
        # through a countdown that has gone through the floor.
        "days_remaining": max(days, 0) if ent.on_trial else 0,
        # Empty unless something ended it early — today only the books-already-
        # trialled case. A screen shows it verbatim; it is written to be read.
        "ended_reason": ent.trial_ended_reason,
    }


def describe(session: Session, organization_id: str) -> dict:
    """Everything a screen needs to say what this organization may use."""
    org = session.get(models.Organization, organization_id)
    ent = resolve(session, organization_id)
    # The sign-up-time request (``wants_more``) is still deliberately not in this
    # payload. It has one consumer — the operator who can act on it, through the
    # CLI — and no screen shows it. A field on the wire that nothing reads is the
    # defect ``GET /api/v1/entitlements`` itself was for two releases.
    #
    # ``pending_request`` below is in it, and by the same rule rather than
    # against it: that comment ended "it belongs here on the day something
    # renders it", and the trial notice renders this one.
    return {
        "plan": ent.licensed.value,
        "plan_label": PLAN_LABEL[ent.licensed],
        "effective_plan": ent.effective.value,
        "effective_label": PLAN_LABEL[ent.effective],
        # The commercial state, named. The client used to infer it from the
        # presence of a trial object and the two plan values, which is three
        # facts to get right in order to render one chip — and which had no way
        # at all to distinguish "trial ended" from "never had one".
        "status": ent.status.value,
        "trial": _trial_view(org, ent),
        "features": {name: allows(ent.effective, name) for name in FEATURES},
        # What this organization has asked for and not yet been given. Present
        # so the upgrade control can say "requested on the 3rd" rather than
        # offering the same button again to somebody who already pressed it.
        "pending_request": _request_view(pending_request(session, organization_id)),
        # What actually goes away when the trial does. Named rather than left to
        # the client to hardcode: the client would then hold a second copy of
        # the plan map, and the two would disagree the first time a feature moved
        # between tiers.
        "loses_on_expiry": (
            sorted(name for name in FEATURES
                   if allows(ent.effective, name) and not allows(ent.licensed, name))
            if ent.on_trial else []),
        # And what is already gone. The same list after the fact, so the locked
        # state can name what subscribing brings back instead of describing the
        # plans in the abstract.
        "locked": sorted(name for name in FEATURES if not allows(ent.effective, name)),
    }


# ── asking, which is not granting ────────────────────────────────────────────
REQUESTED = "REQUESTED"
APPLIED = "APPLIED"
DECLINED = "DECLINED"


class PlanRequestRefused(ValueError):
    """The request cannot be recorded. The message is for the person asking."""


def pending_request(session: Session,
                    organization_id: str) -> Optional[models.PlanChangeRequest]:
    """This organization's outstanding request, or None. Newest first.

    One at a time, and the constraint is deliberate: a second ask while the
    first is open is the same ask again, and two open rows would make "what did
    they want" a question with two answers.
    """
    return session.scalars(
        select(models.PlanChangeRequest)
        .where(models.PlanChangeRequest.organization_id == organization_id,
               models.PlanChangeRequest.status == REQUESTED)
        .order_by(models.PlanChangeRequest.requested_at.desc())
        .limit(1)).first()


def request_plan_change(session: Session, organization_id: str, *,
                        requested_plan: PlanTier, requested_by: str,
                        note: str = "") -> models.PlanChangeRequest:
    """Record that an owner wants a different plan. **Grants nothing.**

    The whole point of the split. ``set_plan`` still moves a plan and is still
    reachable only by an operator, so this can be self-service without an owner
    being able to hand themselves the top tier — which is the property
    ``entitlements``'s own docstring says the missing API was protecting.

    Refuses a request for the plan they are already licensed on. Not out of
    tidiness: an "upgrade" that would change nothing wastes an operator's
    attention on a queue whose whole value is that every row in it is real.
    Refuses a second open request for the same reason.
    """
    licensed = resolve(session, organization_id).licensed
    if requested_plan is licensed:
        raise PlanRequestRefused(
            f"This organization is already on {PLAN_LABEL[licensed]}.")
    existing = pending_request(session, organization_id)
    if existing is not None:
        raise PlanRequestRefused(
            "A plan change is already requested for this organization and has "
            "not been decided yet.")

    row = models.PlanChangeRequest(
        organization_id=organization_id,
        requested_plan=requested_plan.value,
        plan_at_request=licensed.value,
        requested_by=requested_by,
        requested_at=clock.now(),
        note=(note or "").strip()[:2000],
        status=REQUESTED)
    session.add(row)
    session.flush()
    log.info("plan change requested org=%s from=%s to=%s by=%s",
             organization_id, licensed.value, requested_plan.value, requested_by)
    return row


def decide_request(session: Session, request_id: str, *, apply: bool,
                   decided_by: str) -> models.PlanChangeRequest:
    """Apply or decline one request. The only path that both reads and grants.

    Applying calls ``set_plan`` rather than writing ``organizations.plan``
    itself, so there stays exactly one function that moves a plan and exactly
    one log line saying it moved.

    The request is stamped and never rewritten: what was asked for stays what
    was asked for, because the argument six months from now is about that and
    not about what it became.
    """
    row = session.get(models.PlanChangeRequest, request_id)
    if row is None:
        raise LookupError(f"No such request {request_id!r}")
    if row.status != REQUESTED:
        raise PlanRequestRefused(
            f"Request {request_id} was already {row.status.lower()}.")
    if apply:
        set_plan(session, row.organization_id, PlanTier(row.requested_plan))
    row.status = APPLIED if apply else DECLINED
    row.decided_at = clock.now()
    row.decided_by = decided_by
    session.flush()
    log.info("plan change %s request=%s org=%s by=%s",
             row.status.lower(), request_id, row.organization_id, decided_by)
    return row


def _request_view(row: Optional[models.PlanChangeRequest]) -> Optional[dict]:
    if row is None:
        return None
    return {
        "request_id": row.request_id,
        "requested_plan": row.requested_plan,
        "requested_plan_label": PLAN_LABEL.get(
            PlanTier(row.requested_plan), row.requested_plan),
        "requested_at": clock.iso(row.requested_at),
        "status": row.status,
    }


# ── operator CLI ─────────────────────────────────────────────────────────────
def set_plan(session: Session, organization_id: str,
             plan: PlanTier) -> models.OrganizationSubscription:
    """Put an organization on a plan. The only function that grants anything.

    Writes the subscription, which is the authority, **and** the legacy
    ``organizations.plan`` column, which is not read by `resolve` when a
    subscription exists but is still what an un-migrated reader and a rollback
    would find. One writer for both, here, for the reason
    ``memberships._mirror_home_role`` gives about the other legacy mirror.

    Moving to FREE is how a subscription ends: the plan goes, the status
    becomes CANCELLED, and ``subscription_started_at`` is left alone — the
    answer to "how long were they a customer" must survive them stopping being
    one. It does **not** start a new trial, which is the whole point of the
    trial belonging to the organization: it happened once, at the beginning.
    """
    org = session.get(models.Organization, organization_id)
    if org is None:
        raise LookupError(f"No such organization {organization_id!r}")
    org.plan = plan.value

    sub = subscription_for(session, organization_id)
    if sub is None:
        # An organization from before subscriptions, being granted a plan. It
        # gets a row with no trial dates rather than a trial: whatever it had
        # or did not have is behind it, and inventing 30 days here would hand a
        # month to every legacy tenant an operator touched.
        sub = models.OrganizationSubscription(organization_id=organization_id)
        session.add(sub)

    now = clock.now()
    if plan is PlanTier.FREE:
        sub.plan = None
        sub.status = SubscriptionStatus.CANCELLED.value
    else:
        sub.plan = plan.value
        sub.status = SubscriptionStatus.ACTIVE.value
        if sub.subscription_started_at is None:
            sub.subscription_started_at = now
    sub.updated_at = now
    session.flush()
    log.info("plan set org=%s plan=%s status=%s", organization_id, plan.value,
             sub.status)
    return sub


def _main() -> int:
    import argparse

    from .db import SessionLocal

    parser = argparse.ArgumentParser(
        prog="python -m app.entitlements",
        description="Operator plan management. An owner can *ask* over the API; granting stays here, which is the whole of the split.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_set = sub.add_parser("set-plan", help="Put an organization on a plan")
    p_set.add_argument("organization_id")
    p_set.add_argument("plan", choices=[p.value for p in PlanTier])
    p_show = sub.add_parser("show", help="Show an organization's entitlements")
    p_show.add_argument("organization_id")
    sub.add_parser(
        "requests",
        help="Everyone asking for more than they are licensed on, both ways")
    p_apply = sub.add_parser("apply", help="Grant a requested plan change")
    p_apply.add_argument("request_id")
    p_decline = sub.add_parser("decline", help="Refuse a requested plan change")
    p_decline.add_argument("request_id")
    args = parser.parse_args()

    with SessionLocal() as session:
        # **One command over two queues, because an operator asking "who wants a
        # plan" must get one answer.** Two arrived independently and the merge
        # is where that shows: `organizations.requested_plan` is what a business
        # said it wanted *on the sign-up form*, before it was a customer, and it
        # has no decision to make — granting it is `set-plan`. A
        # `PlanChangeRequest` is an existing customer asking *from inside the
        # product*, and it carries a decision, which is why it has an id you can
        # `apply` or `decline`.
        #
        # They are genuinely different questions and both are worth having. They
        # are also two places that answer "what plan does this organization
        # want", which is the responsibility duplication CLAUDE.md §2 names —
        # unifying them means the sign-up answer writing a request row and the
        # column going away, and that is a change to a released feature rather
        # than something to smuggle into a conflict resolution. Listed together
        # so nobody has to know there are two tables; flagged here so the next
        # person to touch it knows there are.
        if args.cmd == "requests":
            asked = [(org, want) for org in session.scalars(
                        select(models.Organization).order_by(
                            models.Organization.created_at))
                     if (want := wants_more(org)) is not None]
            rows = session.scalars(
                select(models.PlanChangeRequest)
                .where(models.PlanChangeRequest.status == REQUESTED)
                .order_by(models.PlanChangeRequest.requested_at)).all()
            if not asked and not rows:
                print("Nobody is asking for more than they have.")
                return 0
            if asked:
                print("Asked for at sign-up — grant with `set-plan`:")
                for org, want in asked:
                    print(f"  {org.organization_id}\t{org.name}\t"
                          f"on {licensed_plan(org).value}\twants {want.value}")
            if rows:
                if asked:
                    print()
                print("Asked from inside the product — `apply` or `decline`:")
                for row in rows:
                    print(f"  {row.request_id}  {row.organization_id}  "
                          f"{row.plan_at_request} -> {row.requested_plan}  "
                          f"asked {clock.iso(row.requested_at)}  "
                          f"by {row.requested_by}")
                    if row.note:
                        print(f"      note: {row.note}")
            return 0
        if args.cmd == "set-plan":
            set_plan(session, args.organization_id, PlanTier(args.plan))
            session.commit()
        elif args.cmd in ("apply", "decline"):
            row = decide_request(session, args.request_id,
                                 apply=args.cmd == "apply", decided_by="operator")
            session.commit()
            print(f"{row.status} — {row.organization_id} is now on "
                  f"{resolve(session, row.organization_id).licensed.value}")
            return 0
        print(describe(session, args.organization_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
