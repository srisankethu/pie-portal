"""The published ingestion contract, as assertions rather than as a convention.

Four things are pinned here, and they fail for four different reasons.

**Membership.** Every ``BaseModel`` in ``domain/schemas.py`` is an entity, a
declared component, or a declared non-ingestion type. A DTO added to that module
and forgotten here fails the suite instead of quietly staying outside the
contract — the discipline ``test_layer_boundaries.py`` arrived at for packages,
for the same reason: a thing nothing screens reads exactly like a clean one.

**Determinism.** ``spec_version()`` is stamped on rows, so a stamp that moves
between runs on unchanged code is not a rounding note, it is the whole mechanism
failing. Proved across two subprocesses under different interpreter hash seeds,
not asserted from one call in one process.

**Absence.** ``source_recorded_at`` is the field this contract exists to
protect. PIE's own Zoho client dropped ``created_time`` from all three document
projections, every row landed with a NULL stamp, every quote line answered
INSUFFICIENT_EVIDENCE, and the sync reported success. The expectation that says
its absence is a defect is published *in* the document and therefore *in* the
hash, so a downgrade moves the version — and the test that proves that is the
one to look at first if this file ever needs trusting.

**Description.** An entity whose DTO carries no class docstring publishes with
no ``description`` at all, and nothing anywhere fails — which is how eight of
the nineteen went out saying nothing about what they were. ``spec_export.py``
renders that absence honestly, but the artifact is the only thing an integrator
reads, so the gap belongs in a failing assertion here rather than in a note in
the published document.

These assertions are the second line and not the first. A test can be edited in
the same commit as the change it guards, which is why the marker is in the hash
rather than only here. What they add is naming the fault: a moved hash says
something changed, and these say which thing.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.domain import schemas, spec

BACKEND = Path(__file__).resolve().parents[2]

#: Every name this module publishes, pinned literally. A rename is then a
#: failing assertion naming both sides rather than a hash that moved for
#: reasons nobody can see in a diff of the digest.
ENTITY_NAMES = (
    "bill",
    "cost_record",
    "credit_note",
    "credit_note_application",
    "customer",
    "invoice",
    "location",
    "payment_receipt",
    "product",
    "purchase_order",
    "quote_doc",
    "sales_order",
    "sales_txn",
    "stock_location_snapshot",
    "stock_snapshot",
    "vendor",
    "vendor_credit",
    "vendor_credit_application",
    "vendor_payment",
)


def _dtos() -> dict[str, type[BaseModel]]:
    """Every pydantic model declared in ``schemas.py`` — the population the
    membership rules are asserted over."""
    return {
        name: obj for name, obj in vars(schemas).items()
        if isinstance(obj, type) and issubclass(obj, BaseModel)
        and obj.__module__ == schemas.__name__
    }


def _definitions() -> dict[str, dict]:
    """Every record definition reachable in the published document, keyed by the
    class name pydantic files it under: each entity's own root document, plus
    everything in any entity's ``$defs``."""
    found: dict[str, dict] = {}
    documents = spec.entity_schemas()
    for model in spec.SPEC_ENTITIES:
        document = documents[spec.entity_name(model)]
        found[model.__name__] = document
        for name, definition in document.get("$defs", {}).items():
            found.setdefault(name, definition)
    return found


def _published_expectations() -> set[tuple[str, str]]:
    """``(declaring type, field)`` for every expectation actually present in the
    published document — read out of the artifact, never out of ``_EXPECTED``."""
    return {(owner, field)
            for owner, definition in _definitions().items()
            for field in definition.get(spec.EXPECTED_KEYWORD, {})}


def _run(code: str, *, seed: str) -> str:
    """The same code in a separate interpreter, under a chosen hash seed."""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": seed, "PYTHONPATH": str(BACKEND)},
        check=True)
    return result.stdout.strip()


def _digest_of(documents: dict) -> str:
    """A spec version over some other set of documents, for the tests that ask
    what would have to change for the stamp to move."""
    body = json.dumps(documents, sort_keys=True, separators=(",", ":"))
    return "spec_" + hashlib.sha256(body.encode()).hexdigest()[:10]


