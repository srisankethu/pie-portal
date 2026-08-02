"""Decoding an item name, and refusing to when the parser has nothing to say.

The endpoint behind the Zoho workflow. The cases that matter are the ones where
a plausible implementation quietly destroys data: writing an empty description
over a human's words, or reporting confidence the engine never had.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.pie_describe import describe, render
from app.routers import pie

KEY = "test-pie-key"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "PIE_API_KEY", KEY)
    app = FastAPI()
    app.include_router(pie.router)
    return TestClient(app)


def _hdr():
    return {"X-Pie-Key": KEY}


# ── the decode itself ───────────────────────────────────────────────────────
def test_an_iso_insert_decodes_into_something_worth_storing():
    d = describe("CNMG 120408 MP")
    assert d.confident
    assert "rhombic 80" in d.description
    assert d.dimensions["edge_length_mm"] == 12
    assert d.dimensions["corner_radius_mm"] == 0.8
    assert d.product_family == "turning_insert"


def test_a_name_the_pack_does_not_know_abstains_rather_than_guessing():
    """The real 4U item that prompted this. The pack is Kennametal/WIDIA and
    this is not one of its shapes — the honest answer is nothing."""
    d = describe("16X16X35X90/ ALU POWER 3LF45 LONG E/M E5E49160")
    assert d.confident is False
    assert d.dimensions == {} and d.attributes == {}


def test_a_family_without_any_measurement_is_not_confident():
    """"Solid carbide end mill" alone is not worth overwriting a description
    with — it says less than the item name already did."""
    d = describe("4FL CARBIDE ENDMILL 12MM")
    assert d.product_family == "solid_carbide_endmill"
    assert d.description  # a family label was produced
    assert d.confident is False, "but no dimension was decoded, so do not write it"


def test_an_empty_name_is_a_result_not_a_crash():
    d = describe("   ")
    assert d.confident is False and d.error


def test_the_same_name_always_renders_the_same_string():
    """A workflow diffs this against what is stored; an unstable render would
    rewrite every item on every edit and loop."""
    assert describe("DNMG 150608 MP").description == describe("DNMG 150608 MP").description


def test_measurements_do_not_gain_invented_precision():
    assert "edge 12 mm" in describe("CNMG 120408 MP").description   # not 12.0
    assert "R0.8 mm" in describe("CNMG 120408 MP").description      # not R0.80


def test_render_omits_the_family_that_means_nothing_was_recognised():
    """"other_tooling / general" is the engine saying it did not route the
    input. Printing it as a description would dress a miss as an answer."""
    assert render({"product_family": "other_tooling", "product_subfamily": "general"}) == ""


# ── the endpoint ────────────────────────────────────────────────────────────
def test_the_endpoint_returns_the_decode(client):
    r = client.post("/api/v1/pie/describe", json={"name": "CNMG 120408 MP"},
                    headers=_hdr())
    assert r.status_code == 200
    body = r.json()
    assert body["confident"] is True
    assert "rhombic 80" in body["description"]
    assert body["pack_id"] and body["engine_version"]


def test_an_unrecognised_name_is_200_with_confident_false_not_an_error(client):
    """A 500 would make the workflow retry forever or mark the item failed.
    "I do not recognise this" is a normal answer."""
    r = client.post("/api/v1/pie/describe",
                    json={"name": "16X16X35X90/ ALU POWER 3LF45 LONG E/M E5E49160"},
                    headers=_hdr())
    assert r.status_code == 200 and r.json()["confident"] is False


def test_a_missing_or_wrong_key_is_refused(client):
    assert client.post("/api/v1/pie/describe",
                       json={"name": "CNMG 120408 MP"}).status_code == 401
    assert client.post("/api/v1/pie/describe", json={"name": "CNMG 120408 MP"},
                       headers={"X-Pie-Key": "wrong"}).status_code == 401


def test_an_unconfigured_key_closes_the_endpoint_rather_than_opening_it(monkeypatch):
    """Forgetting to set PIE_API_KEY must not silently publish the endpoint."""
    monkeypatch.setattr(settings, "PIE_API_KEY", "")
    app = FastAPI()
    app.include_router(pie.router)
    c = TestClient(app)
    r = c.post("/api/v1/pie/describe", json={"name": "CNMG 120408 MP"},
               headers={"X-Pie-Key": "anything"})
    assert r.status_code == 503


def test_the_batch_endpoint_reports_how_many_were_recognised(client):
    r = client.post("/api/v1/pie/describe-batch", headers=_hdr(), json={"names": [
        "CNMG 120408 MP", "DNMG 150608 MP",
        "16X16X35X90/ ALU POWER 3LF45 LONG E/M E5E49160"]})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3 and body["confident"] == 2


def test_an_oversized_batch_is_refused(client):
    r = client.post("/api/v1/pie/describe-batch", headers=_hdr(),
                    json={"names": ["CNMG 120408 MP"] * 500})
    assert r.status_code == 422


# ── the coverage report, which is how you decide to switch this on ──────────
def test_coverage_reports_the_share_that_would_actually_be_written():
    from app.pie_coverage import coverage

    r = coverage(["CNMG 120408 MP", "DNMG 150608 MP",
                  "16X16X35X90/ ALU POWER 3LF45 LONG E/M E5E49160",
                  "VSM17A2000"])
    assert r["total"] == 4 and r["confident"] == 2 and r["abstained"] == 2
    assert "16X16X35X90/ ALU POWER 3LF45 LONG E/M E5E49160" in r["misses"]
