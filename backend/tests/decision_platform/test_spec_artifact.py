"""``docs/spec/`` — the published contract — against the code that generates it.

``test_spec_contract.py`` pins the mechanism: what the entities are, what the
document says, that the stamp is deterministic. None of that says anything about
the twenty files committed under ``docs/spec/``, and those files are the only
form of this contract anybody outside this repository will ever read. A
connector author validates against ``docs/spec/bill.json``; they do not import
``app.domain.spec``.

So a committed artifact is a second copy of something, and the whole reason
CLAUDE.md §6 exists is that second copies drift silently. This file is what
makes that particular drift loud, in the same shape as
``test_a_fresh_database_matches_the_models_exactly``: regenerate, compare,
fail with the command that fixes it.

The three claims, in order of what they cost if they are wrong:

* The files match the code. A stale schema teaches an integrator the wrong
  contract, and they have no way to find out.
* The files re-derive the stamp. ``docs/spec/README.md`` prints a shell snippet
  and tells the reader it will produce ``spec_…``; that snippet is executed
  here, because a verification instruction nobody has run is a claim.
* ``--check`` fails when the artifact is stale. A gate step that cannot go red
  is a gate step that reports nothing, which is the failure CLAUDE.md §6 spends
  its length on.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import sys

# The exporter lives in scripts/, outside the app package: it runs against a
# checkout rather than inside the application. Same arrangement as
# test_deploy_runbook.py.
_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "scripts"))

import spec_export  # noqa: E402

from app.domain import spec  # noqa: E402

SPEC_DIR = _ROOT / "docs" / "spec"


# ── the artifact is what the code says ──────────────────────────────────────
def test_the_committed_artifact_is_byte_for_byte_what_the_code_generates():
    """The drift check. A field added to a DTO moves the schemas and the stamp;
    if the commit did not also carry the regenerated ``docs/spec/``, the
    published contract describes a platform that no longer exists."""
    generated = spec_export.render()
    on_disk = {p.name: p.read_text(encoding="utf-8")
               for p in SPEC_DIR.iterdir() if p.is_file()}

    missing = sorted(set(generated) - set(on_disk))
    extra = sorted(set(on_disk) - set(generated))
    differing = sorted(n for n in set(generated) & set(on_disk)
                       if generated[n] != on_disk[n])

    assert not (missing or extra or differing), (
        f"docs/spec is stale at {spec.spec_version()} — "
        f"missing {missing}, unexpected {extra}, differing {differing}. "
        f"Regenerate and commit it: python3 scripts/spec_export.py")


def test_the_artifact_holds_one_schema_per_entity_and_the_readme():
    """Nothing published that is not an entity, and no entity unpublished.

    Separate from the byte comparison above on purpose: that one fails on any
    difference at all, and this one says which *kind* of difference a whole
    missing entity is."""
    expected = {f"{spec.entity_name(m)}.json" for m in spec.SPEC_ENTITIES}
    expected.add("README.md")
    assert {p.name for p in SPEC_DIR.iterdir() if p.is_file()} == expected


def test_rendering_the_artifact_twice_gives_identical_bytes():
    """The exporter's own determinism, which the spec module's cannot cover.

    ``spec.py`` is pinned across two processes by its own suite; the README is
    generated here, and an unordered traversal in *this* file would make every
    regeneration a diff with no change behind it."""
    assert spec_export.render() == spec_export.render()


# ── the artifact re-derives its own stamp ───────────────────────────────────
def test_the_committed_schemas_re_derive_the_published_stamp():
    """``README.md``'s hand-check, executed rather than described.

    The published files are the hashed pre-image, one document per file, under
    the key ordering ``canonical_json()`` serialises. That property is what
    makes the stamp explainable to somebody who has the docs and no interpreter
    for this application — and it holds only as long as the exporter keeps
    writing ``sort_keys=True``. This is the assertion that notices if it stops.
    """
    documents = {p.stem: json.loads(p.read_text(encoding="utf-8"))
                 for p in SPEC_DIR.glob("*.json")}
    pre_image = json.dumps(documents, sort_keys=True, separators=(",", ":"))

    assert pre_image == spec.canonical_json()
    assert ("spec_" + hashlib.sha256(pre_image.encode()).hexdigest()[:10]
            == spec.spec_version())


def test_the_readme_names_the_version_the_schemas_hash_to():
    """A README quoting an older stamp beside current schemas is the worst of
    both: current files, and a reader who trusts the wrong stamp."""
    readme = (SPEC_DIR / "README.md").read_text(encoding="utf-8")
    assert spec.spec_version() in readme


# ── the expectation survives publication ────────────────────────────────────
def test_every_published_schema_carries_the_expectation_in_full():
    """The one thing this contract says that a validator cannot, present in the
    artifact an integrator actually opens.

    ``test_spec_contract.py`` asserts this of ``entity_schemas()``. This asserts
    it of the committed bytes, which is a different claim — the exporter sits
    between them, and a serialisation that dropped or truncated the reason would
    leave the published spec with an instruction and no argument behind it.
    """
    for model in spec.SPEC_ENTITIES:
        name = spec.entity_name(model)
        document = json.loads((SPEC_DIR / f"{name}.json").read_text(encoding="utf-8"))
        marker = document["$defs"]["SourceRef"][spec.EXPECTED_KEYWORD]
        assert "recorded_at" in marker, name
        assert "INSUFFICIENT_EVIDENCE" in marker["recorded_at"], name


def test_the_readme_carries_every_expectation_reason_whole():
    """Untruncated, because the reason *is* the contract text. "recorded_at is
    expected" without the sentence about what its absence costs is an
    instruction a connector author has no reason to follow."""
    readme = " ".join((SPEC_DIR / "README.md").read_text(encoding="utf-8").split())
    reasons = {c.reason for contracts in spec.entity_field_contracts().values()
               for c in contracts if c.status == spec.EXPECTED}
    assert reasons, "the expectation vocabulary is empty — this asserts nothing"
    for reason in reasons:
        assert " ".join(reason.split()) in readme


# ── the gate step can actually go red ───────────────────────────────────────
def _staged(tmp_path: pathlib.Path, monkeypatch) -> pathlib.Path:
    """A throwaway copy of ``docs/spec`` that ``--check`` is pointed at.

    The repository's own artifact is never written to by these tests. Perturbing
    the real directory and restoring it would leave a wrong contract committed
    on any run that failed in between.
    """
    staging = tmp_path / "spec"
    shutil.copytree(SPEC_DIR, staging)
    monkeypatch.setattr(spec_export, "OUT_DIR", staging)
    return staging


def test_check_passes_against_the_artifact_as_committed(tmp_path, monkeypatch):
    """The control. Without it, every assertion below passes on an exporter
    that returns 1 unconditionally."""
    _staged(tmp_path, monkeypatch)
    assert spec_export.check(spec_export.render()) == 0


def test_check_fails_when_a_published_schema_has_been_edited(tmp_path, monkeypatch):
    """One byte, in the field the whole contract exists to protect."""
    staging = _staged(tmp_path, monkeypatch)
    target = staging / "bill.json"
    target.write_text(target.read_text(encoding="utf-8").replace(
        "INSUFFICIENT_EVIDENCE", "insufficient evidence"), encoding="utf-8")

    assert spec_export.check(spec_export.render()) == 1


def test_check_fails_when_a_published_schema_is_missing(tmp_path, monkeypatch):
    """The stale case that looks most like success: a file simply absent, which
    a comparison over what is present would never reach."""
    staging = _staged(tmp_path, monkeypatch)
    (staging / "customer.json").unlink()

    assert spec_export.check(spec_export.render()) == 1


def test_check_fails_on_a_file_the_exporter_no_longer_generates(tmp_path, monkeypatch):
    """An entity dropped from the contract leaves its schema behind, and a
    document nobody generates any more is the one an integrator still reads."""
    staging = _staged(tmp_path, monkeypatch)
    (staging / "former_entity.json").write_text("{}\n", encoding="utf-8")

    assert spec_export.check(spec_export.render()) == 1


def test_check_fails_when_the_readme_is_stale(tmp_path, monkeypatch):
    """The README is generated too, so a schema change that nobody re-published
    is caught by the prose as well as by the JSON — and a version bump with no
    schema diff (a reworded docstring) is caught *only* here."""
    staging = _staged(tmp_path, monkeypatch)
    readme = staging / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace(
        spec.spec_version(), "spec_0000000000"), encoding="utf-8")

    assert spec_export.check(spec_export.render()) == 1
