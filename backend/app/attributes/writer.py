"""The single writer of ``ProductAttributeValue``. Superseded, never mutated.

One function writes this table, for the reason ``attribution.ledger`` gives
about its own: a second writer is a second opinion about when a row stops being
true, and the two would disagree on the row nobody looks at.

The three outcomes are ``ledger.record``'s and ``enquiry.capture``'s, because a
re-runnable job needs all three:

* no live row for this (product, attribute, source) — append one;
* a live row saying the **same** thing — write nothing, and that is the guard
  working rather than a failure;
* a live row saying something **different** — stamp ``superseded_at`` on it and
  append the replacement. Both stay readable.

A fourth outcome is this table's own. The sources here are *complete* — one
decode of a name, or one catalogue record — so a field the source no longer
mentions is not silence, it is the source having spoken and not said it. Those
rows are superseded with no replacement, which leaves nothing live for that
field: the truthful state, because nothing is currently claimed. Without it a
corrected pack would leave a wrong value live forever, since the only code that
ever revisits a row is the code that writes a new one. It is off by default
(``retract_absent``) for the caller that has only part of a source's answer.

**Why the equality test is the value and not the row.** A rerun must leave the
table byte-identical or the history becomes a log of reruns, so ``decoder_version``
and ``source_ref`` are outside the comparison: a pack rebuild that changes no
value would otherwise supersede every row in the table, and a renamed item whose
name still decodes the same way would supersede its own decode. ``confidence``
*is* inside it — a pack that reads the same value with different certainty has
changed the evidence, and that is a new claim rather than churn. The cost, stated
because it is real: a live row's ``decoder_version`` names the decoder its value
last *changed* under, not the last one to agree with it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..domain import models
from .extract import MAX_TEXT_CHARS, AttributeClaim


@dataclass(frozen=True)
class WriteResult:
    """What one call did, counted rather than described.

    ``unchanged`` is the number that matters on a re-run: it should be the whole
    batch, and anything else means something moved.
    """

    created: int = 0
    superseded: int = 0
    unchanged: int = 0
    retracted: int = 0

    @property
    def wrote_nothing(self) -> bool:
        return not (self.created or self.superseded or self.retracted)

    def __add__(self, other: "WriteResult") -> "WriteResult":
        return WriteResult(
            created=self.created + other.created,
            superseded=self.superseded + other.superseded,
            unchanged=self.unchanged + other.unchanged,
            retracted=self.retracted + other.retracted,
        )


def live_values(session: Session, organization_id: str, product_id: str,
                source_kind: Optional[str] = None
                ) -> List[models.ProductAttributeValue]:
    """Every attribute currently claimed about one product, in this org.

    Org-scoped in the ``WHERE`` clause and not only by the policy. Row-level
    security exists on PostgreSQL alone, so on the development database these
    filters are the entire tenant boundary — the Phase 0 report's §5 point, and
    ``observability.health.check_tenant_isolation`` says the same thing to
    anybody reading a health response.
    """
    stmt = select(models.ProductAttributeValue).where(
        models.ProductAttributeValue.organization_id == organization_id,
        models.ProductAttributeValue.product_id == product_id,
        models.ProductAttributeValue.superseded_at.is_(None))
    if source_kind is not None:
        stmt = stmt.where(models.ProductAttributeValue.source_kind == source_kind)
    return list(session.scalars(stmt.order_by(
        models.ProductAttributeValue.source_kind,
        models.ProductAttributeValue.attribute_key)))


def _same_claim(row: models.ProductAttributeValue, claim: AttributeClaim) -> bool:
    return (row.value_num == claim.value_num
            and row.value_text == claim.value_text
            and row.original_value == claim.original_value
            and row.unit == claim.unit
            and row.confidence == claim.confidence)


def write_claims(session: Session, organization_id: str, product_id: str,
                 source_kind: str, claims: Iterable[AttributeClaim], *,
                 source_ref: Optional[str] = None,
                 decoder_version: Optional[str] = None,
                 retract_absent: bool = True) -> WriteResult:
    """Make this source's claims about one product current. Returns the counts.

    ``claims`` is everything ``source_kind`` says about this product right now.
    An empty iterable is therefore meaningful and not a no-op: it says the
    source spoke and named nothing, which retracts whatever it said last time.
    A caller that could not *ask* the source must not call this at all — that is
    silence, not an empty answer, and the two are the distinction
    ``DecodeRun.unavailable_reason`` exists to preserve.
    """
    ref = str(source_ref)[:MAX_TEXT_CHARS] if source_ref else None
    decoder = str(decoder_version)[:128] if decoder_version else None

    live: Dict[str, models.ProductAttributeValue] = {
        row.attribute_key: row
        for row in live_values(session, organization_id, product_id, source_kind)}

    now = clock.now()
    pending: List[AttributeClaim] = []
    created = superseded = unchanged = retracted = 0

    for claim in sorted(claims, key=lambda c: c.attribute_key):
        row = live.pop(claim.attribute_key, None)
        if row is not None:
            if _same_claim(row, claim):
                unchanged += 1
                continue
            row.superseded_at = now
            superseded += 1
        pending.append(claim)

    if retract_absent:
        for _key, row in sorted(live.items()):
            row.superseded_at = now
            retracted += 1

    if superseded or retracted:
        # The old rows must leave `uq_product_attribute_live` — unique over the
        # live rows only — before their replacements arrive, or one flush holds
        # two live rows for one key.
        #
        # Checked rather than assumed, and the assumption was backwards:
        # SQLAlchemy 2.0.52's `persistence.save_obj` emits a table's UPDATEs
        # *before* its INSERTs, so leaving these to one flush happens to work
        # today. That ordering is an internal of the unit of work and not part
        # of any contract, and a version that batched differently would fail
        # here on a partial index nothing else in the codebase depends on. The
        # explicit flush makes the guarantee this writer's own — and it is the
        # order `enquiry.capture` and `attribution.ledger` already write in.
        session.flush()

    for claim in pending:
        session.add(models.ProductAttributeValue(
            organization_id=organization_id,
            product_id=product_id,
            attribute_key=claim.attribute_key,
            value_num=claim.value_num,
            value_text=claim.value_text,
            original_value=claim.original_value,
            unit=claim.unit,
            source_kind=source_kind,
            source_ref=ref,
            confidence=claim.confidence,
            decoder_version=decoder,
            created_at=now,
        ))
        created += 1
    if pending:
        session.flush()

    return WriteResult(created=created, superseded=superseded,
                       unchanged=unchanged, retracted=retracted)
