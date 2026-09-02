"""The quote workspace: every draft the desk is working on, durably, shared.

Where a quote *lives*. ``store.py`` holds the working object and the
mutations the desk performs on it; this module is the row underneath —
``quote_drafts`` — and the four things a workspace needs that the working
object cannot answer on its own:

* **a number that means something.** ``QB-0042``: per organization, from 1,
  minted from a sequence the row carries and made unique by constraint. The
  builder used to stamp ``QB-`` plus the clock modulo 100000, which read as a
  number, repeated about once a day, and ordered nothing.
* **more than one draft, from more than one browser.** A quote used to be a
  process-wide dict entry with a copy in the browser that opened it. Now a
  draft started on one desk is on every desk's list, and a restart forgets
  nothing.
* **no customer, until somebody chooses.** ``customer_id`` is nullable and
  ``customer_name`` defaults to empty. The builder opened every quote against
  one literal name for a long time, and an empty customer was unrepresentable
  — so the screen's default became whatever it was last told.
* **what each draft is waiting on**, for the list: empty, needs attention,
  no customer yet, needs approval, awaiting approval, ready to send, sent.
  Derived here from the same functions the send enforces, so the list and
  the button never disagree.

Session-passing, like the rest of the codebase. It imports ``store`` (the
dataclasses), ``approvals`` (the gate) and ``commercial.quote_service`` (the
sent-document row) and nothing from ``ai/``; the number is arithmetic over a
column and the readiness is a predicate over rows, which is all §1 asks.
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import approvals, clock
from .commercial import quote_service
from .domain import models
from .domain.enums import ApprovalKind, ApprovalStatus
from .ingestion import connections as conn
from .store import Line, Quote, store

log = logging.getLogger("pie_portal.quote_workspace")

#: The prefix every platform quote number carries. Kept from the builder's
#: original numbering so a saved link or a note that says "QB-…" still reads
#: as one of ours; only the part after the dash changed meaning.
NUMBER_PREFIX = "QB"

#: How many times to re-mint a number when another desk took it first. Two
#: desks starting a quote inside one transaction window is the only way to
#: lose the unique constraint, and once is enough to prove the retry works.
_MINT_ATTEMPTS = 5


def format_number(sequence: int) -> str:
    """``QB-0042``. Four digits because a desk reads them aloud; a fifth
    appears on its own the day the organization has quoted ten thousand times."""
    return f"{NUMBER_PREFIX}-{sequence:04d}"


# ── create ───────────────────────────────────────────────────────────────────
def create(session: Session, org: str, *, user_id: Optional[str],
           customer: str = "", customer_id: Optional[str] = None,
           connection_id: Optional[str] = None) -> Quote:
    """Start a draft: mint its number, write the row, hand back the object.

    The number is ``max(sequence) + 1`` for this organization — archived
    drafts included, which is what stops a removed number coming back —
    taken inside a savepoint so that losing the race to another desk costs
    one retry and not the caller's transaction. ``customer`` may be empty — that is the
    default, and the row says so rather than substituting a placeholder.
    """
    customer = (customer or "").strip()
    customer_id = customer_id or None
    for _ in range(_MINT_ATTEMPTS):
        seq = _next_sequence(session, org)
        row = models.QuoteDraft(
            quote_id=str(uuid.uuid4()), organization_id=org,
            customer_id=customer_id, customer_name=customer,
            salesperson_id=user_id, updated_by_user_id=user_id,
            number=format_number(seq), sequence=seq,
            connection_id=connection_id,
            # The idempotency key for whatever ERP document this becomes. The
            # number is unique per organization now, but the tail stays: a
            # reference written into a shared ledger should not collide with
            # another tenant's ``QB-0042`` either.
            reference=f"{format_number(seq)}-{uuid.uuid4().hex[:8]}",
            lines=[], created_at=clock.now(), updated_at=clock.now())
        try:
            with session.begin_nested():
                session.add(row)
                session.flush()
        except IntegrityError:
            # Somebody else minted this sequence between our read and our
            # write. The savepoint has rolled the insert back; read again.
            continue
        return _to_quote(row)
    raise RuntimeError(
        f"could not mint a quote number for {org} after {_MINT_ATTEMPTS} attempts")


def _next_sequence(session: Session, org: str) -> int:
    latest = session.scalar(
        select(func.max(models.QuoteDraft.sequence))
        .where(models.QuoteDraft.organization_id == org))
    return int(latest or 0) + 1


# ── read / write ─────────────────────────────────────────────────────────────
def load(session: Session, org: str, quote_id: str) -> Optional[Quote]:
    """The draft, only if it belongs to this tenant.

    The org check is the whole of quote authorization — a foreign id and an
    absent one are both ``None``, so nothing here confirms that another
    organization's quote exists.
    """
    row = _row(session, org, quote_id)
    return None if row is None else _to_quote(row)


def save(session: Session, quote: Quote, user_id: Optional[str]) -> None:
    """Write the working object back over its row.

    Every mutation endpoint ends here. The lines go back whole — cost and
    all, see ``Line.to_state`` — and the quote-level fields that can change
    after creation (the customer, and nothing else today) go with them.
    """
    row = _row(session, quote.organizationId, quote.id)
    if row is None:
        # The draft was deleted underneath this request. Nothing to write to,
        # and inventing a row for a quote somebody just removed is the wrong
        # kind of helpful.
        log.warning("quote %s vanished before it could be saved", quote.id)
        return
    now = clock.now()
    row.customer_id = quote.customerId or None
    row.customer_name = quote.customer
    row.lines = quote.lines_state()
    row.updated_by_user_id = user_id
    row.updated_at = now
    quote.savedAt = clock.iso(now)
    session.flush()


def delete(session: Session, org: str, quote_id: str,
           user_id: Optional[str] = None) -> bool:
    """Remove a draft from the workspace. False when there was none to remove.

    An archive stamp rather than a DELETE, so the number stays minted — see
    ``QuoteDraft.archived_at``. From here on the row answers to nothing:
    ``load`` and the list both refuse it, so a second removal is a 404 like
    any unknown id.
    """
    row = _row(session, org, quote_id)
    if row is None:
        return False
    row.archived_at = clock.now()
    row.updated_by_user_id = user_id
    session.flush()
    return True


def line_cost(session: Session, org: str, quote_id: str,
              line_id: str) -> Optional[Decimal]:
    """The landed cost this server already holds against one quote line.

    Read by the assessment path so the gate is judging the same cost the
    grid is showing. One accessor rather than the same lookup in the
    approvals router and the quote-intelligence router, because "where does
    a quote line's cost come from" is exactly the question that had two
    answers in the first place.

    Server-held, never requester-supplied: the cost arrived from the books at
    intake and lives in the row, so passing it into an assessment is not the
    same thing as trusting a number in a request body.

    ``org`` is enforced: this returns a *cost*, and a caller naming another
    tenant's ``quote_id`` must read exactly what an unknown id reads — ``None``.
    """
    quote = load(session, org, quote_id)
    if quote is None:
        return None
    line = next((row for row in quote.lines if row.id == line_id), None)
    if line is None or line.cost is None:
        return None
    # `Decimal(str(...))` rather than `Decimal(float)`: money is Decimal (§1),
    # and the binary-float detour is how 420.0 becomes 419.99999999999994.
    return Decimal(str(line.cost))


def _row(session: Session, org: str, quote_id: str) -> Optional[models.QuoteDraft]:
    if not quote_id:
        return None
    row = session.get(models.QuoteDraft, quote_id)
    if row is None or row.organization_id != org or row.archived_at is not None:
        return None
    return row


def _to_quote(row: models.QuoteDraft) -> Quote:
    return Quote(
        id=row.quote_id, customer=row.customer_name or "", number=row.number,
        organizationId=row.organization_id, connectionId=row.connection_id,
        customerId=row.customer_id or None, reference=row.reference or "",
        lines=[Line.from_state(state) for state in (row.lines or [])],
        savedAt=clock.iso(row.updated_at))


# ── the list ─────────────────────────────────────────────────────────────────
#: What a draft is waiting on. The client maps these to words; the order here
#: is the order they are decided in, and the first that applies wins.
READINESS = ("EMPTY", "NEEDS_ATTENTION", "NO_CUSTOMER", "SENT",
             "AWAITING_APPROVAL", "NEEDS_APPROVAL", "READY")


def list_drafts(session: Session, org: str) -> list[dict[str, Any]]:
    """Every draft in the organization, newest change first, with what each
    is waiting on.

    Shared by design: a quote started by one desk is on every desk's list, so
    a colleague can pick it up, price it, or send it once it is approved.
    ``createdBy`` and ``updatedBy`` say whose it was and who touched it last.

    Nothing here is cost. ``total`` is the quote's own selling total — the
    figure the customer would receive — and ``readiness`` is the send gate's
    own answer, which every role already reads from the gate endpoint.
    """
    rows = list(session.scalars(
        select(models.QuoteDraft)
        .where(models.QuoteDraft.organization_id == org,
               models.QuoteDraft.archived_at.is_(None))
        .order_by(models.QuoteDraft.updated_at.desc(),
                  models.QuoteDraft.sequence.desc())))
    names = _user_names(session, org, {r.salesperson_id for r in rows}
                        | {r.updated_by_user_id for r in rows})
    policy = approvals.get_policy(session, org)
    out = []
    for row in rows:
        quote = _to_quote(row)
        summary = quote.to_dict(False)["summary"]
        sent = quote_service.latest_document(session, org, quote_id=quote.id)
        out.append({
            "id": quote.id, "number": quote.number,
            "customer": quote.customer, "customerId": quote.customerId,
            "lineCount": len(quote.lines),
            "unpriced": summary["unpriced"],
            "total": summary["grand"],
            "readiness": readiness(session, org, quote, policy, sent),
            "sent": None if sent is None else {
                "number": sent.external_document_number,
                "systemLabel": conn.system_label_for(sent.external_system),
                "current": sent.fingerprint == store.priced_fingerprint(quote),
            },
            "createdBy": names.get(row.salesperson_id or "", ""),
            "updatedBy": names.get(row.updated_by_user_id or "", ""),
            "createdAt": clock.iso(row.created_at),
            "updatedAt": clock.iso(row.updated_at),
        })
    return out


def readiness(session: Session, org: str, quote: Quote,
              policy: models.OrgPolicy,
              sent: Optional[models.QuoteDocument]) -> str:
    """What this draft is waiting on — one of ``READINESS``.

    The same tests the send runs, in the same order: technical blockers and
    unpriced lines first (``store.blockers``, the unpriced check), then the
    approval gate (``approvals.quote_submission_block`` with the screen's own
    below-floor lines, exactly as ``create_estimate`` passes them). A draft
    this reports READY is one the send would accept; one it reports
    NEEDS_APPROVAL is one the send would refuse with 403. "Approved" is not a
    state of its own here because it is not one for the send either: an
    approved quote is a READY one, and an approval the price has since moved
    past is NEEDS_APPROVAL again.
    """
    if not quote.lines:
        return "EMPTY"
    if store.blockers(quote) or any(
            ln.supplyCode and ln.quoted is None for ln in quote.lines):
        return "NEEDS_ATTENTION"
    if not quote.has_customer:
        return "NO_CUSTOMER"
    if sent is not None and sent.fingerprint == store.priced_fingerprint(quote):
        return "SENT"
    if not policy.require_approval_for_quotes:
        return "READY"
    blocked = approvals.quote_submission_block(
        session, org, quote.id,
        also_requiring={ln.id: ln.reqCode
                        for ln in quote.lines if ln.economics().below_floor})
    if not blocked:
        return "READY"
    pending = session.scalar(
        select(func.count()).select_from(models.ApprovalRequest).where(
            models.ApprovalRequest.organization_id == org,
            models.ApprovalRequest.kind == ApprovalKind.QUOTE_LINE_PRICE.value,
            models.ApprovalRequest.subject_id == quote.id,
            models.ApprovalRequest.status == ApprovalStatus.PENDING.value))
    return "AWAITING_APPROVAL" if pending else "NEEDS_APPROVAL"


def _user_names(session: Session, org: str, ids: set[Optional[str]]) -> dict[str, str]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    return {u.user_id: u.name for u in session.scalars(
        select(models.User).where(models.User.organization_id == org,
                                  models.User.user_id.in_(wanted)))}
