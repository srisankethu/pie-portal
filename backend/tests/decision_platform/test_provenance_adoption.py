"""A sync must not duplicate the master list when its provenance changes.

Repeating a sync has always been idempotent. What was not is *changing the
identity of the writer*: the upsert matched on ``(connector, connection_id)``
and set provenance only on insert, so the first pull under a new pair did not
recognise anything written under the old one and re-inserted every customer and
item beside it. In the field that produced two of everything — one row reading
"source not recorded", its twin reading "Zoho".
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.domain import models
from app.ingestion.mock_source import FixtureZohoSource
from app.ingestion.sync import SyncService

ORG = "org_prov"


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(models.Organization(organization_id=ORG, name="Provenance"))
    s.commit()
    yield s
    s.close()


def _connect(session, cid: str, label: str, zoho_id: str) -> None:
    session.add(models.ZohoConnection(
        connection_id=cid, organization_id=ORG, label=label, enabled=True,
        zoho_organization_id=zoho_id))
    session.commit()


def _sync(session, **kw) -> None:
    svc = SyncService(session, FixtureZohoSource(), ORG, **kw)
    svc.begin()
    svc.run_reference()
    session.commit()


def _customers(session) -> int:
    return session.scalar(select(func.count()).select_from(models.Customer)
                          .where(models.Customer.organization_id == ORG)) or 0


def test_repeating_a_sync_never_duplicates(session):
    _sync(session)
    once = _customers(session)
    _sync(session)
    assert _customers(session) == once > 0


def test_gaining_a_connection_adopts_rather_than_duplicates(session):
    """The regression this file exists for.

    One connected company, so a connectionless row can only have come from it.
    Before the fix the second sync doubled the master list.
    """
    _connect(session, "conn_sls", "SLS Engineers", "60000000001")
    _sync(session)                                   # legacy: no connection
    before = _customers(session)

    _sync(session, connection_id="conn_sls")         # the same books, now named
    assert _customers(session) == before, "the master list was re-inserted"

    rows = session.scalars(select(models.Customer)).all()
    assert {r.connection_id for r in rows} == {"conn_sls"}, "rows were not adopted"


def test_a_second_company_never_adopts_the_first_s_records(session):
    """With two books a connectionless row is ambiguous, and a guess pools them.

    Duplicating is the *correct* outcome here: two rows that might belong to
    different companies are two rows until a person says otherwise. Silently
    merging them is unrecoverable — the evidence of the mistake is the thing the
    merge destroys.
    """
    _connect(session, "conn_sls", "SLS Engineers", "60000000001")
    _connect(session, "conn_4u", "4U Precision", "60000000002")
    _sync(session)
    before = _customers(session)

    _sync(session, connection_id="conn_sls")
    assert _customers(session) > before, "an ambiguous row must not be claimed"
    left = session.scalars(select(models.Customer)
                           .where(models.Customer.connection_id.is_(None))).all()
    assert len(left) == before, "the unattributed rows must survive untouched"


def test_a_row_belonging_to_another_connection_is_never_taken(session):
    _connect(session, "conn_sls", "SLS Engineers", "60000000001")
    _connect(session, "conn_4u", "4U Precision", "60000000002")
    _sync(session, connection_id="conn_sls")
    sls = _customers(session)

    _sync(session, connection_id="conn_4u")
    by_conn = dict(session.execute(
        select(models.Customer.connection_id, func.count())
        .group_by(models.Customer.connection_id)).all())
    assert by_conn["conn_sls"] == sls, "the other company's rows were reassigned"
    assert by_conn["conn_4u"] == sls
