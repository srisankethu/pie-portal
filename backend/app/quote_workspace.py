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
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import approvals, clock, memberships, quote_fields
from .commercial import quote_service
from .domain import models
from .domain.enums import ApprovalKind, ApprovalStatus, Role
from .ingestion import connections as conn
from .store import Line, Quote, store

log = logging.getLogger("pie_portal.quote_workspace")

#: The prefix every platform quote number carries. Kept from the builder's
#: original numbering so a saved link or a note that says "QB-…" still reads
#: as one of ours; only the part after the dash changed meaning.
NUMBER_PREFIX = "QB"

#: How long a blank, untouched form is left alone before the next New Quote
#: from the same person sweeps it up. Generous on purpose: the row costs
#: almost nothing and the failure mode on the other side is somebody's open
#: tab losing the id it holds.
_STALE_EMPTY_FORM_HOURS = 24

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
    return _mint(session, org, user_id=user_id, customer=customer,
                 customer_id=customer_id, connection_id=connection_id,
                 lines=[], fields={}, form_draft_id=None, updated_by=user_id)


def _mint(session: Session, org: str, *, user_id: Optional[str],
          customer: str, customer_id: Optional[str],
          connection_id: Optional[str], lines: list[dict[str, Any]],
          fields: dict[str, Any], form_draft_id: Optional[str],
          updated_by: Optional[str]) -> Quote:
    """Write the quote row, number and all. The one place a number is minted.

    Two callers: ``create`` starts an empty quote directly, and ``save_form``
    promotes a form somebody has filled in. They differ in what the row carries
    at birth and in nothing else, and a second copy of the retry loop is how
    two quotes eventually end up numbered differently for the same reason.
    """
    customer = (customer or "").strip()
    customer_id = customer_id or None
    for _ in range(_MINT_ATTEMPTS):
        seq = _next_sequence(session, org)
        row = models.QuoteDraft(
            quote_id=str(uuid.uuid4()), organization_id=org,
            customer_id=customer_id, customer_name=customer,
            salesperson_id=user_id, updated_by_user_id=updated_by,
            number=format_number(seq), sequence=seq,
            connection_id=connection_id,
            # The idempotency key for whatever ERP document this becomes. The
            # number is unique per organization now, but the tail stays: a
            # reference written into a shared ledger should not collide with
            # another tenant's ``QB-0042`` either.
            reference=f"{format_number(seq)}-{uuid.uuid4().hex[:8]}",
            form_draft_id=form_draft_id,
            lines=list(lines), fields=dict(fields),
            created_at=clock.now(), updated_at=clock.now())
        try:
            with session.begin_nested():
                session.add(row)
                session.flush()
        except IntegrityError:
            # Two constraints can land here and they mean opposite things.
            # Another desk taking this sequence is a retry: the savepoint has
            # rolled the insert back, so read again. Another *request saving
            # this same form* is not — it means the quote already exists, and
            # minting a second number for it is the duplicate this is here to
            # prevent.
            done = _promoted(session, org, form_draft_id) if form_draft_id else None
            if done is not None:
                return _to_quote(done)
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
# These two are the seam the whole unsaved-form change turns on. A quote and an
# open form are the same working object with the same operations performed on
# it — an RFQ read into lines, a supply chosen, a rate set — and they differ
# only in which table the object came from and goes back to. Resolving that
# here rather than at each endpoint is why the eighteen mutation routes in
# ``routers/quote.py`` needed no edit: they load, mutate and save exactly as
# they did, and never learn which kind they are holding.
def load(session: Session, org: str, quote_id: str,
         user_id: Optional[str] = None) -> Optional[Quote]:
    """The quote, or the open form under that id, if it is this tenant's.

    The org check is the whole of quote authorization — a foreign id and an
    absent one are both ``None``, so nothing here confirms that another
    organization's quote exists.

    A form is looked up second and additionally checked against ``user_id``:
    an unsaved form belongs to the person typing it. Callers that pass no
    ``user_id`` — the assessment path reading a cost, for instance — get the
    tenant check alone, which is the rule a saved quote has always had.
    """
    row = _row(session, org, quote_id)
    if row is not None:
        return _to_quote(row)
    form = _form_row(session, org, quote_id, user_id)
    return None if form is None else _to_unsaved(form)