# ── membership ───────────────────────────────────────────────────────────────
def test_every_dto_in_schemas_is_an_entity_a_component_or_declared_out():
    declared = ({m.__name__ for m in spec.SPEC_ENTITIES}
                | set(spec._COMPONENTS) | set(spec._NOT_INGESTION))
    missing = set(_dtos()) - declared
    assert not missing, (
        f"{sorted(missing)} are in schemas.py and in none of SPEC_ENTITIES, "
        "_COMPONENTS or _NOT_INGESTION. A new DTO joins the published contract "
        "or is excluded with a reason — it does not do neither by default.")
    stale = declared - set(_dtos())
    assert not stale, f"{sorted(stale)} are declared in spec.py but no longer exist"


def test_the_three_membership_lists_do_not_overlap():
    entities = {m.__name__ for m in spec.SPEC_ENTITIES}
    assert not entities & set(spec._COMPONENTS)
    assert not entities & set(spec._NOT_INGESTION)
    assert not set(spec._COMPONENTS) & set(spec._NOT_INGESTION)


def test_the_entities_are_exactly_the_dtos_carrying_their_own_provenance():
    """The inclusion rule stated in ``SPEC_ENTITIES``, asserted against the list.

    A ``source_ref`` is a pointer to one source record, so a type that has one
    is a document a connector produces and a type that has none exists only
    inside a parent. If this ever diverges, either the list is wrong or the
    rule is — and the docstring that states the rule has to be rewritten rather
    than the assertion relaxed.
    """
    by_rule = {name for name, dto in _dtos().items()
               if "source_ref" in dto.model_fields}
    assert by_rule == {m.__name__ for m in spec.SPEC_ENTITIES}


def test_every_declared_component_really_is_published_inside_an_entity():
    """A component is excluded from the entity list *because* it rides in
    ``$defs``. One that no entity references any more is not excluded, it is
    unpublished — and the declaration would go on claiming otherwise."""
    published = set()
    for document in spec.entity_schemas().values():
        published |= set(document.get("$defs", {}))
    orphans = set(spec._COMPONENTS) - published
    assert not orphans, f"{sorted(orphans)} are declared components of nothing"


def test_the_api_read_dtos_are_nowhere_in_the_published_document():
    document = spec.canonical_json()
    for name in spec._NOT_INGESTION:
        assert f'"{name}"' not in document


def test_spec_entities_is_a_fixed_order_with_no_duplicates():
    names = [spec.entity_name(m) for m in spec.SPEC_ENTITIES]
    assert names == sorted(names), "SPEC_ENTITIES is ordered by entity_name"
    assert len(set(names)) == len(names)


# ── naming ───────────────────────────────────────────────────────────────────
def test_entity_name_is_pinned_for_every_entity():
    assert tuple(spec.entity_name(m) for m in spec.SPEC_ENTITIES) == ENTITY_NAMES


def test_entity_name_strips_the_in_suffix_and_snake_cases_the_rest():
    assert spec.entity_name(schemas.CustomerIn) == "customer"
    assert spec.entity_name(schemas.SalesTxnIn) == "sales_txn"
    assert spec.entity_name(schemas.StockLocationSnapshotIn) == "stock_location_snapshot"
    # Not every model is an ``*In``; the function stays total for the ones that
    # are not, which is what lets a component be named in a diagnostic.
    assert spec.entity_name(schemas.SourceRef) == "source_ref"


# ── the document ─────────────────────────────────────────────────────────────
def test_entity_schemas_covers_every_entity_and_nothing_else():
    assert set(spec.entity_schemas()) == set(ENTITY_NAMES)


def test_entity_schemas_returns_a_fresh_document_each_call():
    """Cached mutable state shared between callers is a defect waiting for the
    first caller that edits what it was handed — and the annotation pass writes
    into these documents in place, so a shared one would accumulate."""
    first = spec.entity_schemas()
    first["customer"]["properties"]["name"]["title"] = "mutated"
    assert spec.entity_schemas()["customer"]["properties"]["name"]["title"] == "Name"


def test_shared_components_ride_in_defs_rather_than_being_entities():
    customer = spec.entity_schemas()["customer"]
    assert "SourceRef" in customer["$defs"]
    assert customer["properties"]["source_ref"]["$ref"] == "#/$defs/SourceRef"
    assert "source_ref" not in ENTITY_NAMES  # never an entity of its own
    assert "SourceRef" not in {m.__name__ for m in spec.SPEC_ENTITIES}


