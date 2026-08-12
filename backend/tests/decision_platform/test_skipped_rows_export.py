"""Every skipped row of a sync survives the run, and can leave as a spreadsheet.

The defect these pin: a sync reported ``1304`` skipped rows and persisted twenty
of them. The screen said "first 20 of 1304" — truthfully — but the other 1,284
existed only in the job's memory and were gone the moment it ended, so the one
question worth asking about them (*which* rows, on which documents, from which
supplier) had no answer anywhere in the system. A gap that can be seen and not
worked reads, over time, as a gap nobody can do anything about.

So the assertions here are about completeness rather than about the shape of a
response: the number of rows kept equals the number reported, the export carries
every one of them, and when the two genuinely cannot agree the response says so
instead of handing over a short file that looks whole.
"""
from __future__ import annotations

import csv
import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_session
from app.domain import models
from app.ingestion import jobs
from app.ingestion.sync import SyncReport
from app.routers import data_status, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

#: More than the twenty ``skipped_sample`` holds, so a truncation cannot pass.
MANY = 57


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(data_status.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    tc = TestClient(app)
    tc.Maker = Maker
    return tc


def _hdr(c, email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _run_with_skips(client, count: int = MANY, *, org: str = "org_pie",
                    connection_id: str | None = "conn_a") -> str:
    """A finished run whose report skipped ``count`` rows, persisted as the job
    itself would persist them."""
    session = client.Maker()
    try:
        run = models.SyncRun(organization_id=org, source="fixture", status="OK",
                             connection_id=connection_id)
        session.add(run)
        session.flush()

        report = SyncReport(organization_id=org)
        for i in range(count):
            report.skip(
                "cost_record", f"bill{i}:line{i}", "UNKNOWN_PRODUCT",
                f"no product 34521610000012{i:05d}",
                context={
                    "missing_id": f"34521610000012{i:05d}",
                    "label": f"CNMG 1204{i:02d}",
                    "sku": f"SKU-{i}",
                    "document": f"KTI/25-26/{i:04d}",
                    "document_date": "2025-06-01",
                    "party": "Kennametal India",
                    "qty": 10,
                    "line_value": 1234.50,
                    "fix": "Restore the item in Zoho or repoint the line.",
                })
        run.skipped_count = len(report.skipped)
        run.skipped_sample = report.skipped[:20]
        jobs._persist_skips(session, run, [(connection_id, report.skipped)])
        session.commit()
        return run.sync_run_id
    finally:
        session.close()


def test_every_skipped_row_is_kept_not_the_first_twenty(client):
    """The defect itself. 1,304 reported and 20 kept is not a record of 1,304."""
    run_id = _run_with_skips(client)
    owner = _hdr(client, "s.menon@pie.example")

    body = client.get(f"/api/v1/data/sync-runs/{run_id}/skipped",
                      headers=owner).json()

    assert body["skipped_count"] == MANY
    assert body["held"] == MANY, "the kept rows must equal the reported count"
    assert len(body["rows"]) == MANY
    assert body["incomplete"] is None
    # And the sample the status card reads is still the sample — this is a new
    # place for the full list, not a change to what that card renders.
    assert len(body["rows"]) > 20


def test_the_export_carries_every_row_and_names_itself(client):
    """A CSV built from the page the grid is showing would reproduce the bug one
    level up, so it is built from the query rather than from the view."""
    run_id = _run_with_skips(client)
    owner = _hdr(client, "s.menon@pie.example")

    r = client.get(f"/api/v1/data/sync-runs/{run_id}/skipped.csv", headers=owner)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in r.headers["content-disposition"]
    assert run_id[:8] in r.headers["content-disposition"]

    text = r.text.lstrip("﻿")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][0] == "Row" and "What to do" in rows[0]
    body = [row for row in rows[1:] if row and row[0]]
    assert len(body) == MANY, "the file is the whole list, not a page of it"
    # The columns a person actually reconciles on are populated, not blank.
    header = rows[0]
    first = dict(zip(header, body[0]))
    assert first["Document"] == "KTI/25-26/0000"
    assert first["Customer / supplier"] == "Kennametal India"
    assert first["Missing id"].startswith("3452161")
    assert first["Item on the document"] == "CNMG 120400"


def test_a_run_that_kept_no_rows_says_so_rather_than_exporting_nothing(client):
    """An empty file that calls itself complete is worse than no file: somebody
    reconciles a blank sheet and concludes the sync is clean."""
    session = client.Maker()
    try:
        run = models.SyncRun(organization_id="org_pie", source="fixture",
                             status="OK", skipped_count=1304)
        session.add(run)
        session.commit()
        run_id = run.sync_run_id
    finally:
        session.close()

    owner = _hdr(client, "s.menon@pie.example")
    body = client.get(f"/api/v1/data/sync-runs/{run_id}/skipped",
                      headers=owner).json()

    assert body["held"] == 0 and body["skipped_count"] == 1304
    assert body["incomplete"], "silence here reads as 'nothing was skipped'"
    # And the caveat travels with the file, which leaves the screen behind.
    csv_text = client.get(f"/api/v1/data/sync-runs/{run_id}/skipped.csv",
                          headers=owner).text
    assert "before the full list was kept" in csv_text


def test_the_rows_say_which_company_they_came_from(client):
    """Three legal entities read three books. 'Which company' is the first thing
    to know about a thousand skips, and the merged report cannot say."""
    session = client.Maker()
    try:
        session.add(models.ZohoConnection(
            connection_id="conn_a", organization_id="org_pie",
            zoho_organization_id="z1", label="SLS Engineers"))
        session.commit()
    finally:
        session.close()

    run_id = _run_with_skips(client, count=3, connection_id="conn_a")
    owner = _hdr(client, "s.menon@pie.example")
    rows = client.get(f"/api/v1/data/sync-runs/{run_id}/skipped",
                      headers=owner).json()["rows"]

    assert {r["company"] for r in rows} == {"SLS Engineers"}


def test_the_order_is_the_order_the_pull_met_them(client):
    """So an export sorts back to the sequence of the run rather than to whatever
    order the rows come out of the table in."""
    run_id = _run_with_skips(client, count=25)
    owner = _hdr(client, "s.menon@pie.example")
    rows = client.get(f"/api/v1/data/sync-runs/{run_id}/skipped",
                      headers=owner).json()["rows"]

    assert [r["seq"] for r in rows] == list(range(25))


def test_another_organizations_run_is_not_found_rather_than_forbidden(client):
    """Each organization is a separate tenant. A 403 would confirm the run
    exists, which is itself a fact about another company's books."""
    run_id = _run_with_skips(client, count=2, org="org_other")
    owner = _hdr(client, "s.menon@pie.example")

    assert client.get(f"/api/v1/data/sync-runs/{run_id}/skipped",
                      headers=owner).status_code == 404
    assert client.get(f"/api/v1/data/sync-runs/{run_id}/skipped.csv",
                      headers=owner).status_code == 404


def test_a_real_pull_writes_its_skips_through_the_job(client, monkeypatch):
    """The wiring, not the helper. Everything above calls ``_persist_skips``
    directly; this drives a whole ``execute_sync`` over a book containing a bill
    line whose item is not in the master — the exact shape that produced 1,304
    ``UNKNOWN_PRODUCT`` rows — and asserts the run's own count is matched by
    rows on the table afterwards."""
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "fixture")

    from app.ingestion import mock_source

    class BookWithARetiredItem(mock_source.FixtureZohoSource):
        """One bill against an item id the master never returns."""

        def list_bills(self, skip=None):
            return [
                {"bill_id": "bill-9001", "date": "2026-05-30",
                 "bill_number": "KTI/25-26/0043", "vendor_name": "Kennametal India",
                 "line_items": [
                     {"line_item_id": "l9", "item_id": "itm-retired",
                      "name": "CNMG 120408 KCP25", "quantity": 40, "rate": 512},
                 ]},
            ]

    monkeypatch.setattr("app.ingestion.sync.get_source",
                        lambda session, org, **kw: BookWithARetiredItem())

    session = client.Maker()
    try:
        run = models.SyncRun(organization_id="org_pie", source="fixture",
                             status="QUEUED")
        session.add(run)
        session.flush()
        jobs.execute_sync(session, run, analysis=False)
        session.commit()
        run_id, counted = run.sync_run_id, run.skipped_count
    finally:
        session.close()

    assert counted > 0, "a bill line with no item in the master is a skip"

    owner = _hdr(client, "s.menon@pie.example")
    body = client.get(f"/api/v1/data/sync-runs/{run_id}/skipped",
                      headers=owner).json()

    assert body["held"] == counted, "the run counted skips it did not keep"
    assert body["incomplete"] is None
    row = next(r for r in body["rows"] if r["code"] == "UNKNOWN_PRODUCT")
    assert row["document"] == "KTI/25-26/0043"
    assert row["party"] == "Kennametal India"
    assert row["label"] == "CNMG 120408 KCP25"
    assert row["fix"], "a row nobody can act on is the thing this replaces"


