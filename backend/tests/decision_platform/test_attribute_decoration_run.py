"""The two callers that make Phase 1's store stop being empty.

``decorate_products`` shipped with the store and nothing called it: verified by
grep across ``backend/`` before this work, zero call sites, so in production the
table was empty and every later phase read nothing. These pin the two doors that
fix that — ``python -m app.attributes`` for an operator, and the sync phase in
``ingestion.jobs.execute_analysis`` so a re-sync leaves the store current — and
the three ways a caller can undo the package's own guarantees:

* **An absent engine must not wipe the store.** The package distinguishes "the
  pack covered nothing here" from "nobody asked the pack", and a *caller* is
  where that distinction gets lost: a decoration run against a deployment with
  no pie-parser writing an empty answer for every product would retract the
  whole table, and read on the coverage report as a Phase 1 regression rather
  than as a missing checkout. Tested for both doors.
* **Batching must not change the answer.** The whole-organization run exists to
  keep the write transaction short (CLAUDE.md §4), and a batch boundary that
  changed which rows exist would be a batching bug nothing else would catch.
* **Decoration failing must not take the sync down.** ``execute_sync`` never
  raises because a job that vanishes is indistinguishable from one that never
  started, and the run has to carry a row saying what happened.

The decoder is the real one — ``PIE_PARSER_ROOT`` must point at a pie-parser
checkout, as it must for ``test_product_attributes``, and for that file's
reason: a stub that returns ``corner_radius_mm`` proves the store works on a
value the stub chose. The catalogue is stubbed the same way ``test_catalog_link``
stubs it, because driving these through the real 13 MB index would make them
slow and silent about the absent-catalogue branch.
"""
from __future__ import annotations

from typing import List

import pytest

# Twelve tests across this file and its sibling `test_product_attributes.py`
# read the REAL decode rather than a stub, so they need the engine and carry
# `requires_pie`. They shipped unmarked: on a checkout without the submodule
# they did not skip, they FAILED — twelve assertion errors that read as code
# defects and are a missing directory. `conftest.py` has the mechanism that
# prevents exactly this and it simply had not been applied to these files.
# Mark a test here when it asserts on a decoded field; leave it unmarked when
# it asserts on what happens with NO engine, which is the other half of both
# files and must keep running without one.
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.attributes import DECODED_NAME, DecorationReport
from app.attributes import decorate_organization as _decorate_organization
from app.attributes import decorate_products as _decorate_products
from app.config import settings
from app.domain import models


# See the note in test_product_attributes.py: the rule set is named by the
# caller, and every caller here names the organization layer.
def decorate_products(session, organization_id, **kw):
    kw.setdefault("rule_set", settings.PIE_PACK)
    return _decorate_products(session, organization_id, **kw)


def decorate_organization(session, organization_id, **kw):
    kw.setdefault("rule_set", settings.PIE_PACK)
    return _decorate_organization(session, organization_id, **kw)

ORG = "org_a"

#: And one that decodes to nothing, so the "no evidence, no row" half is real.
UNDECODABLE_NAME = "MISC BRACKET ASSEMBLY 4 OFF"


class _Catalogue:
    """``pie_service``, or the absence of it — ``test_catalog_link``'s shape."""

    def __init__(self, available: bool = False):
        self.catalog_available = available
        self.catalog_version = "ck_test_v1"

    def lookup_record(self, identifier):        # pragma: no cover - never linked
        return None


def _install(monkeypatch, catalogue: _Catalogue) -> None:
    import app.pie_service as ps

    monkeypatch.setattr(ps, "pie_service", catalogue)


def _org(session) -> None:
    if session.get(models.Organization, ORG) is None:
        session.add(models.Organization(organization_id=ORG, name="Acme Tools"))
        session.flush()


