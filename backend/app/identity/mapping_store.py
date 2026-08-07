"""A pie-parser mapping store backed by this organization's confirmed rows.

pie-parser resolves a customer's own code through a ``MappingStore``, which it
loads from a CSV inside its own repository. That file ships empty and is shared
by everyone who runs the engine, so the authoritative-mapping path never fired
for anybody. A confirmation is made by a person about one organization's
customer — tenant data — so the rows live in this database and are handed to the
engine per resolution instead.

Only the ``lookup`` half of the interface is needed: the engine reads mappings,
it never writes them. Writing is ``identity.service.confirm_code_mapping``,
where the audit trail and the supersede rule live.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from . import service


class OrgMappingStore:
    """Reads confirmed mappings for one organization.

    Snapshotted at construction rather than queried per lookup. A quote's lines
    are resolved in one pass, and a mapping confirmed halfway through that pass
    changing what the remaining lines resolve to would make the same RFQ text
    resolve two ways in a single quote. It also keeps the engine — which knows
    nothing about sessions — from holding one open across a long resolution.
    """

    def __init__(self, session: Session, organization_id: str) -> None:
        self._by_key: Dict[str, Any] = {}
        for row in service.active_code_mappings(session, organization_id):
            # pie-parser's own key shape, built without importing it: this
            # module must stay importable when the engine is absent.
            self._by_key[f"CUSTOMER_ITEM:{row.identity_id}:{row.code}"] = row

    def lookup(self, identifier: Any) -> Optional[Any]:
        row = self._by_key.get(identifier.key())
        if row is None:
            return None
        # Built lazily so the engine's types are only needed when there is
        # actually a mapping to return.
        from identity.model import RelationshipType
        from identity.store import ConfirmedMapping
        try:
            rel = RelationshipType(row.relationship)
        except ValueError:
            rel = RelationshipType.SAME_PRODUCT
        return ConfirmedMapping(
            identifier=identifier, target_record_id=row.target_record_id,
            relationship=rel, confidence="confirmed",
            source_ref=row.source_ref or "confirmed in the portal")

    def __len__(self) -> int:
        return len(self._by_key)
