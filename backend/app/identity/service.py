"""The identity layer: link records, never merge them.

Every connector — Zoho today, Tally and ERPNext next — hands its records to
``ingest_customer`` / ``ingest_item`` and gets back an identity. That is the
whole integration surface. A new connector needs an importer and nothing else:
there is no connector name anywhere in this module or in ``matchers.py``, and a
rule that needed one would be in the wrong place.

**Connector records are immutable source data.** They are updated in place from
their own connector and from nowhere else. Nothing here writes a field of one
connector's record from another connector's value, and nothing deletes a record
because a different system disagrees with it. That is the property that makes a
figure on screen traceable to the system it came from, and it is the reason this
is a linking layer rather than a de-duplicating one.

**Linking asks first.** An exact GSTIN match is strong evidence, not proof — a
group can register several trading names against one GSTIN. Automatic linking is
therefore off unless an owner turns it on, and an import that finds a match
records a suggestion instead of acting. The asymmetry is deliberate: an
unreviewed suggestion costs a click, while an unreviewed merge costs the
evidence needed to notice it was wrong.

Every link, unlink and decision is appended to ``IdentityEvent``. "Who decided
these were the same company, and when?" is the first question asked of a link
somebody disagrees with, and it must be answerable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import models
from .matchers import (NAME_STRATEGY, Candidate, RecordFacts, find_candidates,
                       has_eligible_key, normalize_gstin, normalize_name,
                       normalize_sku)

log = logging.getLogger("pie_portal.identity")

CUSTOMER = "CUSTOMER"
ITEM = "ITEM"

#: Which model plays which role for each entity type. This is the only place
#: the two entity kinds differ, which keeps the resolution logic itself single.
_SHAPES: dict[str, dict[str, Any]] = {
    CUSTOMER: {
        "identity": models.CustomerIdentity,
        "record": models.CustomerConnectorRecord,
        "key": "gstin",
        # The column a name-based proposal reads, and whether it may.
        "text": "name",
        # A business name identifies the business. Two records reading
        # "Pitti Engineering Ltd" are one buyer typed twice, which is the gap the
        # NAME strategy exists to close.
        "name_match": True,
        "auto_flag": "auto_link_customers",
    },
    ITEM: {
        "identity": models.ItemIdentity,
        "record": models.ItemConnectorRecord,
        "key": "sku",
        "text": "description",
        # An item description does *not* identify the item. "Milling insert"
        # describes hundreds of distinct parts, so matching on it would fill the
        # review queue with proposals that are wrong more often than right — and
        # a queue that is usually wrong is one people learn to scroll past, which
        # costs the GSTIN and SKU proposals their audience too. A SKU is the
        # item's identifier; where there is none, there is nothing to propose.
        "name_match": False,
        "auto_flag": "auto_link_items",
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class IdentityError(ValueError):
    """A link that would break the layer's own guarantees."""


@dataclass
class Ingested:
    """What an import did, in terms a caller can act on."""

    record: Any
    identity_id: str
    created_identity: bool
    linked: bool                      # joined an existing identity outright
    suggestions: list[Candidate]      # matches recorded for review instead


# ── policy ──────────────────────────────────────────────────────────────────
def get_policy(session: Session, organization_id: str) -> models.IdentityPolicy:
    row = session.get(models.IdentityPolicy, organization_id)
    if row is None:
        row = models.IdentityPolicy(organization_id=organization_id)
        session.add(row)
        session.flush()
    return row


# ── audit ───────────────────────────────────────────────────────────────────
def record_event(session: Session, organization_id: str, *, entity_type: str,
                 identity_id: str, action: str, actor: str,
                 record_id: Optional[str] = None, detail: str = "") -> None:
    session.add(models.IdentityEvent(
        organization_id=organization_id, entity_type=entity_type,
        identity_id=identity_id, record_id=record_id, action=action,
        actor=actor or "SYSTEM", detail=detail[:512]))


