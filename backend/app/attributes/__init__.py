"""Product attributes: decoded facts, persisted with their provenance.

The core of Phase 1 (decision 002). Everything later — retrieval, compatibility
rules, ranking, learning — is built over technical facts about products, and
before this package there were none stored: the pie-parser engine decodes 44
distinct fact fields over the 6,717-row catalogue, ``master_health`` measured
that and kept three of them in memory for one report, and nothing wrote a row.
This is where those facts start existing.

    extract    decoded fields -> storable claims. Pure; no session, no engine.
    writer     the single writer of ``ProductAttributeValue``. Superseded,
               never mutated, and unchanged means nothing is written.
    decorate   the orchestration: which products, which source, then the writer.
    coverage   the Phase 1 exit criterion — what IS covered, per organization
               and per attribute key. No target, no band, no verdict.

**Two sources, stored separately on purpose.** ``DECODED_NAME`` is the item's
own description run through the parser and reaches about 21% of the master.
``CATALOGUE_LINK`` is the manufacturer catalogue record the item *is* — about
9% of items carry ``products.pie_record_id`` — and that is the maker's own data
rather than a reading of a description, so it is a stronger claim. Both are
written, each under its own ``source_kind``, because which one wins is a
read-time policy question: collapsing them at write time would discard the
disagreement, and the disagreement is the only thing the pair can tell you that
neither says alone. The table's partial unique index is keyed on ``source_kind``
for exactly this reason.

**Deterministic, and one of the layers §1 names.** Nothing here is interpreted,
phrased or guessed by a model; this package must never import ``ai/``, and
``test_layer_boundaries`` enforces it. Nothing here computes a price, a cost or
a margin either — there is no such column on this table and no such value in
this package.

**Org-scoped on every read and every write** (decision 026). The attribute
source is a distributor export licensed to the organization that obtained it,
so a row belongs to one tenant and two tenants may legitimately hold different
values for the same product. There is a ``tenant_isolation`` policy on the
table, but it binds on PostgreSQL only: the ``organization_id`` filters in this
package are the primary control and the whole of it on SQLite.

**Absence of evidence is not a pass**, which here has a specific shape. A
product whose name decodes to nothing gets no rows — not a zero, not an
empty-string row. A source that could not be *asked* — pie-parser absent, the
catalogue unbuilt — writes nothing and retracts nothing, because "the pack
covered nothing here" and "nobody asked the pack" are different facts and only
the first is evidence.
"""
from .coverage import CoverageReport, KeyCoverage, attribute_coverage
from .decorate import DecorationReport, decorate_products
from .extract import (
    CATALOGUE_LINK,
    DECODED_NAME,
    AttributeClaim,
    Extraction,
    claims_from_catalogue_record,
    claims_from_decode,
    claims_from_values,
    decoded_facts,
    unit_for,
)
from .writer import WriteResult, live_values, write_claims

__all__ = [
    "AttributeClaim",
    "CATALOGUE_LINK",
    "CoverageReport",
    "DECODED_NAME",
    "DecorationReport",
    "Extraction",
    "KeyCoverage",
    "WriteResult",
    "attribute_coverage",
    "claims_from_catalogue_record",
    "claims_from_decode",
    "claims_from_values",
    "decoded_facts",
    "decorate_products",
    "live_values",
    "unit_for",
    "write_claims",
]
