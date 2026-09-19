"""Every ordered pair of connectors, in four topologies, plus adoption. Opt-in.

``test_erp_connectors.py`` pins the cases that must never regress and runs on
every push. This module is the other half: the combinatorial sweep behind those
pins — 7 connectors x 7 connectors x 4 topologies, plus one per connector for
adoption, 204 scenarios. A targeted pin says "this pair still works"; only the
matrix says "and so does every other one". It is marked ``matrix`` and excluded
from the default *pytest* run (``pytest.ini``), because 204 fresh databases is
a minute the edit loop should not pay — but ``scripts/verify.sh`` runs it, so
it is in the gate and ``make test-matrix`` runs it alone.

**What this sweep did and did not find.** The two defects the multi-ERP
investigation turned up were found by the investigation, not by these
assertions, and the first version of this docstring claimed otherwise. It was
checkable and wrong: with ``upsert_vendor`` put back to its pre-fix exact-source
select, every scenario here passed while the targeted pin failed on the same
code. The reason was structural — every scenario passed a real connection id,
so ``adopt_connectionless`` was reachable from none of them. Topology D exists
because of that, and fails on all seven connectors against the unfixed upsert.
The rotation defect is still not visible from here and is not meant to be: no
scenario rotates a credential, and the pins for that live in
``test_erp_connectors.py``. What this file sweeps is non-pooling, tenant
isolation and adoption.

The topologies, and the different question each one asks:

  A. **one org, two connections, native fixtures.** A customer who already had
     one ERP connected adds a second. Ids differ naturally between systems, so
     this asks whether a second connection lands *beside* the first.
  B. **two organisations, one connection each, native fixtures.** Tenant
     isolation: org B's pull may not touch, add to or remove anything of org A's.
  C. **forced id collision, in both of the above.** The non-pooling invariant —
     "the same external id under two connectors is two records" — is decided by
     the repository's unique key ``(organization_id, connector, connection_id,
     external_id)``, which is connector-blind. Natural per-ERP fixtures use
     different ids, so a cross-connector pair never actually collides and the
     invariant is never exercised. Both sides therefore run over one canonical
     stub built at the same id prefix, and the scenario asserts that the ids
     genuinely did collide before concluding anything from the row counts.
  D. **one org, one connector, the connection recorded between two pulls.**
     Adoption: rows written before any connection existed are claimed by the
     one connection there is, rather than re-inserted beside themselves. Not a
     pair — adoption is a connector meeting its own earlier rows — so this axis
     is one scenario per connector.

**The sources are real.** Topologies A and B drive each connector's own source
class over its own native fixture rows from
``tests/spec_conformance/connectors`` — every translator, sign convention and
line filter runs for real and only the HTTP call is replaced. Zoho is
deliberately not in the ``ingestion/erp`` registry (its connect flow predates
it) and so has no fixture there; it uses the repo's own
``app.ingestion.mock_source.FixtureZohoSource`` rather than a third hand-written
Zoho stub.

Two fixture facts the assertions below expect rather than flag:

* **sage100 writes no ``CostRecord`` rows.** Its AP history carries GL
  distributions rather than item lines, so its spec declares no bills
  permission and its source implements no ``list_bills``; ``sync.py`` probes
  with ``hasattr`` and skips the stage. Zero is the right answer, and
  ``FOOTPRINT`` says so out loud.
* **The Zoho fixture names a salesperson on every invoice.** Without a platform
  user holding that email, ``_sync_assignments`` records an
  ``UNMAPPED_SALESPERSON`` skip — a fact about this harness, not about the
  product, that would make the zero-skip assertion meaningless. ``_seed`` gives
  the scenario that user.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import Base
from app.domain import models
from app.domain.enums import MembershipStatus, Role
from app.ingestion import erp
from app.ingestion.mock_source import FixtureZohoSource
from app.ingestion.sync import SyncService
from tests.spec_conformance.connectors import (acumatica, dynamics365, netsuite,
                                               prophet21, sage100, sagex3)

# The ``session`` fixture (tests/decision_platform/conftest.py) hands out a
# disposable database from ``dbsupport.fresh_engine`` — SQLite in memory by
# default, a per-worker Postgres when ``PIE_TEST_DATABASE_URL`` is set. Taken
# from there rather than built here so this suite runs on both backends like
# every other one.

pytestmark = pytest.mark.matrix

ORG_A, ORG_B = "org_alpha", "org_bravo"
CONN_A, CONN_B = "conn-a", "conn-b"

#: Every connector the platform declares. Zoho is included although it is not a
#: registry entry: it is a connector to everything downstream of the source —
#: the unique key, the provenance stamp and the org filter do not know the
#: difference — so leaving it out would exclude the one connector most of this
#: business actually runs on.
CONNECTORS = ("zoho", "netsuite", "dynamics365", "acumatica", "prophet21",
              "sagex3", "sage100")

#: Ordered, not combinations: which system arrives second is half the question.
PAIRS = [(a, b) for a in CONNECTORS for b in CONNECTORS]

_NATIVE_FIXTURE = {
    "netsuite": netsuite.build_source,
    "dynamics365": dynamics365.build_source,
    "acumatica": acumatica.build_source,
    "prophet21": prophet21.build_source,
    "sagex3": sagex3.build_source,
    "sage100": sage100.build_source,
}

#: Every table a pull writes that carries provenance, against the column holding
#: the source system's own id — masters key on ``external_id``, the document
#: tables on ``external_ref``. Same split ``ReadModelRepository._for_upsert``
#: takes as ``ref_col``, and the reason a count of customers alone is not enough:
#: a regression that pooled the invoice and bill lines while leaving the masters
#: intact would read clean.
SOURCED = {
    models.Customer: "external_id",
    models.Product: "external_id",
    models.Vendor: "external_id",
    models.SalesTxn: "external_ref",
    models.CostRecord: "external_ref",
}

_ONE_OF_EACH = {models.Customer: 1, models.Product: 1, models.Vendor: 1,
                models.SalesTxn: 1, models.CostRecord: 1}

#: Rows one connector's fixture writes into an empty organisation. Written down
#: rather than measured by a reference sync: an expectation computed by running
#: the code under test agrees with it by construction, and a connector that
#: silently stopped emitting invoices would take the expectation down with it.
FOOTPRINT = {
    "zoho": {models.Customer: 3, models.Product: 2, models.Vendor: 2,
             models.SalesTxn: 3, models.CostRecord: 1},
    "netsuite": _ONE_OF_EACH,
    "dynamics365": _ONE_OF_EACH,
    "acumatica": _ONE_OF_EACH,
    "prophet21": _ONE_OF_EACH,
    "sagex3": _ONE_OF_EACH,
    # No ``list_bills`` — see the module docstring.
    "sage100": {**_ONE_OF_EACH, models.CostRecord: 0},
}

#: Both sides of a collision scenario are built at this one prefix, on purpose.
COLLIDING_PREFIX = "X"

#: ``name_vault`` re-encrypts on every write — the ciphertext for one unchanged
#: name differs between two stores because the nonce does — so its rows move
#: under a second sync by design, and a snapshot that included them would report
#: a mutation on every scenario.
VOLATILE_TABLES = {"name_vault"}


class CollidingStub:
    """A source already speaking the canonical shape, as every ERP source does
    once its own translator has run.

    A deliberate copy of ``test_erp_connectors._CanonicalStub``, as ``SOURCED``
    is of its ``_SOURCED``. Importing them would couple a module the gate never
    collects to two private names inside a module it collects on every push:
    renaming either would leave this suite broken until the nightly run, and a
    default-excluded suite is exactly the one nobody would notice was red. The
    copy is the cheaper failure — it goes stale loudly, when a scenario here
    stops agreeing with the pin there.
    """

    def __init__(self, prefix: str):
        self.p = prefix

    def list_contacts(self):
        return [{"contact_id": f"{self.p}-C1", "contact_name": "Acme Industrial",
                 "status": "active"}]

    def list_vendors(self):
        return [{"contact_id": f"{self.p}-V1", "contact_name": "Kennametal Inc",
                 "status": "active"}]

    def list_items(self):
        return [{"item_id": f"{self.p}-I1", "name": "CNMG 120408",
                 "sku": "CNMG-1", "status": "active"}]

    def list_invoices(self, skip=None):
        return [{"invoice_id": f"{self.p}-INV1", "customer_id": f"{self.p}-C1",
                 "date": "2026-06-01", "currency_code": "USD",
                 "total": 250, "balance": 250,
                 "line_items": [{"line_item_id": "1", "item_id": f"{self.p}-I1",
                                 "quantity": 10, "rate": 25, "item_total": 250}]}]

    def list_bills(self, skip=None):
        return [{"bill_id": f"{self.p}-B1", "vendor_id": f"{self.p}-V1",
                 "date": "2026-05-20", "currency_code": "USD", "total": 1200,
                 "line_items": [{"line_item_id": "1", "item_id": f"{self.p}-I1",
                                 "quantity": 100, "rate": 12, "item_total": 1200}]}]

    def list_users(self):
        return []


def native_source(key: str):
    """That connector's real source class, over its own native fixture rows."""
    if key == "zoho":
        return FixtureZohoSource()
    return _NATIVE_FIXTURE[key]()