# ── the lookup a strategy is given ──────────────────────────────────────────
class _Lookup:
    """Answers "which identities already hold a record with this key value?".

    Scoped to one organization and one entity type, so a strategy cannot reach
    across tenants or confuse a customer with an item. Only linked, active
    identities are returned — a retired one is not a candidate.
    """

    def __init__(self, session: Session, entity_type: str) -> None:
        self.s = session
        self.shape = _SHAPES[entity_type]

    def identities_by_key(self, organization_id: str, key: str, value: str,
                          exclude_record_id: str) -> list[str]:
        if key != self.shape["key"] or not value:
            return []
        record = self.shape["record"]
        identity = self.shape["identity"]
        rows = self.s.execute(
            select(record.identity_id)
            .join(identity, identity.identity_id == record.identity_id)
            .where(record.organization_id == organization_id,
                   getattr(record, key) == value,
                   record.record_id != exclude_record_id,
                   identity.active.is_(True))
            .distinct()).scalars().all()
        return list(rows)

    def identities_by_name(self, organization_id: str, name: str,
                           exclude_record_id: str) -> list[str]:
        """Same question, on a value that is not stored in canonical form.

        A GSTIN is normalised on the way in and compared in SQL. A name is not:
        the record keeps exactly what its connector called it, because a record
        rewritten to match another system's spelling is the one thing this module
        must never do. So the comparison happens here, over the org's records for
        this entity type.

        That is a scan, and it is bounded by being asked only for a record with no
        eligible key — `by_name` returns nothing otherwise, so a book whose
        customers all carry a GSTIN never reaches this. A book with tens of
        thousands of keyless records on a first full sync would want a stored
        normalised column and an index on it; that is a migration, and it is not
        worth one before a book needs it.
        """
        if not name:
            return []
        record = self.shape["record"]
        identity = self.shape["identity"]
        text_col = getattr(record, self.shape["text"])
        rows = self.s.execute(
            select(record.identity_id, text_col)
            .join(identity, identity.identity_id == record.identity_id)
            .where(record.organization_id == organization_id,
                   record.record_id != exclude_record_id,
                   identity.active.is_(True))).all()
        # A set, then sorted: two records of one identity both matching must not
        # propose it twice, and the order has to be stable or the same sync would
        # produce suggestions in a different order each run.
        return sorted({iid for iid, text in rows if normalize_name(text) == name})


