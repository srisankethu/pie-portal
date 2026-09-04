"""The public site's one form, and the queue an operator reads it from.

The landing page states no price, in either currency, on any panel. That is the
product decision this module exists to serve: what a distributor pays depends
on how many companies they run, which ERP they run them on and how much
catalogue there is to build, none of which a panel knows — so the section
describes what each plan *is* and ends in a form.

A form has to land somewhere. This is that somewhere, and everything odd about
it follows from what a visitor is at the moment they fill it in: not a tenant.

- **No ``organization_id``.** There is no organization yet, so
  :class:`~app.domain.models.ContactRequest` is one of the few tables in this
  schema with no tenant column, alongside ``zoho_credentials`` (which belongs
  to a person), ``process_leases`` (which is about processes) and
  ``operator_keys`` (which is PIE's own staff credential). The row-level
  security suite names them by hand and fails on one it has not been told
  about, which is the right way round: adding one has to be a decision somebody
  made rather than something discovered later. ``operator_keys`` is what that
  decision looks like when it is made on purpose —
  ``docs/operator-console.md`` argues it.
- **No principal.** The endpoint is unauthenticated by necessity, which is why
  the refusals below are strict about what a row must contain and why the
  router rate-limits it.
- **It grants nothing.** No account is created, no plan is licensed, no mail is
  sent. It records an ask. That is the same non-promise ``PlanChangeRequest``
  makes from inside the product and ``organizations.requested_plan`` makes on
  the sign-up form, and it is deliberate in all three places: a plan moves only
  through ``entitlements.set_plan``, which only an operator can reach.

**A queue nobody reads is a form that lies.** ``python -m app.contact`` lists
what is waiting and marks one answered; ``python -m app.entitlements requests``
prints this queue beside the other two, because an operator asking "who wants
to buy something" must get one answer rather than having to know there are
three tables. The operator console (``docs/operator-console.md``) reads the same
rows over HTTP. This module owns them; everything else borrows them.

**And a queue you have to remember to look at is the same lie, slower.** Every
reader above is something a person has to *decide* to open. ``contact alert`` is
the one that goes the other way: run on a schedule, it sends what has arrived
since the last time it sent anything and stamps those rows, so an enquiry is
announced once and a run with nothing new says nothing at all. The stamp is
``notified_at`` and it is written only after a delivery succeeded — what a
failed webhook loses is an announcement, never an enquiry.
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock, onboarding
from .domain import models

log = logging.getLogger("pie_portal.contact")

#: Waiting for a reply. Rows only ever move NEW → HANDLED; a second enquiry is
#: a second row, for the reason ``PlanChangeRequest`` gives.
NEW = "NEW"
HANDLED = "HANDLED"

#: What the message field will hold, at most. Generous on purpose — a buyer
#: describing their book in four paragraphs is the best enquiry on the list —
#: and truncated rather than refused, because losing an enquiry over its length
#: is the one failure this form cannot afford. The router declares the same
#: ceiling so the refusal happens before the body is parsed where it can.
MAX_MESSAGE = 4000


class ContactRefused(ValueError):
    """The details supplied cannot be answered. The message is for a person."""


def capture(session: Session, *, company: str, name: str, email: str,
            phone: str = "", plan: Optional[str] = None, erp: str = "",
            message: str = "") -> models.ContactRequest:
    """Record one enquiry from the public site. Appends, always.

    Refuses two things and nothing else:

    **An enquiry with no way to answer it.** An address is required, and it is
    checked for shape by ``onboarding._clean_email`` — the same rule the sign-up
    form applies, because a page with two ideas of what an email address is will
    accept one at one door and refuse it at the other.

    **An enquiry with nobody in it.** A name or a company, at least one. A row
    holding only an address is not an enquiry, it is a scrape, and storing it
    puts a line in the operator's queue with nothing to act on.

    Everything else is optional and stored as typed. The plan is parsed through
    ``onboarding.parse_requested_plan`` rather than re-checked here: which
    strings name a plan is one fact, and the sign-up form already owns it. Its
    refusal is re-raised as this module's, because a visitor asking a question
    on a marketing page is not signing up and should not be told they are.
    """
    try:
        address = onboarding._clean_email(email)
    except onboarding.SignupRefused as e:
        raise ContactRefused(str(e)) from None

    who = (name or "").strip()
    firm = (company or "").strip()
    if not who and not firm:
        raise ContactRefused("Tell us who you are, or which company you are with")

    try:
        wants = onboarding.parse_requested_plan(plan)
    except onboarding.SignupRefused as e:
        raise ContactRefused(str(e)) from None

    row = models.ContactRequest(
        company=firm[:255], name=who[:255], email=address[:255],
        phone=(phone or "").strip()[:64],
        plan=(wants.value if wants else ""),
        erp=(erp or "").strip()[:64],
        message=(message or "").strip()[:MAX_MESSAGE],
        status=NEW,
    )
    session.add(row)
    session.flush()
    # The address is not logged. It is the one field here that identifies a
    # person, the log is not the queue, and `trust/` is built on the rule that
    # a name lives where it can be erased from.
    log.info("contact request %s company=%r plan=%r",
             row.contact_request_id, row.company, row.plan or "-")
    return row


def pending(session: Session) -> list[models.ContactRequest]:
    """Everyone still waiting for a reply, oldest first.

    Oldest first because that is the order they should be answered in, and
    because the top of this list is the enquiry that has been ignored longest —
    which is the number worth seeing.
    """
    return list(session.scalars(
        select(models.ContactRequest)
        .where(models.ContactRequest.status == NEW)
        .order_by(models.ContactRequest.created_at)))


def unannounced(session: Session) -> list[models.ContactRequest]:
    """Everything nobody has been told about yet, oldest first.

    Deliberately *not* filtered to ``status == NEW``. An enquiry answered
    straight from the console before the alert ran was still never announced,
    and the run that follows should say it arrived — the alert reports what came
    in, the queue reports what is outstanding. Two questions, and conflating
    them here would make the alert silently skip anything handled quickly.
    """
    return list(session.scalars(
        select(models.ContactRequest)
        .where(models.ContactRequest.notified_at.is_(None))
        .order_by(models.ContactRequest.created_at)))


def mark_announced(session: Session,
                   rows: list[models.ContactRequest]) -> None:
    """Stamp the rows an alert actually delivered.

    One timestamp for the batch rather than one per row: they were announced by
    the same message, and giving them times that differ by microseconds would
    invent a precision the fact does not have.
    """
    at = clock.now()
    for row in rows:
        row.notified_at = at


def alert_text(rows: list[models.ContactRequest], *, waiting: int) -> str:
    """What the alert says. Plain text, because it has to survive being read on
    a phone in a message app that renders nothing.

    Both numbers are here on purpose: what arrived (the news) and what is
    outstanding (the reason to act). A message carrying only the first lets a
    backlog grow while every individual alert still looks small.
    """
    lines = ["1 new enquiry" if len(rows) == 1
             else f"{len(rows)} new enquiries"]
    for row in rows:
        who = row.company or row.name
        detail = " · ".join(filter(None, [
            row.name if row.company else "",
            row.email,
            f"runs {row.erp}" if row.erp else "",
            f"asked about {row.plan}" if row.plan else "",
        ]))
        lines.append(f"\n{who}\n{detail}")
        if row.message:
            # Trimmed: the whole message is in the console, and an alert that
            # pastes four paragraphs into a phone is one nobody reads to the end.
            trimmed = (row.message if len(row.message) <= 240
                       else row.message[:237] + "…")
            lines.append(trimmed)
    lines.append(f"\n{waiting} waiting for a reply in total.")
    return "\n".join(lines)


def mark_handled(session: Session, contact_request_id: str, *,
                 handled_by: str) -> models.ContactRequest:
    """Somebody replied. Stamps the row and leaves what was asked intact.

    Refuses a second stamp rather than overwriting the first: when this was
    answered, and by whom, is the only audit this row has.
    """
    row = session.get(models.ContactRequest, contact_request_id)
    if row is None:
        raise ContactRefused(f"No such contact request: {contact_request_id}")
    if row.status == HANDLED:
        raise ContactRefused(
            f"Already handled by {row.handled_by} at {clock.iso(row.handled_at)}")
    row.status = HANDLED
    row.handled_at = clock.now()
    row.handled_by = handled_by
    return row



def describe(row: models.ContactRequest) -> str:
    """One enquiry as an operator's line. Shared by both CLIs, so the queue
    reads the same whichever command printed it."""
    head = f"  {row.contact_request_id}  {row.company or row.name}"
    detail = "  ".join(filter(None, [
        row.name if row.company else "",
        row.email,
        row.phone,
        f"runs {row.erp}" if row.erp else "",
        f"asked about {row.plan}" if row.plan else "",
        f"asked {clock.iso(row.created_at)}",
    ]))
    lines = [head, f"      {detail}"]
    if row.message:
        lines.append(f"      {row.message}")
    return "\n".join(lines)


def _alert(session: Session, *, mark_only: bool = False) -> int:  # pragma: no cover — CLI
    """The scheduled half: say what arrived, once, and stay quiet otherwise.

    Exit code 0 whether or not there was anything to say. A cron entry that
    fails on "nothing happened" is one somebody silences, and a silenced job is
    the state this command exists to get out of.

    The order below is the whole correctness argument: deliver, *then* stamp,
    *then* commit. A webhook that was down leaves the rows unannounced for the
    next run, and a crash between the two leaves them unannounced too — the
    failure mode is a repeated announcement, never a missed one. That direction
    is chosen deliberately: being told twice is an annoyance, being told never
    is the defect.
    """
    from . import alerts

    fresh = unannounced(session)
    if not fresh:
        return 0

    if mark_only:
        mark_announced(session, fresh)
        session.commit()
        print(f"Marked {len(fresh)} enquiries as already announced. Nothing sent.")
        return 0

    text = alert_text(fresh, waiting=len(pending(session)))
    if not alerts.configured():
        # No destination. Print it and say so — a deployment that has not chosen
        # one must find that out, rather than running a silent job forever.
        print(text)
        print("\nALERT_WEBHOOK is not set, so this was not sent anywhere and "
              "nothing was stamped. Set it, or pipe this command's output.",
              file=sys.stderr)
        return 0

    if not alerts.deliver(text):
        print("The alert did not send. These stay unannounced and the next run "
              "will try again.", file=sys.stderr)
        return 0

    mark_announced(session, fresh)
    session.commit()
    log.info("announced %d contact requests", len(fresh))
    return 0


def _main() -> int:                                  # pragma: no cover — CLI
    from .db import SessionLocal

    parser = argparse.ArgumentParser(
        prog="python -m app.contact",
        description="Enquiries from the public site's form.")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("list", help="Everyone still waiting for a reply")
    p_done = sub.add_parser("handled", help="Mark one enquiry answered")
    p_done.add_argument("contact_request_id")
    p_done.add_argument("--by", default="operator")
    p_alert = sub.add_parser(
        "alert",
        help="Send what has arrived since the last time this said anything. "
             "Silent when there is nothing new — meant for cron.")
    p_alert.add_argument(
        "--mark-only", action="store_true",
        help="Stamp everything as announced without sending. For the first run "
             "after deploying, when the backlog is history rather than news.")
    args = parser.parse_args()

    with SessionLocal() as session:
        if args.cmd == "alert":
            return _alert(session, mark_only=args.mark_only)
        if args.cmd == "handled":
            row = mark_handled(session, args.contact_request_id,
                               handled_by=args.by)
            session.commit()
            print(f"HANDLED — {row.company or row.name} <{row.email}>")
            return 0
        rows = pending(session)
        if not rows:
            print("Nobody is waiting for a reply.")
            return 0
        print(f"{len(rows)} waiting — reply, then `handled <id>`:")
        for row in rows:
            print(describe(row))
    return 0


if __name__ == "__main__":                           # pragma: no cover — CLI
    raise SystemExit(_main())