# ── setup ───────────────────────────────────────────────────────────────────
def seed(session, *organization_ids: str) -> None:
    """The organisations a scenario needs, and the salesperson they share.

    One identity with an ACTIVE membership in each organisation, rather than one
    user per organisation: ``users.email`` is globally unique, so the Zoho
    fixture's ``r.nair@pie.example`` can exist exactly once — and
    ``ReadModelRepository.users_by_email`` resolves through ``memberships``, not
    through ``users.organization_id``, so the one row serves both.
    """
    for organization_id in organization_ids:
        session.add(models.Organization(organization_id=organization_id,
                                        name=organization_id, currency="USD"))
    session.add(models.User(user_id="u_matrix", organization_id=organization_ids[0],
                            email="r.nair@pie.example", name="R. Nair",
                            role=Role.SALESPERSON.value))
    session.flush()
    for organization_id in organization_ids:
        session.add(models.OrganizationMembership(
            organization_id=organization_id, user_id="u_matrix",
            role=Role.SALESPERSON.value, status=MembershipStatus.ACTIVE.value))
    session.flush()


def record_connection(session, org: str, connection_id: str, connector: str) -> None:
    """A connected company on the books, which is what ``_sole_connection``
    counts. That query filters on the organisation alone — the connector is
    incidental to it — but it is set truthfully so the row is not a shape that
    could only exist in a test."""
    session.add(models.ZohoConnection(connection_id=connection_id,
                                      organization_id=org, connector=connector,
                                      label=connection_id,
                                      zoho_organization_id=connection_id))
    session.flush()