# ── ingestion: the one entry point a connector uses ─────────────────────────
def ingest(session: Session, organization_id: str, *, entity_type: str,
           connector: str, external_id: str,
           connection_id: Optional[str] = None,
           name: str = "", gstin: Optional[str] = None,
           sku: Optional[str] = None, description: str = "",
           source_ref: Optional[dict] = None,
           local_id: Optional[str] = None) -> Ingested:
    """Upsert one connector record and resolve its identity.

    The order is the one the brief describes, and each step exists to stop a
    specific failure:

    1. Already mapped? Update that record and stop. A record that has been
       reviewed and linked must not be re-resolved on every sync, or a human
       decision would be silently re-litigated nightly.
    2. Otherwise create it, and give it its own identity immediately, so it is
       addressable and its data is never in limbo.
    3. Then look for matches. Link outright only if the organization has
       switched that on; otherwise record a suggestion.
    """
    shape = _SHAPES[entity_type]
    record_cls, identity_cls = shape["record"], shape["identity"]
    source_ref = source_ref or {}

    gstin_n = normalize_gstin(gstin)
    sku_n = normalize_sku(sku)

    existing = session.scalars(
        select(record_cls).where(
            record_cls.organization_id == organization_id,
            record_cls.connector == connector,
            record_cls.connection_id == connection_id,
            record_cls.external_id == str(external_id))).first()

    if existing is not None:
        # Step 1 — its own connector's data, and only its own.
        if entity_type == CUSTOMER:
            existing.name = name or existing.name
            existing.gstin = gstin_n or existing.gstin
            if local_id:
                existing.customer_id = local_id
        else:
            existing.sku = sku_n or existing.sku
            existing.description = description or name or existing.description
            if local_id:
                existing.product_id = local_id
        if source_ref:
            existing.source_ref = source_ref
        existing.last_synced_at = _now()
        session.flush()
        return Ingested(record=existing, identity_id=existing.identity_id,
                        created_identity=False, linked=False, suggestions=[])

    # Step 2 — a new record always gets an identity of its own first. Creating
    # it unattached and hoping a match turns up would leave rows the rest of the
    # platform cannot reference.
    identity = identity_cls(organization_id=organization_id)
    session.add(identity)
    session.flush()

    # Built from the fields the two shapes share, then completed per shape. A
    # customer record carries a name and a GSTIN; an item record a SKU and a
    # description. Passing one shape's fields to the other is how a generic
    # constructor quietly stops being generic.
    record = record_cls(
        organization_id=organization_id, identity_id=identity.identity_id,
        connector=connector, connection_id=connection_id,
        external_id=str(external_id), source_ref=source_ref,
        last_synced_at=_now())
    if entity_type == CUSTOMER:
        record.name = name
        record.gstin = gstin_n
        record.customer_id = local_id
    else:
        record.sku = sku_n
        record.description = description or name
        record.product_id = local_id
    session.add(record)
    session.flush()

    record_event(session, organization_id, entity_type=entity_type,
                 identity_id=identity.identity_id, record_id=record.record_id,
                 action="CREATED", actor="SYSTEM",
                 detail=f"{connector} {external_id}")

    # Step 3 — look for an existing identity to join.
    facts = RecordFacts(
        organization_id=organization_id, record_id=record.record_id,
        keys={"gstin": gstin_n, "sku": sku_n},
        # Empty unless this shape's free text is identity-bearing. Decided here
        # rather than inside the strategy: `matchers.py` must not branch on which
        # entity type it is looking at, and "is this text an identifier" is a
        # property of the shape, not of the matching rule.
        text=(name or description) if shape["name_match"] else "")
    candidates = [c for c in find_candidates(facts, _Lookup(session, entity_type))
                  if c.identity_id != identity.identity_id]

    if not candidates:
        return Ingested(record=record, identity_id=identity.identity_id,
                        created_identity=True, linked=False, suggestions=[])

    policy = get_policy(session, organization_id)
    # Automatic linking applies to exact identifiers only. A name match is a
    # question for a person however the policy is set: the switch an owner turned
    # on says "link on an exact match", and its own help text calls that
    # "unrecoverable when the match was a group trading under one registration" —
    # which is a far better bet than two businesses that happen to share a name.
    # Checked here rather than by omitting the candidate, so the suggestion is
    # still recorded and the reviewer still sees it.
    auto = [c for c in candidates if c.strategy != NAME_STRATEGY]
    if auto and getattr(policy, shape["auto_flag"], False):
        best = auto[0]
        link_record(session, organization_id, entity_type=entity_type,
                    record_id=record.record_id, identity_id=best.identity_id,
                    actor="AUTO", detail=f"{best.strategy}: {best.evidence}")
        return Ingested(record=record, identity_id=best.identity_id,
                        created_identity=True, linked=True, suggestions=[])

    for c in candidates:
        suggest(session, organization_id, entity_type=entity_type,
                record_id=record.record_id, target_identity_id=c.identity_id,
                strategy=c.strategy, evidence=c.evidence)
    return Ingested(record=record, identity_id=identity.identity_id,
                    created_identity=True, linked=False, suggestions=candidates)


def ingest_customer(session: Session, organization_id: str, **kw) -> Ingested:
    return ingest(session, organization_id, entity_type=CUSTOMER, **kw)


def ingest_item(session: Session, organization_id: str, **kw) -> Ingested:
    return ingest(session, organization_id, entity_type=ITEM, **kw)


# ── suggestions ─────────────────────────────────────────────────────────────
def suggest(session: Session, organization_id: str, *, entity_type: str,
            record_id: str, target_identity_id: str, strategy: str,
            evidence: str) -> Optional[models.IdentitySuggestion]:
    """Record a proposed link. Idempotent — a re-sync must not stack duplicates."""
    existing = session.scalars(
        select(models.IdentitySuggestion).where(
            models.IdentitySuggestion.record_id == record_id,
            models.IdentitySuggestion.target_identity_id == target_identity_id)).first()
    if existing is not None:
        return existing

    row = models.IdentitySuggestion(
        organization_id=organization_id, entity_type=entity_type,
        record_id=record_id, target_identity_id=target_identity_id,
        strategy=strategy, evidence=evidence)
    session.add(row)
    record_event(session, organization_id, entity_type=entity_type,
                 identity_id=target_identity_id, record_id=record_id,
                 action="SUGGESTED", actor="SYSTEM",
                 detail=f"{strategy}: {evidence}")
    session.flush()
    return row


