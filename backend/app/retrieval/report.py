"""How the suggestion layers are doing, counted from the quotes a tenant kept.

Every stored quote line carries its candidates as they were offered — each
flagged with how it was found (the engine's ranking, description retrieval, a
confirmed code, a remembered phrase) — and how the supply was chosen: the
engine's own pick, a person choosing among the offers, or a person typing a
code nothing offered. That is enough to answer, per organization, the three
questions the next investments depend on:

* **Is retrieval finding what the ranking could not?** The share of a person's
  choices that took a retrieved candidate. High means the right record is
  being found beneath the ranking — the case for teaching the ranking the
  learned words.
* **Is there a meaning gap left?** The share of a person's choices that typed a
  code no candidate offered. High, after the vocabulary has been running,
  means the gap is meaning rather than spelling — the case for a trained
  embedder, once there are enough pairs.
* **How much has been learned?** Active phrase aliases and confirmed codes,
  and how many customers they cover — how much there is to train on.

Counts and ratios over rows; nothing here is interpreted. The thresholds the
docs name (a fifth of choices; two to three thousand pairs) are printed
beside the numbers as the reading, so the person looking does not have to
remember them.

    python -m app.retrieval.report [--org X] [--since YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, time, timezone
from typing import Any, Dict, Iterable, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import models

log = logging.getLogger("pie_portal.retrieval.report")

#: A person's choice among the offers, or typed in. ``AUTO`` is the engine's.
PERSON_SELECTED = ("USER", "MANUAL")
#: The share of choices taken from retrieval past which the ranking is the
#: bottleneck and worth teaching the learned words.
RANKING_TRIGGER = 0.2
#: The share of choices nothing offered past which the gap is meaning.
MEANING_TRIGGER = 0.2
#: Pairs a tenant needs before fine-tuning an embedder on them is sensible.
TRAINING_PAIRS = 2000


def _empty() -> Dict[str, int]:
    return {"lines": 0, "auto_selected": 0, "chosen_by_person": 0,
            "chosen_from_ranking": 0, "chosen_from_retrieval": 0,
            "chosen_from_confirmed_code": 0, "chosen_from_phrase": 0,
            "typed_unoffered": 0, "left_open": 0}


def count_lines(lines: Iterable[Mapping[str, Any]], into: Dict[str, int]) -> None:
    """Add one draft's customer-linked requirement lines to the counts."""
    for ln in lines:
        if not ln.get("customerScope") or ln.get("semantics") == "IDENTITY":
            continue
        into["lines"] += 1
        code = ln.get("supplyCode")
        if not code:
            into["left_open"] += 1
            continue
        if ln.get("sel") not in PERSON_SELECTED:
            into["auto_selected"] += 1
            continue
        into["chosen_by_person"] += 1
        chosen = next((c for c in ln.get("candidates") or []
                       if c.get("code") == code), None)
        if chosen is None:
            into["typed_unoffered"] += 1
        elif chosen.get("alias") and chosen.get("alias_kind") == "phrase":
            into["chosen_from_phrase"] += 1
        elif chosen.get("alias"):
            into["chosen_from_confirmed_code"] += 1
        elif chosen.get("retrieved"):
            into["chosen_from_retrieval"] += 1
        else:
            into["chosen_from_ranking"] += 1


def _share(part: int, whole: int) -> Optional[float]:
    """A ratio, or None when there is nothing to take a share of — never a
    zero that reads as "measured and found none"."""
    return round(part / whole, 3) if whole else None


def report_for(session: Session, organization_id: str,
               since: Optional[date] = None) -> Dict[str, Any]:
    counts = _empty()
    query = select(models.QuoteDraft).where(
        models.QuoteDraft.organization_id == organization_id)
    if since is not None:
        query = query.where(models.QuoteDraft.updated_at
                            >= datetime.combine(since, time.min, tzinfo=timezone.utc))
    drafts = 0
    for draft in session.scalars(query):
        drafts += 1
        count_lines(draft.lines or [], counts)

    aliases = list(session.scalars(select(models.CustomerPhraseAlias).where(
        models.CustomerPhraseAlias.organization_id == organization_id,
        models.CustomerPhraseAlias.active.is_(True))))
    codes = session.scalars(select(models.ConfirmedCodeMapping).where(
        models.ConfirmedCodeMapping.organization_id == organization_id,
        models.ConfirmedCodeMapping.active.is_(True))).all()

    chosen = counts["chosen_by_person"]
    found_beneath = (counts["chosen_from_retrieval"] + counts["chosen_from_confirmed_code"]
                     + counts["chosen_from_phrase"])
    retrieval_share = _share(found_beneath, chosen)
    unoffered_share = _share(counts["typed_unoffered"], chosen)
    pairs = len(aliases)

    readings = []
    if chosen == 0:
        readings.append("No choices by a person on customer-linked requirement "
                        "lines yet, so nothing can be read from the shares.")
    else:
        if retrieval_share is not None and retrieval_share >= RANKING_TRIGGER:
            readings.append(
                f"{found_beneath} of {chosen} choices took a record found beneath "
                f"the ranking: the ranking is the bottleneck, and teaching it the "
                f"learned words is worth doing.")
        else:
            readings.append(
                f"{found_beneath} of {chosen} choices took a record found beneath "
                f"the ranking, under the {RANKING_TRIGGER:.0%} that would make the "
                f"ranking the bottleneck.")
        if unoffered_share is not None and unoffered_share >= MEANING_TRIGGER:
            readings.append(
                f"{counts['typed_unoffered']} of {chosen} choices were typed in with "
                f"nothing offered: a meaning gap." +
                (f" With {pairs} pairs, a trained embedder is worth planning."
                 if pairs >= TRAINING_PAIRS else
                 f" With {pairs} pairs, there is not yet enough to train on "
                 f"({TRAINING_PAIRS} is the floor)."))
        else:
            readings.append(
                f"{counts['typed_unoffered']} of {chosen} choices were typed in with "
                f"nothing offered, under the {MEANING_TRIGGER:.0%} that would say "
                f"the gap is meaning.")

    return {
        "organization_id": organization_id,
        "since": since.isoformat() if since else None,
        "drafts": drafts,
        "counts": counts,
        "shares": {
            "found_beneath_ranking": retrieval_share,
            "typed_unoffered": unoffered_share,
            "auto_selected": _share(counts["auto_selected"], counts["lines"]),
        },
        "learned": {
            "phrase_aliases": pairs,
            "confirmed_codes": len(codes),
            "customers_with_aliases": len({a.identity_id for a in aliases}),
            "training_pairs_floor": TRAINING_PAIRS,
        },
        "triggers": {"ranking": RANKING_TRIGGER, "meaning": MEANING_TRIGGER},
        "readings": readings,
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--org", help="one organization id; default is every one")
    parser.add_argument("--since", type=date.fromisoformat,
                        help="only drafts updated on or after this date (YYYY-MM-DD)")
    args = parser.parse_args(argv)
    from ..db import SessionLocal  # noqa: PLC0415 — the CLI's own session

    session = SessionLocal()
    try:
        orgs = ([args.org] if args.org else sorted({
            o for o in session.scalars(select(models.QuoteDraft.organization_id).distinct())}))
        for org in orgs:
            print(json.dumps(report_for(session, org, since=args.since), indent=2))
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
