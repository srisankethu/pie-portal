"""What the contract asks, turned into something a connector can fail.

Every check below reads ``spec.entity_field_contracts()`` — the typed rendering
of the published JSON Schema documents — rather than a list of fields written
out here. That is deliberate and it is the whole design: a conformance suite
carrying its own copy of the contract has two homes for one concern, and the
copy that gets edited is never the copy that runs. Adding a field to a DTO
moves the contract and reaches these checks with no edit here at all.

Where the DTOs are captured
---------------------------
At ``app.ingestion.sync``'s own ``normalize_*`` names, wrapped for the duration
of one pull. The alternative was a table here mapping each source stage to the
normalizer that reads it — which ``sync.py`` already knows, spread across its
``_sync_*`` methods, and a second statement of it would drift the moment a
stage moved. Wrapping means the objects examined are exactly the objects the
real sync built, for exactly the stages that sync really runs.

The three statuses, and what each one is worth here
---------------------------------------------------
REQUIRED is the schema's own ``required``: the key must be present. Pydantic
already refuses a payload without it, so this check is a regression guard
rather than the point.

**EXPECTED is the point.** It marks a field the schema permits to be absent and
this contract does not — the state JSON Schema cannot express and the exact
shape of the incident. Two fields carry it today and both are read here.

OPTIONAL is asserted about in one direction only: nothing requires it, and
nothing here may quietly start to.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterator, Optional, Union, get_args, get_origin

from pydantic import BaseModel

from app.domain import spec
from app.ingestion import sync as sync_module

#: The one contract path the per-connector declaration answers, and the only
#: place this suite names a field. It is a join between two published
#: statements — ``ConnectorSpec.records_source_time`` says whether the ERP
#: exposes a system-record timestamp, and this is where the contract expects
#: the value to land — not a third declaration of either.
#:
#: ``test_the_contract_still_says_what_this_suite_screens_for`` asserts it is
#: really an EXPECTED contract on every entity. A screen with nothing in it
#: reports clean, and that is the failure mode this constant would have.
SOURCE_TIME_PATH = "source_ref.recorded_at"

#: Where the platform *reads* a source time back, and why it is these three.
#:
#: ``SalesTxn``, ``CostRecord`` and ``QuoteDocument`` each promote it out of
#: ``source_ref`` into a ``source_recorded_at`` column, and
#: ``commercial/quote_diagnosis`` filters evidence on that column. Those three
#: are where an absence causes the thing the expectation's own reason describes
#: — "every quote line that needed it answers INSUFFICIENT_EVIDENCE".
#:
#: Written out rather than derived, and then held against the read model by
#: ``test_the_evidence_entities_are_the_rows_that_promote_a_source_time``: a
#: fourth table gaining the column fails that pin, which is the moment somebody
#: decides rather than a silent widening. The same discipline ``SPEC_ENTITIES``
#: uses on itself.
EVIDENCE_ENTITIES: frozenset[str] = frozenset({"cost_record", "quote_doc", "sales_txn"})

#: Where the source time is *asserted*, which is a wider question than where it
#: is read.
#:
#: The contract publishes the expectation on all nineteen entities, because it
#: is keyed on ``SourceRef`` and every entity carries one. What a screen may
#: usefully ask for is narrower, and the bound is the connector rather than the
#: reader: **an entity belongs here when some registered connector actually
#: supplies ``created_time`` on the payload behind it.** Asking anywhere else
#: is the permanently-red check nobody can act on, which is a check people
#: learn to scroll past (CLAUDE.md §9).
#:
#: This used to be ``EVIDENCE_ENTITIES`` alone, for a reason that has since
#: been fixed rather than argued away: sixteen entities carried the marker and
#: no normalizer set ``recorded_at`` on any of them, so no connector *could*
#: satisfy it whatever its ERP held. ``normalize`` now calls ``_recorded_at``
#: from every normalizer that builds a ``SourceRef``, so the question is once
#: again about the connector, and three more entities answer it: ``invoice``
#: and ``bill`` ride the same two document payloads as ``sales_txn`` and
#: ``cost_record``, and ``payment_receipt`` rides a third that no other entity
#: screens at all — Acumatica and NetSuite both carry a creation stamp on it.
#:
#: The remaining thirteen stay out because no connector in the registry sends
#: one for them, which is a connector gap and is reported as one rather than as
#: an assertion that could only fail.
#:
#: Written out rather than derived, and the derivation pinned by
#: ``test_the_screened_entities_are_the_ones_a_connector_can_satisfy`` — which
#: fails both ways: at an entry nothing carries, and at a connector that starts
#: carrying one for an entity this set leaves out.
SOURCE_TIME_ENTITIES: frozenset[str] = EVIDENCE_ENTITIES | {
    "bill", "invoice", "payment_receipt"}

#: Placeholder for a connector that has declared nothing. Distinct from
#: ``False``, which is a claim about the ERP somebody can check; absence is not
#: a permitted answer and must not read like one.
UNDECLARED = object()


@dataclass(frozen=True)
class Declaration:
    """What a connector says about its ERP's system-record timestamp.

    Read off ``ConnectorSpec`` rather than taken as one, so the broken fixture
    connector can make the same claims without being registrable anywhere.
    """

    key: str
    records_source_time: Any
    source_time_note: str


def declaration_of(connector_spec: Any) -> Declaration:
    return Declaration(
        key=connector_spec.key,
        records_source_time=getattr(connector_spec, "records_source_time", UNDECLARED),
        source_time_note=str(getattr(connector_spec, "source_time_note", "") or ""),
    )


@dataclass(frozen=True)
class Emission:
    """One canonical record a connector produced, and what it was built from.

    ``payload`` is the connector's own output — the canonical wire shape its
    translator emits. ``record`` is what ``normalize`` made of it. Both are
    kept because the two answer different questions: whether the ERP's dress
    was read correctly, and whether the result satisfies the contract.
    """

    connector: str
    entity: str
    payload: dict[str, Any]
    record: BaseModel

    def values(self) -> dict[str, Any]:
        return self.record.model_dump()

    def where(self) -> str:
        return f"{self.connector}/{self.entity}"


# ── capture ──────────────────────────────────────────────────────────────────
_ENTITY_OF: dict[str, str] = {
    model.__name__: spec.entity_name(model) for model in spec.SPEC_ENTITIES
}


def _records(value: Any) -> Iterator[BaseModel]:
    """Every contract entity inside whatever a normalizer returned.

    Normalizers return one DTO, a list of them (invoice and bill lines), or a
    tuple of a document and its applications. Walking the result rather than
    knowing which does what keeps this blind to the stage table above it.
    """
    if isinstance(value, BaseModel):
        if type(value).__name__ in _ENTITY_OF:
            yield value
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _records(item)


@contextlib.contextmanager
def capturing(connector: str, sink: list[Emission]) -> Iterator[None]:
    """Record every canonical DTO the sync builds, at the one place it builds
    them.

    Wraps the ``normalize_*`` names bound into ``app.ingestion.sync``, by
    prefix rather than by a list — a normalizer added to the pipeline is
    captured without an edit here, which is the same discipline the checks
    follow with the contract.
    """
    wrapped = [name for name in dir(sync_module)
               if name.startswith("normalize_") and callable(getattr(sync_module, name))]
    assert wrapped, "no normalizers found on the sync module — the capture screens nothing"
    originals = {name: getattr(sync_module, name) for name in wrapped}

    def wrap(fn):
        def recorded(raw, *args, **kwargs):
            result = fn(raw, *args, **kwargs)
            payload = raw if isinstance(raw, dict) else {}
            for record in _records(result):
                sink.append(Emission(connector, _ENTITY_OF[type(record).__name__],
                                     payload, record))
            return result
        return recorded

    for name, fn in originals.items():
        setattr(sync_module, name, wrap(fn))
    try:
        yield
    finally:
        for name, fn in originals.items():
            setattr(sync_module, name, fn)


# ── reading a contract path off an emitted record ────────────────────────────
def _resolve(value: Any, parts: list[str]) -> Iterator[tuple[str, Any]]:
    """Every concrete value a dotted contract path selects, with its own path.

    ``[]`` in a contract path means "of each element that is present", which is
    load-bearing: a REQUIRED status under a list is required of the elements
    there are, and reading it as "the list must be non-empty" would reject an
    invoice paid in one go.
    """
    if not parts:
        yield "", value
        return
    head, rest = parts[0], parts[1:]
    is_list = head.endswith("[]")
    key = head[:-2] if is_list else head
    if not isinstance(value, dict) or key not in value:
        return
    inner = value[key]
    if not is_list:
        for tail, found in _resolve(inner, rest):
            yield (f"{key}.{tail}" if tail else key), found
        return
    for index, element in enumerate(inner or []):
        for tail, found in _resolve(element, rest):
            yield (f"{key}[{index}].{tail}" if tail else f"{key}[{index}]"), found


def values_at(values: dict[str, Any], path: str) -> list[tuple[str, Any]]:
    return list(_resolve(values, path.split(".")))


def _declares(values: dict[str, Any], path: str) -> bool:
    """Whether the record carries the key at all, ignoring what is in it.

    A status below a ``[]`` is required *of each element that is present*, so
    an empty list satisfies it vacuously — reading it as "the list must be
    non-empty" would reject an invoice that bills no sales order, which is the
    ordinary case for a counter sale.
    """
    parts = path.split(".")
    parent, leaf = parts[:-1], parts[-1]
    if not parent:
        return leaf in values
    nodes = [node for _, node in _resolve(values, parent)]
    if not nodes:
        return "[]" in path
    return any(isinstance(node, dict) and leaf in node for node in nodes)


# ── the checks ───────────────────────────────────────────────────────────────
# Each returns the failures it found, one readable line apiece naming the
# connector, the entity and the field. A list rather than an assertion so a
# single run can report everything that is wrong rather than the first thing —
# the same reason `scripts/verify.sh` does not stop at its first red step.


def missing_required(emissions: list[Emission], _: Declaration) -> list[str]:
    """Contract 1, the schema's own half: every REQUIRED key is present."""
    out = []
    contracts = spec.entity_field_contracts()
    for emission in emissions:
        values = emission.values()
        for contract in contracts[emission.entity]:
            if contract.status != spec.REQUIRED:
                continue
            if not _declares(values, contract.path):
                out.append(f"{emission.where()}: REQUIRED {contract.path} is not present")
    return out