def decide_suggestion(session: Session, organization_id: str, suggestion_id: str,
                      *, accept: bool, actor: str) -> models.IdentitySuggestion:
    row = session.get(models.IdentitySuggestion, suggestion_id)
    if row is None or row.organization_id != organization_id:
        raise IdentityError("No such suggestion")
    if row.status != "PENDING":
        raise IdentityError(f"That suggestion was already {row.status.lower()}")

    row.status = "ACCEPTED" if accept else "REJECTED"
    row.decided_by_user_id = actor
    row.decided_at = _now()

    if accept:
        link_record(session, organization_id, entity_type=row.entity_type,
                    record_id=row.record_id, identity_id=row.target_identity_id,
                    actor=actor, detail=f"{row.strategy}: {row.evidence}")
    else:
        record_event(session, organization_id, entity_type=row.entity_type,
                     identity_id=row.target_identity_id, record_id=row.record_id,
                     action="SUGGESTION_REJECTED", actor=actor,
                     detail=f"{row.strategy}: {row.evidence}")
    session.flush()
    return row


# ── linking ─────────────────────────────────────────────────────────────────
def link_record(session: Session, organization_id: str, *, entity_type: str,
                record_id: str, identity_id: str, actor: str,
                detail: str = "") -> Any:
    """Move one connector record onto an identity.

    Only the pointer moves. No field of the record changes, nothing is copied
    between connectors, and the record it joins is not touched at all — which is
    what "link, don't merge" means concretely.

    The identity the record is leaving is retired if it becomes empty. Deleting
    it would orphan the audit trail that explains this very link.
    """
    shape = _SHAPES[entity_type]
    record = session.get(shape["record"], record_id)
    if record is None or record.organization_id != organization_id:
        raise IdentityError("No such connector record")
    target = session.get(shape["identity"], identity_id)
    if target is None or target.organization_id != organization_id:
        raise IdentityError("No such identity")
    if not target.active:
        raise IdentityError("That identity has been retired")
    if record.identity_id == identity_id:
        return record

    previous = record.identity_id
    record.identity_id = identity_id
    session.flush()

    record_event(session, organization_id, entity_type=entity_type,
                 identity_id=identity_id, record_id=record_id,
                 action="LINKED", actor=actor,
                 detail=detail or f"moved from {previous}")
    _retire_if_empty(session, organization_id, entity_type, previous, actor)

    # Any other suggestion aimed at this record is now answered.
    for s in session.scalars(select(models.IdentitySuggestion).where(
            models.IdentitySuggestion.record_id == record_id,
            models.IdentitySuggestion.status == "PENDING")):
        if s.target_identity_id == identity_id:
            s.status, s.decided_by_user_id, s.decided_at = "ACCEPTED", actor, _now()
        else:
            s.status, s.decided_by_user_id, s.decided_at = "SUPERSEDED", actor, _now()
    session.flush()
    return record


def unlink_record(session: Session, organization_id: str, *, entity_type: str,
                  record_id: str, actor: str, reason: str = "") -> Any:
    """Split a record back out onto an identity of its own.

    The inverse of linking, and it has to exist: a wrong link that cannot be
    undone is worse than no linking at all, because the analysis built on it
    keeps compounding.
    """
    shape = _SHAPES[entity_type]
    record = session.get(shape["record"], record_id)
    if record is None or record.organization_id != organization_id:
        raise IdentityError("No such connector record")

    previous = record.identity_id
    identity = shape["identity"](organization_id=organization_id)
    session.add(identity)
    session.flush()
    record.identity_id = identity.identity_id
    session.flush()

    record_event(session, organization_id, entity_type=entity_type,
                 identity_id=previous, record_id=record_id, action="UNLINKED",
                 actor=actor, detail=reason or "split to its own identity")
    record_event(session, organization_id, entity_type=entity_type,
                 identity_id=identity.identity_id, record_id=record_id,
                 action="CREATED", actor=actor, detail="from an unlink")
    _retire_if_empty(session, organization_id, entity_type, previous, actor)
    return record


def relabel(session: Session, organization_id: str, *, entity_type: str,
            identity_id: str, label: str, actor: str) -> Any:
    """Name an identity. The one field on it that is a person's, not a system's."""
    shape = _SHAPES[entity_type]
    identity = session.get(shape["identity"], identity_id)
    if identity is None or identity.organization_id != organization_id:
        raise IdentityError("No such identity")
    identity.label = label.strip() or None
    record_event(session, organization_id, entity_type=entity_type,
                 identity_id=identity_id, action="RELABELLED", actor=actor,
                 detail=label[:200])
    session.flush()
    return identity


