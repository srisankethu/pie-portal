"""The jurisdiction gate: a statute is the law of one country.

The MSME and 194Q modules are Indian law, and before ``Organization.country``
existed they answered for every tenant — an April financial year and Indian
deadlines applied to books they may not govern. What these tests pin:

* the pure table in ``commercial/jurisdiction`` — IN supported, everything
  else refused, and the two refusals (country unsupported / country not set)
  naming *different* gaps, because they need different things done;
* the statutory endpoints refusing for a non-IN and for a NULL-country tenant
  as an explicit refusal, never an empty success and never today's
  confidently-wrong Indian answer;
* an IN tenant — which the seeded demo org now is — seeing exactly what it saw
  before the gate existed;
* the migration itself applying to an empty database.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from app.commercial import jurisdiction
from app.commercial.insight import msme, withholding
from app.domain import models

MANAGER = "m.rao@pie.example"

BACKEND = Path(__file__).resolve().parents[2]


def _api(session):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.routers import insight as insight_router
    from app.routers import platform_auth
    from app.seed import SEED_PASSWORD, ensure_org_and_users

    ensure_org_and_users(session)
    session.commit()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight_router.router)
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)

    def token(email: str) -> dict:
        r = client.post("/api/v1/auth/login",
                        json={"email": email, "password": SEED_PASSWORD})
        return {"Authorization": f"Bearer {r.json()['token']}"}

    return client, token


def _org(session) -> str:
    from app.config import settings
    return settings.DEFAULT_ORG_ID


def _set_country(session, value):
    session.get(models.Organization, _org(session)).country = value
    session.flush()


def _vendor(session, name="Precision Carbide Works", external_id="v-1"):
    row = models.Vendor(organization_id=_org(session), external_id=external_id,
                        name=name, connector="zoho", payment_terms_days=45)
    session.add(row)
    session.flush()
    return row


def _bill(session, vendor, *, ref="b-1", days_ago=40, balance=200_000.0):
    row = models.BillDoc(
        organization_id=_org(session), external_ref=ref, number=f"BILL/{ref}",
        vendor_id=vendor.vendor_id, date=date.today() - timedelta(days=days_ago),
        due_date=date.today() + timedelta(days=5), status="open",
        total=balance, balance=balance)
    session.add(row)
    session.flush()
    return row


#: Every statutory GET screen, with the list key its clients render.
STATUTORY_SCREENS = [("msme-watchlist", "rows"),
                     ("msme-capture-backlog", "suppliers"),
                     ("withholding-crossings", "crossings")]


# ── the table itself ────────────────────────────────────────────────────────
def test_india_is_the_only_supported_jurisdiction():
    assert jurisdiction.for_country("IN") is jurisdiction.INDIA
    assert jurisdiction.for_country(" in ") is jurisdiction.INDIA
    assert jurisdiction.for_country("US") is None
    assert jurisdiction.for_country(None) is None
    assert jurisdiction.for_country("") is None


def test_the_calendar_moved_behind_the_jurisdiction_module_unchanged():
    """IN keeps the April year — the statute modules read it, byte for byte."""
    assert jurisdiction.INDIA.fy_start_month == 4
    assert msme.fy_of(date(2026, 3, 31)) == "FY2025-26"
    assert msme.fy_of(date(2026, 4, 1)) == "FY2026-27"
    assert msme.fy_of(date(2026, 4, 1)) == jurisdiction.INDIA.fy_of(date(2026, 4, 1))
    assert withholding.fy_bounds("FY2026-27") == (date(2026, 4, 1),
                                                  date(2027, 4, 1))
    assert (withholding.fy_bounds("FY2026-27")
            == jurisdiction.INDIA.fy_bounds("FY2026-27"))


def test_an_indian_tenant_is_not_refused():
    assert jurisdiction.msme_refusal("IN") is None
    assert jurisdiction.withholding_refusal("IN") is None
    assert jurisdiction.msme_refusal("in") is None  # case is not a jurisdiction


def test_a_null_country_is_refused_naming_the_unset_fact():
    """Unknown is not India, and the refusal says what to record."""
    for refusal in (jurisdiction.msme_refusal, jurisdiction.withholding_refusal):
        for value in (None, "", "   "):
            reason = refusal(value)
            assert reason is not None
            assert "country is not set" in reason


def test_an_unsupported_country_is_refused_by_name():
    """The other refusal: the country is known, and it is not India."""
    reason = jurisdiction.msme_refusal("US")
    assert reason is not None
    assert "US" in reason
    assert "not supported" in reason
    # Two different gaps, two different sentences — "set the country" is wrong
    # advice for a tenant whose country is set.
    assert "country is not set" not in reason


# ── the endpoints refuse ────────────────────────────────────────────────────
@pytest.mark.parametrize("path,list_key", STATUTORY_SCREENS)
def test_a_non_indian_tenant_gets_a_refusal_not_indian_deadlines(
        session, path, list_key):
    client, token = _api(session)
    _bill(session, _vendor(session))  # data exists; the refusal still wins
    _set_country(session, "US")

    body = client.get(f"/api/v1/insight/{path}", headers=token(MANAGER)).json()

    assert body["jurisdiction_supported"] is False
    assert body[list_key] == []
    assert "US" in body["blocked_by"]
    assert "not supported" in body["blocked_by"]
    # The refusal is what the screen's empty state renders.
    assert body["empty_reason"] == body["blocked_by"]
    # And nothing of the Indian answer leaks around it.
    assert "financial_year" not in body
    assert "confirmed" not in body


@pytest.mark.parametrize("path,list_key", STATUTORY_SCREENS)
def test_a_tenant_with_no_country_gets_the_refusal_too(session, path, list_key):
    """NULL is not IN — the pre-migration default must not read as India."""
    client, token = _api(session)
    _bill(session, _vendor(session))
    _set_country(session, None)

    body = client.get(f"/api/v1/insight/{path}", headers=token(MANAGER)).json()

    assert body["jurisdiction_supported"] is False
    assert body[list_key] == []
    assert "country is not set" in body["blocked_by"]
    assert body["empty_reason"] == body["blocked_by"]


def test_capturing_an_msme_status_is_refused_outside_india(session):
    """A write, so the refusal is a status code rather than an empty envelope —
    and nothing is stored behind it."""
    client, token = _api(session)
    vendor = _vendor(session)
    _set_country(session, "US")

    r = client.put("/api/v1/insight/msme-status",
                   json={"vendor_id": vendor.vendor_id, "classification": "MICRO",
                         "enterprise_activity": "MANUFACTURER",
                         "evidence": "UDYAM_CERT"},
                   headers=token(MANAGER))

    assert r.status_code == 409
    assert "US" in r.json()["detail"]
    assert session.query(models.VendorMsmeStatus).count() == 0


def test_capturing_an_msme_status_is_refused_with_no_country(session):
    client, token = _api(session)
    vendor = _vendor(session)
    _set_country(session, None)

    r = client.put("/api/v1/insight/msme-status",
                   json={"vendor_id": vendor.vendor_id, "classification": "MICRO",
                         "enterprise_activity": "MANUFACTURER",
                         "evidence": "UDYAM_CERT"},
                   headers=token(MANAGER))

    assert r.status_code == 409
    assert "country is not set" in r.json()["detail"]


# ── an Indian tenant is untouched ───────────────────────────────────────────
def test_the_seeded_demo_org_is_indian(session):
    from app.seed import ensure_org_and_users
    ensure_org_and_users(session)
    assert session.get(models.Organization, _org(session)).country == "IN"


def test_the_seeder_backfills_a_demo_org_that_predates_the_column(session):
    """A database seeded before ``country`` existed keeps its statutory
    screens — for the demo org only, because only the demo org is *known*
    to be Indian. A real tenant's NULL stays NULL."""
    from app.seed import ensure_org_and_users
    session.add(models.Organization(organization_id=_org(session), name="Demo",
                                    currency="INR", config={}))
    session.add(models.Organization(organization_id="org_other", name="Other",
                                    currency="INR", config={}))
    session.flush()

    ensure_org_and_users(session)

    assert session.get(models.Organization, _org(session)).country == "IN"
    assert session.get(models.Organization, "org_other").country is None


