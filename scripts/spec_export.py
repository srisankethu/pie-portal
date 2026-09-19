#!/usr/bin/env python3
"""Publish the canonical ingestion spec to ``docs/spec/`` — one JSON Schema per
entity, plus the README an integrator actually opens.

``app/domain/spec.py`` is the mechanism: it selects the ingestion DTOs, names
them, annotates them with what this contract expects, serialises them and hashes
the result. This script is the *publication* of that, and nothing more. It
computes no schema, decides no membership and states no field. Every line it
writes comes out of ``entity_schemas()``, ``entity_field_contracts()`` and
``spec_version()``, so a new field on a DTO reaches the artifact by itself and
an edit here cannot make the artifact disagree with the code.

Why the artifact is committed rather than built on demand
--------------------------------------------------------
An integrator writing a connector has this repository's ``docs/`` and nothing
else — no interpreter, no dependencies, no idea that ``model_json_schema``
exists. A contract they cannot read is a contract they will guess at, and the
incident behind ``spec.py`` is exactly what guessing costs: three document
projections dropped ``created_time``, every row landed with a NULL
``source_recorded_at``, every quote line answered INSUFFICIENT_EVIDENCE, and the
sync reported success.

A committed artifact is a second copy of something, though, and a second copy
goes stale. That is what ``--check`` is for, and why ``scripts/verify.sh`` runs
it: the artifact is not documentation *about* the contract, it is the contract's
published form, and a published form that lags the code is worse than none.

What the files are
------------------
``<entity>.json`` is that entity's document from ``entity_schemas()``, pretty
printed with ``sort_keys=True`` — the same ordering ``canonical_json()``
serialises under. So the nineteen files together **are** the hashed pre-image:
load them, re-serialise compactly, sha256, and the ``spec_`` stamp comes back
out. The README says how, and ``test_spec_artifact.py`` asserts it. A stamp
nobody outside this repository can resolve to the contract it stood for is only
distinguishable, never explainable.

``README.md`` carries the narrative a human has to write — what the contract is,
what ``x-pie-expected`` obliges, how versions compare — and *generates*
everything else. Nothing describing a field, an entity or a status is typed
here: entity prose is the DTO's own docstring as pydantic published it, field
rows come from ``entity_field_contracts()``, and the expectation reasons are
carried whole. A hand-written field list in a docs directory is the second home
CLAUDE.md §2 names by example, and the copy that gets edited is never the copy
that runs.

Usage:
    python3 scripts/spec_export.py            write docs/spec/
    python3 scripts/spec_export.py --check    regenerate in memory, write
                                              nothing, exit 1 on any difference
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

# After the path insert, necessarily — the backend package is not installed, and
# `scripts/` is not inside it. Same arrangement as diagnose_attribution.py.
from app.domain import spec                                          # noqa: E402
from app.domain.spec import EXPECTED, OPTIONAL, REQUIRED             # noqa: E402

#: Published under ``docs/`` rather than beside the code, because the audience
#: is somebody writing a connector against this platform, not somebody editing
#: it. Relative paths inside this directory are what the README links.
OUT_DIR = REPO / "docs" / "spec"

#: Pulled back out of a committed README so a stale artifact can say *which*
#: version it is stale at, rather than only that it differs.
_STAMP = re.compile(r"\bspec_[0-9a-f]{10}\b")


# ── rendering ────────────────────────────────────────────────────────────────
def _schema_file(document: dict) -> str:
    """One entity's document, as the file holds it.

    ``sort_keys`` is not cosmetic and ``indent`` is not either. Sorted, the file
    is byte-for-byte the ordering ``canonical_json()`` hashes, so the artifact
    re-serialises back to the exact pre-image; indented, a reviewer reads a
    schema change as a diff of a few lines rather than as one 6 KB line that
    every tool renders as "modified".
    """
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def _cell(text: str) -> str:
    """One generated string, safe in a Markdown table cell and otherwise whole.

    Whitespace collapses because a table row is one line; ``|`` is escaped
    because it would end the cell. Nothing is truncated — the expectation
    reasons are contract text, and a reason cut at 80 characters is an
    instruction with its argument removed.
    """
    return " ".join(text.split()).replace("|", "\\|")


def _expectation_rows(contracts: dict) -> list[tuple[str, list[str], str]]:
    """``(field path, the entities carrying it, the reason)`` for every
    expectation in the published document, deepest-first by how widely it holds.

    Read out of ``entity_field_contracts()`` rather than out of ``spec.py``'s
    declaration, for the reason that function exists: it renders what was
    *published*, so this table cannot claim an obligation the schemas do not
    carry. The paths are grouped because ``source_ref.recorded_at`` is one
    obligation stated on all nineteen entities, and nineteen identical rows
    would bury the one that is not.
    """
    grouped: dict[tuple[str, str], list[str]] = {}
    for entity, field_contracts in contracts.items():
        for contract in field_contracts:
            if contract.status == EXPECTED:
                grouped.setdefault((contract.path, contract.reason), []).append(entity)
    return [(path, entities, reason)
            for (path, reason), entities in sorted(
                grouped.items(), key=lambda kv: (-len(kv[1]), kv[0][0]))]


def _readme(documents: dict, contracts: dict, version: str) -> str:
    """The document an ERP consultant opens.

    The division is deliberate and it is the whole discipline of this file:
    prose a human must write (what the contract is, what the marker obliges, how
    two versions compare) is typed here; everything that describes an entity, a
    field or a count is generated. So this file can go out of date about its own
    reasoning — which a reader can judge — and cannot go out of date about the
    contract, which a reader cannot.
    """
    counts = {status: 0 for status in (REQUIRED, EXPECTED, OPTIONAL)}
    for field_contracts in contracts.values():
        for contract in field_contracts:
            counts[contract.status] += 1
    total = sum(counts.values())

    out: list[str] = []
    w = out.append

    w("# The PIE canonical ingestion spec")
    w("")
    w(f"`{version}` · {len(documents)} entities · {total} field contracts "
      f"({counts[REQUIRED]} REQUIRED, {counts[EXPECTED]} EXPECTED, "
      f"{counts[OPTIONAL]} OPTIONAL)")
    w("")
    w("> **Generated — do not edit any file in this directory by hand.**")
    w("> `scripts/spec_export.py` writes it from `backend/app/domain/schemas.py`,")
    w("> and `scripts/verify.sh` regenerates the whole directory on every run and")
    w("> fails the build if one byte differs. An edit here is reverted by the next")
    w("> person who runs the gate.")
    w("")
    w("This is what a connector must produce for PIE: the records, the fields on")
    w("each, and which of those fields may legitimately be absent. One file per")
    w("entity, each a standard JSON Schema — validate a payload against it with")
    w("any off-the-shelf validator, in any language, with no knowledge of this")
    w("platform.")
    w("")
    w(f"The version above is a content hash over all {len(documents)} documents")
    w("together. It is the stamp a sync run records, so a row in this platform")
    w("can say which contract it was written under, and")
    w("it moves on any change to any document at all. [Checking the stamp by")
    w("hand](#checking-the-stamp-by-hand) below re-derives it from these files.")
    w("")

    # ── the marker ───────────────────────────────────────────────────────────
    w("## `x-pie-expected` — the part no validator will check for you")
    w("")
    w("Read this before writing a line of connector code. It is the one thing")
    w("this spec says that a JSON Schema cannot.")
    w("")
    w("JSON Schema has two states for a field: required, or not. Every real")
    w("omission this contract exists to catch lives in the second one — the field")
    w("is optional **because a source system may genuinely not have the concept**,")
    w("and a connector that has the value and drops it is, to every validator ever")
    w("written, indistinguishable from one whose source has nothing to send. That")
    w("is not hypothetical. It is what happened here: a client dropped the source")
    w("record's creation timestamp from three document types, every row landed")
    w("without it, every downstream answer degraded to INSUFFICIENT_EVIDENCE, and")
    w("the sync reported success.")
    w("")
    w("So each record definition that declares such a field carries")
    w("`x-pie-expected` beside its own `required` array — an object mapping the")
    w("field name to the reason its absence is a defect:")
    w("")
    w("```json")
    w('"required": ["system", "record_type", "record_id"],')
    w('"x-pie-expected": {')
    w('  "recorded_at": "When the source system recorded the document. …"')
    w("}")
    w("```")
    w("")
    w("**What it obliges you to do.** For every field listed below, exactly one of")
    w("these is true of your connector, and you have to know which:")
    w("")
    w("1. Your source exposes the value → **you send it.** Omitting it is a defect")
    w("   in your connector, not a property of your ERP, and nothing downstream")
    w("   will tell you: the rows load, the sync goes green, and the answers go")
    w("   quiet.")
    w("2. Your source genuinely has no such concept → **say so, in writing, to")
    w("   whoever integrates you.** Absence then means something, and this")
    w("   platform can count it rather than impute it.")
    w("")
    w("There is no third case, and \"we will map it later\" is case 1 with the")
    w("defect still in it. A stock validator will ignore `x-pie-expected` entirely")
    w("— it is a vendor extension, which is exactly why it is safe to ship inside")
    w("the schema — so this check is yours to run.")
    w("")
    rows = _expectation_rows(contracts)
    w("| Field | Declared on | Why absence is a defect |")
    w("|---|---|---|")
    for path, entities, reason in rows:
        where = (f"all {len(documents)} entities" if len(entities) == len(documents)
                 else ", ".join(f"`{e}`" for e in sorted(entities)))
        w(f"| `{path}` | {where} | {_cell(reason)} |")
    w("")

    # ── conformance ──────────────────────────────────────────────────────────
    w("## Conformance — the two steps")
    w("")
    w("1. **Validate** your payload for an entity against `<entity>.json` with any")
    w("   JSON Schema validator. That settles types, formats, enum members and the")
    w("   `required` fields.")
    w("2. **Assert the expectations** in the table above yourself. Step 1 passes")
    w("   without them by design.")
    w("")
    w("Every field is in one of three states, and the per-entity tables below")
    w("state which:")
    w("")
    w(f"- **{REQUIRED}** — the schema will not accept the record without it.")
    w(f"- **{EXPECTED}** — the schema accepts the record without it, and this")
    w("  contract does not. See the section above.")
    w(f"- **{OPTIONAL}** — absent is a legitimate answer. Absent is **not** zero,")
    w("  not today's date, and not an empty string; if you do not have the fact,")
    w("  leave the field out.")
    w("")
    w("Paths are dotted, and `[]` marks a list element — `source_ref.recorded_at`,")
    w("`applications[].document_date`. A status below `[]` is asked of each")
    w("element that is present; it never means the list must be non-empty.")
    w("")

    # ── versioning ───────────────────────────────────────────────────────────
    w("## Versioning and compatibility")
    w("")
    w("**The stamp is a content hash, not a semantic version.**")
    w(f"`{version}` is sha256 over all {len(documents)} documents, truncated. It")
    w("moves on *any* change to any of them,")
    w("including a reworded description — deliberately, because a contract whose")
    w("stated meaning can be rewritten under a stable stamp is not a contract. It")
    w("does not encode a major, a minor, or an ordering: two stamps are equal or")
    w("they are not.")
    w("")
    w("The compatibility rule is therefore a rule about **changes**, applied by")
    w("whoever makes one:")
    w("")
    w("- **Additive is a minor bump.** A new OPTIONAL field, a new entity, a")
    w("  loosened constraint, a clarified description. A connector in the field")
    w("  keeps working untouched, and may adopt the addition when it likes.")
    w("- **A newly REQUIRED field or a semantic change is major.** Promoting a")
    w("  field to `required`, promoting one to `x-pie-expected`, tightening a")
    w("  constraint, or changing what an existing field *means* while leaving its")
    w("  name and type alone. Every connector must be revisited before it ships,")
    w("  and the last of those is the dangerous one: it breaks nothing a validator")
    w("  can see.")
    w("")
    w("**Which kind a change was is answerable from these files**, without trusting")
    w("anyone's summary. The counts in the header line move as follows: REQUIRED or")
    w("EXPECTED going up is major; OPTIONAL going up alone is additive; anything")
    w("going down is major. Beyond that, `git diff` over this directory shows every")
    w("changed constraint, and a description that changed while no count moved is")
    w("the semantic case — read it.")
    w("")
    w("**How a connector declares which version it implements.** There is no field")
    w("in this artifact for a connector to fill in, and inventing one would be a")
    w("number nothing checks. What exists instead is a comparison you can actually")
    w("make: record the `spec_` stamp you built and tested against, in your own")
    w("connector's documentation or manifest; this platform records, per sync run,")
    w("the stamp the rows were written under. When the two differ, the change is")
    w("classified by the rule above — the file diff is the evidence, not a")
    w("changelog entry somebody remembered to write.")
    w("")

    # ── the stamp, by hand ───────────────────────────────────────────────────
    w("## Checking the stamp by hand")
    w("")
    w("These files are not a rendering of the hashed bytes — they **are** the")
    w("hashed bytes, one document per file, under the same key ordering. So the")
    w("stamp can be re-derived from this directory alone, by anyone with a")
    w("checkout and no dependencies:")
    w("")
    w("```bash")
    w("python3 - <<'EOF'")
    w("import hashlib, json, pathlib")
    w("docs = {p.stem: json.loads(p.read_text())")
    w('        for p in pathlib.Path("docs/spec").glob("*.json")}')
    w('pre = json.dumps(docs, sort_keys=True, separators=(",", ":"))')
    w('print("spec_" + hashlib.sha256(pre.encode()).hexdigest()[:10])')
    w("EOF")
    w("```")
    w("")
    w(f"That prints `{version}`. A hash does not invert, so publishing the")
    w("pre-image is what makes the stamp explainable rather than merely")
    w("distinguishable — the same reason this platform publishes the serialised")
    w("form behind its other policy stamps.")
    w("")

    # ── the entities ─────────────────────────────────────────────────────────
    w("## The entities")
    w("")
    w("| Entity | Schema | Fields | REQUIRED | EXPECTED | OPTIONAL |")
    w("|---|---|---:|---:|---:|---:|")
    for entity in documents:
        per = {status: 0 for status in (REQUIRED, EXPECTED, OPTIONAL)}
        for contract in contracts[entity]:
            per[contract.status] += 1
        # The anchor is the heading verbatim: GitHub lowercases and replaces
        # spaces, and keeps underscores — so `### cost_record` is `#cost_record`.
        w(f"| [`{entity}`](#{entity}) | [`{entity}.json`]({entity}.json) | "
          f"{sum(per.values())} | {per[REQUIRED]} | {per[EXPECTED]} | "
          f"{per[OPTIONAL]} |")
    w("")
    w("Each section below states what the entity is — the DTO's own prose, as")
    w("published in the schema's `description` — and every field the contract")
    w("asks of it, nested records flattened. Types, formats, enum members and")
    w("constraints are in the JSON file beside it; they are not repeated here,")
    w("because a second copy of a type is a copy that will one day disagree.")
    w("")
    for entity, document in documents.items():
        w(f"### {entity}")
        w("")
        w(f"[`{entity}.json`]({entity}.json)")
        w("")
        description = document.get("description")
        if description:
            w(description.strip())
        else:
            w("*The DTO carries no docstring, so this entity is published with no")
            w("description at all. That is a gap in the contract rather than a")
            w("record whose meaning is obvious — an integrator reading only this")
            w("artifact has nothing here to go on.*")
        w("")
        w("| Field | Status | Why absence is a defect |")
        w("|---|---|---|")
        for contract in contracts[entity]:
            w(f"| `{contract.path}` | {contract.status} | "
              f"{_cell(contract.reason) if contract.reason else ''} |")
        w("")

    return "\n".join(out).rstrip("\n") + "\n"


def render() -> dict[str, str]:
    """``filename`` → the text that file should hold. The whole artifact, in
    memory — which is what lets ``--check`` compare without writing."""
    documents = spec.entity_schemas()
    contracts = spec.entity_field_contracts()
    version = spec.spec_version()
    files = {f"{entity}.json": _schema_file(document)
             for entity, document in documents.items()}
    files["README.md"] = _readme(documents, contracts, version)
    return files


# ── the two modes ────────────────────────────────────────────────────────────
def check(files: dict[str, str]) -> int:
    """Compare the rendered artifact against what is committed. Write nothing.

    Reports every difference rather than the first, for the reason
    ``verify.sh`` does: one failing run should tell you everything that is
    wrong. Files on disk that the exporter does not produce are a difference
    too — an entity removed from the contract leaves a schema behind, and a
    stale document nobody generates any more is the one an integrator reads.
    """
    stale: list[str] = []
    on_disk = {p.name for p in OUT_DIR.iterdir() if p.is_file()} \
        if OUT_DIR.is_dir() else set()

    for name, text in files.items():
        path = OUT_DIR / name
        if not path.exists():
            stale.append(f"missing: docs/spec/{name}")
        elif path.read_text(encoding="utf-8") != text:
            stale.append(f"differs: docs/spec/{name}")
    for name in sorted(on_disk - set(files)):
        stale.append(f"not generated by this script: docs/spec/{name}")

    if not stale:
        print(f"docs/spec is current at {spec.spec_version()} "
              f"({len(files)} files).")
        return 0

    committed = _STAMP.search((OUT_DIR / "README.md").read_text(encoding="utf-8")) \
        if (OUT_DIR / "README.md").exists() else None
    print("docs/spec is stale — the published contract does not match the code.")
    for line in stale:
        print(f"  {line}")
    if committed and committed.group() != spec.spec_version():
        print(f"\n  committed: {committed.group()}\n"
              f"  code says: {spec.spec_version()}")
    print("\nRegenerate it and commit the result:\n"
          "  python3 scripts/spec_export.py")
    return 1


def write(files: dict[str, str]) -> int:
    """Write the artifact, and remove anything in the directory this script does
    not produce — so a write is always followed by a clean ``--check``."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    changed = 0
    for name, text in sorted(files.items()):
        path = OUT_DIR / name
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue
        path.write_text(text, encoding="utf-8")
        changed += 1
    removed = 0
    for path in sorted(OUT_DIR.iterdir()):
        if path.is_file() and path.name not in files:
            path.unlink()
            removed += 1
    print(f"docs/spec: {len(files)} files at {spec.spec_version()} "
          f"({changed} written, {removed} removed).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Publish the canonical ingestion spec to docs/spec/.")
    ap.add_argument("--check", action="store_true",
                    help="regenerate in memory and exit non-zero if the "
                         "committed artifact differs; writes nothing")
    args = ap.parse_args()
    files = render()
    return check(files) if args.check else write(files)


if __name__ == "__main__":
    raise SystemExit(main())
