"""The one repair ``scripts/diagnose_attribution.py`` will perform, pinned.

``upsert_vendor`` was for a while the one master upsert that did not go through
``_for_upsert``, so a book that first recorded a connection kept a
NULL-connection twin of every vendor. That is fixed, and the fix does not reach
the twins already on disk: the exact-source lookup finds the good row and
returns before adoption is consulted, so they are permanent until something
deletes them.

``--repair`` deletes them. It is the only write this repository performs against
a master table outside a sync, which is why the conditions are pinned here
rather than left to the script's own prose:

* a twin with a real connection must survive, or the row is the only copy of
  that vendor and deleting it is data loss rather than de-duplication;
* nothing may point at the row, over every foreign key into
  ``vendors.vendor_id`` — derived from the metadata, so a table added later is
  covered without anyone remembering to add it here.

The negative cases are the point. A test that only proves the deletable row is
deleted would pass just as happily against a script that deleted all three.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

BACKEND = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = BACKEND.parent / "scripts" / "diagnose_attribution.py"

from app.db import Base            # noqa: E402
from app.domain import models      # noqa: E402


def _vendor(ext: str, connection_id, name: str) -> models.Vendor:
    return models.Vendor(organization_id="o", external_id=ext,
                         connector="netsuite", connection_id=connection_id,
                         name=name)


@pytest.fixture()
def seeded(tmp_path):
    """A book holding one of each kind of NULL-connection vendor row.

    Built with ``create_all`` rather than Alembic because this is a fixture and
    the script under test reads whatever ``DATABASE_URL`` names — the schema's
    provenance is not what is being tested here, and the migration chain has its
    own suite.
    """
    db = tmp_path / "attribution.db"
    url = f"sqlite:///{db}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()

    s.add(models.Organization(organization_id="o", name="O", currency="INR"))
    s.flush()

    # 1. A twin with nothing pointing at it — the one row --repair may delete.
    s.add(_vendor("V-1", None, "orphan, free"))
    s.add(_vendor("V-1", "conn-b", "survivor"))

    # 2. A twin something still points at. Blocked: re-pointing the cost record
    #    onto the survivor would be a guess about which vendor the bill meant.
    held = _vendor("V-2", None, "orphan, held")
    s.add(held)
    s.add(_vendor("V-2", "conn-b", "survivor"))

    # 3. No twin at all. Not a duplicate — the only copy of that vendor, and the
    #    row a careless repair would destroy.
    s.add(_vendor("V-3", None, "only copy"))

    product = models.Product(organization_id="o", external_id="P-1",
                             connector="netsuite", connection_id="conn-b",
                             name="CNMG 120408")
    s.add(product)
    s.flush()
    s.add(models.CostRecord(
        organization_id="o", external_ref="B-9:1", vendor_id=held.vendor_id,
        product_id=product.product_id, connector="netsuite", connection_id=None,
        date=dt.date(2026, 1, 1), qty=Decimal("1"), unit_cost=Decimal("10"),
        source_ref={"system": "netsuite"}))
    s.commit()
    s.close()
    return url, engine


def _run(url: str, *args: str) -> str:
    proc = subprocess.run([sys.executable, str(SCRIPT), *args],
                          cwd=BACKEND, capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                               "DATABASE_URL": url,
                               "HOME": "/tmp"})
    assert proc.returncode == 0, proc.stderr[-3000:]
    return proc.stdout


def _external_ids(engine) -> list[tuple[str, str | None]]:
    s = sessionmaker(bind=engine, future=True)()
    # Sorted on a substitute for the NULL so the ordering is total: an
    # unattributed row is exactly the case this file is about, and it must not
    # be the thing that makes the comparison raise.
    rows = sorted(((v.external_id, v.connection_id)
                   for v in s.scalars(select(models.Vendor))),
                  key=lambda r: (r[0], r[1] or ""))
    s.close()
    return rows


def test_the_read_only_run_separates_the_three_kinds_and_writes_nothing(seeded):
    """Default is read-only, and it says which rows it would and would not take."""
    url, engine = seeded
    before = _external_ids(engine)

    out = _run(url)

    assert "2 have a surviving twin, 1 are the only copy" in out
    assert "1 deletable, 1 blocked by a reference" in out
    assert "cost_records.vendor_id=1" in out, "a blocked row must name its holder"
    assert _external_ids(engine) == before, "a read-only run wrote something"


def test_repair_deletes_the_free_twin_and_nothing_else(seeded):
    """The whole contract, in one assertion over the surviving rows."""
    url, engine = seeded

    out = _run(url, "--repair")
    assert "deleted 1 twinned vendor orphan(s)" in out

    assert _external_ids(engine) == [
        ("V-1", "conn-b"),      # the survivor kept
        ("V-2", None),          # blocked by the cost record — still here
        ("V-2", "conn-b"),
        ("V-3", None),          # the only copy — never a candidate
    ]


def test_a_vendor_with_no_twin_is_never_deleted(seeded):
    """The condition that separates a repair from data loss.

    A NULL-connection vendor with no surviving twin is not a duplicate. It is
    the only record of that supplier, and a sync on a single-connection book
    will adopt it. Deleting it would lose the row to save a tidy count.
    """
    url, engine = seeded
    _run(url, "--repair")

    s = sessionmaker(bind=engine, future=True)()
    lonely = s.scalars(select(models.Vendor).where(
        models.Vendor.external_id == "V-3")).all()
    s.close()
    assert [(v.connection_id, v.name) for v in lonely] == [(None, "only copy")]


def test_a_referenced_orphan_survives_with_its_reference_intact(seeded):
    """Blocked means blocked: neither the row nor what points at it moves."""
    url, engine = seeded
    _run(url, "--repair")

    s = sessionmaker(bind=engine, future=True)()
    held = s.scalar(select(models.Vendor).where(
        models.Vendor.external_id == "V-2",
        models.Vendor.connection_id.is_(None)))
    assert held is not None, "a referenced orphan was deleted"
    cost = s.scalar(select(models.CostRecord))
    assert cost.vendor_id == held.vendor_id, "the reference was re-pointed"
    s.close()


def test_repair_is_idempotent(seeded):
    """Running it twice is the normal way it is used — report, repair, confirm."""
    url, engine = seeded
    _run(url, "--repair")
    after_first = _external_ids(engine)

    out = _run(url, "--repair")
    assert "nothing to do" in out
    assert _external_ids(engine) == after_first


def test_the_referring_tables_are_read_from_the_metadata(seeded):
    """Not a list in the script, which would go stale the first time one is added."""
    url, _engine = seeded
    out = _run(url)

    referrers = sum(1 for t in Base.metadata.tables.values()
                    for c in t.columns
                    for fk in c.foreign_keys
                    if fk.column is models.Vendor.__table__.c.vendor_id)
    assert referrers > 1, "the derivation itself found nothing — check the target"
    assert f"tables referencing a vendor: {referrers}" in out