def save(session: Session, quote: Quote, user_id: Optional[str]) -> None:
    """Write the working object back over its row.

    Every mutation endpoint ends here. The lines go back whole — cost and
    all, see ``Line.to_state`` — and the quote-level fields that can change
    after creation (the customer, and nothing else today) go with them.

    An unsaved form is written back to its own table, which is the one thing
    that makes editing one safe: the work survives a reload and a colleague's
    list never learns about it, because no quote exists yet.
    """
    if not quote.saved:
        _save_form(session, quote, user_id)
        return
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
    row.fields = dict(quote.fields)
    row.salesperson_id = quote.ownerId
    row.updated_by_user_id = user_id
    row.updated_at = now
    quote.savedAt = clock.iso(now)
    session.flush()


def _save_form(session: Session, quote: Quote, user_id: Optional[str]) -> None:
    row = _form_row(session, quote.organizationId, quote.id, user_id)
    if row is None:
        log.warning("form %s vanished before it could be written", quote.id)
        return
    row.customer_id = quote.customerId or None
    row.customer_name = quote.customer
    row.lines = quote.lines_state()
    row.fields = dict(quote.fields)
    row.updated_at = clock.now()
    # `savedAt` stays None. It means "when the server last wrote this quote's
    # row", and the chip that reads it says "Saved 10:42" — which is exactly
    # what an unsaved form must not claim.
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


@dataclass(frozen=True)
class LineCostBasis:
    """The two costs a quote line can hold, kept apart.

    ``system`` is what the books returned for the item; ``custom`` is what a
    person sourced for this deal. They are separate fields rather than one
    resolved number because the assessment has to *say* which answered — a
    cost with no traceable origin is what §1 forbids, and "a manager typed
    it" and "a bill says so" are different claims about the same rupees.
    """

    system: Optional[Decimal] = None
    custom: Optional[Decimal] = None


def line_cost_basis(session: Session, org: str, quote_id: str,
                    line_id: str) -> LineCostBasis:
    """Both of one quote line's costs, in one load.

    One accessor rather than two, because the callers read them together and
    ``load`` walks the whole quote: a second call per line doubles that on a
    forty-line assessment to answer half a question.
    """
    quote = load(session, org, quote_id)
    if quote is None:
        return LineCostBasis()
    line = next((row for row in quote.lines if row.id == line_id), None)
    if line is None:
        return LineCostBasis()
    return LineCostBasis(system=_money(line.cost), custom=_money(line.customCost))


def _money(value: Optional[float]) -> Optional[Decimal]:
    # `Decimal(str(...))` rather than `Decimal(float)`: money is Decimal (§1),
    # and the binary-float detour is how 420.0 becomes 419.99999999999994.
    return None if value is None else Decimal(str(value))


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
    return line_cost_basis(session, org, quote_id, line_id).system


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
        savedAt=clock.iso(row.updated_at),
        ownerId=row.salesperson_id, fields=dict(row.fields or {}),
        saved=True)


