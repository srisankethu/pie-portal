"""Every version column is classified, and nothing writes one behind the ORM.

Two checks, and both exist because the threshold registry's coverage guarantee
is only as good as its two assumptions.

**Assumption one: the recorder knows which columns are stamps.** It reads
``column.info["policy_stamp"]``. A new ``*_version`` column that nobody marks is
invisible to it — the rows carrying it are stamped and permanently
unexplainable, and no test would have said a word. So classification is made
compulsory: a version column is either marked as a stamp or listed below with a
reason. Same shape as ``EVENT_TYPES`` refusing an unregistered event type — the
decision stays a human one, only the *coverage* is automatic.

**Assumption two: stamped rows go through the ORM's flush.** The recorder is a
``before_flush`` listener, so a Core ``insert()`` or ``update()`` against a
stamped table bypasses it completely and writes a stamp nothing records. There
is no such statement today; this is what keeps it that way, and it parses the
source rather than grepping it so a table named in a docstring cannot fail the
build (the same reason ``test_layer_boundaries.py`` gives).
"""
from __future__ import annotations

import ast
import pathlib

from app.db import Base
from app.domain import models  # noqa: F401  (populate the metadata)

_APP = pathlib.Path(__file__).resolve().parents[2] / "app"

#: Columns whose name contains "version" and which are **not** a threshold
#: stamp, each with the reason. A reason, not a bare list: the whole point is
#: that somebody thought about it, and "it was in the list already" is not
#: thinking about it.
NOT_A_POLICY_STAMP: dict[str, str] = {
    "products.pie_catalog_version":
        "a checksum of the parsed catalogue pack, not a policy. It says which "
        "catalogue an identity was resolved against; no threshold went into it.",
    "quote_decisions.engine_version":
        "the identity of the code that priced the line — a build, not a policy. "
        "Two versions of the engine can apply exactly the same thresholds.",
    "quote_decisions.catalog_version":
        "the same catalogue checksum as products.pie_catalog_version.",
    "signals.detector_version":
        "code identity: which detector implementation emitted the signal. The "
        "thresholds it judged against are the sibling column, and marking this "
        "one too would record the same stamp twice per row.",
    "ai_call_logs.prompt_version":
        "which prompt template was sent to a model. Prompts are not "
        "deterministic policy and ai/ may never compute a number (§1); there is "
        "no threshold behind this at all.",
    "product_attribute_values.decoder_version":
        "the ruleset checksum of the pack that decoded the value — the same "
        "kind of stamp as products.pie_catalog_version, and for the same "
        "reason: it says which catalogue read the name, not which policy "
        "judged it. No threshold goes into an attribute at all. A decoded "
        "0.8 mm corner radius is a reading of the world; whether 0.8 is close "
        "enough to 0.4 is policy, it lives in the equivalence bands, and it is "
        "applied at read time by a layer that has not been built yet.",
    "threshold_versions.version":
        "the registry's own primary key — the stamp being dereferenced, not a "
        "stamp on a computed row. Marking it would make the recorder try to "
        "record the recording.",
    # The per-company catalogue's stamp. All four are pie-parser's own identity
    # for the decode — which pack, which engine, which record shape — carried so
    # a resolution can say *which* catalogue answered it. None of them is a
    # threshold: no margin floor, target or band goes into any of them, and the
    # commercial policy that judges a scored equivalence is applied long after,
    # per request, from `CommercialThresholds`. Same class as
    # `products.pie_catalog_version` above, which is the same fact stored on the
    # item it resolved.
    "company_catalogues.pack_version":
        "the version of the manufacturer nomenclature pack that decoded this "
        "company's export. Pack identity, not policy.",
    "company_catalogues.org_version":
        "the version of the organisation layer that routed it — how one "
        "export phrases a description. Also pack identity.",
    "company_catalogues.engine_version":
        "which build of pie-parser ran, on the reasoning "
        "quote_decisions.engine_version already gives: two engine versions can "
        "apply identical rules.",
    "company_catalogues.schema_version":
        "the shape of the emitted record, so a reader knows which fields to "
        "expect. A serialization format, not a threshold.",
}

#: Tables the flush recorder must be the only writer of a stamp on.
STAMPED_TABLES = {
    table.name for table in Base.metadata.tables.values()
    if any(column.info.get("policy_stamp") for column in table.columns)
}


def _version_columns() -> dict[str, object]:
    return {
        f"{table.name}.{column.name}": column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if "version" in column.name.lower()
    }


