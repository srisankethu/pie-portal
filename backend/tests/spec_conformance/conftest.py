"""One pull per connector, captured once and shared by every check.

A sync is not free and there are six of them, so each connector is pulled once
per session and the emissions are handed to whichever check asks. The database
under it is per-session for the same reason; the idempotency test, which needs
to count rows before and after a second pull, takes its own private one.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest
from sqlalchemy.orm import Session, sessionmaker

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

import dbsupport  # noqa: E402
from app.db import Base  # noqa: E402
from app.domain import models  # noqa: E402,F401  (populate metadata)
from app.ingestion.sync import SyncService  # noqa: E402

from .harness import Emission, capturing  # noqa: E402

#: The organization every fixture pull writes into. Its currency matches every
#: fixture's, deliberately: ``sync._refuses_currency`` drops a document stated
#: in anything else, and a suite whose documents were all refused would report
#: no failures because it had examined no records.
ORG = "org_conformance"
CURRENCY = "USD"


def make_org(session: Session, organization_id: str = ORG) -> str:
    session.add(models.Organization(organization_id=organization_id,
                                    name="Conformance", currency=CURRENCY))
    session.flush()
    return organization_id


def open_session(engine) -> Session:
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    return maker()


@dataclass(frozen=True)
class Pull:
    """What one sync produced, and what stopped it if anything did.

    The error is carried rather than raised out of the fixture because a sync
    that dies part-way still emitted records worth checking, and losing them
    would turn one defect into no coverage. ``test_every_connector_completes_a
    _whole_sync`` is what makes the death loud; everything else reads
    ``emissions`` and says what it examined.
    """

    emissions: list[Emission]
    error: Optional[BaseException] = None


def pull(session: Session, under_test, organization_id: str = ORG,
         connection_id: str = "conn-conformance") -> Pull:
    """One whole sync, with every canonical DTO it built recorded.

    The real ``SyncService`` over the real source: which stages run, which
    normalizer reads each of them and what the sync does with the result are
    all its own decisions, not this suite's.
    """
    emissions: list[Emission] = []
    try:
        with capturing(under_test.key, emissions):
            SyncService(session, under_test.build_source(), organization_id,
                        connector=under_test.key, connection_id=connection_id).run()
    except Exception as error:  # noqa: BLE001 — reported by name, never swallowed
        session.rollback()
        return Pull(emissions, error)
    return Pull(emissions)


@pytest.fixture(scope="session")
def pulled() -> dict[str, Pull]:
    """Every connector's emissions, from one pull each."""
    from .connectors import UNDER_TEST

    engine = dbsupport.fresh_engine()
    session = open_session(engine)
    try:
        out: dict[str, Pull] = {}
        for index, under_test in enumerate(UNDER_TEST):
            # One organization per connector. Sharing one would let a second
            # connector's pull resolve against the first's customers and hide
            # a missing reference behind a neighbour's row.
            organization_id = make_org(session, f"{ORG}_{index}")
            out[under_test.key] = pull(session, under_test, organization_id,
                                       f"conn-{under_test.key}")
        return out
    finally:
        session.rollback()
        session.close()
        if not dbsupport.TEST_SERVER_URL:
            Base.metadata.drop_all(engine)