def _retire_if_empty(session: Session, organization_id: str, entity_type: str,
                     identity_id: str, actor: str) -> None:
    shape = _SHAPES[entity_type]
    remaining = session.scalar(
        select(shape["record"].record_id)
        .where(shape["record"].identity_id == identity_id).limit(1))
    if remaining is not None:
        return
    identity = session.get(shape["identity"], identity_id)
    if identity is None or not identity.active:
        return
    identity.active = False
    record_event(session, organization_id, entity_type=entity_type,
                 identity_id=identity_id, action="RETIRED", actor=actor,
                 detail="its last connector record was linked elsewhere")
    session.flush()


# ── reading ─────────────────────────────────────────────────────────────────
def review_coverage(session: Session, organization_id: str,
                    entity_type: str) -> dict[str, Any]:
    """Why there is nothing to review, when there is nothing to review.

    An empty review queue had one sentence for two unrelated situations, and the
    screen chose the reassuring reading of both: "no exact matches were found —
    not that matching is switched off". For a book whose customers carry no GSTIN
    that sentence is false in the way that matters. Nothing was found because
    nothing could be looked at, and no number of syncs would change it.

    So the queue reports its own reach: how many records exist, how many carry an
    identifier a strong strategy can compare, and how many are still alone. A
    screen can then say which of the two it is looking at instead of guessing.

    Counted from the records rather than from the suggestions, deliberately: a
    count derived from an empty queue can only ever describe the queue.
    """
    shape = _SHAPES[entity_type]
    record = shape["record"]
    identity = shape["identity"]

    rows = session.execute(
        select(record.record_id, record.identity_id, record.gstin
               if entity_type == CUSTOMER else record.sku)
        .join(identity, identity.identity_id == record.identity_id)
        .where(record.organization_id == organization_id,
               identity.active.is_(True))).all()

    per_identity: dict[str, int] = {}
    for _, iid, _key in rows:
        per_identity[iid] = per_identity.get(iid, 0) + 1

    keyed = sum(1 for _, _, key in rows if has_eligible_key(
        {"gstin": key, "sku": None} if entity_type == CUSTOMER
        else {"gstin": None, "sku": key}))
    alone = sum(1 for _, iid, _ in rows if per_identity.get(iid, 0) == 1)

    return {
        "records": len(rows),
        # Records an exact-identifier strategy is able to compare at all.
        "with_key": keyed,
        "without_key": len(rows) - keyed,
        # Records that are the only one on their identity, so nothing has been
        # linked to them. High with a low `with_key` is the gap; high with a high
        # `with_key` means the identifiers genuinely disagree.
        "unlinked": alone,
        "key_name": "GSTIN" if entity_type == CUSTOMER else "SKU",
    }


def records_for(session: Session, organization_id: str, entity_type: str,
                identity_id: str) -> list[Any]:
    shape = _SHAPES[entity_type]
    return list(session.scalars(
        select(shape["record"]).where(
            shape["record"].organization_id == organization_id,
            shape["record"].identity_id == identity_id)
        .order_by(shape["record"].connector, shape["record"].external_id)))


def history(session: Session, organization_id: str, entity_type: str,
            identity_id: str) -> list[models.IdentityEvent]:
    return list(session.scalars(
        select(models.IdentityEvent).where(
            models.IdentityEvent.organization_id == organization_id,
            models.IdentityEvent.entity_type == entity_type,
            models.IdentityEvent.identity_id == identity_id)
        .order_by(models.IdentityEvent.at)))


# ── the cross-connector name for a record ───────────────────────────────────
def identity_for_source(session: Session, organization_id: str, *,
                        entity_type: str, connector: str, connection_id: str,
                        external_id: str) -> Optional[str]:
    """The identity a connector record belongs to, or ``None`` if unlinked.

    This is what a *downstream* consumer should key on when it needs to name a
    real-world customer or item rather than one connector's row for it. The
    motivating case is pie-parser's identity scope: a confirmed "this customer's
    part code means MM# X" is a fact about the customer, not about which of our
    three companies happened to trade with them, so scoping it to the connector
    row would record the same fact twice and let the two drift.

    Returns ``None`` rather than inventing a scope when the record is unlinked.
    Linking is deliberately manual here, so an unlinked record is the normal
    early state, not an error — and a caller that substituted the connector's
    own id would be writing keys it intends to replace the moment somebody
    links the record.
    """
    shape = _SHAPES[entity_type]
    record = shape["record"]
    row = session.scalars(
        select(record).where(
            record.organization_id == organization_id,
            record.connector == connector,
            record.connection_id == connection_id,
            record.external_id == external_id)).first()
    return row.identity_id if row is not None else None