def test_every_entity_publishes_a_description():
    """Pydantic lifts a class docstring into ``description`` and lifts nothing
    else, so a DTO written with only field comments joins the contract saying
    nothing about what it is. An integrator has this artifact and no access to
    ``schemas.py``, which makes a silent entity a gap in the contract rather
    than a record whose meaning is obvious."""
    silent = sorted(name for name, document in spec.entity_schemas().items()
                    if not (document.get("description") or "").strip())
    assert not silent, (
        f"{silent} publish no description at all. Give the DTO in schemas.py a "
        "class docstring — that is the only prose pydantic lifts.")


def test_canonical_json_is_the_sorted_compact_serialisation_of_the_schemas():
    assert spec.canonical_json() == json.dumps(
        spec.entity_schemas(), sort_keys=True, separators=(",", ":"))


def test_canonical_json_is_published_so_the_hash_can_be_checked_by_hand():
    """The pre-image is public for the reason ``CommercialThresholds.serialized``
    is: a hash does not invert, and a stamp nobody can resolve to the contract
    it stood for is only distinguishable, never explainable."""
    recomputed = "spec_" + hashlib.sha256(
        spec.canonical_json().encode()).hexdigest()[:10]
    assert recomputed == spec.spec_version()


# ── determinism ──────────────────────────────────────────────────────────────
def test_the_version_is_identical_on_two_calls_with_the_cache_cleared():
    first = spec.spec_version()
    spec.spec_version.cache_clear()
    spec.canonical_json.cache_clear()
    assert spec.spec_version() == first


def test_the_version_is_identical_in_two_separate_processes():
    """The claim that matters, and the one an in-process assertion cannot make.

    Different interpreter hash seeds, so a set iteration anywhere in schema
    generation or in the annotation pass would show up here rather than on the
    day two machines disagree about what a stamped row means.
    """
    code = "from app.domain import spec; print(spec.spec_version())"
    one = _run(code, seed="0")
    two = _run(code, seed="99991")
    assert one == two == spec.spec_version()


def test_the_canonical_bytes_are_identical_in_two_separate_processes():
    code = ("import hashlib;from app.domain import spec;"
            "b=spec.canonical_json().encode();"
            "print(len(b), hashlib.sha256(b).hexdigest())")
    assert _run(code, seed="1") == _run(code, seed="524287")


def test_the_version_has_the_shape_the_other_stamps_in_this_codebase_have():
    version = spec.spec_version()
    assert re.fullmatch(r"spec_[0-9a-f]{10}", version), version


def test_the_version_moves_when_a_schema_constraint_changes():
    """A hash nothing can move is a hash that proves nothing. Hashed over a
    mutated copy rather than by editing a DTO, so the check costs no global
    state."""
    mutated = spec.entity_schemas()
    mutated["customer"]["properties"]["name"]["minLength"] = 2
    assert _digest_of(mutated) != spec.spec_version()


def test_minting_the_version_remembers_its_pre_image():
    """``CommercialThresholds.version`` records at the mint for a reason that
    holds here: minting is the only moment the bytes behind a stamp are in
    hand."""
    from app import threshold_registry

    assert threshold_registry.pre_image("spec", spec.spec_version()) \
        == spec.canonical_json()


# ── absence, in the document ─────────────────────────────────────────────────
def test_the_expectation_declaration_is_not_empty():
    """A screen with nothing in it reports clean. This is the assertion that
    keeps ``_EXPECTED`` from being emptied and every conformance run from then
    on passing for the wrong reason."""
    assert spec._EXPECTED
    assert _published_expectations()


def test_the_expectation_is_published_beside_required_on_the_declaring_record():
    """The marker's scope is the point: it sits on the definition that owns the
    field and lists that definition's own field names, which is exactly how
    ``required`` is read. An integrator reading ``docs/spec/bill.json`` sees the
    two together or does not see the second one at all."""
    source_ref = spec.entity_schemas()["bill"]["$defs"]["SourceRef"]
    assert set(source_ref["required"]) == {"system", "record_type", "record_id"}
    assert set(source_ref[spec.EXPECTED_KEYWORD]) == {"recorded_at"}
    assert "INSUFFICIENT_EVIDENCE" in source_ref[spec.EXPECTED_KEYWORD]["recorded_at"]