def missing_expected(emissions: list[Emission], declaration: Declaration) -> list[str]:
    """Contract 2: an EXPECTED field carries a value, or the connector has
    said why its system cannot supply one.

    ``source_ref.recorded_at`` is the field the whole suite exists for, and the
    only one with a declared escape: a connector whose ERP exposes no
    system-record timestamp says so on its spec. There is no third state — a
    connector that has declared nothing fails here, because silence is exactly
    what the incident looked like. It is read on ``SOURCE_TIME_ENTITIES``; see
    that constant for the scope and for what is deliberately left out of it.

    One line per (entity, field) rather than per record. A connector emitting
    four hundred invoice lines with no source time has one defect, and four
    hundred lines of it is a failure nobody finishes reading.
    """
    seen: set[tuple[str, str]] = set()
    out = []
    contracts = spec.entity_field_contracts()
    for emission in emissions:
        values = emission.values()
        for contract in contracts[emission.entity]:
            if contract.status != spec.EXPECTED:
                continue
            if contract.path == SOURCE_TIME_PATH:
                if emission.entity not in SOURCE_TIME_ENTITIES:
                    continue
                if declaration.records_source_time is UNDECLARED:
                    key = (emission.entity, "undeclared")
                    if key not in seen:
                        seen.add(key)
                        out.append(
                            f"{emission.where()}: {contract.path} is EXPECTED and "
                            f"{declaration.key} declares neither records_source_time nor "
                            f"source_time_note — absence of the declaration is not an answer")
                    continue
                if declaration.records_source_time is not True:
                    continue
            for where, found in values_at(values, contract.path) or [(contract.path, None)]:
                if found is not None:
                    continue
                key = (emission.entity, contract.path)
                if key in seen:
                    continue
                seen.add(key)
                out.append(f"{emission.where()}: EXPECTED {where} is absent"
                           f"{_why_absent(emission, contract.path)} — {contract.reason}")
    return out


