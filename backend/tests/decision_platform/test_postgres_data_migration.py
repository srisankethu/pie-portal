"""The SQLite → Postgres data move: exact, refusable, and rerunnable.

These tests drive ``scripts/migrate_to_postgres.py`` — the one supported way
to carry an existing SQLite deployment's rows into PostgreSQL — against a real
Postgres server. They skip without one (start it with
``scripts/pg_sandbox.sh start`` and export ``PIE_TEST_DATABASE_URL``), and are
skipped rather than silently passed for the reason the pie-parser tests are:
absence of the infrastructure narrows the run, it does not bless it.

What is pinned here, in order of how expensive each would be to learn in
production:

* every row arrives, and money survives to the paisa (Decimal, not float);
* naive-UTC timestamps come back as the same instant, now timezone-aware;
* the first INSERT after cutover does not collide with a copied serial id —
  the classic forgotten-``setval`` failure;
* a target that already holds tables is refused untouched;
* a source that is not at this codebase's Alembic head is refused, because a
  column set that differs from the models is a copy that silently drops data.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.domain import models

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent

pytestmark = pytest.mark.skipif(
    not dbsupport.TEST_SERVER_URL,
    reason="needs a Postgres server: scripts/pg_sandbox.sh start, then set "
           "PIE_TEST_DATABASE_URL")


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "migrate_to_postgres", REPO / "scripts" / "migrate_to_postgres.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ORG = "org_mig_test"
STAMP = datetime(2026, 3, 14, 9, 26, 53)  # naive — UTC by app.clock convention


def _seed_source(db_path: Path) -> None:
    """A small dataset that exercises what the copy must not corrupt:
    Decimals, JSON, naive-UTC datetimes, an FK chain, and a serial id."""
    from app.bootstrap import ensure_schema
    from app.seed import ensure_org_and_users

    ensure_schema(f"sqlite:///{db_path}")
    eng = create_engine(f"sqlite:///{db_path}", future=True)
    s = sessionmaker(bind=eng, future=True)()
    try:
        ensure_org_and_users(s)
        s.add(models.Organization(organization_id=ORG, name="Migration Test",
                                  config={"note": "carried through JSON"}))
        s.add(models.Customer(customer_id="cus_1", organization_id=ORG,
                              external_id="Z-1", name="Precision Auto",
                              created_at=STAMP))
        s.add(models.Product(product_id="prd_1", organization_id=ORG,
                             external_id="I-1", name="CNMG 120408"))
        s.add(models.SalesTxn(sales_txn_id="txn_1", organization_id=ORG,
                              external_ref="INV-1", customer_id="cus_1",
                              product_id="prd_1", date=date(2026, 3, 1),
                              qty=Decimal("10"), unit_price=Decimal("417.23"),
                              line_revenue=Decimal("4172.30")))
        for i in range(3):
            s.add(models.BusinessEvent(organization_id=ORG,
                                       event_type="SALE_RECORDED",
                                       occurred_on=date(2026, 3, 1 + i),
                                       source_doc_type="invoice",
                                       source_doc_id=f"INV-{i}",
                                       payload={"n": i}))
        s.commit()
    finally:
        s.close()
        eng.dispose()


@pytest.fixture()
def target_url():
    """An empty Postgres database of our own, never the shared fixture one."""
    url = dbsupport.sibling_database_url(
        dbsupport.TEST_SERVER_URL, f"mig_{dbsupport.worker_id()}")
    dbsupport.ensure_database(url)
    dbsupport.wipe_schema(url)
    return url


@pytest.fixture()
def source_db(tmp_path):
    db = tmp_path / "platform.db"
    _seed_source(db)
    return db


def test_the_copy_is_exact_and_commits(source_db, target_url):
    tool = _load_tool()
    report = tool.migrate(source_db, target_url, backup=False)

    assert report["result"] == "COMMITTED"
    assert report["mismatches"] == []
    assert report["tables"]["sales_txns"] == {"source": 1, "target": 1}
    assert report["tables"]["business_events"] == {"source": 3, "target": 3}
    # Money survived as exact decimals, not floats. Decimal comparison, not
    # string: Postgres renders the column's full scale ("4172.3000"), and
    # 4172.3000 == 4172.30 is exactly the equality that matters.
    assert Decimal(report["numeric_sums"]["sales_txns"]["line_revenue"][1]) \
        == Decimal("4172.30")

    eng = create_engine(target_url, future=True)
    try:
        with eng.connect() as c:
            txn = c.execute(select(models.SalesTxn.__table__)).mappings().one()
            assert txn["unit_price"] == Decimal("417.23")
            created = c.execute(text(
                "SELECT created_at FROM customers WHERE customer_id='cus_1'"
            )).scalar()
            # timestamptz returns the same instant, now explicitly UTC-aware.
            assert created == STAMP.replace(tzinfo=timezone.utc)
            org_cfg = c.execute(text(
                "SELECT config FROM organizations WHERE organization_id=:o"),
                {"o": ORG}).scalar()
            assert org_cfg == {"note": "carried through JSON"}
    finally:
        eng.dispose()


def test_the_first_insert_after_cutover_gets_a_fresh_serial_id(source_db, target_url):
    """The forgotten-setval failure: copied rows hold ids 1..3, and a sequence
    still at 1 makes the first post-cutover insert a unique-violation."""
    tool = _load_tool()
    assert tool.migrate(source_db, target_url, backup=False)["result"] == "COMMITTED"

    eng = create_engine(target_url, future=True)
    try:
        with eng.begin() as c:
            c.execute(text("SET timezone = 'UTC'"))
            seq = c.execute(models.BusinessEvent.__table__.insert().returning(
                models.BusinessEvent.__table__.c.seq),
                {"organization_id": ORG, "event_type": "SALE_RECORDED",
                 "occurred_on": date(2026, 4, 1), "source_doc_type": "invoice",
                 "source_doc_id": "INV-9", "payload": {},
                 "recorded_at": STAMP}).scalar_one()
        assert seq == 4, "sequence must continue past the copied rows"
    finally:
        eng.dispose()


def test_a_populated_target_is_refused_untouched(source_db, target_url):
    tool = _load_tool()
    assert tool.migrate(source_db, target_url, backup=False)["result"] == "COMMITTED"

    with pytest.raises(tool.MigrationRefused, match="only migrates into an empty"):
        tool.migrate(source_db, target_url, backup=False)


def test_a_source_behind_head_is_refused(tmp_path, target_url):
    """A source whose schema predates the models would copy a narrower column
    set and silently drop whatever the newer columns hold. Refuse instead."""
    tool = _load_tool()
    db = tmp_path / "old.db"
    _seed_source(db)
    eng = create_engine(f"sqlite:///{db}", future=True)
    try:
        with eng.begin() as c:
            c.execute(text("UPDATE alembic_version SET version_num='41730a334a54'"))
    finally:
        eng.dispose()

    with pytest.raises(tool.MigrationRefused, match="source"):
        tool.migrate(db, target_url, backup=False)


def test_the_backup_is_taken_and_the_source_is_untouched(source_db, target_url, tmp_path):
    tool = _load_tool()
    before = source_db.read_bytes()
    report = tool.migrate(source_db, target_url, backup=True)
    assert report["result"] == "COMMITTED"
    backup = Path(report["backup"])
    assert backup.exists() and backup.read_bytes() == before
    assert source_db.read_bytes() == before, "the source file must never change"
