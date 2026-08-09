"""The statutory-timing endpoints: scope, capture, and what the screen is told.

The pure arithmetic is covered in ``test_msme`` and ``test_withholding``. What
these check is the part a router can get wrong on its own — who may see a
supplier balance, that a captured status survives the sync that rewrites the
vendor row, and that the refusals reach the client as refusals rather than as
an empty list.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.commercial import policy
from app.domain import models

MANAGER = "m.rao@sanketh.in"
SALES = "r.nair@sanketh.in"
OWNER = "s.menon@sanketh.in"


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


def _capture(client, token, vendor_id, **overrides):
    body = {"vendor_id": vendor_id, "classification": "MICRO",
            "enterprise_activity": "MANUFACTURER", "evidence": "UDYAM_CERT"}
    body.update(overrides)
    return client.put("/api/v1/insight/msme-status", json=body,
                      headers=token(MANAGER))


# ── scope ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["msme-watchlist", "msme-capture-backlog",
                                  "withholding-crossings"])
def test_a_salesperson_cannot_see_supplier_deadlines(session, path):
    """Every row is a supplier balance, which is purchase cost by another name.

    Scoped like ``/payables`` rather than like ``/payments``, and the whole
    screen is economics — there is nothing left of it once the restricted
    fields are removed, so an honest refusal beats an empty table.
    """
    client, token = _api(session)

    assert client.get(f"/api/v1/insight/{path}",
                      headers=token(SALES)).status_code == 403


def test_a_salesperson_cannot_capture_a_status(session):
    client, token = _api(session)
    vendor = _vendor(session)

    r = client.put("/api/v1/insight/msme-status",
                   json={"vendor_id": vendor.vendor_id, "classification": "MICRO",
                         "enterprise_activity": "MANUFACTURER",
                         "evidence": "UDYAM_CERT"},
                   headers=token(SALES))
    assert r.status_code == 403


def test_the_owner_sees_the_watchlist(session):
    client, token = _api(session)
    _bill(session, _vendor(session))

    assert client.get("/api/v1/insight/msme-watchlist",
                      headers=token(OWNER)).status_code == 200


# ── capture ─────────────────────────────────────────────────────────────────
def test_a_captured_status_survives_a_resync_of_the_vendor(session):
    """The reason this is not a column on ``vendors``.

    ``upsert_vendor`` rewrites the vendor row from the Zoho payload on every
    pull, so a status stored there would last exactly until the next sync.
    """
    client, token = _api(session)
    vendor = _vendor(session)

    assert _capture(client, token, vendor.vendor_id).status_code == 200

    # The vendor row is rewritten, as a pull would.
    vendor.name = "Precision Carbide Works Pvt Ltd"
    vendor.payment_terms_days = 30
    session.flush()

    row = session.query(models.VendorMsmeStatus).one()
    assert row.classification == "MICRO"
    assert row.enterprise_activity == "MANUFACTURER"


def test_capturing_twice_updates_rather_than_duplicating(session):
    client, token = _api(session)
    vendor = _vendor(session)

    _capture(client, token, vendor.vendor_id)
    r = _capture(client, token, vendor.vendor_id, classification="SMALL")

    assert r.status_code == 200
    assert session.query(models.VendorMsmeStatus).count() == 1
    assert session.query(models.VendorMsmeStatus).one().classification == "SMALL"


def test_a_classification_without_evidence_is_refused(session):
    client, token = _api(session)
    vendor = _vendor(session)

    r = _capture(client, token, vendor.vendor_id, evidence="NONE")

    assert r.status_code == 422
    assert "evidence" in r.json()["detail"]


def test_days_without_a_written_agreement_are_refused(session):
    """The Zoho-term confusion, refused at the door rather than resolved.

    Zoho holds 45 days for this supplier. Accepting that as an agreed term
    would move them from the fifteen-day limit to the forty-five-day one on the
    strength of a dropdown.
    """
    client, token = _api(session)
    vendor = _vendor(session)

    r = _capture(client, token, vendor.vendor_id, agreed_days=45)

    assert r.status_code == 422
    assert "not that agreement" in r.json()["detail"]


def test_a_written_agreement_without_days_is_refused(session):
    client, token = _api(session)
    vendor = _vendor(session)

    r = _capture(client, token, vendor.vendor_id, written_agreement=True)

    assert r.status_code == 422


def test_a_written_agreement_with_days_is_accepted(session):
    client, token = _api(session)
    vendor = _vendor(session)

    r = _capture(client, token, vendor.vendor_id,
                 written_agreement=True, agreed_days=45)

    assert r.status_code == 200
    assert r.json()["agreed_days"] == 45
    assert r.json()["scope"] == "IN_SCOPE"


def test_capturing_against_another_organizations_supplier_is_a_404(session):
    client, token = _api(session)
    session.add(models.Organization(organization_id="org_other", name="Other",
                                    erp="zoho", currency="INR", config={}))
    other = models.Vendor(organization_id="org_other", external_id="v-x",
                          name="Someone Else", connector="zoho")
    session.add(other)
    session.flush()

    r = _capture(client, token, other.vendor_id)

    assert r.status_code == 404


# ── what the screen is told ─────────────────────────────────────────────────
def test_an_uncaptured_supplier_lands_in_the_gap_band(session):
    client, token = _api(session)
    _bill(session, _vendor(session))

    body = client.get("/api/v1/insight/msme-watchlist",
                      headers=token(MANAGER)).json()

    assert body["confirmed"]["bills"] == 0
    assert body["gaps"]["bills"] == 1
    assert body["gaps"]["amount_if_protected"] == 200_000.0


def test_a_captured_supplier_moves_from_the_gap_band_to_confirmed(session):
    client, token = _api(session)
    vendor = _vendor(session)
    _bill(session, vendor)

    _capture(client, token, vendor.vendor_id)
    body = client.get("/api/v1/insight/msme-watchlist",
                      headers=token(MANAGER)).json()

    assert body["gaps"]["bills"] == 0
    assert body["confirmed"]["bills"] == 1
    assert body["confirmed"]["amount_at_risk"] == 200_000.0


def test_a_supplier_confirmed_out_of_scope_leaves_the_list_entirely(session):
    client, token = _api(session)
    vendor = _vendor(session)
    _bill(session, vendor)

    _capture(client, token, vendor.vendor_id, enterprise_activity="TRADER")
    body = client.get("/api/v1/insight/msme-watchlist",
                      headers=token(MANAGER)).json()

    assert body["rows"] == []
    assert body["gaps"]["bills"] == 0


def test_the_watchlist_states_its_basis_and_declines_to_advise(session):
    client, token = _api(session)
    _bill(session, _vendor(session))

    body = client.get("/api/v1/insight/msme-watchlist",
                      headers=token(MANAGER)).json()

    assert "not tax advice" in body["basis_note"]
    assert body["rows"][0]["deadline_start_basis"] == "BILL_DATE"
    assert body["tax_rate_set"] is False


def test_the_cost_estimate_appears_once_an_owner_sets_a_rate(session):
    client, token = _api(session)
    vendor = _vendor(session)
    _bill(session, vendor, balance=1_000_000.0)
    _capture(client, token, vendor.vendor_id)

    policy.save_for_org(session, _org(session), {"effective_tax_rate": 0.25},
                        user_id="u-owner")
    session.commit()

    body = client.get("/api/v1/insight/msme-watchlist",
                      headers=token(MANAGER)).json()

    assert body["tax_rate_set"] is True
    # A year's carry on the tax brought forward, not the tax itself.
    assert body["rows"][0]["estimated_carry_cost"] == 30_000.0


def test_the_capture_backlog_ranks_the_uncaptured(session):
    client, token = _api(session)
    vendor = _vendor(session)
    _bill(session, vendor)

    body = client.get("/api/v1/insight/msme-capture-backlog",
                      headers=token(MANAGER)).json()

    assert [s["vendor_id"] for s in body["suppliers"]] == [vendor.vendor_id]


def test_a_captured_supplier_drops_off_the_capture_backlog(session):
    client, token = _api(session)
    vendor = _vendor(session)
    _bill(session, vendor)
    _capture(client, token, vendor.vendor_id)

    body = client.get("/api/v1/insight/msme-capture-backlog",
                      headers=token(MANAGER)).json()

    assert body["suppliers"] == []


# ── 194Q ────────────────────────────────────────────────────────────────────
def test_the_194q_check_says_it_is_gated_rather_than_returning_nothing(session):
    client, token = _api(session)
    vendor = _vendor(session)
    _bill(session, vendor, balance=90_000_000.0)

    body = client.get("/api/v1/insight/withholding-crossings",
                      headers=token(MANAGER)).json()

    assert body["gate_confirmed"] is False
    assert body["crossings"] == []
    assert "Settings" in body["note"]


def test_confirming_the_gate_turns_the_194q_check_on(session):
    client, token = _api(session)
    vendor = _vendor(session)
    _bill(session, vendor, balance=9_000_000.0, days_ago=1)

    policy.save_for_org(session, _org(session), {"s194q_org_gate_met": True},
                        user_id="u-owner")
    session.commit()

    body = client.get("/api/v1/insight/withholding-crossings",
                      headers=token(MANAGER)).json()

    assert body["gate_confirmed"] is True
    assert body["crossings"][0]["vendor_id"] == vendor.vendor_id
    assert body["crossings"][0]["crossed"] is True