#: The canonical payload key a source time arrives under, read only to tell a
#: reader *which layer* lost it. Never used to decide whether the contract is
#: satisfied — that is the record's business, and a check that accepted a
#: carried-but-unusable stamp would pass a connector contributing no evidence.
_SOURCE_TIME_KEY = "created_time"


def _why_absent(emission: Emission, path: str) -> str:
    """Whether the connector sent nothing, or sent something unplaceable.

    Carrying the stamp is necessary and not sufficient. ``clock.utc_stamp``
    refuses a timestamp with no offset and ``normalize._recorded_at`` drops what
    it refuses, so a connector can put a real creation time in its payload and
    still write a NULL ``source_recorded_at`` — the same silent success as the
    incident, one layer along. The two have different remedies and a failure
    that does not say which one applies sends somebody to the wrong file.
    """
    if path != SOURCE_TIME_PATH:
        return ""
    sent = emission.payload.get(_SOURCE_TIME_KEY)
    if not sent:
        return f" and the payload carried no {_SOURCE_TIME_KEY}"
    return (f" although the payload carried {_SOURCE_TIME_KEY}={sent!r} — it was "
            f"dropped in normalisation, so the remedy is the stamp's form "
            f"(clock.utc_stamp refuses one with no offset), not the connector's "
            f"reading of it")