# ── the unsaved form ─────────────────────────────────────────────────────────
# A quote used to become a row, with a number, the instant somebody pressed
# "New quote": open the builder and close it again and the desk's shared list
# had an empty QB-0042 in it for good. What follows is the other half of the
# lifecycle — a form somebody has open, which is not yet a quote and is not in
# any listing — and ``save_form`` is where it becomes one.
#
# ``models.QuoteFormDraft`` says why the scratch is on the server rather than in
# the browser; the short version is that a browser-held draft would carry cost
# to a salesperson or carry none at all, and §1 refuses both.
def create_form(session: Session, org: str, *, user_id: Optional[str],
                customer: str = "", customer_id: Optional[str] = None,
                connection_id: Optional[str] = None) -> Quote:
    """Open a blank form. No number is minted and no quote exists yet.

    Collects this person's abandoned *empty* forms on the way through — a tab
    closed on an untouched form leaves a row nothing will ever discard, and one
    holding no customer, no lines and no fields is carrying nothing anybody
    could want back. Two conditions, and both are needed:

    * **Empty.** A form with anything in it is left alone, always. Losing typed
      work to a housekeeping rule would be far worse than the row it saves.
    * **Stale.** A blank form this person opened minutes ago is very likely a
      second tab they are about to paste into, and collecting it would break
      that tab on the next keystroke — its id would no longer resolve. Only one
      left untouched for ``_STALE_EMPTY_FORM_HOURS`` is treated as abandoned.
    """
    _collect_empty_forms(session, org, user_id)
    row = models.QuoteFormDraft(
        form_draft_id=str(uuid.uuid4()), organization_id=org,
        owner_user_id=user_id,
        customer_id=(customer_id or None), customer_name=(customer or "").strip(),
        connection_id=connection_id,
        lines=[], fields={}, created_at=clock.now(), updated_at=clock.now())
    session.add(row)
    session.flush()
    return _to_unsaved(row)


def _collect_empty_forms(session: Session, org: str,
                         user_id: Optional[str]) -> None:
    if not user_id:
        return
    cutoff = clock.now() - timedelta(hours=_STALE_EMPTY_FORM_HOURS)
    rows = session.scalars(
        select(models.QuoteFormDraft).where(
            models.QuoteFormDraft.organization_id == org,
            models.QuoteFormDraft.owner_user_id == user_id))
    for row in rows:
        if row.lines or row.fields or row.customer_name or row.customer_id:
            continue
        if (clock.aware(row.updated_at) or cutoff) > cutoff:
            continue
        session.delete(row)


def discard_form(session: Session, org: str, form_id: str,
                 user_id: Optional[str]) -> bool:
    """Throw the form away. False when there was none to throw.

    A real delete rather than the archive stamp ``delete`` uses on a quote:
    that stamp exists to keep a minted number from being handed out twice, and
    a form has no number. There is nothing here to preserve.
    """
    row = _form_row(session, org, form_id, user_id)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def save_form(session: Session, org: str, form_id: str,
              *, user_id: Optional[str]) -> Quote:
    """Promote a form to a quote: mint the number, write the row, drop the form.

    **Idempotent, and that is the whole of duplicate-save prevention.** The
    quote records which form it came from under a unique constraint, so a
    second click, a double-submit or a retried request finds the quote already
    made and returns it. Two requests racing cannot both win the constraint;
    the loser re-reads and answers with the same quote, which is what the first
    caller got. Nothing here depends on the button being disabled.

    Raises ``FormNotFound`` when the form is neither open nor already saved —
    the caller turns that into a 404.
    """
    already = _promoted(session, org, form_id)
    if already is not None:
        return _to_quote(already)

    row = _form_row(session, org, form_id, user_id)
    if row is None:
        raise FormNotFound(form_id)

    lines = list(row.lines or [])
    fields = dict(row.fields or {})
    customer = row.customer_name or ""
    customer_id = row.customer_id or None
    connection_id = row.connection_id
    owner = row.owner_user_id or user_id

    quote = _mint(session, org, user_id=owner, customer=customer,
                  customer_id=customer_id, connection_id=connection_id,
                  lines=lines, fields=fields, form_draft_id=form_id,
                  updated_by=user_id)
    session.delete(row)
    session.flush()
    return quote


class FormNotFound(LookupError):
    """No open form under that id, and no quote already saved from one."""


def _promoted(session: Session, org: str,
              form_id: str) -> Optional[models.QuoteDraft]:
    if not form_id:
        return None
    return session.scalar(
        select(models.QuoteDraft).where(
            models.QuoteDraft.form_draft_id == form_id,
            models.QuoteDraft.organization_id == org))


