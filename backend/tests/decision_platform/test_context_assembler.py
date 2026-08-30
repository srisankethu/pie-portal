"""Context assembly: redaction (defense in depth), unknowns, grounding set."""
from __future__ import annotations

import ast
import pathlib

import app
from app.context.assembler import assemble_from_signal
from app.domain import models
from app.domain.enums import Role


def _margin_signal(org="org_test"):
    return models.Signal(
        organization_id=org, signal_type="MARGIN_DETERIORATION",
        subject_entity_type="PRODUCT", subject_entity_id="p1",
        detector_version="v0", threshold_config_version="th_x",
        window={}, severity_base=50,
        metrics={"current_margin_pct": 0.12, "baseline_margin_pct": 0.30,
                 "recent_unit_cost": 70.0, "recent_unit_price": 80.0},
        evidence_refs=[{"record_type": "invoice", "record_id": "i1"}],
        sufficiency={"level": "SUFFICIENT", "missing_fields": [], "anomalies": []})


def test_manager_sees_restricted_facts(session):
    session.add(models.Product(product_id="p1", organization_id="org_test", name="Insert",
                               external_id="p1"))
    sig = _margin_signal()
    session.add(sig)
    session.flush()
    b = assemble_from_signal(session, sig, Role.SALES_MANAGER)
    labels = b.fact_labels()
    assert "current_margin_pct" in labels and "recent_unit_cost" in labels
    assert b.redactions_applied == []
    assert "RESTRICTED" in b.permitted_data_classes


def test_salesperson_gets_restricted_redacted(session):
    session.add(models.Product(product_id="p1", organization_id="org_test", name="Insert",
                               external_id="p1"))
    sig = _margin_signal()
    session.add(sig)
    session.flush()
    b = assemble_from_signal(session, sig, Role.SALESPERSON)
    labels = b.fact_labels()
    # restricted economics are ABSENT (not masked)
    assert "current_margin_pct" not in labels and "recent_unit_cost" not in labels
    assert b.redactions_applied                       # recorded for audit
    # and their numbers are not in the grounding set (can't be cited)
    assert 70.0 not in b.allowed_numbers()


def test_context_hash_is_stable(session):
    session.add(models.Product(product_id="p1", organization_id="org_test", name="Insert",
                               external_id="p1"))
    sig = _margin_signal()
    session.add(sig)
    session.flush()
    a = assemble_from_signal(session, sig, Role.SALES_MANAGER).context_hash()
    b = assemble_from_signal(session, sig, Role.SALES_MANAGER).context_hash()
    assert a == b and a.startswith("cx_")


_APP = pathlib.Path(app.__file__).resolve().parent

#: The tag every context fact, price reference and quote exception carries.
_TAG_NAMES = frozenset({"OPERATIONAL", "RESTRICTED"})

#: Every module that writes the tag, reads it, or names its permitted set.
#:
#: Four packages, not three. The first version of this list held the three that
#: *declared* the literals and missed the two that merely *spelled* them —
#: ``decisions/quote_support`` runs its own copy of the redaction
#: (``dc == "RESTRICTED"``) on the client-facing quote path, and
#: ``context/assembler`` builds ``permitted_data_classes`` from bare strings.
#: A list that screens where the constant was declared rather than where the
#: tag is *used* leaves the drift it exists to prevent on the one path that
#: reaches a salesperson.
_TAG_MODULES = ("commercial/references.py", "signals/quote_context.py",
                "context/quote_bundle.py", "context/assembler.py",
                "decisions/quote_support.py")


def _module_level_assignments(tree: ast.Module) -> set[str]:
    """Names bound at module level, however the binding is spelled.

    ``ast.Assign`` with a plain ``Name`` target is only the common spelling.
    An annotated ``RESTRICTED: str = "RESTRICTED"`` and a tuple unpack
    ``OPERATIONAL, X = "OPERATIONAL", 1`` bind the same name and would both
    have walked past a helper that only looked at the first — which makes the
    check read as passing while the re-declaration it exists to catch sits in
    the file.
    """
    out: set[str] = set()
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Name):
                    out.add(sub.id)
    return out


def _names_from_domain_enums(tree: ast.Module) -> set[str]:
    return {alias.asname or alias.name for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").endswith("domain.enums")
            for alias in node.names}


def test_the_data_class_tag_is_declared_once():
    """One declaration of OPERATIONAL / RESTRICTED, in ``domain/enums``.

    The redaction rule spans four packages: ``commercial/references`` and
    ``signals/quote_context`` write the tag; ``context/quote_bundle`` and
    ``decisions/quote_support`` each compare against it to drop a RESTRICTED
    fact for a salesperson; ``context/assembler`` names the permitted set. Each
    held its own literals, which is the one arrangement this rule cannot
    survive — rename or mistype the tag in a single copy and nothing goes red,
    the comparison just stops matching and a cost fact reaches a salesperson
    (§1: cost and margin never reach a salesperson).

    Asserted on the parsed source rather than at runtime, because CPython
    interns identifier-like literals: a re-declared ``RESTRICTED = "RESTRICTED"``
    would still be ``is``-identical to the imported one, so an equality or
    identity check would pass over exactly the defect this test exists for.
    """
    for rel in _TAG_MODULES:
        tree = ast.parse((_APP / rel).read_text())
        redeclared = _TAG_NAMES & _module_level_assignments(tree)
        assert not redeclared, (
            f"app/{rel} declares {sorted(redeclared)} itself; import it from "
            "app.domain.enums so the write side and the read side cannot drift")
        assert _TAG_NAMES <= _names_from_domain_enums(tree), (
            f"app/{rel} handles the data-class tag but does not take it from "
            "app.domain.enums")
