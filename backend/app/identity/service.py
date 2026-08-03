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
from .matchers import Candidate, RecordFacts, find_candidates, normalize_gstin, normalize_sku

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
        "auto_flag": "auto_link_customers",
    },
    ITEM: {
        "identity": models.ItemIdentity,
        "record": models.ItemConnectorRecord,
        "key": "sku",
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
        text=name or description)
    candidates = [c for c in find_candidates(facts, _Lookup(session, entity_type))
                  if c.identity_id != identity.identity_id]

    if not candidates:
        return Ingested(record=record, identity_id=identity.identity_id,
                        created_identity=True, linked=False, suggestions=[])

    policy = get_policy(session, organization_id)
    if getattr(policy, shape["auto_flag"], False):
        best = candidates[0]
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
