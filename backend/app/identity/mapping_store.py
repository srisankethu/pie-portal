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

Rows are indexed by ``ScopedIdentifier.key()`` — the engine's own builder, not a
copy of its format. That is the one thing this module must get exactly right: a
key that disagrees produces a lookup miss, and a miss is indistinguishable from
"nobody has confirmed this mapping", so the failure is silent.
"""
from __future__ import annotations

import hashlib
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
        # The engine's own key builder, so there is one definition of the key
        # shape rather than a copy here that agrees until somebody changes it.
        # A miss reads as "nobody confirmed this mapping", so a drifted format
        # would not raise — it would quietly stop resolving confirmed products.
        # pie-parser pins the format in test_scoped_identifier_key_is_a_
        # published_format; test_the_store_answers_in_the_engine_s_own_key_shape
        # is this side of that contract.
        #
        # Imported lazily, not at module scope: this module is imported by the
        # quote router at startup and must stay importable when the engine is
        # absent. Constructing a store *does* need it — but the store exists
        # only to be read by the engine, so there is nothing to serve without
        # one, and the router degrades to None (see quote._mapping_store).
        from identity.model import Namespace, ScopedIdentifier

        self._by_key: Dict[str, Any] = {}
        for row in service.active_code_mappings(session, organization_id):
            key = ScopedIdentifier(
                namespace=Namespace.CUSTOMER_ITEM,
                value=row.code,
                scope=row.identity_id,
            ).key()
            self._by_key[key] = row

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

    def fingerprint(self) -> str:
        """A value that changes when what this store answers changes.

        Read by the resolution cache: an engine result depends on the mappings
        the engine could see, so the cache key has to carry them. Content, not
        a row count or a timestamp — a confirmation that supersedes another
        leaves the count identical while changing the answer, and a clock is
        not evidence about content.

        Cheap by construction: this is one pass over a snapshot that was just
        built from the database, and it is computed once per resolution batch,
        not once per line.
        """
        digest = hashlib.sha256()
        for key in sorted(self._by_key):
            row = self._by_key[key]
            for field in (key, row.target_record_id, row.relationship):
                # Every field terminated rather than joined: a separator that
                # can appear inside a value makes two different mapping sets
                # hash the same, which is a stale cache with no way to see it.
                digest.update(str(field).encode("utf-8"))
                digest.update(b"\x00")
        return digest.hexdigest()[:32]