def _master(session, count: int) -> List[models.Product]:
    """A small master: mostly decodable names, and one that says nothing.

    The names are made distinct with a trailing edge-length variant rather than
    a suffix the pack would not route, so every one of them is a real decode
    and the population under test is not one row repeated.
    """
    _org(session)
    rows = []
    for n in range(count):
        name = (UNDECODABLE_NAME if n % 5 == 4
                else f"CNMG 1204{n % 4 + 4:02d}-49 - TN2000")
        row = models.Product(organization_id=ORG, external_id=f"i{n}", name=name,
                             connector="zoho", connection_id="c1")
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def _rows(session, org: str = ORG) -> List[models.ProductAttributeValue]:
    return list(session.scalars(
        select(models.ProductAttributeValue)
        .where(models.ProductAttributeValue.organization_id == org)
        .order_by(models.ProductAttributeValue.product_id,
                  models.ProductAttributeValue.source_kind,
                  models.ProductAttributeValue.attribute_key,
                  models.ProductAttributeValue.created_at)))


def _snapshot(session, org: str = ORG) -> List[tuple]:
    """Every column of every row, ids and timestamps included."""
    return [(r.attribute_value_id, r.product_id, r.attribute_key, r.value_num,
             r.value_text, r.original_value, r.unit, r.source_kind, r.source_ref,
             r.confidence, r.decoder_version, r.created_at, r.superseded_at)
            for r in _rows(session, org)]