def run_sync(session, source, org: str, connector: str, connection_id: str):
    """One pull, flushed.

    The flush is load-bearing. This session runs with ``autoflush=False``, and
    the snapshots below read at Core level: without it a Core ``select`` sees
    the database as it was before the pull, and the *next* sync's flush then
    shows up as that sync having mutated the first connection's rows.
    """
    report = SyncService(session, source, org, connector=connector,
                         connection_id=connection_id).run()
    session.flush()
    return report


# ── inspection ──────────────────────────────────────────────────────────────
def snapshot(session, org: str) -> dict:
    """Every column of every row filed under one organisation.

    Keyed by table and primary key, and taken over ``Base.metadata`` rather than
    over the five read models: a second connection rewriting a derived metric or
    an identity record would be exactly as much of a leak, and only a
    whole-schema snapshot can see one.
    """
    out = {}
    for table in Base.metadata.sorted_tables:
        if "organization_id" not in table.c or table.name in VOLATILE_TABLES:
            continue
        key_columns = [c.name for c in table.primary_key]
        if not key_columns:
            continue
        rows = session.execute(
            select(table).where(table.c.organization_id == org)).mappings()
        for row in rows:
            out[(table.name, tuple(row[c] for c in key_columns))] = {
                column: str(value) for column, value in row.items()}
    return out


def disturbed(before: dict, after: dict) -> list[str]:
    """Rows of ``before`` that ``after`` no longer holds, or holds differently.

    Rows only ``after`` holds are not disturbance: in a one-org topology the
    second connection is *supposed* to add its own.
    """
    out = []
    for key, row in before.items():
        table, primary_key = key
        if key not in after:
            out.append(f"{table}{primary_key}: REMOVED")
            continue
        out += [f"{table}{primary_key}.{column}: {value!r} -> {after[key][column]!r}"
                for column, value in row.items() if after[key][column] != value]
    return out


def rows_of(session, model, org: str, connection_id: str | None = None):
    rows = session.scalars(select(model).where(model.organization_id == org)).all()
    if connection_id is None:
        return list(rows)
    return [r for r in rows if r.connection_id == connection_id]