def _decimal_paths(model: type[BaseModel], prefix: str = "",
                   seen: frozenset[str] = frozenset()) -> Iterator[str]:
    """Every field of a contract entity typed ``Decimal``, nested included.

    Read off the models the published schemas are generated from, which is the
    same declaration rather than a second one: ``Decimal`` in ``schemas.py`` is
    what becomes ``anyOf[number, string]`` in ``docs/spec``.
    """
    for name, field in model.model_fields.items():
        annotation = field.annotation
        members = get_args(annotation) if get_origin(annotation) is Union else (annotation,)
        if Decimal in members:
            yield f"{prefix}{name}"
        for member in members:
            inner = get_args(member)[0] if get_origin(member) is list and get_args(member) else member
            if (isinstance(inner, type) and issubclass(inner, BaseModel)
                    and inner.__name__ not in seen):
                suffix = "[]." if get_origin(member) is list else "."
                yield from _decimal_paths(inner, f"{prefix}{name}{suffix}",
                                          seen | {inner.__name__})


def money_is_not_decimal(emissions: list[Emission], _: Declaration) -> list[str]:
    """Contract 3, first half: a money field holds ``Decimal``, never ``float``.

    ``bool`` is excluded from nothing and ``int`` is not accepted either: the
    DTOs convert through ``Decimal(str(v))`` precisely so a binary float never
    becomes a stored figure, and a value that arrived past that conversion is a
    field somebody added without the validator.
    """
    out = []
    for emission in emissions:
        values = emission.values()
        for path in _decimal_paths(type(emission.record)):
            for where, found in values_at(values, path):
                if found is None or isinstance(found, Decimal):
                    continue
                out.append(f"{emission.where()}: {where} is {type(found).__name__} "
                           f"({found!r}), not Decimal")
    return out