def identity_for_customer(session: Session, organization_id: str,
                          customer: Any) -> Optional[str]:
    """Convenience over :func:`identity_for_source` for a resolved customer row.

    Takes the ``models.Customer`` a caller already has rather than a reference
    to match, so the tolerant matching stays in the one place that owns it
    (``decisions.quote_support._resolve_customer``, reached through
    ``commercial.quote_service.resolve_customer``) instead of gaining a second
    implementation here.
    """
    if customer is None:
        return None
    return identity_for_source(
        session, organization_id, entity_type=CUSTOMER,
        connector=customer.connector, connection_id=customer.connection_id,
        external_id=customer.external_id)


# ── confirmed customer-code mappings ────────────────────────────────────────
#
# The loop this closes: pie-parser proposes "your 7781 is probably MM# X" and
# refuses to assert it; a person confirms; the confirmation is recorded here;
# every later resolution of that code answers authoritatively without asking
# again. Before this, the confirmation was made on the quote screen and thrown
# away, so the same question came back every quarter.

def normalize_code(raw: str) -> str:
    """Trim and upper-case, leaving internal punctuation alone.

    Deliberately the same rule as pie-parser's ``normalize_identifier``: the two
    have to agree or a mapping written here will not be found there. Stripping
    separators would merge two genuinely different part numbers into one key.
    """
    return (raw or "").strip().upper()


def confirm_code_mapping(session: Session, organization_id: str, *,
                         identity_id: str, code: str, target_record_id: str,
                         relationship: str = "SAME_PRODUCT",
                         source_ref: str = "",
                         user_id: Optional[str] = None
                         ) -> Optional[models.ConfirmedCodeMapping]:
    """Record that a customer's code means a manufacturer product.

    Idempotent: re-confirming the same target returns the existing row rather
    than stacking duplicates. A *different* target supersedes the previous
    mapping instead of overwriting it, so the quote sent under the old one can
    still be explained.

    Returns None when there is nothing to record — no identity to scope to, no
    code, or no target. A missing scope is the normal early state for an
    unlinked customer and must not raise on a quote screen.
    """
    key = normalize_code(code)
    if not (identity_id and key and target_record_id):
        return None

    current = session.scalars(
        select(models.ConfirmedCodeMapping).where(
            models.ConfirmedCodeMapping.organization_id == organization_id,
            models.ConfirmedCodeMapping.identity_id == identity_id,
            models.ConfirmedCodeMapping.code == key,
            models.ConfirmedCodeMapping.active.is_(True))).first()
    if current is not None and current.target_record_id == target_record_id:
        return current

    row = models.ConfirmedCodeMapping(
        organization_id=organization_id, identity_id=identity_id, code=key,
        target_record_id=target_record_id, relationship=relationship,
        source_ref=source_ref[:255], confirmed_by_user_id=user_id)
    session.add(row)
    session.flush()                     # the new row needs its id below
    if current is not None:
        current.active = False
        current.superseded_by = row.mapping_id
        # Flushed too, not left pending until the caller happens to commit:
        # otherwise anything reading in this same session — `OrgMappingStore`
        # being built for the next line of the same quote — still sees the old
        # mapping as active and resolves to the record just corrected.
        session.flush()
    record_event(session, organization_id, entity_type=CUSTOMER,
                 identity_id=identity_id, action="CODE_MAPPING_CONFIRMED",
                 actor=user_id or "SYSTEM",
                 detail=f"{key} -> {target_record_id} ({source_ref})")
    return row