def test_an_indian_tenant_sees_the_watchlist_it_always_saw(session):
    client, token = _api(session)
    _bill(session, _vendor(session))

    body = client.get("/api/v1/insight/msme-watchlist",
                      headers=token(MANAGER)).json()

    assert "jurisdiction_supported" not in body
    assert "blocked_by" not in body
    assert body["gaps"]["bills"] == 1
    assert body["gaps"]["amount_if_protected"] == 200_000.0


def test_an_indian_tenant_still_sees_the_194q_settings_gate(session):
    """The jurisdiction refusal must not swallow the *other* gate: for an IN
    org with the turnover unconfirmed, the answer is still "confirm it in
    Settings", not a jurisdiction message."""
    client, token = _api(session)
    _bill(session, _vendor(session), balance=90_000_000.0)

    body = client.get("/api/v1/insight/withholding-crossings",
                      headers=token(MANAGER)).json()

    assert "jurisdiction_supported" not in body
    assert body["gate_confirmed"] is False
    assert "Settings" in body["note"]


# ── the migration applies to an empty database ──────────────────────────────
def test_the_country_migration_applies_to_an_empty_database(tmp_path):
    """The real CLI against a real empty file, the way a deployment runs it."""
    db = tmp_path / "t.db"
    sqlite3.connect(db).close()
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db}"}
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                       cwd=BACKEND, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]

    columns = {c["name"]
               for c in inspect(create_engine(f"sqlite:///{db}"))
               .get_columns("organizations")}
    assert "country" in columns
