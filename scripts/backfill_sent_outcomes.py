#!/usr/bin/env python3
"""Open the outcome row and the ERP link for every quote sent while the send could not.

Between the merge of #223 (2026-09-05) and Phase 0 of
``docs/quote-lifecycle-plan.md``, ``POST /api/v1/quotes/{id}/estimate`` wrote
its ``quote_documents`` row and then failed — silently, into a log line — to
move the quote's outcome to SENT or to record which ERP document the quote had
become. Those quotes have a document row and either no outcome row at all, or
one still at DRAFT with no document reference. Nothing on screen could mark
them won.

This repairs exactly those, through the function the send itself uses
(``quote_service.set_outcome``), and touches nothing else:

- a quote with a document and **no outcome row** → SENT, pointing at the newest
  document, with ``sent_at`` set to the document's ``written_at`` — the send
  happened then, not now;
- a quote whose row is **DRAFT with no reference** → SENT, same pointer;
- everything else is left alone and listed. A row already SENT, WON or LOST
  keeps its status (the link is filled in if it is empty); a row pointing at a
  *different* document is a fact a person recorded and is reported, never
  moved; a reference another row already holds is reported for a person to
  reconcile.

**Dry run by default.** ``--apply`` writes, one commit per organization.
Idempotent: a second ``--apply`` finds nothing to do.

    python3 scripts/backfill_sent_outcomes.py                 # what would change
    python3 scripts/backfill_sent_outcomes.py --apply         # do it
    python3 scripts/backfill_sent_outcomes.py --org org_pie   # one organization

Deliberately a script an owner runs and not a migration: migrations here are
literal schema, never data, and a repair of human-lifecycle rows belongs where a
person can read the list first (CLAUDE.md §4).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select                              # noqa: E402

from app.commercial import quote_service                   # noqa: E402
from app.db import SessionLocal                            # noqa: E402
from app.domain import models                              # noqa: E402
from app.domain.enums import QuoteLossReason, QuoteOutcomeStatus  # noqa: E402


def _newest_documents(session, org: str | None) -> dict[tuple[str, str], models.QuoteDocument]:
    """The newest document per (org, quote) — the one the outcome should name."""
    stmt = select(models.QuoteDocument).order_by(
        models.QuoteDocument.written_at.desc(),
        models.QuoteDocument.quote_document_id.desc())
    if org:
        stmt = stmt.where(models.QuoteDocument.organization_id == org)
    newest: dict[tuple[str, str], models.QuoteDocument] = {}
    for doc in session.scalars(stmt):
        newest.setdefault((doc.organization_id, doc.quote_id), doc)
    return newest


def _draft(session, org: str, quote_id: str) -> models.QuoteDraft | None:
    return session.get(models.QuoteDraft, quote_id) if quote_id else None


def plan(session, org: str | None) -> list[dict]:
    """One entry per sent quote: what is there, and what would be done."""
    out: list[dict] = []
    for (o, quote_id), doc in _newest_documents(session, org).items():
        row = quote_service.get_outcome(session, o, quote_id)
        draft = _draft(session, o, quote_id)
        entry = {
            "org": o, "quote_id": quote_id,
            "number": draft.number if draft is not None else "?",
            "document": doc.external_document_number or doc.external_document_id or "?",
            "document_id": doc.external_document_id, "written_at": doc.written_at,
            "status": row.status if row is not None else None,
            "ref": row.quote_document_ref if row is not None else None,
            # Carried so ``apply`` can put back what ``set_outcome`` re-stamps.
            # See the comment on the restore, which is where the reason is.
            "decided_at": row.decided_at if row is not None else None,
            "loss_reason": row.loss_reason if row is not None else None,
            "lost_to": row.lost_to if row is not None else None,
        }
        if not doc.external_document_id:
            entry["action"] = "skip: document row carries no ERP id"
        elif row is None:
            entry["action"] = "open SENT and link"
        elif row.status == QuoteOutcomeStatus.DRAFT.value and row.quote_document_ref is None:
            entry["action"] = "DRAFT -> SENT and link"
        elif row.quote_document_ref is None:
            entry["action"] = f"keep {row.status}, fill link"
        elif row.quote_document_ref != doc.external_document_id:
            entry["action"] = (f"REPORT: outcome names {row.quote_document_ref}, newest "
                               f"document is {doc.external_document_id} — a person decides")
        else:
            entry["action"] = "nothing to do"
        out.append(entry)
    return out


def apply(session, entries: list[dict]) -> int:
    """Perform the plan through ``set_outcome``. Returns how many rows changed.

    The whole job on a "keep" entry is to fill an empty link, and
    ``set_outcome`` is the one writer, so that is what it goes through. But
    ``set_outcome`` is the *decision* writer: called with a status a row
    already holds, it re-runs the decision's side effects. Two of them would
    quietly rewrite a person's record, so both are handled here rather than by
    weakening the function every other caller depends on.

    Refusals are caught per entry and reported. One unrepairable row must not
    abort the run: a LOST row whose reason predates the vocabulary raises
    ``MissingLossReason`` before ``set_outcome`` touches anything, and
    uncaught that took every other repair in the same organization down with
    it, before the commit, with a traceback instead of a list.
    """
    changed = 0
    for e in entries:
        if not (e["action"].startswith("open") or e["action"].startswith("DRAFT")
                or e["action"].startswith("keep")):
            continue
        draft = _draft(session, e["org"], e["quote_id"])
        status = (QuoteOutcomeStatus.SENT if e["status"] in (None, "DRAFT")
                  else QuoteOutcomeStatus(e["status"]))
        # Handed back, never invented: on the LOST edge ``set_outcome`` rewrites
        # BOTH ``loss_reason`` and ``lost_to`` from what it was given, so
        # passing neither would erase the reason a person chose and the
        # competitor they typed. These are those same two values, read off the
        # row in ``plan``. A row holding no reason still refuses below, which is
        # correct — a lost quote with no reason on record is a person's decision
        # to make, not a repair's.
        #
        # ``lost_to`` is the half this originally missed, and a test caught it:
        # preserving the reason alone still blanked the competitor, which is the
        # one field the whole competitor mix is built from.
        reason, lost_to = None, None
        if status is QuoteOutcomeStatus.LOST:
            lost_to = e["lost_to"]
            if e["loss_reason"]:
                try:
                    reason = QuoteLossReason(e["loss_reason"])
                except ValueError:
                    reason = None
        try:
            row = quote_service.set_outcome(
                session, e["org"], quote_id=e["quote_id"],
                quote_document_ref=e["document_id"], status=status,
                customer_ref=(draft.customer_name if draft is not None else ""),
                customer_id=(draft.customer_id if draft is not None else None),
                loss_reason=reason, lost_to=lost_to, user_id=None)
        except (quote_service.QuoteOutcomeRepointed,
                quote_service.MissingLossReason,
                quote_service.InvalidTransition) as exc:
            e["action"] = f"REPORT: {exc}"
            continue
        # The send happened when the document was written, and the row must
        # say so — ``set_outcome`` stamps "now" on a fresh SENT, which would
        # date every repaired quote to the day of the repair.
        if status is QuoteOutcomeStatus.SENT and e["status"] in (None, "DRAFT"):
            row.sent_at = e["written_at"]
        # And the decision happened when the person recorded it. ``set_outcome``
        # stamps ``decided_at`` on every WON or LOST call, including one that
        # merely restates the status the row already holds — so filling the link
        # on an already-won quote moved its decision date to the day of the
        # repair. Every win-rate window and every "decided in this period"
        # count reads that date, so the repair would have silently re-dated the
        # book's history while reporting itself as a link fill.
        if e["decided_at"] is not None:
            row.decided_at = e["decided_at"]
        changed += 1
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="write the repairs (default: print what would change)")
    ap.add_argument("--org", default=None, help="one organization id; default all")
    args = ap.parse_args()

    with SessionLocal() as session:
        entries = plan(session, args.org)
        if not entries:
            print("No sent quotes found.")
            return 0
        width = max(len(e["number"]) for e in entries)
        for e in entries:
            print(f"{e['org']:<12} {e['number']:<{width}}  {e['document']:<14}  "
                  f"{str(e['status'] or '—'):<6}  {e['action']}")
        todo = [e for e in entries if not e["action"].startswith(("nothing", "REPORT", "skip"))]
        reports = [e for e in entries if e["action"].startswith("REPORT")]
        print(f"\n{len(entries)} sent quote(s): {len(todo)} to repair, "
              f"{len(reports)} for a person to look at.")
        if not args.apply:
            print("Dry run — nothing written. Re-run with --apply to write.")
            return 0
        changed = apply(session, entries)
        session.commit()
        print(f"Repaired {changed} outcome row(s).")
        for e in entries:
            if e["action"].startswith("REPORT"):
                print(f"  {e['org']} {e['number']}: {e['action']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