def _form_row(session: Session, org: str, form_id: str,
              user_id: Optional[str]) -> Optional[models.QuoteFormDraft]:
    """The form, only if it is this tenant's and this person's.

    Both checks, and the second is not the quote rule relaxed — it is the
    opposite question. A *quote* is the desk's and a colleague may pick it up;
    a form is one person typing, and there is nothing to hand over until it has
    been saved.
    """
    if not form_id:
        return None
    row = session.get(models.QuoteFormDraft, form_id)
    if row is None or row.organization_id != org:
        return None
    if row.owner_user_id and user_id and row.owner_user_id != user_id:
        return None
    return row


def _to_unsaved(row: models.QuoteFormDraft) -> Quote:
    """The form as the working object, wearing no number.

    ``saved=False`` is a field rather than something a reader infers from an
    empty ``number``: the producer knows which of the two tables answered and
    a consumer re-deriving that from the output shape would be guessing.
    """
    return Quote(
        id=row.form_draft_id, customer=row.customer_name or "", number="",
        organizationId=row.organization_id, connectionId=row.connection_id,
        customerId=row.customer_id or None, reference="",
        lines=[Line.from_state(state) for state in (row.lines or [])],
        savedAt=None, ownerId=row.owner_user_id,
        fields=dict(row.fields or {}), saved=False)


# ── ownership ────────────────────────────────────────────────────────────────
def may_edit(quote: Quote, *, user_id: str, role: Role,
             policy: models.OrgPolicy) -> bool:
    """Whether this person may change this quote.

    The owner always may. A manager or owner of the organization may when the
    policy says so (``managers_may_edit_any_quote``, on by default). Nobody
    else: a colleague may read a draft, price nothing on it, and ask the owner.
    A draft with no owner on record — one from before ownership was kept —
    is editable by anyone who could read it, which is what it always was.
    """
    if quote.ownerId is None or quote.ownerId == user_id:
        return True
    return (role in (Role.SALES_MANAGER, Role.OWNER)
            and bool(policy.managers_may_edit_any_quote))


def set_owner(session: Session, org: str, quote: Quote, new_owner_id: str,
              *, by_user_id: str) -> str:
    """Hand the quote to another member of the organization; returns their name.

    The new owner must hold an active membership here — a quote handed to
    somebody who cannot open it is a quote nobody can change.
    """
    member = next((u for u in memberships.users_in(session, org)
                   if u.user_id == new_owner_id), None)
    if member is None:
        raise LookupError("That person is not a member of this organization.")
    quote.ownerId = member.user_id
    save(session, quote, by_user_id)
    return member.name or ""


def assignees(session: Session, org: str) -> list[dict[str, str]]:
    """Who a quote can be handed to: the organization's active members."""
    return [{"id": u.user_id, "name": u.name or ""}
            for u in memberships.users_in(session, org)]


# ── the list ─────────────────────────────────────────────────────────────────
#: What a draft is waiting on. The client maps these to words; the order here
#: is the order they are decided in, and the first that applies wins.
READINESS = ("EMPTY", "NEEDS_ATTENTION", "MISSING_DETAILS", "NO_CUSTOMER", "SENT",
             "AWAITING_APPROVAL", "NEEDS_APPROVAL", "READY")


def list_drafts(session: Session, org: str, *, user_id: str = "",
                role: Optional[Role] = None) -> list[dict[str, Any]]:
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
    defs = quote_fields.definitions_for(session, org)
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
            "readiness": readiness(session, org, quote, policy, sent, defs),
            "ownerId": quote.ownerId,
            "owner": names.get(quote.ownerId or "", ""),
            "canEdit": (may_edit(quote, user_id=user_id, role=role, policy=policy)
                        if role is not None else False),
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
              sent: Optional[models.QuoteDocument],
              defs: Optional[list[models.QuoteFieldDefinition]] = None) -> str:
    """What this draft is waiting on — one of ``READINESS``.

    The same tests the send runs, in the same order: technical blockers and
    unpriced lines first (``store.blockers``, the unpriced check), then the
    organization's mandatory fields (``quote_fields.missing_required``), then
    the customer, then the approval gate (``approvals.quote_submission_block`` with the screen's own
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
    if defs is None:
        defs = quote_fields.definitions_for(session, org)
    if quote_fields.missing_required(defs, quote.fields):
        return "MISSING_DETAILS"
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