def test_every_version_column_is_classified():
    """A new ``*_version`` column fails this suite until somebody decides.

    The failure is deliberately loud and deliberately cheap to clear: either
    mark it ``info={"policy_stamp": …}`` so the registry records it, or write
    down here why it is not a policy stamp.
    """
    unclassified = sorted(
        name for name, column in _version_columns().items()
        if not column.info.get("policy_stamp") and name not in NOT_A_POLICY_STAMP)

    assert not unclassified, (
        "These version columns are neither marked as a threshold stamp nor "
        "listed in NOT_A_POLICY_STAMP with a reason: " + ", ".join(unclassified)
        + ". If a column holds a ci_/th_ threshold version, mark it with "
        "info={'policy_stamp': 'commercial'|'signal'|'either'} so "
        "threshold_registry records its pre-image — an unmarked stamp column "
        "produces rows nobody can ever explain. If it holds something else, say "
        "what, above.")


def test_the_classification_list_has_not_gone_stale():
    """A reason for a column that no longer exists is a reason nobody re-reads."""
    known = set(_version_columns())
    ghosts = sorted(set(NOT_A_POLICY_STAMP) - known)
    assert not ghosts, (
        "NOT_A_POLICY_STAMP names columns that no longer exist: "
        + ", ".join(ghosts))


def test_the_stamp_markers_name_a_kind_the_registry_understands():
    from app import threshold_registry

    kinds = set(threshold_registry._PREFIXES.values()) | {"either"}
    wrong = sorted(
        f"{name}={column.info['policy_stamp']}"
        for name, column in _version_columns().items()
        if column.info.get("policy_stamp")
        and column.info["policy_stamp"] not in kinds)
    assert not wrong, (
        "policy_stamp markers must be one of " + ", ".join(sorted(kinds))
        + " — got: " + ", ".join(wrong))


def test_the_expected_stamped_tables_are_all_still_marked():
    """The ten tables that carry a threshold stamp, pinned by name.

    Pinned so that *removing* a marker fails too. The check above only notices a
    column nobody classified; silently deleting an ``info=`` would leave a
    perfectly classified column that the recorder no longer sees.

    Two arrived in a merge and are the reason this list is worth pinning rather
    than deriving. ``quote_documents`` records a quote written into a source
    system and keeps the commercial policy in force when it went out;
    ``audit_entries`` is the signed, append-only log and carries **either**
    stamp, reading the kind off the value's own prefix. Both were stamped and
    unmarked, so the values behind those stamps were not being recorded — an
    audit trail of hashes, which is the state the registry exists to end.
    """
    assert STAMPED_TABLES == {
        "customer_item_metrics", "signals", "approval_requests",
        "quote_decisions", "outcome_snapshots", "value_events",
        "evaluation_baselines", "business_states",
        "quote_documents", "audit_entries",
    }


# ── nothing writes a stamp behind the ORM ────────────────────────────────────
def _core_dml_targets(path: pathlib.Path) -> set[str]:
    """Model names passed to a Core ``insert()``/``update()`` in this file.

    Parsed, not grepped: ``update(models.QueuedMessage)`` is a real call and
    "update(models.Signal)" inside a docstring is not, and only an AST can tell
    them apart. Attribute chains are flattened to the last name, which is the
    class, so both ``models.Signal`` and a bare ``Signal`` are caught.
    """
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name not in ("insert", "update"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Attribute):
                found.add(arg.attr)
            elif isinstance(arg, ast.Name):
                found.add(arg.id)
    return found


def test_no_core_insert_or_update_targets_a_stamped_table():
    """A Core statement bypasses ``before_flush`` and writes an unrecorded stamp.

    ``threshold_registry`` itself is exempt: its ON CONFLICT insert targets
    ``threshold_versions``, which is the registry, not a stamped table — and
    that table is deliberately not marked, so it is not in ``STAMPED_TABLES``
    and needs no exemption at all. Stated here anyway, because the first person
    to hit this test will go looking for the exception and finding none should
    be reassuring rather than confusing.
    """
    by_table = {}
    for table in Base.metadata.tables.values():
        if table.name in STAMPED_TABLES:
            for mapper in Base.registry.mappers:
                if mapper.local_table is table:
                    by_table[mapper.class_.__name__] = table.name

    offenders = []
    for path in sorted(_APP.rglob("*.py")):
        for target in _core_dml_targets(path):
            if target in by_table:
                offenders.append(
                    f"{path.relative_to(_APP.parent)} → {target} "
                    f"({by_table[target]})")

    assert not offenders, (
        "These Core insert()/update() statements target a table carrying a "
        "threshold stamp, so they bypass the before_flush recorder and would "
        "write a version whose values nothing captured: " + "; ".join(offenders)
        + ". Write through the ORM, or record the pre-image explicitly with "
        "threshold_registry.record_current in the same transaction.")