def confirm_proposed_identity(
        session: Session, organization_id: str, *,
        identity_id: Optional[str], code: str,
        proposed_record_id: Optional[str], selected_record_id: Optional[str],
        source_ref: str = "", user_id: Optional[str] = None
) -> Optional[models.ConfirmedCodeMapping]:
    """Record a mapping **only** when the caller answered the engine's question.

    The gate, in one place, for every path that can create a confirmed mapping.
    It was previously the first half of ``routers.quote._confirm_identity`` and
    nothing else could reach it; the public API needs the same decision, and a
    second copy of a correctness boundary is the one duplication
    ``tests/test_identity_confirmation_gate.py`` exists to prevent.

    **Why it is this narrow.** A confirmed mapping is *asserted* identity:
    afterwards the engine resolves that customer's code AUTHORITATIVELY and its
    MIXED path will derive an effective requirement from the record and rank
    equivalents off it. Selecting the record the engine itself proposed —
    pie-parser's single-candidate ``NEEDS_REVIEW``, an exact catalogue hit
    downgraded for namespace safety — is a person answering that question, and
    the hop carries no tolerance. Selecting *anything else* is a substitution on
    one quote: the engine put it a band away and said so, and filing that as
    identity would make the approximate exact by storage and license
    ``tolerance ∘ tolerance`` on every later "same as their 7781 but 12 mm".

    Three ways to get ``None``, and all three are refusals rather than errors:
    no identity to scope the fact to, no proposal on this line, or a selection
    that is not the proposal. ``proposed_record_id`` being ``None`` refuses even
    a ``None`` selection — ``None == None`` must not read as a match.
    """
    if not identity_id or not proposed_record_id:
        return None
    if selected_record_id != proposed_record_id:
        return None
    return confirm_code_mapping(
        session, organization_id, identity_id=identity_id, code=code,
        target_record_id=selected_record_id, source_ref=source_ref,
        user_id=user_id)


def phrase_key(phrase: str) -> str:
    """The key a phrase is recorded under: upper-cased, whitespace collapsed.
    Punctuation is kept — "CNMG 120408-49" and "CNMG 120408 49" are two ways
    a person writes one thing, and the retrieval pass is what bridges those;
    this key only has to make the *same* words idempotent."""
    return " ".join((phrase or "").upper().split())


def record_phrase_alias(session: Session, organization_id: str, *,
                        identity_id: Optional[str], phrase: str,
                        target_record_id: Optional[str], source_ref: str = "",
                        user_id: Optional[str] = None
                        ) -> Optional[models.CustomerPhraseAlias]:
    """Record that, for this customer, this phrase was quoted as this product.

    The learning half of retrieval, and deliberately *not* the identity gate:
    ``confirm_proposed_identity`` files a fact the engine will assert, and
    refuses everything but the engine's own proposal because an asserted
    identity composes tolerances. This files nothing the engine reads. It
    records a person's choice so ``app/retrieval/aliases`` can offer the same
    record back when the customer writes something close — an option beneath
    the ranking, never an answer — so any selection a person makes on a
    requirement line is worth remembering, a substitution included: "last
    time you quoted them Y for this" is exactly what the next person needs
    to see and is free to ignore.

    Idempotent for the same phrase and target; a different target supersedes
    the old row rather than overwriting it. ``None`` — a refusal, not an error
    — without a scope, a phrase, or a target.
    """
    key = phrase_key(phrase)
    if not (identity_id and key and target_record_id):
        return None
    current = session.scalars(
        select(models.CustomerPhraseAlias).where(
            models.CustomerPhraseAlias.organization_id == organization_id,
            models.CustomerPhraseAlias.identity_id == identity_id,
            models.CustomerPhraseAlias.phrase_key == key,
            models.CustomerPhraseAlias.active.is_(True))).first()
    if current is not None and current.target_record_id == target_record_id:
        return current
    row = models.CustomerPhraseAlias(
        organization_id=organization_id, identity_id=identity_id,
        phrase=(phrase or "").strip()[:512], phrase_key=key[:512],
        target_record_id=target_record_id, source_ref=source_ref[:255],
        recorded_by_user_id=user_id)
    session.add(row)
    session.flush()
    if current is not None:
        current.active = False
        current.superseded_by = row.alias_id
        session.flush()
    record_event(session, organization_id, entity_type=CUSTOMER,
                 identity_id=identity_id, action="PHRASE_ALIAS_RECORDED",
                 actor=user_id or "SYSTEM",
                 detail=f"{key!r} -> {target_record_id} ({source_ref})")
    return row


def active_phrase_aliases(session: Session, organization_id: str
                          ) -> list[models.CustomerPhraseAlias]:
    return list(session.scalars(
        select(models.CustomerPhraseAlias).where(
            models.CustomerPhraseAlias.organization_id == organization_id,
            models.CustomerPhraseAlias.active.is_(True))))


def active_code_mappings(session: Session, organization_id: str
                         ) -> list[models.ConfirmedCodeMapping]:
    return list(session.scalars(
        select(models.ConfirmedCodeMapping).where(
            models.ConfirmedCodeMapping.organization_id == organization_id,
            models.ConfirmedCodeMapping.active.is_(True))))