def test_one_declaration_reaches_every_document_that_carries_the_field():
    """``SourceRef.recorded_at`` is declared once and holds at all nineteen
    places a ``source_ref`` appears. Nineteen copies would be nineteen chances
    to update eighteen, so this is the assertion that the once-declared form
    really does arrive everywhere."""
    documents = spec.entity_schemas()
    assert len(documents) == 19
    for name, document in documents.items():
        marker = document["$defs"]["SourceRef"].get(spec.EXPECTED_KEYWORD, {})
        assert "recorded_at" in marker, name


def test_the_marker_never_names_a_field_the_schema_already_requires():
    """An EXPECTED marker on a required field says nothing JSON Schema does not
    already say, and reads as a screen doing work it is not doing."""
    for owner, definition in _definitions().items():
        overlap = (set(definition.get(spec.EXPECTED_KEYWORD, {}))
                   & set(definition.get("required", ())))
        assert not overlap, f"{owner}: {sorted(overlap)} is required and expected"


def test_the_marker_never_names_a_field_that_does_not_exist():
    for owner, definition in _definitions().items():
        for field in definition.get(spec.EXPECTED_KEYWORD, {}):
            assert field in definition["properties"], f"{owner}.{field}"


def test_the_marker_keyword_does_not_collide_with_one_pydantic_emits():
    """The extension prefix earns its keep only if it is genuinely unused. A
    collision would mean this module silently overwriting a standard keyword."""
    for model in spec.SPEC_ENTITIES:
        raw = model.model_json_schema(by_alias=True, mode="validation")
        assert spec.EXPECTED_KEYWORD not in raw
        for definition in raw.get("$defs", {}).values():
            assert spec.EXPECTED_KEYWORD not in definition


def test_every_expectation_declared_is_actually_published():
    """A declaration naming a type that is no longer in the contract writes
    nothing into any document and is a no-op nobody would notice."""
    unpublished = set(spec._EXPECTED) - _published_expectations()
    assert not unpublished, f"{sorted(unpublished)} are declared but reach no document"


def test_every_expectation_published_was_actually_declared():
    assert _published_expectations() == set(spec._EXPECTED)


def test_every_expectation_names_a_field_that_exists_and_is_really_optional():
    """The way a declaration like this goes quietly wrong: the field is renamed,
    or it becomes required, and the entry stays — asserting something about
    nothing. Either case reads as a passing screen. Asserted against
    ``_EXPECTED`` directly rather than against the published marker, so a bad
    declaration is caught even in the case where publishing it silently did
    nothing."""
    definitions = _definitions()
    for (owner, field), reason in spec._EXPECTED.items():
        assert owner in definitions, f"{owner} is not a published record"
        definition = definitions[owner]
        assert field in definition["properties"], f"{owner}.{field} does not exist"
        assert field not in definition.get("required", ()), (
            f"{owner}.{field} is already required by the schema — an EXPECTED "
            "marker on it says nothing that JSON Schema does not already say")
        assert reason.strip(), f"{owner}.{field} is declared with no reason"


# ── absence, in the hash ─────────────────────────────────────────────────────
def test_downgrading_source_recorded_at_moves_the_version():
    """The regression this contract is for, as an assertion.

    Editing ``SourceRef.recorded_at`` from EXPECTED to OPTIONAL is exactly the
    change the stamp has to be able to name — a connector omitting it would
    then look compliant, which is the incident. Because the marker rides in the
    published document, the hash moves; when it lived beside the document, this
    edit was invisible to the version and only this file stood in its way.
    """
    downgraded = spec.entity_schemas()
    for document in downgraded.values():
        document["$defs"]["SourceRef"][spec.EXPECTED_KEYWORD].pop("recorded_at")
    assert _digest_of(downgraded) != spec.spec_version()


def test_removing_every_expectation_moves_the_version():
    stripped = spec.entity_schemas()
    for document in stripped.values():
        document.pop(spec.EXPECTED_KEYWORD, None)
        for definition in document.get("$defs", {}).values():
            definition.pop(spec.EXPECTED_KEYWORD, None)
    assert _digest_of(stripped) != spec.spec_version()