def _no_pie_parser(monkeypatch, tmp_path) -> None:
    """A deployment that shipped without the engine, in both its halves.

    ``PIE_PARSER_ROOT`` at a directory holding no ``engine/pipeline.py`` is what
    ``decode_names`` checks, and the catalogue is absent for the same reason it
    would be: nothing built it.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "PIE_PARSER_ROOT", tmp_path / "not-here")
    _install(monkeypatch, _Catalogue(available=False))


# ── the whole-organization run ──────────────────────────────────────────────
@pytest.mark.requires_pie
def test_decorating_an_organization_in_batches_writes_what_one_pass_writes(
        session, monkeypatch):
    """The batch boundary is a transaction boundary, not a decision boundary.

    Batching exists to keep the write window short on a master (§4). If a
    boundary changed *which* rows exist — a claim lost at the seam, a retraction
    fired because a product's other batch had not been seen yet — nothing else
    in the system would notice, because both runs would look internally
    consistent.
    """
    _master(session, 12)
    _install(monkeypatch, _Catalogue(available=False))

    one_pass = decorate_products(session, ORG)
    whole = _snapshot(session)
    assert whole, "nothing was written, so this compares two empty tables"

    # A second organization, an identical master, decorated three at a time.
    session.add(models.Organization(organization_id="org_b", name="Other"))
    for row in session.scalars(select(models.Product).where(
            models.Product.organization_id == ORG)):
        session.add(models.Product(organization_id="org_b", external_id=row.external_id,
                                   name=row.name, connector="zoho", connection_id="c1"))
    session.flush()

    batched = decorate_organization(session, "org_b", batch_size=3)

    assert batched.products_considered == one_pass.products_considered
    assert batched.names_decoded == one_pass.names_decoded
    assert batched.written.created == one_pass.written.created
    assert batched.written.retracted == 0
    # Compared on what a claim *says* rather than on its row id: the two orgs
    # allocate different product ids, so the ids cannot match and the values
    # must.
    def claims(rows):
        return sorted((r.attribute_key, r.value_num, r.value_text, r.unit,
                       r.source_kind, r.source_ref) for r in rows)

    assert claims(_rows(session, "org_b")) == claims(_rows(session, ORG))


def test_the_caller_owns_the_transaction_and_is_told_where_the_boundaries_are(
        session, monkeypatch):
    """``on_batch`` is called once per batch, with a progress pair.

    The package writes rows and does not commit; both real callers commit in
    this callback, so a run that never called it would be one long write lock on
    a 15,000-item master — the §4 failure that made ``/api/health`` time out.
    """
    _master(session, 10)
    _install(monkeypatch, _Catalogue(available=False))
    seen: List[tuple] = []

    decorate_organization(session, ORG, batch_size=4,
                          on_batch=lambda done, total: seen.append((done, total)))

    assert seen == [(4, 10), (8, 10), (10, 10)]


def test_an_organization_with_no_products_claims_nothing_about_either_source(
        session, monkeypatch):
    """Nothing was asked because there was nothing to ask about.

    Not "the decoder was unavailable" and not "the decoder found nothing" —
    ``attribute_coverage`` reports the same organization's rate as ``None``
    rather than 0% for the same reason.
    """
    _org(session)
    _install(monkeypatch, _Catalogue(available=False))

    report = decorate_organization(session, ORG)

    assert report.products_considered == 0
    assert report.decoded_name_unavailable == ""
    assert report.catalogue_unavailable == ""
    assert _rows(session) == []


def test_batch_counts_fold_into_one_answer_and_the_unknown_is_said_once(session):
    """``DecorationReport.merge`` — counters add, the UNKNOWN does not repeat."""
    a = DecorationReport(organization_id=ORG, products_considered=3, names_decoded=2,
                         decoded_name_unavailable="pie-parser is not checked out",
                         refusals={("grade", "blank"): 1})
    b = DecorationReport(organization_id=ORG, products_considered=4, names_decoded=1,
                         decoded_name_unavailable="pie-parser is not checked out",
                         refusals={("grade", "blank"): 2, ("coating", "no value"): 1})

    merged = a.merge(b)

    assert merged.products_considered == 7
    assert merged.names_decoded == 3
    assert merged.decoded_name_unavailable == "pie-parser is not checked out"
    assert merged.refusals == {("grade", "blank"): 3, ("coating", "no value"): 1}


# ── the absent engine, through both doors ───────────────────────────────────
@pytest.mark.requires_pie
def test_a_batched_run_with_no_engine_writes_nothing_and_retracts_nothing(
        session, monkeypatch, tmp_path):
    """The failure that would read as a Phase 1 regression rather than a fault.

    A deployment ships without pie-parser; the sync runs; the store empties; the
    coverage report says Phase 1 went backwards. The package refuses to make
    that write, and this pins that the batching caller does not reintroduce it —
    the loop touches every product in the organization, which is exactly the
    shape that would retract everything.
    """
    _master(session, 7)
    _install(monkeypatch, _Catalogue(available=False))
    decorate_organization(session, ORG, batch_size=2)
    before = _snapshot(session)
    assert before, "nothing was decorated, so an empty table would prove nothing"

    _no_pie_parser(monkeypatch, tmp_path)
    report = decorate_organization(session, ORG, batch_size=2)

    assert report.decoded_name_unavailable
    assert report.catalogue_unavailable
    assert report.written.retracted == 0
    assert report.written.created == 0
    assert _snapshot(session) == before


@pytest.mark.requires_pie
def test_the_cli_reports_an_engine_it_could_not_ask_rather_than_a_low_number(
        session, monkeypatch, tmp_path, capsys):
    """Exit 1, and the reason printed as a sentence.

    An operator scripting this needs "nothing was measured" apart from "the
    coverage is low", because only one of them is fixed by fetching pie-parser.
    """
    _master(session, 3)
    session.commit()
    _cli_session(session, monkeypatch)

    # Decorate FIRST, with the engine present, so there is something an absent
    # engine could destroy. The first version of this test asserted an empty
    # table after a run over a never-decorated master — empty before and empty
    # after, so it held whether or not the CLI retracted, which is no assertion
    # at all. It is the same defect as a test that asserts a value is one of
    # three rather than which one: a shape assertion cannot see a wrong value.
    decorate_products(session, ORG)
    session.commit()
    before = _rows(session)
    assert before, (
        "the engine decoded nothing for this master, so the rest of this test "
        "would prove nothing about retraction")

    _no_pie_parser(monkeypatch, tmp_path)

    from app.attributes.__main__ import main

    assert main([ORG]) == 1

    out = capsys.readouterr().out
    assert "UNKNOWN, not zero" in out
    assert "pie-parser is not checked out" in out
    assert _rows(session) == before, (
        "an engine that could not be ASKED retracted rows it never contradicted "
        "— 'the pack covered nothing here' and 'nobody asked the pack' are "
        "different facts and only the first is evidence")


# ── the CLI ─────────────────────────────────────────────────────────────────
def _cli_session(session, monkeypatch) -> None:
    """Point ``SessionLocal`` at the test's database.

    The CLI opens its own session because it is a process entry point, and
    ``main`` imports ``SessionLocal`` inside itself so this patch reaches it.
    The in-memory engine is a ``StaticPool``, so both sessions are the same
    connection and see each other's committed rows.
    """
    import app.db as db

    monkeypatch.setattr(
        db, "SessionLocal",
        sessionmaker(bind=session.get_bind(), autoflush=False,
                     expire_on_commit=False, future=True))


def _reported(out: str, label: str) -> int:
    """The number the report prints against ``label``.

    Read out of the line rather than asserted as a formatted string: the column
    widths are presentation and a test that pins them fails on a cosmetic edit
    while saying nothing about the number, which is the part that matters.
    """
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith(label):
            return int(stripped[len(label):].strip().replace(",", ""))
    raise AssertionError(f"the report has no {label!r} line:\n{out}")


@pytest.mark.requires_pie
def test_the_cli_decorates_an_organization_and_says_what_it_did(
        session, monkeypatch, capsys):
    """The whole point: after this, the table is not empty.

    Every number the operator was promised is asserted against what is actually
    in the database rather than against itself, because a report that agrees
    with a run that wrote nothing is the failure mode here.
    """
    _master(session, 10)
    session.commit()
    _cli_session(session, monkeypatch)
    _install(monkeypatch, _Catalogue(available=False))

    from app.attributes.__main__ import main

    assert main([ORG, "--batch-size", "4"]) == 0

    session.expire_all()
    written = _rows(session)
    assert written, "the CLI ran and the table is still empty"
    decorated = {r.product_id for r in written}

    out = capsys.readouterr().out
    assert _reported(out, "products considered") == 10
    assert _reported(out, "names decoded") == len(decorated)
    assert _reported(out, "created") == len(written)
    assert _reported(out, "retracted") == 0
    # And the coverage, which is what decision 002 judges the phase on — with
    # the per-key census, not only the headline.
    assert "Coverage — the Phase 1 exit criterion" in out
    assert f"products with any attribute  {len(decorated):,} of 10" in out
    assert "corner_radius_mm" in out
    # The two undecodable names are the difference, and they hold no rows.
    assert len(decorated) == 8


def test_the_cli_refuses_an_organization_this_database_does_not_have(
        session, monkeypatch, capsys):
    """Exit 2 and a usable message, rather than a run over nothing reported as
    a clean zero — the same reason coverage refuses a denominator it lacks."""
    _org(session)
    session.commit()
    _cli_session(session, monkeypatch)

    from app.attributes.__main__ import main

    assert main(["org_that_is_not_here"]) == 2
    assert "--list-organizations" in capsys.readouterr().err


@pytest.mark.requires_pie
def test_a_rerun_of_the_cli_writes_nothing_and_reports_that(
        session, monkeypatch, capsys):
    """Idempotence, seen through the door an operator actually uses.

    The property is the writer's and is pinned there; what is pinned here is
    that running the *tool* twice does not churn the table, because a batched
    caller that re-read its own writes wrongly would supersede on every run and
    nothing outside would notice.
    """
    _master(session, 6)
    session.commit()
    _cli_session(session, monkeypatch)
    _install(monkeypatch, _Catalogue(available=False))

    from app.attributes.__main__ import main

    main([ORG, "--batch-size", "2"])
    session.expire_all()
    before = _snapshot(session)
    capsys.readouterr()

    assert main([ORG, "--batch-size", "2"]) == 0

    session.expire_all()
    assert _snapshot(session) == before
    out = capsys.readouterr().out
    assert _reported(out, "created") == 0
    assert _reported(out, "superseded") == 0
    assert _reported(out, "unchanged") == len(before)


# ── the sync phase ──────────────────────────────────────────────────────────
def _run(session, org: str = ORG) -> models.SyncRun:
    row = models.SyncRun(organization_id=org, source="test", status="RUNNING")
    session.add(row)
    session.flush()
    return row


@pytest.fixture()
def quiet_analysis(monkeypatch):
    """Stub the trade half of ``execute_analysis``.

    Patched at the names ``execute_analysis`` imports them under, inside its own
    body — so this replaces exactly the four phases and leaves the fifth, the
    decoration, running for real.
    """
    import app.commercial.compute as compute
    import app.commercial.policy as policy
    import app.decisions.opportunities as opportunities
    import app.decisions.service as decisions
    import app.signals.engine as signals
    import app.state.engine as state

    class _Ci:
        signals_by_type: dict = {}

        def to_dict(self):
            return {}

    class _State:
        def to_dict(self):
            return {}

    class _Th:
        version = "ci_test"

    monkeypatch.setattr(signals, "run_detectors", lambda *a, **k: {"signals_emitted": 0})
    monkeypatch.setattr(compute, "recompute", lambda *a, **k: _Ci())
    monkeypatch.setattr(policy, "load_for_org", lambda *a, **k: _Th())
    monkeypatch.setattr(state, "build", lambda *a, **k: _State())
    monkeypatch.setattr(opportunities, "generate_from_state", lambda *a, **k: {})
    monkeypatch.setattr(decisions, "DecisionService",
                        lambda *a, **k: type("_D", (), {"generate": lambda self: {}})())


@pytest.mark.requires_pie
def test_the_sync_decorates_the_master_it_just_pulled(
        session, monkeypatch, quiet_analysis):
    """A re-sync leaves the store current rather than stale — the whole reason
    the phase exists, and what makes the table non-empty in production."""
    from app.ingestion import jobs

    _master(session, 6)
    run = _run(session)
    _install(monkeypatch, _Catalogue(available=False))
    monkeypatch.setattr(jobs, "_run_attribution", lambda *a, **k: {})
    phases: List[str] = []

    def phase(name: str) -> None:
        phases.append(name)
        session.commit()

    notes = jobs.execute_analysis(session, run, ORG, on_phase=phase)

    assert _rows(session), "the sync ran and the attribute store is still empty"
    assert "Decoding product attributes" in phases

    # And the run says what the phase did, or the sync screen shows a phase that
    # ran and nothing about it.
    attributes = notes["attributes"]
    assert attributes["products_considered"] == 6
    assert attributes["rows_created"] == len(_rows(session))
    assert attributes["products_with_any_attribute"] == len(
        {r.product_id for r in _rows(session)})
    assert attributes["coverage_rate"] == pytest.approx(
        attributes["products_with_any_attribute"] / 6)
    assert attributes["by_source_kind"] == {DECODED_NAME: attributes["live_values"]}
    assert "decoded_name_unavailable" not in attributes
    # Written where the sync screen reads it: `data_status` projects `notes`
    # wholesale, so this is a JSON column and everything in it must survive one.
    import json

    json.dumps(notes)


@pytest.mark.requires_pie
def test_the_sync_phase_says_it_could_not_ask_rather_than_reporting_a_zero(
        session, monkeypatch, tmp_path, quiet_analysis):
    """And it leaves the store alone. The two halves are one test because
    reporting the UNKNOWN while still retracting would be the worse bug."""
    from app.ingestion import jobs

    _master(session, 5)
    _install(monkeypatch, _Catalogue(available=False))
    decorate_organization(session, ORG)
    session.commit()
    before = _snapshot(session)
    assert before

    _no_pie_parser(monkeypatch, tmp_path)
    run = _run(session)
    monkeypatch.setattr(jobs, "_run_attribution", lambda *a, **k: {})
    notes = jobs.execute_analysis(session, run, ORG, on_phase=lambda _n: session.commit())

    attributes = notes["attributes"]
    assert "pie-parser is not checked out" in attributes["decoded_name_unavailable"]
    assert attributes["catalogue_unavailable"]
    assert attributes["rows_retracted"] == 0
    assert _snapshot(session) == before


def test_a_decoration_that_fails_does_not_fail_the_sync(
        session, monkeypatch, quiet_analysis):
    """``execute_sync`` never raises, because a job that vanishes is
    indistinguishable from one that never started — so neither may this.

    The failure has to be *visible*, though: it lands on the run's unresolved
    list through ``analysis_gaps``, not only in a notes blob nobody opens.
    """
    from app.ingestion import jobs

    _master(session, 3)
    run = _run(session)
    monkeypatch.setattr(jobs, "_run_attribution", lambda *a, **k: {})

    def boom(*_a, **_k):
        raise RuntimeError("the pack is corrupt")

    import app.attributes as attributes_pkg

    monkeypatch.setattr(attributes_pkg, "decorate_organization", boom)

    notes = jobs.execute_analysis(session, run, ORG,
                                  on_phase=lambda _n: session.commit())

    assert "the pack is corrupt" in notes["attributes"]["failed"]
    gaps = jobs.analysis_gaps(notes)
    assert [g["label"] for g in gaps] == ["Decoding product attributes"]
    # The phases after it still ran: one phase failing costs that phase.
    assert "state" in notes


# ── the touched set ─────────────────────────────────────────────────────────

def test_a_named_product_set_narrows_the_run_instead_of_redoing_the_master(session):
    """The sync hands over what it actually moved, the way it already hands
    ``customer_ids`` to the same function two lines above.

    Without this every cycle re-decodes the whole master — measured at ~11s over
    6,717 products, so tens of seconds per sync on a real book spent rewriting
    rows that already say the same thing.
    """
    from app.attributes import decorate_organization

    _master(session, 4)
    session.commit()
    ids = sorted(p.product_id for p in session.query(models.Product).all())

    report = decorate_organization(session, ORG, product_ids=ids[:1])
    session.commit()

    assert report.products_considered == 1, (
        "the run read the whole master rather than the set it was given")
    assert {r.product_id for r in session.query(
        models.ProductAttributeValue).all()} <= {ids[0]}


def test_an_empty_touched_set_decorates_nothing_and_none_decorates_everything(session):
    """The two are different instructions and must not collapse.

    An empty *touched* set means a sync that changed no product; re-decorating
    the master for it would be the opposite of the point. ``None`` means no set
    was given — a first run, a full re-sync — and decorates everything.
    """
    from app.attributes import decorate_organization

    _master(session, 4)
    session.commit()

    assert decorate_organization(
        session, ORG, product_ids=[]).products_considered == 0
    assert decorate_organization(
        session, ORG, product_ids=None).products_considered == 4


def test_a_product_id_from_another_tenant_decorates_nothing(session):
    """The set narrows; it never widens across an organization boundary."""
    from app.attributes import decorate_organization

    _master(session, 2)
    session.add(models.Product(
        product_id="p-other", organization_id="org_other", external_id="x",
        name="CNMG 120408 - KCK15", source_ref={}))
    session.commit()

    report = decorate_organization(session, ORG, product_ids=["p-other"])
    assert report.products_considered == 0
    assert session.query(models.ProductAttributeValue).filter(
        models.ProductAttributeValue.product_id == "p-other").count() == 0
