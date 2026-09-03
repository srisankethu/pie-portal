"""The training-pair export: one tenant per file, records from one catalogue."""
from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.identity import service as identity_service
from app.retrieval.export_pairs import export


@pytest.fixture()
def session():
    s = sessionmaker(bind=dbsupport.fresh_engine())()
    yield s
    s.close()


def test_pairs_are_one_tenants_aliases_against_one_catalogue(session, tmp_path):
    catalogue = tmp_path / "products.jsonl"
    catalogue.write_text(json.dumps({
        "record_id": "4149315", "description_raw": "SC DRILL 12mm COOLANT",
        "product_family": "solid_carbide_drill"}) + "\n", encoding="utf-8")
    for org, phrase, target in [("org_a", "12mm drill for SS", "4149315"),
                                ("org_a", "some other tool", "9999999"),
                                ("org_b", "12mm drill for SS", "4149315")]:
        identity_service.record_phrase_alias(
            session, org, identity_id="cust", phrase=phrase, target_record_id=target)
    session.commit()

    out = tmp_path / "pairs.jsonl"
    assert export(session, "org_a", catalogue, out) == 1
    (pair,) = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert pair["anchor"] == "12mm drill for SS"
    assert pair["positive"].startswith("4149315 SC DRILL 12mm COOLANT")
    assert pair["scope"] == "cust" and pair["record_id"] == "4149315"
    # org_b's identical alias is not in org_a's file, and the record the
    # catalogue does not hold is not a pair here.
