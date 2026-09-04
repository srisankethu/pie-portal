"""The remembered-phrases screen's endpoints, and the suggestion report.

Listing is a manager's; retiring is an owner's; a salesperson gets neither. A
retired alias stops being offered, is not deleted, and is not confirmable as
another tenant's row — one 404 for "not yours" and "already retired".
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.db import get_session
from app.domain import models
from app.identity import service as identity_service
from app.routers import data_status, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

OWNER = "s.menon@pie.example"
MANAGER = "m.rao@pie.example"
SALES = "r.nair@pie.example"


@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.add(models.CustomerIdentity(identity_id="id-pitti", organization_id="org_pie",
                                  label="Pitti Engineering"))
    identity_service.record_phrase_alias(
        s, "org_pie", identity_id="id-pitti", phrase="12mm drill for SS",
        target_record_id="4149315", source_ref="quote q1 line l1", user_id="usr_sales")
    identity_service.record_phrase_alias(
        s, "org_other", identity_id="id-x", phrase="12mm drill for SS",
        target_record_id="4149315", source_ref="quote q9 line l1")
    s.add(models.QuoteDraft(
        quote_id="q1", organization_id="org_pie", customer_name="Pitti", lines=[
            {"id": "l1", "reqCode": "12mm drill for SS", "supplyCode": "4149315",
             "semantics": "REQUIREMENT", "sel": "USER", "customerScope": "id-pitti",
             "candidates": [{"code": "4149315", "retrieved": True, "alias": None}]},
            {"id": "l2", "reqCode": "CNMG 120408", "supplyCode": "2001174",
             "semantics": "REQUIREMENT", "sel": "AUTO", "customerScope": "id-pitti",
             "candidates": [{"code": "2001174"}]},
        ]))
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


def test_a_manager_sees_the_organizations_own_phrases_with_the_customer_named(client):
    body = client.get("/api/v1/data/catalog/aliases", headers=_hdr(client, MANAGER)).json()
    assert [a["phrase"] for a in body["aliases"]] == ["12mm drill for SS"]
    alias = body["aliases"][0]
    assert alias["customer"] == "Pitti Engineering"
    assert alias["target_record_id"] == "4149315"
    assert alias["source_ref"] == "quote q1 line l1"
    assert body["can_manage"] is False
    assert client.get("/api/v1/data/catalog/aliases",
                      headers=_hdr(client, OWNER)).json()["can_manage"] is True


def test_a_salesperson_sees_nothing_here(client):
    assert client.get("/api/v1/data/catalog/aliases",
                      headers=_hdr(client, SALES)).status_code == 403


def test_an_owner_can_retire_a_phrase_and_it_stops_being_offered(client):
    owner = _hdr(client, OWNER)
    (alias,) = client.get("/api/v1/data/catalog/aliases", headers=owner).json()["aliases"]

    assert client.delete(f"/api/v1/data/catalog/aliases/{alias['alias_id']}",
                         headers=owner).json() == {"retired": alias["alias_id"]}
    assert client.get("/api/v1/data/catalog/aliases", headers=owner).json()["aliases"] == []

    s = client.Maker()
    try:
        row = s.get(models.CustomerPhraseAlias, alias["alias_id"])
        assert row is not None and row.active is False, "retired, never deleted"
        from app.identity.mapping_store import OrgMappingStore
        assert OrgMappingStore(s, "org_pie").aliases() == []
        assert [e.action for e in s.query(models.IdentityEvent)
                if e.action == "PHRASE_ALIAS_RETIRED"]
    finally:
        s.close()
    # Twice is a 404: it is already retired.
    assert client.delete(f"/api/v1/data/catalog/aliases/{alias['alias_id']}",
                         headers=owner).status_code == 404


def test_retiring_is_an_owners_act_and_never_confirms_another_tenants_row(client):
    s = client.Maker()
    try:
        other = s.query(models.CustomerPhraseAlias).filter_by(
            organization_id="org_other").one()
    finally:
        s.close()
    assert client.delete(f"/api/v1/data/catalog/aliases/{other.alias_id}",
                         headers=_hdr(client, OWNER)).status_code == 404
    mine = client.get("/api/v1/data/catalog/aliases",
                      headers=_hdr(client, OWNER)).json()["aliases"][0]
    assert client.delete(f"/api/v1/data/catalog/aliases/{mine['alias_id']}",
                         headers=_hdr(client, MANAGER)).status_code == 403


def test_the_report_counts_this_organizations_quotes_and_reads_them(client):
    body = client.get("/api/v1/data/catalog/retrieval-report",
                      headers=_hdr(client, MANAGER)).json()
    assert body["organization_id"] == "org_pie" and body["drafts"] == 1
    assert body["counts"]["lines"] == 2
    assert body["counts"]["auto_selected"] == 1
    assert body["counts"]["chosen_by_person"] == 1
    assert body["counts"]["chosen_from_retrieval"] == 1
    assert body["shares"]["found_beneath_ranking"] == 1.0
    assert body["shares"]["typed_unoffered"] == 0.0
    assert body["learned"]["phrase_aliases"] == 1
    assert body["learned"]["customers_with_aliases"] == 1
    assert any("bottleneck" in r for r in body["readings"])
    assert client.get("/api/v1/data/catalog/retrieval-report",
                      headers=_hdr(client, SALES)).status_code == 403
