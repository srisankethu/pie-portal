"""The public site's one form, and the queue an operator reads it from.

The landing page states no price, in either currency, on any panel. That is the
product decision this module exists to serve: what a distributor pays depends
on how many companies they run, which ERP they run them on and how much
catalogue there is to build, none of which a panel knows — so the section
describes what each plan *is* and ends in a form.

A form has to land somewhere. This is that somewhere, and everything odd about
it follows from what a visitor is at the moment they fill it in: not a tenant.

- **No ``organization_id``.** There is no organization yet, so
  :class:`~app.domain.models.ContactRequest` is the third table in this schema
  with no tenant column, alongside ``zoho_credentials`` (which belongs to a
  person) and ``process_leases`` (which is about processes). The row-level
  security suite names all three and fails on a fourth, which is the right way
  round: adding one has to be a decision somebody made rather than something
  discovered later.
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
three tables. This module owns the rows; that command borrows them.
"""
from __future__ import annotations

import argparse
import logging
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
    args = parser.parse_args()

    with SessionLocal() as session:
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
