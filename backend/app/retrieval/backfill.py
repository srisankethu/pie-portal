"""Teach the vocabulary from the quotes a tenant already has.

The learned vocabulary (``vocabulary.py``) and the phrase aliases it is built
from start empty and fill as people select supply on quotes. A tenant that has
been quoting through this platform for a year already made those choices —
they are in ``quote_drafts.lines`` as ``store.Line.to_state`` wrote them: the
request as typed, the customer scope it was resolved under, and the record a
person put on the line. This replays them through the same writer the quote
screen uses, ``identity.service.record_phrase_alias``, under the same rules
(a requirement line, a linked customer, a person's selection, not the line
itself), so a backfilled alias is indistinguishable from one recorded live
except for its ``source_ref``.

What it does not read, and why: ``enquiry`` lines carry a customer's words and
a disposition ("QUOTED", "DECLINED"), but no record — the disposition names a
quote, not a product — so they are not pairs. The quote is where the product
is, and that is what this reads.

Idempotent: a second run records nothing new, because the writer is.

    python -m app.retrieval.backfill            # every organization
    python -m app.retrieval.backfill --org X    # one
    python -m app.retrieval.backfill --dry-run  # count, write nothing
"""
from __future__ import annotations

import argparse
import logging
from typing import Any, Dict, Iterable, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import models
from ..identity import service as identity_service

log = logging.getLogger("pie_portal.retrieval.backfill")

#: A person's selections. ``AUTO`` is the engine's own pick and teaches
#: nothing about words a person would not have chosen themselves.
PERSON_SELECTED = ("USER", "MANUAL")


def pairs_in(lines: Iterable[Mapping[str, Any]]) -> Iterable[Dict[str, str]]:
    """The lines of one draft that are a person's choice on a requirement
    for a linked customer — the same rule ``routers.quote._learn_phrase``
    applies live, restated here on the stored shape."""
    for ln in lines:
        scope = ln.get("customerScope")
        phrase = ln.get("reqCode") or ""
        code = ln.get("supplyCode") or ""
        if (not scope or not phrase or not code or code == phrase
                or ln.get("semantics") == "IDENTITY"
                or ln.get("sel") not in PERSON_SELECTED):
            continue
        yield {"scope": scope, "phrase": phrase, "code": code,
               "line_id": str(ln.get("id") or "")}


def backfill_from_drafts(session: Session, organization_id: Optional[str] = None,
                         dry_run: bool = False) -> Dict[str, int]:
    """Record a phrase alias for every qualifying line of every draft.

    Returns counts: drafts read, lines that qualified, aliases newly recorded
    (a line whose alias already exists counts as qualified, not recorded).
    """
    query = select(models.QuoteDraft)
    if organization_id:
        query = query.where(models.QuoteDraft.organization_id == organization_id)
    drafts = 0
    qualified = 0
    recorded = 0
    for draft in session.scalars(query.order_by(models.QuoteDraft.quote_id)):
        drafts += 1
        for pair in pairs_in(draft.lines or []):
            qualified += 1
            if dry_run:
                continue
            before = session.query(models.CustomerPhraseAlias).count()
            row = identity_service.record_phrase_alias(
                session, draft.organization_id,
                identity_id=pair["scope"], phrase=pair["phrase"],
                target_record_id=pair["code"],
                source_ref=f"backfill quote {draft.quote_id} line {pair['line_id']}",
                user_id=draft.updated_by_user_id)
            if row is not None and session.query(models.CustomerPhraseAlias).count() > before:
                recorded += 1
    if not dry_run:
        session.commit()
    return {"drafts": drafts, "qualified": qualified, "recorded": recorded}


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--org", help="one organization id; default is all")
    parser.add_argument("--dry-run", action="store_true",
                        help="count what would be recorded and write nothing")
    args = parser.parse_args(argv)
    from ..db import SessionLocal  # noqa: PLC0415 — the CLI's own session

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    session = SessionLocal()
    try:
        counts = backfill_from_drafts(session, args.org, dry_run=args.dry_run)
    finally:
        session.close()
    print(f"drafts read: {counts['drafts']}  lines qualifying: {counts['qualified']}  "
          f"aliases {'that would be ' if args.dry_run else ''}recorded: {counts['recorded']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
