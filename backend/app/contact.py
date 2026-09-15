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
- **It grants nothing.** No account is created and no plan is licensed. It
  records an ask — and, since ``app/mailer.py``, tells somebody it did; what
  that mail grants a visitor is still nothing. That is the same non-promise ``PlanChangeRequest``
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
import html
import logging
import sys
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import clock, mailer, onboarding
from .domain import models

log = logging.getLogger("pie_portal.contact")

#: Waiting for a reply. Rows only ever move NEW → HANDLED; a second enquiry is
#: a second row, for the reason ``PlanChangeRequest`` gives.
NEW = "NEW"
HANDLED = "HANDLED"

#: How the last attempt to *email* an enquiry went — a different question from
#: whether it has been answered, which is what NEW/HANDLED above track, and a
#: different question again from ``notified_at``.
#:
#: **Two channels, two keys, on purpose.** ``notified_at`` is the webhook's
#: once-only stamp and stays exactly what it was; ``notification_status`` is the
#: email's. Sharing one key would mean a deployment that turned mail on quietly
#: stopped getting its Slack message — the row would already be stamped by the
#: time the sweep composed one — and a channel that switches itself off when you
#: add another is the kind of surprise nobody debugs until it matters. They are
#: independent: either, both or neither may be configured, and each announces
#: every enquiry exactly once.
SENT = "SENT"
FAILED = "FAILED"
#: Deliberately not sent, and not to be retried — what ``alert --mark-only``
#: writes. It exists so the backlog escape hatch does not have to lie: a
#: deployment turning mail on for the first time wants its history treated as
#: history, and the two ways to say that without a third state are writing SENT
#: over rows nothing was sent for, or leaving them to be emailed as news. This
#: is the honest third option, and ``unemailed`` excludes it for the same
#: reason it excludes SENT: neither is outstanding.
SKIPPED = "SKIPPED"

#: What ``notification_error`` will hold, at most — the column's own width.
#: Truncated rather than refused, because losing the diagnosis of a failed send
#: to the length of the diagnosis would be its own small joke.
MAX_NOTIFICATION_ERROR = 255

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