# ── §1: cost does not reach a salesperson ────────────────────────────────────

def test_a_salesperson_cannot_read_the_skip_export(client):
    """``line_value`` on a ``cost_record`` skip is a *purchase* line total, and
    beside its quantity that is a unit cost. The whole surface is withheld
    rather than blanked field by field, because the row is only worth reading if
    the money is on it."""
    run_id = _run_with_skips(client)
    sales = _hdr(client, "r.nair@pie.example")

    assert client.get(f"/api/v1/data/sync-runs/{run_id}/skipped",
                      headers=sales).status_code == 403
    assert client.get(f"/api/v1/data/sync-runs/{run_id}/skipped.csv",
                      headers=sales).status_code == 403


def test_the_status_screen_does_not_hand_a_salesperson_purchase_values(client,
                                                                      monkeypatch):
    """The same number by a quieter route. ``unresolved`` sums ``line_value``
    per missing item beside a line count, and the skip sample carries it per
    row — both on an endpoint any signed-in user may read."""
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "fixture")
    session = client.Maker()
    try:
        run = models.SyncRun(
            organization_id="org_pie", source="fixture", status="OK",
            skipped_count=2,
            skipped_sample=[{"kind": "cost_record", "ref": "b1:l1",
                             "code": "UNKNOWN_PRODUCT", "detail": "no product 1",
                             "context": {"line_value": 1234.5, "qty": 10,
                                         "label": "CNMG 120408"}}],
            unresolved=[{"kind": "cost_record", "code": "UNKNOWN_PRODUCT",
                         "missing_id": "1", "label": "CNMG 120408", "lines": 2,
                         "value": 2469.0,
                         "examples": [{"document": "KTI/1", "qty": 10,
                                       "value": 1234.5}]}])
        session.add(run)
        session.commit()
    finally:
        session.close()

    sales = client.get("/api/v1/data/status",
                       headers=_hdr(client, "r.nair@pie.example")).json()["last_sync"]

    assert sales["unresolved"][0]["value"] is None
    assert sales["unresolved"][0]["examples"][0]["value"] is None
    assert sales["unresolved"][0]["examples"][0]["qty"] is None
    assert "context" not in sales["skipped_sample"][0]
    # The control survives: they can still see that something is unresolved,
    # what it is called, and how many lines it holds up.
    assert sales["unresolved"][0]["lines"] == 2
    assert sales["unresolved"][0]["label"] == "CNMG 120408"
    assert sales["skipped_count"] == 2

    owner = client.get("/api/v1/data/status",
                       headers=_hdr(client, "s.menon@pie.example")).json()["last_sync"]
    assert owner["unresolved"][0]["value"] == 2469.0, "a manager still ranks the worklist"