def _floats(value: Any, path: str = "") -> Iterator[tuple[str, float]]:
    if isinstance(value, float):
        yield path, value
    elif isinstance(value, dict):
        for key, inner in value.items():
            yield from _floats(inner, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, inner in enumerate(value):
            yield from _floats(inner, f"{path}[{index}]")


def money_computed_in_float(emissions: list[Emission], _: Declaration) -> list[str]:
    """Contract 3, first half again, one layer earlier — where it actually bites.

    Every fixture in ``connectors/`` states its ERP's numbers as strings, which
    is what these APIs mostly send and, more to the point, what makes this
    check mean something: a value that passes through a translator untouched
    stays a string, so **a ``float`` in a canonical payload is a number the
    translator computed**. ``Decimal(str(x))`` of a computed float carries the
    binary residue into the stored figure, and the DTO check above cannot see
    it because by then it is a perfectly well-formed ``Decimal``.
    """
    out = []
    for emission in emissions:
        for where, found in _floats(emission.payload):
            out.append(f"{emission.where()}: payload {where} is a float ({found!r}) — "
                       f"computed in binary floating point before the Decimal conversion")
    return out


#: The two line-grain entities and the fields that carry a discount, so the
#: rule below can be stated once. Written out rather than derived because
#: "which field is the list rate and which is the amount actually paid" is a
#: business fact the schema cannot say — ``rate`` and ``unit_price`` are both
#: just ``Decimal`` to it. Their prose in ``schemas.py`` is the source; this is
#: the same pair of statements in a form a check can read.
_DISCOUNT_GRAIN: dict[str, tuple[str, str, Optional[str]]] = {
    "sales_txn": ("rate", "unit_price", "line_revenue"),
    "cost_record": ("rate", "unit_cost", None),
}


def line_total_is_the_list_rate(emissions: list[Emission], _: Declaration) -> list[str]:
    """Contract 3, second half: a line is net of its discount, never the list
    rate.

    A live defect class rather than a hypothetical — found on the quote side
    and again on the bill side, which is why it is checked on both grains here.
    ``normalize._effective_unit_amount`` is the ladder that resolves it; this
    asserts the result reached the record.

    The fixtures are required to carry a discounted line, and a fixture without
    one fails rather than passes. A check that runs against nothing reports
    clean, which is the failure mode this whole package is about.
    """
    out = []
    discounted = 0
    for emission in emissions:
        grain = _DISCOUNT_GRAIN.get(emission.entity)
        if grain is None:
            continue
        rate_field, net_field, total_field = grain
        values = emission.values()
        rate, net = values.get(rate_field), values.get(net_field)
        if rate is None or net is None:
            continue
        if net > rate:
            out.append(f"{emission.where()}: {net_field} {net} is above the list "
                       f"{rate_field} {rate} — the discount has been read backwards")
            continue
        if net == rate:
            continue
        discounted += 1
        qty = values.get("qty")
        if total_field is None or qty is None:
            continue
        total = values.get(total_field)
        if total is not None and total == qty * rate:
            out.append(f"{emission.where()}: {total_field} {total} is qty x the list "
                       f"{rate_field} {rate}, not qty x the net {net_field} {net}")
    if emissions and not discounted:
        out.append(f"{emissions[0].connector}: no emitted line carries a discount, so "
                   f"the net-of-discount rule was screened against nothing")
    return out


#: Strings that mean a reference was assembled out of something absent.
#: ``f"{doc_id}:{line_id}"`` over a missing id produces a stable, non-empty,
#: entirely useless key, which every other check here would accept.
_NOT_A_REFERENCE = ("none", "null", "nan", "")


def _reference_fields(values: dict[str, Any]) -> list[str]:
    """Which fields carry this record's identity in the source system.

    Most entities key on their own ``external_ref`` or ``external_id``. The
    snapshots do not have one — a stock reading is identified by the product it
    is of and the day it was taken — so where neither is present the foreign
    references are what has to be usable instead. Derived from the record
    rather than tabulated per entity, so a new DTO needs no edit here.
    """
    named = [field for field in ("external_ref", "external_id") if field in values]
    if named:
        return named
    return [field for field in values if field.endswith("external_id")]


def identity_is_unusable(emissions: list[Emission], declaration: Declaration) -> list[str]:
    """Contract 4: external ids are non-empty and real, and ``SourceRef`` is
    populated with the system that actually read the record."""
    out = []
    for emission in emissions:
        values = emission.values()
        fields = _reference_fields(values)
        if not fields:
            out.append(f"{emission.where()}: carries no external reference of any kind")
        for reference_field in fields:
            raw = values[reference_field]
            if raw is None:
                # A nullable foreign reference is the contract's business:
                # ``cost_record.vendor_external_id`` is EXPECTED and checked
                # there, and a sales order with no customer is OPTIONAL there.
                continue
            reference = str(raw)
            if reference.strip().lower() in _NOT_A_REFERENCE:
                out.append(f"{emission.where()}: {reference_field} is {reference!r}")
            elif any(part.strip().lower() in _NOT_A_REFERENCE
                     for part in reference.split(":")):
                out.append(f"{emission.where()}: {reference_field} {reference!r} was built "
                           f"from something absent")
        ref = values.get("source_ref") or {}
        for field in ("system", "record_type", "record_id"):
            if str(ref.get(field) or "").strip().lower() in _NOT_A_REFERENCE:
                out.append(f"{emission.where()}: source_ref.{field} is {ref.get(field)!r}")
        if ref.get("system") and ref["system"] != declaration.key:
            out.append(f"{emission.where()}: source_ref.system is {ref['system']!r}, "
                       f"but this pull read {declaration.key!r}")
    return out


#: Every check, in the order the contract states them. Named so a parameterised
#: run reports which one failed rather than only that something did.
CHECKS = {
    "required-fields-present": missing_required,
    "expected-fields-carried": missing_expected,
    "money-is-decimal": money_is_not_decimal,
    "money-not-computed-in-float": money_computed_in_float,
    "line-total-net-of-discount": line_total_is_the_list_rate,
    "identity-and-provenance": identity_is_unusable,
}