def test_rewording_a_reason_moves_the_version():
    """The reason is published, so it is contract text and not a comment. An
    expectation whose argument can be rewritten under a stable stamp is an
    instruction a reader has no way to check they still understand."""
    reworded = spec.entity_schemas()
    for document in reworded.values():
        document["$defs"]["SourceRef"][spec.EXPECTED_KEYWORD]["recorded_at"] = "because"
    assert _digest_of(reworded) != spec.spec_version()


# ── absence, as a conformance suite would read it ────────────────────────────
def test_source_recorded_at_is_expected_on_every_single_entity():
    contracts = spec.entity_field_contracts()
    assert set(contracts) == set(ENTITY_NAMES)
    for name, fields in contracts.items():
        matching = [f for f in fields if f.path == "source_ref.recorded_at"]
        assert len(matching) == 1, name
        assert matching[0].status == spec.EXPECTED, name
        assert "INSUFFICIENT_EVIDENCE" in matching[0].reason


def test_a_connector_omitting_source_recorded_at_is_visibly_defective():
    """The incident, as the assertion a conformance suite would make. The DTO
    validates a payload with no ``recorded_at`` — it has to, a source may
    genuinely have no such stamp — so validation alone cannot be what catches
    this. The published contract can."""
    payload = {"system": "acme", "record_type": "invoice", "record_id": "7"}
    assert schemas.SourceRef(**payload).recorded_at is None

    status = {f.path: f.status
              for f in spec.entity_field_contracts()["sales_txn"]}
    assert status["source_ref.recorded_at"] == spec.EXPECTED
    assert status["source_ref.recorded_at"] != spec.OPTIONAL


def test_the_field_contracts_are_a_rendering_of_the_document_not_a_second_list():
    """One declaration, two renderings. Every EXPECTED status has to trace back
    to a marker in the published document, or the typed read is saying something
    ``docs/spec`` does not — which is the two-declarations failure this design
    exists to avoid."""
    published = {field for _, field in _published_expectations()}
    for name, fields in spec.entity_field_contracts().items():
        for field in fields:
            if field.status == spec.EXPECTED:
                assert field.path.rsplit(".", 1)[-1] in published, f"{name}:{field.path}"


def test_the_three_statuses_are_used_and_only_expected_carries_a_reason():
    seen = set()
    for fields in spec.entity_field_contracts().values():
        for field in fields:
            seen.add(field.status)
            assert bool(field.reason) == (field.status == spec.EXPECTED), field
    assert seen == {spec.REQUIRED, spec.EXPECTED, spec.OPTIONAL}


def test_top_level_statuses_come_from_the_documents_own_two_keywords():
    """``required`` and ``x-pie-expected`` sit side by side on the definition,
    and the walk reads both off it. Nothing here consults ``_EXPECTED``, which
    is what makes the two renderings incapable of disagreeing."""
    document = spec.entity_schemas()["cost_record"]
    required = set(document["required"])
    expected = set(document.get(spec.EXPECTED_KEYWORD, {}))
    for field in spec.entity_field_contracts()["cost_record"]:
        if "." in field.path or "[]" in field.path:
            continue
        if field.path in required:
            assert field.status == spec.REQUIRED, field.path
        elif field.path in expected:
            assert field.status == spec.EXPECTED, field.path
        else:
            assert field.status == spec.OPTIONAL, field.path


def test_paths_descend_into_nested_records_and_mark_list_elements():
    paths = {f.path for f in spec.entity_field_contracts()["payment_receipt"]}
    assert "source_ref.recorded_at" in paths
    assert "applications[].document_date" in paths, (
        "a list element's fields are per element — a suite that read this as a "
        "plain path would demand the list be non-empty")


def test_field_paths_are_unique_within_an_entity():
    for name, fields in spec.entity_field_contracts().items():
        paths = [f.path for f in fields]
        assert len(set(paths)) == len(paths), name


def test_an_enum_is_not_walked_as_if_it_were_a_nested_record():
    paths = {f.path for f in spec.entity_field_contracts()["customer"]}
    assert "status" in paths
    assert not any(p.startswith("status.") for p in paths)


@pytest.mark.parametrize("entity", ENTITY_NAMES)
def test_every_entity_states_a_contract_for_every_field_it_publishes(entity):
    document = spec.entity_schemas()[entity]
    top_level = {f.path for f in spec.entity_field_contracts()[entity]
                 if "." not in f.path and "[]" not in f.path}
    assert top_level == set(document["properties"])