def unemailed(session: Session) -> list[models.ContactRequest]:
    """Everything no email has gone out for yet, oldest first.

    The email channel's equivalent of ``unannounced`` above, keyed on its own
    column for the reason the ``SENT``/``FAILED`` note gives. Both a row nobody
    has attempted (NULL) and a row whose send failed (FAILED) are in here, and
    they are in here for the same reason: neither has produced an email.

    Not filtered to ``status == NEW``, matching ``unannounced`` — an enquiry
    answered straight from the console before the sweep ran still never
    produced a message, and the run that follows should send one.
    """
    # The NULL branch is spelled out rather than left to `not_in`: in SQL,
    # `NULL NOT IN ('SENT', …)` is NULL rather than true, so a plain `not_in`
    # would silently drop every row nobody has attempted yet — which is most of
    # them, and exactly the rows this query exists to find.
    return list(session.scalars(
        select(models.ContactRequest)
        .where(or_(models.ContactRequest.notification_status.is_(None),
                   models.ContactRequest.notification_status.not_in(
                       [SENT, SKIPPED])))
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


# ── telling one person, by email, that one enquiry arrived ───────────────────
#: What the operator sees in their inbox list before opening anything. The
#: company (or the person, where there is no company) is in the subject because
#: a list of twenty identical "New demo request" lines is a list nobody triages.
def email_subject(row: models.ContactRequest) -> str:
    return f"Demo request — {row.company or row.name}"


#: The fields of an enquiry, in the order a person reads them, as
#: (label, value) pairs with the empty ones dropped.
#:
#: One function so the HTML and the plain text cannot drift into saying
#: different things about the same row — which is the failure mode of writing a
#: template twice, and the reason CLAUDE.md §2 treats a second copy as the
#: defect rather than the backup.
def _fields(row: models.ContactRequest) -> list[tuple[str, str]]:
    return [(label, value) for label, value in (
        ("Name", row.name),
        ("Work email", row.email),
        ("Company", row.company),
        ("Phone", row.phone),
        # The form asks which ERP they run rather than what their job title is.
        # It is labelled as what it is: a made-up "Role: Prophet 21" would be a
        # worse answer than the true one.
        ("Runs", row.erp),
        ("Asked about", row.plan),
        ("Requirements", row.message),
        ("Submitted", clock.iso(row.created_at)),
    ) if (value or "").strip()]


def demo_email(row: models.ContactRequest) -> mailer.EmailMessage:
    """One demo request, composed for a person's inbox.

    **Reply-To is the prospect.** It is the single most useful thing this
    message does: the operator presses reply and reaches the buyer, rather than
    reaching the sending domain and then having to go and find the address. The
    From address stays the verified sender, because a message claiming to come
    *from* a stranger's domain is one that fails SPF and lands in spam.

    **Both bodies, always.** HTML for a client that renders it and plain text
    for one that does not, built from the same ``_fields`` list so they cannot
    disagree. A message with only one half renders as nothing somewhere, and the
    person who finds out is the one who was supposed to be told.

    **Everything a visitor typed is escaped.** ``company``, ``name`` and
    ``message`` are free text from an unauthenticated form — ``capture`` bounds
    their length and deliberately does not argue with their content — so the one
    place that content becomes markup is the one place it has to be escaped.
    """
    fields = _fields(row)
    text = "\n".join([
        "A demo request just came in through the website.", "",
        *(f"{label}: {value}" for label, value in fields),
    ])
    rows = "".join(
        f'<tr>'
        f'<td style="padding:4px 12px 4px 0;vertical-align:top;color:#555;'
        f'white-space:nowrap">{html.escape(label)}</td>'
        f'<td style="padding:4px 0;vertical-align:top">'
        f'{html.escape(value)}</td>'
        f'</tr>'
        for label, value in fields)
    body = (
        '<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;'
        'font-size:14px;line-height:1.5;color:#111">'
        '<p>A demo request just came in through the website.</p>'
        f'<table cellspacing="0" cellpadding="0">{rows}</table>'
        '<p style="color:#777;font-size:12px">Reply to this message to answer '
        'them directly.</p>'
        '</div>')
    return {
        "to": mailer.recipients(),
        "subject": email_subject(row),
        "html": body,
        "text": text,
        # The prospect, so replying reaches the buyer. This is requirement one
        # of the whole feature.
        "reply_to": row.email,
    }


def notify_one(session: Session, row: models.ContactRequest, *,
               sender: Optional[mailer.EmailSender] = None) -> bool:
    """Announce one enquiry by email, now. Returns whether it went.

    Called straight after the enquiry was committed, so the visitor is told
    within a second or two rather than at the next sweep — and *only* after,
    which is the ordering the whole reliability argument rests on: the row is
    durable before a provider is ever contacted, so no failure here can lose an
    enquiry or roll one back.

    Four things it will not do:

    **It will not raise.** The one caller is an unauthenticated public form that
    must answer 202 whether or not a mail provider is reachable.
    ``mailer.send`` already returns rather than raises; this adds the database
    half, where a commit that fails must still not propagate.

    **It will not send twice.** ``notification_status`` is re-read here, inside
    this transaction, rather than trusted from whenever the row was loaded. A
    resubmitted form is a *new row* and gets its own message; the same row
    reaching this function twice — the sweep racing the request, a retried
    worker — sends once. It is this column and not ``notified_at``: that one is
    the webhook's stamp, and the two channels are independent.

    **It will not stamp what it did not send.** ``notification_status`` becomes
    SENT only on ``result.ok``, so a provider that was down leaves the row
    FAILED and ``python -m app.contact alert`` retries it. §1's rule about
    absent evidence: a send that did not happen is not a send.

    **It will not pretend an unconfigured deployment sent something.** With no
    provider configured this returns False and stamps nothing at all — not even
    FAILED, because nothing was attempted and "we tried and it did not work" is
    a different fact from "this deployment does not send mail".

    ``sender`` is injectable for tests. It defaults to ``select_sender()``,
    which is a mock unless a key is set — so a suite cannot reach a provider by
    accident even if it forgets to pass one.
    """
    if row.notification_status == SENT:
        return False
    if sender is None:
        if not mailer.configured():
            return False
        sender = mailer.select_sender()

    result = sender.send(demo_email(row))
    if result.ok:
        row.notification_status = SENT
        row.notification_message_id = result.message_id
        row.notification_error = None
    else:
        # Left un-SENT on purpose: not-SENT is what the sweep retries.
        row.notification_status = FAILED
        row.notification_error = (result.error or "unknown")[:MAX_NOTIFICATION_ERROR]

    try:
        session.commit()
    except Exception:  # noqa: BLE001 — a public form must not 500 over this
        # The enquiry is already committed; this transaction only carries the
        # announcement bookkeeping. Losing it means the sweep emails the row
        # again later, which is the direction this whole module chooses:
        # being told twice is an annoyance, being told never is the defect.
        log.warning("could not record the notification state of %s",
                    row.contact_request_id)
        session.rollback()
        return False
    return result.ok


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

    # `--mark-only` first, and before either channel reads what is outstanding.
    # It must run even when one channel has nothing waiting: a deployment that
    # has been sending webhooks for months and is only now turning mail on has
    # an empty `unannounced` and a full `unemailed`, and that is precisely the
    # case this flag exists for.
    if mark_only:
        # Both channels, because both would otherwise announce this backlog as
        # news. SKIPPED rather than SENT on the mail side: nothing was sent, and
        # a row claiming otherwise is the kind of small lie that costs somebody
        # an afternoon later.
        fresh, stale = unannounced(session), unemailed(session)
        for row in stale:
            row.notification_status = SKIPPED
        mark_announced(session, fresh)
        session.commit()
        print(f"Marked {len(fresh)} enquiries as already announced and "
              f"{len(stale)} as already emailed. Nothing sent.")
        return 0

    # The email channel, independently: it has its own key, its own retry and
    # its own idea of what is outstanding, so a webhook that is down must not
    # hold up the mail and vice versa. This is also where a send that failed at
    # submit time actually gets retried — `notify_one` commits per row, so one
    # address the provider refuses does not cost the rest of the batch.
    if mailer.configured():
        for row in unemailed(session):
            notify_one(session, row)

    # Read after the mail pass, not before: `notify_one` commits, and a list of
    # ORM rows collected either side of a commit is a list this function would
    # rather not be holding. The two channels share no state, so re-reading
    # costs one query and removes the question entirely.
    fresh = unannounced(session)
    if not fresh:
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