def counts(session, org: str, connection_id: str | None = None) -> dict:
    return {model: len(rows_of(session, model, org, connection_id))
            for model in SOURCED}


def provenance(session, org: str) -> dict:
    """``{connection_id: {(connector, source_ref.system)}}`` over every sourced row.

    Both halves of the stamp in one structure, because the two lie in different
    ways: the column decides which connection owns the row, and ``source_ref``
    is what a screen shows as its origin. A row can carry a right column and a
    wrong badge.
    """
    out: dict = {}
    for model in SOURCED:
        for row in rows_of(session, model, org):
            stamp = (row.connector, (row.source_ref or {}).get("system"))
            out.setdefault(row.connection_id, set()).add(stamp)
    return out


def external_ids(session, org: str, connection_id: str) -> dict:
    return {model: {getattr(r, ref) for r in rows_of(session, model, org, connection_id)}
            for model, ref in SOURCED.items()}


def skip_codes(report) -> list[str]:
    return sorted({s["code"] for s in report.skipped})


# ── the matrix covers what the registry declares ────────────────────────────
def test_every_registered_connector_has_a_row_and_a_column():
    """A connector missing from ``CONNECTORS`` would be tested by nothing here,
    and this suite would still report green on every scenario it did run.

    ``spec_conformance/test_conformance.py`` makes the same demand of its own
    fixture list and it is the one that blocks the gate; this is the local
    half — that the tuple above, and the footprint each pair is measured
    against, did not drift away from it.
    """
    assert set(CONNECTORS) == {spec.key for spec in erp.catalog()} | {"zoho"}
    assert set(FOOTPRINT) == set(CONNECTORS)
    assert set(_NATIVE_FIXTURE) == set(CONNECTORS) - {"zoho"}


# ── topology A: one organisation, two connections, native fixtures ──────────
@pytest.mark.parametrize("a,b", PAIRS)
def test_a_second_connection_lands_beside_the_first(session, a, b):
    seed(session, ORG_A)
    report_a = run_sync(session, native_source(a), ORG_A, a, CONN_A)
    before = snapshot(session, ORG_A)
    report_b = run_sync(session, native_source(b), ORG_A, b, CONN_B)

    assert skip_codes(report_a) == [] and skip_codes(report_b) == []
    assert provenance(session, ORG_A) == {CONN_A: {(a, a)}, CONN_B: {(b, b)}}
    assert counts(session, ORG_A, CONN_A) == FOOTPRINT[a]
    assert counts(session, ORG_A, CONN_B) == FOOTPRINT[b]
    assert counts(session, ORG_A) == {
        model: FOOTPRINT[a][model] + FOOTPRINT[b][model] for model in SOURCED}
    assert disturbed(before, snapshot(session, ORG_A)) == []


# ── topology B: two organisations, one connection each, native fixtures ─────
@pytest.mark.parametrize("a,b", PAIRS)
def test_a_second_organisation_syncs_without_touching_the_first(session, a, b):
    seed(session, ORG_A, ORG_B)
    report_a = run_sync(session, native_source(a), ORG_A, a, CONN_A)
    before = snapshot(session, ORG_A)
    report_b = run_sync(session, native_source(b), ORG_B, b, CONN_B)
    after = snapshot(session, ORG_A)

    assert skip_codes(report_a) == [] and skip_codes(report_b) == []
    assert provenance(session, ORG_A) == {CONN_A: {(a, a)}}
    assert provenance(session, ORG_B) == {CONN_B: {(b, b)}}
    assert counts(session, ORG_A) == FOOTPRINT[a]
    assert counts(session, ORG_B) == FOOTPRINT[b]
    assert disturbed(before, after) == []
    # Nothing ADDED to org A either. The other topology tolerates additions
    # because the second connection shares the organisation; here a single new
    # row under org A would be org B's pull writing across the tenant line.
    assert set(after) == set(before)


