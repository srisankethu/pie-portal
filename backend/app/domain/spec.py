"""The ingestion contract: which records a connector must produce, published as
JSON Schema under one content hash.

``schemas.py`` already **is** PIE's canonical ingestion model. It is what
``ingestion/normalize.py`` emits, what ``repositories.py`` writes, and where
every field's meaning is argued out in prose. A second, hand-written
description of the same records would be the responsibility duplication
CLAUDE.md §2 names by example: two homes for one concern, and the copy that
gets edited is never the copy that runs. So nothing in this module re-describes
a field, a type or a constraint. It *selects*, *names*, *annotates*,
*serialises* and *hashes* what ``schemas.py`` already says — which is why a new
field on a DTO needs no edit here at all, and moves the version by itself.

Why this is versioned
---------------------
PIE's own Zoho client dropped ``created_time`` from all three document
projections. Every row landed with a NULL ``source_recorded_at``, every quote
line came back INSUFFICIENT_EVIDENCE, and the sync reported success — because
nothing anywhere stated that the field was supposed to be there. An omission
that nothing checks is indistinguishable from a source that has no such
concept, and CLAUDE.md §1 has the general form: absence of evidence is not a
pass.

A published contract turns that class of omission into a comparison somebody
can fail. ``spec_version()`` is the stamp a sync carries so a row can say which
contract it was written under, and the version moving is what makes a silent
change loud.

Following ``CommercialThresholds``
----------------------------------
Deliberately the same mechanism as ``commercial/config.py``: a ``serialized``
pre-image, a prefixed short sha256 over it, and a ``remember`` call at the one
site where a stamp is minted. Three stamps in this codebase already work that
way (``ci_``, ``th_``, ``mon_``); a fourth invented shape would make one stamp
on a record look like a different kind of thing from its neighbours.

The pre-image is public, for the same reason ``CommercialThresholds.serialized``
is. A hash does not invert, so a stamp nobody can resolve to the contract it
stood for is a stamp that is only distinguishable, never explainable —
``docs/concepts/09-policy-replay.md`` names that as the blocker under
everything else it wants. ``canonical_json()`` is those exact bytes.

What the hash covers
--------------------
Every byte ``model_json_schema()`` emits for every entity, plus the
``x-pie-expected`` marker this module injects. Two parts of that are worth
stating, because both are choices rather than defaults.

**The descriptions are in.** ``SourceRef.recorded_at``'s prose is where "a row
without it is not usable as point-in-time evidence" is *stated*, and an edit
turning that into "defaults to sync time" would be a change to the contract in
the only place the contract makes that claim. The cost is that a typo fix in a
docstring moves the version; the alternative is a contract whose stated
semantics can be rewritten under a stable stamp, and over-sensitivity is the
safe direction here.

**The marker is in, and the first draft of this module had it out.** It was
kept beside the document — reachable from :func:`entity_field_contracts` and
nowhere else — on the grounds that ``canonical_json`` is fixed as the
serialisation of :func:`entity_schemas` alone. That reading of the rule was
right and the conclusion was wrong: the answer is to change what
:func:`entity_schemas` *emits*, after which the rule holds exactly as written.
Two things broke otherwise, and they are the reason the marker now rides in the
document:

* **An integrator reads ``docs/spec/bill.json``, not a Python function.** The
  one thing this contract exists to say — that a connector omitting
  ``source_ref.recorded_at`` has a defect rather than a legitimately absent
  concept — would have been missing from the only artifact anyone outside this
  repository ever sees. A spec that cannot say that is a spec failing at its
  job.
* **Downgrading an expectation would not have moved the version.** Editing
  ``SourceRef.recorded_at`` from EXPECTED to OPTIONAL is precisely the class of
  change the stamp exists to name. A test holding that line is weaker than the
  hash holding it, because a test can be edited in the same commit as the
  downgrade and read as intentional.

One declaration, two renderings. ``_EXPECTED`` is the only place an expectation
is written down; :func:`entity_schemas` publishes it into the document and
:func:`entity_field_contracts` reads it back *out of the document* for a
conformance suite. Neither rendering can disagree with the other, because the
second one never looks at ``_EXPECTED`` at all.

Determinism
-----------
Identical code must give identical bytes in a separate process, or the stamp
is decorative. Three things secure that, and all three are pinned by tests:

* ``sort_keys=True`` in :func:`canonical_json`, which orders every mapping in
  the document recursively — so a field's position in ``schemas.py`` cannot
  reach the hash through ``properties`` ordering, and the injected marker's
  own ordering cannot either.
* ``ref_template`` and ``mode`` passed explicitly in :func:`entity_schemas`
  rather than left to pydantic's defaults, so a patch release that changes a
  default cannot silently rename every ``$ref`` or swap validation shapes for
  serialisation ones.
* ``SPEC_ENTITIES`` as a written tuple rather than a scan of the module, so
  membership is a decision somebody made and not an accident of import order.

The remaining lists in the output — ``required``, ``enum``, ``anyOf`` — are
left exactly as pydantic emits them. They derive from declaration order, which
is a property of the source file rather than of the run, and the hash is stable
across interpreter hash seeds because of it. Sorting them would additionally be
a transformation this module would then have to maintain forever, against a
document whose whole value is being what pydantic actually produces.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterator, Optional

from pydantic import BaseModel

from .. import threshold_registry
from . import schemas

#: The ingestion contract, in a fixed order: alphabetical by
#: :func:`entity_name`, which is pinned by a test so the order cannot drift
#: from the published document's. Order does not reach the hash —
#: :func:`canonical_json` sorts — so this is for readers of ``docs/spec``.
#:
#: **The inclusion rule, so the next person can apply it to a new DTO.** An
#: entity is a record a connector produces *as a top-level document*. In
#: ``schemas.py`` that is exactly the set carrying its own ``source_ref``:
#: provenance is per source record, so a type that has one is a thing a source
#: system holds, and a type that has none exists only inside a parent. The two
#: other kinds of type in that module are excluded for the two different
#: reasons written out in ``_NOT_INGESTION`` and ``_COMPONENTS`` below.
#:
#: The list is written out rather than derived from that rule, and the rule is
#: then asserted against the list in ``test_spec_contract.py``. Deriving it
#: would mean a DTO added to ``schemas.py`` joins the published contract and
#: moves the version with nobody having decided that it should; declaring it
#: means a new DTO fails the suite until somebody chooses. That is the
#: membership discipline ``test_layer_boundaries.py`` arrived at for packages,
#: for the same reason: an unchecked thing reads exactly like a clean one.
SPEC_ENTITIES: tuple[type[BaseModel], ...] = (
    schemas.BillIn,
    schemas.CostRecordIn,
    schemas.CreditNoteIn,
    schemas.CreditNoteApplicationIn,
    schemas.CustomerIn,
    schemas.InvoiceIn,
    schemas.LocationIn,
    schemas.PaymentReceiptIn,
    schemas.ProductIn,
    schemas.PurchaseOrderIn,
    schemas.QuoteDocIn,
    schemas.SalesOrderIn,
    schemas.SalesTxnIn,
    schemas.StockLocationSnapshotIn,
    schemas.StockSnapshotIn,
    schemas.VendorIn,
    schemas.VendorCreditIn,
    schemas.VendorCreditApplicationIn,
    schemas.VendorPaymentIn,
)

#: Types in ``schemas.py`` that no connector produces at all. These are the
#: API read DTOs — scope-filtered projections this platform *returns*. The
#: ingestion contract is about what arrives; putting a read shape in it would
#: make every change to an API response move the stamp on a sync.
_NOT_INGESTION: dict[str, str] = {
    "DecisionRead": "API read projection — what the platform returns, not what "
                    "a connector sends.",
    "ActionRequest": "API request body for a human action on a decision; it "
                     "originates in a browser, not in an ERP.",
}

#: Types a connector produces only *inside* a parent record. They are fully
#: published and fully hashed — they ride in the ``$defs`` of every entity that
#: references them — and listing them again as entities of their own would
#: describe the same bytes twice and invite a conformance suite to ask for one
#: standalone, which no source system emits.
_COMPONENTS: dict[str, str] = {
    "SourceRef": "Provenance, carried by every entity. Shared component.",
    "DocumentApplicationIn": "One payment set against one document; arrives "
                             "inside PaymentReceiptIn or VendorPaymentIn.",
    "QuoteLineIn": "A line of a quote; arrives inside QuoteDocIn.",
    "InvoiceSalesOrderRef": "An order an invoice bills against; arrives inside "
                            "InvoiceIn.",
}

# ── field status vocabulary ──────────────────────────────────────────────────
#: The schema says the field must be present.
REQUIRED = "REQUIRED"
#: The schema permits absence and this contract does not. See ``_EXPECTED``.
EXPECTED = "EXPECTED"
#: Absent is a legitimate answer — the source genuinely has no such concept.
OPTIONAL = "OPTIONAL"

#: The JSON Schema keyword the expectation is published under, and the reason
#: it is a keyword at all rather than a sidecar file.
#:
#: JSON Schema ignores keywords it does not know, so a stock validator still
#: validates a payload against this document unchanged and an integrator's
#: toolchain needs no awareness of PIE. That tolerance is what makes the
#: annotation safe to put *inside* the artifact — and putting it inside is the
#: whole point, because the statement it makes is one no validator can make:
#: "this may be absent, and your being absent is still a defect". The ``x-``
#: prefix is the conventional marker for a vendor extension, so a reader who
#: has never seen this repository can tell at a glance which keywords are the
#: standard's and which are ours.
#:
#: Shape: an object at the scope that *declares* the fields, beside ``required``
#: and read the same way — field name to the reason its absence is a defect.
#: The reason is carried rather than dropped because the artifact is read by
#: somebody with no access to this module's source, and "recorded_at is
#: expected" without the sentence about INSUFFICIENT_EVIDENCE is an instruction
#: with no argument behind it.
EXPECTED_KEYWORD = "x-pie-expected"


@dataclass(frozen=True)
class FieldContract:
    """What the contract asks of one field, at one path inside one entity.

    ``path`` is dotted, with ``[]`` where a list element is traversed —
    ``source_ref.recorded_at``, ``applications[].document_date``. The bracket
    is load-bearing rather than decorative: a REQUIRED status below a list is
    required *of each element that is present*, and a conformance suite that
    read it as "the list must be non-empty" would reject an invoice paid in
    one go.
    """

    path: str
    status: str
    #: Why absence is a defect. Non-empty exactly when ``status`` is EXPECTED,
    #: so a conformance failure can say what is wrong rather than only that
    #: something is.
    reason: str = ""


#: ``(declaring type, field)`` → why its absence is a defect rather than a
#: legitimate "this ERP has no such concept".
#:
#: This is the declaration the module exists for, and the only place an
#: expectation is written down. :func:`_publish_expectations` copies it into
#: every document that declares the field, under :data:`EXPECTED_KEYWORD`,
#: which is how it reaches both the published artifact and the hash.
#:
#: JSON Schema has two states, required and not, and the whole ``recorded_at``
#: incident lived in the second one: the field is optional because *a connector
#: may not expose one*, and a connector that has one and drops it is
#: indistinguishable from a connector that does not — which is how three
#: document projections lost ``created_time`` and the sync reported success.
#:
#: Keyed by the type that **declares** the field rather than by an entity path,
#: so ``SourceRef.recorded_at`` is stated once and holds at all nineteen places
#: a ``source_ref`` appears. Nineteen copies would be nineteen chances to
#: update eighteen.
#:
#: **The bar for adding one, because a marker that fires on everything is a
#: marker people learn to scroll past (CLAUDE.md §9).** A field belongs here
#: when its own prose in ``schemas.py`` says it is optional to tolerate a
#: *connector or history* limitation rather than a business absence, and when
#: its absence disables a downstream computation silently rather than loudly.
#: Exactly two fields in ``schemas.py`` say "Optional because …", and they are
#: exactly these two. Fields optional because the business genuinely may not
#: have the fact — ``BillIn.balance``, ``QuoteDocIn.expires_on``,
#: ``StockSnapshotIn.reorder_level``, all three of which argue at length that
#: absent must not be read as zero — are OPTIONAL and must stay OPTIONAL.
_EXPECTED: dict[tuple[str, str], str] = {
    ("SourceRef", "recorded_at"):
        "When the source system recorded the document. A row without it is not "
        "usable as point-in-time evidence: it is counted, never imputed, and "
        "every quote line that needed it answers INSUFFICIENT_EVIDENCE. A "
        "connector whose source exposes a creation timestamp and omits this is "
        "defective; one whose source has none must say so, because silence here "
        "is what the incident behind this module looked like.",
    ("CostRecordIn", "vendor_external_id"):
        "Who the line was bought from, copied down from the bill header. Every "
        "bill has a vendor, so absence here is a connector or history gap, not "
        "a fact about the trade. Lines without it fold as unattributable and "
        "supplier concentration is silently answerable only per product.",
}

#: Passed explicitly wherever a schema is generated, never left to pydantic's
#: defaults. These *are* the defaults today, which is the point: a default is a
#: value somebody else may change, and a ``$ref`` naming scheme that moved in a
#: patch release would rewrite every reference in the document and every byte
#: of the hash with no change in this repository behind it.
_REF_TEMPLATE = "#/$defs/{model}"
#: ``validation`` describes what a connector must *send*. ``serialization``
#: describes what comes back out, and for ``Decimal`` the two genuinely differ.
_SCHEMA_MODE = "validation"

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def entity_name(model: type[BaseModel]) -> str:
    """``CustomerIn`` → ``customer``, ``SalesTxnIn`` → ``sales_txn``.

    The published name of an entity, and the key everything downstream joins
    on — the docs page, the conformance report, the stamp's own document. Taken
    from the class name rather than declared per entity so the two cannot
    disagree; the naming convention in ``schemas.py`` is uniform enough to carry
    it, and ``test_spec_contract.py`` pins all nineteen results literally so a
    rename shows up as a failing assertion rather than as a moved hash.

    No acronym handling, deliberately: ``schemas.py`` has no run of capitals in
    any DTO name, and a rule for a case that does not exist is a rule nobody can
    check. A DTO named ``GSTReturnIn`` would come out ``g_s_t_return`` and fail
    the pinned list, which is the right moment to decide what it should be.
    """
    name = model.__name__
    if name.endswith("In") and len(name) > 2:
        name = name[:-2]
    return _CAMEL_BOUNDARY.sub("_", name).lower()


def _publish_expectations(model_name: str, document: dict[str, Any]) -> None:
    """Write ``_EXPECTED`` into one document, at the scope that declares each
    field, in place.

    The scope is the point. ``x-pie-expected`` sits on the record definition
    that owns the field and lists that definition's own field names — exactly
    the scope and the reading of ``required``, which it is meant to be read
    beside. So ``SourceRef``'s definition carries ``recorded_at`` and nothing
    else, and it carries it in all nineteen documents because ``SourceRef`` is
    in all nineteen ``$defs``. A flat list of dotted paths at the top of each
    document would have been the same information spelled nineteen different
    ways, and the one spelling that went stale would be the one nobody read.

    A declaring type absent from this document is skipped rather than
    complained about — ``CostRecordIn`` is not in ``customer``'s definitions and
    should not be. The case that *is* an error, a declaration naming a type or
    field that exists nowhere at all, is caught by the suite rather than here,
    because this runs on the hot path of every hash and a raise in it would
    make the contract unreadable rather than merely wrong.
    """
    definitions = {model_name: document, **document.get("$defs", {})}
    for (owner, field), reason in _EXPECTED.items():
        definition = definitions.get(owner)
        if definition is None:
            continue
        definition.setdefault(EXPECTED_KEYWORD, {})[field] = reason


def entity_schemas() -> dict[str, dict[str, Any]]:
    """``entity_name`` → the published JSON Schema for it: what pydantic
    generates, annotated with what this contract expects.

    The single site in this codebase that calls ``model_json_schema``. Every
    other view of the contract — the canonical bytes, the version, the field
    contracts, whatever ``docs/spec`` renders — is built from this one, so
    there is no second answer to "what is the schema" that could drift from the
    first.

    Returned fresh on each call rather than cached, because the value is a
    mutable nest of dicts and a cached one is a shared mutable a caller can
    edit under everybody else. It is also what lets the annotation be written
    in place without a deep copy. The immutable views below are the cached
    ones.
    """
    documents: dict[str, dict[str, Any]] = {}
    for model in SPEC_ENTITIES:
        document = model.model_json_schema(
            by_alias=True, ref_template=_REF_TEMPLATE, mode=_SCHEMA_MODE)
        _publish_expectations(model.__name__, document)
        documents[entity_name(model)] = document
    return documents


@lru_cache(maxsize=1)
def canonical_json() -> str:
    """The exact bytes :func:`spec_version` hashes. Public, because a hash does
    not invert.

    ``sort_keys`` orders every mapping recursively and ``separators`` removes
    the whitespace that carries no meaning, so two runs of the same code cannot
    differ on formatting. ``json.dumps`` escapes non-ASCII by default and that
    default is kept: the DTO docstrings are full of em dashes, and an ASCII
    pre-image is one that survives a terminal, a diff and a database column
    without an encoding question attached.

    Cached because it is immutable and building nineteen schemas is not free —
    ``spec_version`` asks for it twice on the one call that mints.
    """
    return json.dumps(entity_schemas(), sort_keys=True, separators=(",", ":"))


@lru_cache(maxsize=1)
def spec_version() -> str:
    """``"spec_"`` plus the first ten hex characters of the sha256 of
    :func:`canonical_json`.

    The same shape as ``CommercialThresholds.version`` down to the truncation,
    and the ``remember`` call is not a stray side effect any more than it is
    there: this is the only place in the codebase where a spec stamp is minted,
    so it is the only moment the bytes behind one are in hand.

    Minted on access and never at import time. A module-level constant would
    build nineteen JSON Schemas during ``import app.domain.spec``, which is
    imported transitively by things that only want a DTO.

    One honest limit on what ``remember`` buys here. It is process-local memory
    only, and the registry's *persisted* half is keyed on the prefixes
    ``threshold_registry._PREFIXES`` knows — ``ci_``, ``th_``, ``gr_``.
    ``spec_`` is not among them, so ``kind_of`` reads a spec stamp as
    ``"unknown"`` and the flush handler skips it: a column marked
    ``info={"policy_stamp": …}`` holding one of these would be silently not
    recorded. Widening that map is a decision for whoever owns the persisted
    side, not a thing to do from here on the way past. It costs little either
    way, because unlike a threshold's pre-image this one is not a policy row
    somebody edited — it is regenerated from the code at ``canonical_json()``
    by anyone who has the checkout.
    """
    serialized = canonical_json()
    version = "spec_" + hashlib.sha256(serialized.encode()).hexdigest()[:10]
    threshold_registry.remember("spec", version, serialized)
    return version


# ── field contracts ──────────────────────────────────────────────────────────
def _referent(sub: dict[str, Any], defs: dict[str, Any],
              ) -> Optional[tuple[str, dict[str, Any], bool]]:
    """``(definition name, definition, is_list)`` for a property that points at
    another record, or ``None`` for a scalar.

    Three shapes reach here from the DTOs in ``schemas.py`` and no others: a
    bare ``$ref`` (a required nested record), a ``$ref`` inside ``anyOf``
    alongside ``{"type": "null"}`` (an optional one), and ``items`` holding
    either (a list of them). A definition with no ``properties`` is an enum
    rather than a record and stops the walk, which is why ``CustomerStatus``
    does not appear as a path.
    """
    node = sub
    is_list = False
    items = node.get("items")
    if isinstance(items, dict):
        node, is_list = items, True
    ref = node.get("$ref")
    if ref is None:
        for member in node.get("anyOf", ()):
            if isinstance(member, dict) and "$ref" in member:
                ref = member["$ref"]
                break
    if ref is None:
        return None
    name = ref.rsplit("/", 1)[-1]
    target = defs.get(name)
    if not isinstance(target, dict) or "properties" not in target:
        return None
    return name, target, is_list


def _walk(schema: dict[str, Any], defs: dict[str, Any], prefix: str,
          seen: frozenset[str]) -> Iterator[FieldContract]:
    """Every field of one record and of the records it nests, depth first.

    All three statuses are read off the schema node itself — ``required`` and
    ``x-pie-expected`` sit side by side on the definition that declares the
    fields, which is why this function never consults ``_EXPECTED`` and cannot
    disagree with what was published.

    ``seen`` carries the definitions already open on this branch. No DTO in
    ``schemas.py`` is self-referential today, and a walk that would not
    terminate if one became so is a defect waiting for a feature rather than a
    hypothetical.
    """
    required = set(schema.get("required", ()))
    expected = schema.get(EXPECTED_KEYWORD, {})
    for field, sub in schema.get("properties", {}).items():
        path = f"{prefix}{field}"
        if field in required:
            yield FieldContract(path, REQUIRED)
        elif field in expected:
            yield FieldContract(path, EXPECTED, expected[field])
        else:
            yield FieldContract(path, OPTIONAL)
        referent = _referent(sub, defs)
        if referent is None:
            continue
        name, target, is_list = referent
        if name in seen:
            continue
        yield from _walk(target, defs, f"{path}[]." if is_list else f"{path}.",
                         seen | {name})


def entity_field_contracts() -> dict[str, tuple[FieldContract, ...]]:
    """``entity_name`` → what the contract asks of each of its fields.

    The typed read a conformance suite asserts against, and a *rendering* of
    the published document rather than a second declaration: every status comes
    out of :func:`entity_schemas`' own output, so this cannot say anything
    ``docs/spec`` does not.

    JSON Schema alone answers only "may this be absent", and the answer that
    matters for the omission this module exists to catch is the third one:
    absent is permitted by the schema and is still a defect. :data:`EXPECTED`
    is that state, published as :data:`EXPECTED_KEYWORD` and read back here.
    It is in the hash, so downgrading one moves the version — see the module
    docstring for why that took two attempts to get right.

    The flattening is the value this adds over the raw document. Paths descend
    through ``$ref`` into nested records, so ``source_ref.recorded_at`` is
    stated on each of the nineteen entities rather than left as a reference a
    reader has to resolve.
    """
    documents = entity_schemas()
    contracts: dict[str, tuple[FieldContract, ...]] = {}
    for model in SPEC_ENTITIES:
        name = entity_name(model)
        document = documents[name]
        contracts[name] = tuple(_walk(
            document, document.get("$defs", {}), "",
            frozenset({model.__name__})))
    return contracts