# ── topology C: the same, with the external ids forced to collide ───────────
@pytest.mark.parametrize("a,b", PAIRS)
def test_colliding_ids_in_one_organisation_stay_two_sets_of_records(session, a, b):
    seed(session, ORG_A)
    report_a = run_sync(session, CollidingStub(COLLIDING_PREFIX), ORG_A, a, CONN_A)
    before = snapshot(session, ORG_A)
    report_b = run_sync(session, CollidingStub(COLLIDING_PREFIX), ORG_A, b, CONN_B)

    assert skip_codes(report_a) == [] and skip_codes(report_b) == []
    # Asserted, not assumed: a non-pooling scenario whose two sides quietly
    # stopped colliding would pass on row counts while testing nothing.
    assert external_ids(session, ORG_A, CONN_A) == external_ids(session, ORG_A, CONN_B)
    assert provenance(session, ORG_A) == {CONN_A: {(a, a)}, CONN_B: {(b, b)}}
    assert counts(session, ORG_A, CONN_A) == _ONE_OF_EACH
    assert counts(session, ORG_A, CONN_B) == _ONE_OF_EACH
    assert counts(session, ORG_A) == {model: 2 for model in SOURCED}
    assert disturbed(before, snapshot(session, ORG_A)) == []


@pytest.mark.parametrize("a,b", PAIRS)
def test_colliding_ids_in_two_organisations_stay_in_their_own_organisation(session, a, b):
    seed(session, ORG_A, ORG_B)
    report_a = run_sync(session, CollidingStub(COLLIDING_PREFIX), ORG_A, a, CONN_A)
    before = snapshot(session, ORG_A)
    report_b = run_sync(session, CollidingStub(COLLIDING_PREFIX), ORG_B, b, CONN_B)
    after = snapshot(session, ORG_A)

    assert skip_codes(report_a) == [] and skip_codes(report_b) == []
    assert external_ids(session, ORG_A, CONN_A) == external_ids(session, ORG_B, CONN_B)
    assert provenance(session, ORG_A) == {CONN_A: {(a, a)}}
    assert provenance(session, ORG_B) == {CONN_B: {(b, b)}}
    assert counts(session, ORG_A) == _ONE_OF_EACH
    assert counts(session, ORG_B) == _ONE_OF_EACH
    assert disturbed(before, after) == []
    assert set(after) == set(before)


# ── topology D: the connection arrives after the rows do ────────────────────
#
# Not a pair: adoption is one connector meeting its own earlier rows, so the
# second axis would be 48 repetitions of the same question. Per connector is
# the whole of it.
@pytest.mark.parametrize("key", CONNECTORS)
def test_a_connection_recorded_after_the_first_pull_adopts_what_it_wrote(session, key):
    """A pull that ran before any connection existed is claimed, not twinned.

    The topology the first version of this file did not have, and the reason
    that mattered: every scenario above passes a real connection id, so
    ``adopt_connectionless`` was reachable from none of them. The matrix
    therefore reported 196 green against the unfixed ``upsert_vendor`` — the
    one master upsert running its own exact-source select instead of going
    through ``_for_upsert`` — while the targeted pin in
    ``test_erp_connectors.py`` failed on the same code. A sweep that cannot see
    the defect its own docstring claims to have found is worse than no sweep,
    because it is read as coverage.

    Swept across all seven rather than pinned on one because the twinning is
    per *table*, not per connector, and which tables a connector writes differs
    — sage100 has no bills stage at all. A connector whose only vendor rows
    arrive through a path this one does not exercise would look adopted here
    for the wrong reason, and ``FOOTPRINT`` is what says which rows to expect.
    """
    seed(session, ORG_A)
    run_sync(session, native_source(key), ORG_A, key, None)
    # Everything the first pull wrote is unattributed: there was nowhere to
    # attribute it to. Asserted, because a fixture that quietly started
    # stamping a connection would make the adoption below a no-op.
    assert all(r.connection_id is None
               for model in SOURCED for r in rows_of(session, model, ORG_A))

    record_connection(session, ORG_A, CONN_A, key)
    run_sync(session, native_source(key), ORG_A, key, CONN_A)

    for model, ref in SOURCED.items():
        rows = rows_of(session, model, ORG_A)
        assert len(rows) == FOOTPRINT[key][model], (
            f"{model.__name__} twinned: "
            f"{[(getattr(r, ref), r.connection_id) for r in rows]}")
    assert counts(session, ORG_A, CONN_A) == FOOTPRINT[key]
    # The badge as well as the column: an adopted row keeps whatever
    # ``source_ref`` the pull that claimed it wrote, and a row claimed by the
    # right connection while still advertising the wrong system is a screen
    # showing the wrong origin.
    assert provenance(session, ORG_A) == {CONN_A: {(key, key)}}
