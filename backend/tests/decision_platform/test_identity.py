"""Identity linking across connectors — and never merging them.

The platform reads several ERPs at once. The same customer is in Zoho and in
ERPNext under different ids; the same item under different codes. The rule that
shapes all of this: **connector data is never merged.** Each connector stays the
source of truth for its own records; an identity only says which records are the
same business entity.

The tests below are mostly about what must *not* happen. A merge is easy to
write and impossible to undo — by the time anyone notices, the evidence of the
mistake is the thing the merge destroyed.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.db import get_session
from app.domain import models
from app.identity import service as identity
from app.identity.matchers import normalize_gstin, normalize_name, normalize_sku
from app.routers import identity as identity_router, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"
OWNER = "s.menon@pie.example"
MANAGER = "m.rao@pie.example"
SALES = "r.nair@pie.example"

GSTIN = "29ABCDE1234F1Z5"


@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(identity_router.router)

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


def _hdr(c, email=OWNER):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _zoho(s, external_id="12345", name="ABC Industries Pvt Ltd", gstin=GSTIN):
    return identity.ingest_customer(
        s, ORG, connector="zoho", connection_id="conn-a",
        external_id=external_id, name=name, gstin=gstin)


def _erpnext(s, external_id="CUST-102", name="ABC Industries", gstin=GSTIN):
    return identity.ingest_customer(
        s, ORG, connector="erpnext", external_id=external_id, name=name, gstin=gstin)


# ── normalisation ───────────────────────────────────────────────────────────
def test_a_gstin_matches_however_the_erp_formatted_it():
    """Two systems writing one registration differently must still match, or
    the link would depend on which of them typed it."""
    assert normalize_gstin(" 29abcde1234f1z5 ") == GSTIN
    assert normalize_gstin("29 ABCDE 1234 F1Z5") == GSTIN
    assert normalize_gstin("") is None and normalize_gstin(None) is None


def test_a_sku_matches_across_punctuation():
    for written in ("KCMT090304LF", "kcmt-090304-lf", "KCMT 090304 LF", "kcmt.090304/lf"):
        assert normalize_sku(written) == "KCMT090304LF"


def test_a_malformed_gstin_never_matches_on_the_gstin(client):
    """Shape is checked before it is trusted. Matching two records on a shared
    placeholder like 'NA' would link every customer that has one.

    Renamed from `..._proposes_nothing`, because it no longer proposes nothing and
    that is deliberate: 'NA' is not an eligible key, so these two fall through to
    the NAME strategy, which is the whole point of it. The property this test
    exists for is untouched and asserted more precisely — the placeholder itself
    must never be the evidence, and nothing is linked either way.
    """
    s = client.Maker()
    a = _zoho(s, gstin="NA")
    b = _erpnext(s, gstin="NA")
    s.commit()
    assert [c.strategy for c in b.suggestions] == ["NAME"], "'NA' is not a registration"
    assert not any("NA" in c.evidence for c in b.suggestions)
    assert a.identity_id != b.identity_id, "a suggestion is not a link"
    s.close()


# ── the core rule: link, never merge ────────────────────────────────────────
def test_matching_records_are_linked_not_merged(client):
    s = client.Maker()
    a = _zoho(s)
    b = _erpnext(s)
    s.commit()

    # The suggestion is raised, but nothing has moved yet.
    assert [c.strategy for c in b.suggestions] == ["GSTIN"]
    assert b.suggestions[0].identity_id == a.identity_id

    sugg = s.query(models.IdentitySuggestion).one()
    identity.decide_suggestion(s, ORG, sugg.suggestion_id, accept=True, actor="u1")
    s.commit()

    records = identity.records_for(s, ORG, identity.CUSTOMER, a.identity_id)
    assert {r.connector for r in records} == {"zoho", "erpnext"}
    # Both source records survive, byte for byte as their own connector gave them.
    assert {r.name for r in records} == {"ABC Industries Pvt Ltd", "ABC Industries"}
    assert {r.external_id for r in records} == {"12345", "CUST-102"}
    s.close()


def test_linking_does_not_rewrite_either_record(client):
    """The failure this whole design exists to prevent: one connector's value
    quietly overwriting another's."""
    s = client.Maker()
    a = _zoho(s)
    b = _erpnext(s)
    before = (b.record.name, b.record.external_id, b.record.connector)
    identity.link_record(s, ORG, entity_type=identity.CUSTOMER,
                         record_id=b.record.record_id, identity_id=a.identity_id,
                         actor="u1")
    s.commit()

    assert (b.record.name, b.record.external_id, b.record.connector) == before
    assert a.record.name == "ABC Industries Pvt Ltd", "the other side is untouched too"
    s.close()


def test_an_identity_holds_no_connector_fields(client):
    """Identity rows are deliberately almost empty. A cached name copied from
    whichever connector was read first would make that connector authoritative."""
    columns = set(models.CustomerIdentity.__table__.columns.keys())
    assert columns == {"identity_id", "organization_id", "label", "active",
                       "created_at", "updated_at"}
    assert "gstin" not in columns and "connector" not in columns


def test_two_connectors_can_reuse_the_same_external_id(client):
    """Zoho contact 100 and ERPNext customer 100 are different records. Keying
    on the external id alone would silently fuse them."""
    s = client.Maker()
    a = identity.ingest_customer(s, ORG, connector="zoho", external_id="100",
                                 name="One", gstin=None)
    b = identity.ingest_customer(s, ORG, connector="erpnext", external_id="100",
                                 name="Two", gstin=None)
    s.commit()
    assert a.record.record_id != b.record.record_id
    assert a.identity_id != b.identity_id
    s.close()


def test_two_instances_of_one_connector_stay_separate(client):
    """Two Zoho companies under one organization each have their own id space."""
    s = client.Maker()
    a = identity.ingest_customer(s, ORG, connector="zoho", connection_id="conn-a",
                                 external_id="460000000123", name="A", gstin=None)
    b = identity.ingest_customer(s, ORG, connector="zoho", connection_id="conn-b",
                                 external_id="460000000123", name="B", gstin=None)
    s.commit()
    assert a.record.record_id != b.record.record_id
    s.close()


# ── re-sync behaviour ───────────────────────────────────────────────────────
def test_a_resync_updates_the_record_in_place(client):
    s = client.Maker()
    first = _zoho(s, name="ABC Industries Pvt Ltd")
    s.commit()
    again = _zoho(s, name="ABC Industries Private Limited")
    s.commit()

    assert again.record.record_id == first.record.record_id
    assert again.record.name == "ABC Industries Private Limited"
    assert s.query(models.CustomerConnectorRecord).count() == 1
    s.close()


def test_a_resync_does_not_relitigate_a_human_decision(client):
    """A record that has been reviewed and linked must not be re-resolved every
    night, or the sync would quietly argue with the person who decided."""
    s = client.Maker()
    a = _zoho(s)
    b = _erpnext(s)
    identity.link_record(s, ORG, entity_type=identity.CUSTOMER,
                         record_id=b.record.record_id, identity_id=a.identity_id,
                         actor="u1")
    s.commit()

    again = _erpnext(s)      # the next sync sees it again
    s.commit()
    assert again.identity_id == a.identity_id
    assert again.suggestions == [], "already decided — nothing to ask"
    s.close()


def test_a_resync_does_not_stack_duplicate_suggestions(client):
    s = client.Maker()
    _zoho(s)
    _erpnext(s)
    s.commit()
    # A second sync of a record that is still unresolved.
    s.query(models.CustomerConnectorRecord).filter_by(connector="erpnext").delete()
    s.commit()
    _erpnext(s)
    s.commit()
    assert s.query(models.IdentitySuggestion).count() <= 2
    s.close()


# ── matching where there is no identifier at all ────────────────────────────
#
# The gap these cover: matching ran on GSTIN only, so a customer with no GSTIN
# could never be proposed for a link however many times a sync ran — and the
# review screen reported that as "no exact matches were found", which reads as
# "the book is fine". Two `Pitti Engineering Ltd` rows billed separately under it.
def test_two_records_with_no_gstin_and_one_name_are_proposed(client):
    s = client.Maker()
    a = identity.ingest_customer(s, ORG, connector="zoho", external_id="p1",
                                 name="Pitti Engineering Ltd", gstin=None)
    b = identity.ingest_customer(s, ORG, connector="zoho", connection_id="conn-b",
                                 external_id="p2", name="Pitti Engineering Ltd.",
                                 gstin=None)
    s.commit()
    assert [c.strategy for c in b.suggestions] == ["NAME"]
    assert b.suggestions[0].evidence == "name PITTI ENGINEERING", (
        "the evidence has to be the value compared, not a score")
    assert b.suggestions[0].identity_id == a.identity_id
    s.close()


def test_a_name_match_is_never_linked_automatically(client):
    """The one place a strategy's strength changes what happens.

    The auto-link switch says "link on an exact match" and its own help calls that
    unrecoverable when a group trades under one registration. Two businesses that
    merely share a name is a far weaker bet than that, so this stays a question
    for a person however the setting is set.
    """
    s = client.Maker()
    identity.get_policy(s, ORG).auto_link_customers = True
    s.commit()

    a = identity.ingest_customer(s, ORG, connector="zoho", external_id="p1",
                                 name="Pitti Engineering Ltd", gstin=None)
    b = identity.ingest_customer(s, ORG, connector="zoho", connection_id="conn-b",
                                 external_id="p2", name="Pitti Engineering",
                                 gstin=None)
    s.commit()
    assert b.linked is False, "a name is not grounds to link without a person"
    assert b.identity_id != a.identity_id
    assert [c.strategy for c in b.suggestions] == ["NAME"], (
        "and it must still be offered for review, not dropped")
    s.close()


def test_a_gstin_match_still_links_automatically_alongside(client):
    """The weak strategy must not have disabled the strong one's auto-link."""
    s = client.Maker()
    identity.get_policy(s, ORG).auto_link_customers = True
    s.commit()
    a = _zoho(s)
    b = _erpnext(s)
    s.commit()
    assert b.linked is True and b.identity_id == a.identity_id
    s.close()


def test_a_record_with_a_gstin_gets_no_name_proposal_beside_it(client):
    """Asking a reviewer to weigh a name against a registration is asking a
    question with an obvious answer, repeatedly."""
    s = client.Maker()
    _zoho(s, name="ABC Industries Pvt Ltd", gstin=GSTIN)
    b = identity.ingest_customer(s, ORG, connector="erpnext", external_id="x9",
                                 name="ABC Industries", gstin=GSTIN)
    s.commit()
    assert [c.strategy for c in b.suggestions] == ["GSTIN"]
    s.close()


def test_a_name_that_normalises_to_nothing_proposes_nothing(client):
    """Otherwise every record named "Ltd." would match every other one."""
    s = client.Maker()
    identity.ingest_customer(s, ORG, connector="zoho", external_id="n1",
                             name="Ltd.", gstin=None)
    b = identity.ingest_customer(s, ORG, connector="zoho", connection_id="conn-b",
                                 external_id="n2", name="Pvt Ltd", gstin=None)
    s.commit()
    assert b.suggestions == []
    s.close()


def test_an_item_description_is_not_an_identifier(client):
    """Deliberately narrower than customers. "Milling insert" describes hundreds
    of parts, and a review queue that is wrong more often than right costs the
    GSTIN and SKU proposals their audience too."""
    s = client.Maker()
    identity.ingest_item(s, ORG, connector="zoho", external_id="a", sku=None,
                         description="Milling insert")
    b = identity.ingest_item(s, ORG, connector="sap", external_id="b", sku=None,
                             description="Milling insert")
    s.commit()
    assert b.suggestions == []
    s.close()


def test_the_empty_queue_says_which_kind_of_empty_it_is(client):
    """`coverage` is what lets the screen tell "nothing matched" from "nothing
    could be looked at". Counted from the records, so it still describes the book
    when the queue is empty."""
    s = client.Maker()
    _zoho(s, external_id="k1", gstin=GSTIN)                       # has a key
    identity.ingest_customer(s, ORG, connector="zoho", connection_id="conn-b",
                             external_id="k2", name="Alone Ltd", gstin=None)
    s.commit()
    s.close()

    c = client
    body = c.get("/api/v1/identity/customers/suggestions/pending",
                 headers=_hdr(c)).json()
    cov = body["coverage"]
    assert cov["records"] == 2
    assert cov["with_key"] == 1 and cov["without_key"] == 1
    assert cov["key_name"] == "GSTIN"
    assert cov["unlinked"] == 2, "neither has been linked to anything"


def test_coverage_names_the_right_key_for_items(client):
    body = client.get("/api/v1/identity/items/suggestions/pending",
                      headers=_hdr(client)).json()
    assert body["coverage"]["key_name"] == "SKU"


def test_a_normalised_name_sets_aside_form_not_identity():
    assert normalize_name("Pitti Engineering Ltd") == "PITTI ENGINEERING"
    assert normalize_name("PITTI  ENGINEERING PVT. LTD.") == "PITTI ENGINEERING"
    assert normalize_name("pitti-engineering") == "PITTI ENGINEERING"
    # Not the same buyer, and normalisation must not make them one.
    assert normalize_name("Pitti Engineering") != normalize_name("Pitti Castings")
    assert normalize_name("Ltd.") is None and normalize_name("") is None
    # A digit is part of a name, not punctuation to be discarded.
    assert normalize_name("Forge 9 Industries") == "FORGE 9 INDUSTRIES"


# ── the auto-link setting ───────────────────────────────────────────────────
def test_nothing_links_automatically_by_default(client):
    s = client.Maker()
    _zoho(s)
    b = _erpnext(s)
    s.commit()
    assert b.linked is False
    assert s.query(models.IdentitySuggestion).filter_by(status="PENDING").count() == 1
    s.close()


def test_auto_link_is_obeyed_when_an_owner_turns_it_on(client):
    s = client.Maker()
    identity.get_policy(s, ORG).auto_link_customers = True
    s.commit()

    a = _zoho(s)
    b = _erpnext(s)
    s.commit()
    assert b.linked is True and b.identity_id == a.identity_id
    events = [e.action for e in identity.history(s, ORG, identity.CUSTOMER, a.identity_id)]
    assert "LINKED" in events
    actors = {e.actor for e in identity.history(s, ORG, identity.CUSTOMER, a.identity_id)}
    assert "AUTO" in actors, "an automatic link must be attributable as automatic"
    s.close()


# ── unlinking, because a wrong link must be reversible ──────────────────────
def test_unlinking_splits_a_record_back_out(client):
    s = client.Maker()
    a = _zoho(s)
    b = _erpnext(s)
    identity.link_record(s, ORG, entity_type=identity.CUSTOMER,
                         record_id=b.record.record_id, identity_id=a.identity_id,
                         actor="u1")
    s.commit()

    identity.unlink_record(s, ORG, entity_type=identity.CUSTOMER,
                           record_id=b.record.record_id, actor="u1",
                           reason="different legal entity")
    s.commit()
    assert b.record.identity_id != a.identity_id
    assert len(identity.records_for(s, ORG, identity.CUSTOMER, a.identity_id)) == 1
    assert b.record.name == "ABC Industries", "the record itself is unchanged"
    s.close()


def test_an_emptied_identity_is_retired_not_deleted(client):
    """Deleting it would orphan the audit trail explaining the very link that
    emptied it."""
    s = client.Maker()
    a = _zoho(s)
    b = _erpnext(s)
    orphaned = b.identity_id
    identity.link_record(s, ORG, entity_type=identity.CUSTOMER,
                         record_id=b.record.record_id, identity_id=a.identity_id,
                         actor="u1")
    s.commit()

    row = s.get(models.CustomerIdentity, orphaned)
    assert row is not None, "still there"
    assert row.active is False, "but retired"
    s.close()


# ── audit ───────────────────────────────────────────────────────────────────
def test_every_decision_is_recorded_with_who_made_it(client):
    s = client.Maker()
    a = _zoho(s)
    b = _erpnext(s)
    identity.link_record(s, ORG, entity_type=identity.CUSTOMER,
                         record_id=b.record.record_id, identity_id=a.identity_id,
                         actor="user-7", detail="same GSTIN, confirmed by finance")
    s.commit()

    events = identity.history(s, ORG, identity.CUSTOMER, a.identity_id)
    linked = [e for e in events if e.action == "LINKED"]
    assert len(linked) == 1
    assert linked[0].actor == "user-7"
    assert "finance" in linked[0].detail
    s.close()


# ── items ───────────────────────────────────────────────────────────────────
def test_items_match_on_sku_across_connectors(client):
    s = client.Maker()
    a = identity.ingest_item(s, ORG, connector="zoho", external_id="itm-1",
                             sku="KCMT090304LF", description="Milling insert")
    b = identity.ingest_item(s, ORG, connector="sap", external_id="100045",
                             sku="kcmt-090304-lf", description="Wendeschneidplatte")
    s.commit()

    assert [c.strategy for c in b.suggestions] == ["SKU"]
    assert b.suggestions[0].identity_id == a.identity_id
    # And the SAP description is not overwritten by the Zoho one.
    assert b.record.description == "Wendeschneidplatte"
    s.close()


def test_an_item_without_a_sku_proposes_nothing(client):
    """No key, no claim. Guessing from the description is exactly the kind of
    silent merge this design refuses."""
    s = client.Maker()
    identity.ingest_item(s, ORG, connector="zoho", external_id="a",
                         sku=None, description="Milling insert")
    b = identity.ingest_item(s, ORG, connector="sap", external_id="b",
                             sku=None, description="Milling insert")
    s.commit()
    assert b.suggestions == []
    s.close()


# ── the API ─────────────────────────────────────────────────────────────────
def test_the_screen_lists_identities_with_their_linked_records(client):
    s = client.Maker()
    a = _zoho(s)
    b = _erpnext(s)
    identity.link_record(s, ORG, entity_type=identity.CUSTOMER,
                         record_id=b.record.record_id, identity_id=a.identity_id,
                         actor="u1")
    s.commit()
    s.close()

    body = client.get("/api/v1/identity/customers", headers=_hdr(client)).json()
    row = next(d for d in body["identities"] if d["record_count"] == 2)
    assert row["connector_count"] == 2
    assert {r["connector"] for r in row["records"]} == {"zoho", "erpnext"}
    assert {r["gstin"] for r in row["records"]} == {GSTIN}
    assert row["display_name"] == "ABC Industries Pvt Ltd", "the fuller name"


def test_a_suggestion_carries_the_evidence_that_produced_it(client):
    s = client.Maker()
    _zoho(s)
    _erpnext(s)
    s.commit()
    s.close()

    body = client.get("/api/v1/identity/customers/suggestions/pending",
                      headers=_hdr(client)).json()
    assert len(body["suggestions"]) == 1
    sugg = body["suggestions"][0]
    assert sugg["strategy"] == "GSTIN"
    assert GSTIN in sugg["evidence"], "reviewable, unlike a score"
    assert sugg["incoming"]["connector"] == "erpnext"
    assert sugg["target"]["records"][0]["connector"] == "zoho"


def test_accepting_a_suggestion_links_the_records(client):
    s = client.Maker()
    _zoho(s)
    _erpnext(s)
    s.commit()
    s.close()

    listed = client.get("/api/v1/identity/customers/suggestions/pending",
                        headers=_hdr(client)).json()["suggestions"][0]
    r = client.post(f"/api/v1/identity/customers/suggestions/{listed['suggestion_id']}",
                    json={"accept": True}, headers=_hdr(client))
    assert r.status_code == 200 and r.json()["status"] == "ACCEPTED"

    after = client.get("/api/v1/identity/customers", headers=_hdr(client)).json()
    assert any(d["connector_count"] == 2 for d in after["identities"])
    assert after["pending_suggestions"] == 0


def test_rejecting_a_suggestion_leaves_the_records_apart(client):
    s = client.Maker()
    a = _zoho(s)
    b = _erpnext(s)
    s.commit()
    s.close()

    listed = client.get("/api/v1/identity/customers/suggestions/pending",
                        headers=_hdr(client)).json()["suggestions"][0]
    client.post(f"/api/v1/identity/customers/suggestions/{listed['suggestion_id']}",
                json={"accept": False}, headers=_hdr(client))

    s = client.Maker()
    assert s.get(models.CustomerConnectorRecord, b.record.record_id).identity_id != a.identity_id
    s.close()


def test_a_rejected_suggestion_is_not_offered_again(client):
    s = client.Maker()
    _zoho(s)
    _erpnext(s)
    s.commit()
    s.close()
    listed = client.get("/api/v1/identity/customers/suggestions/pending",
                        headers=_hdr(client)).json()["suggestions"][0]
    client.post(f"/api/v1/identity/customers/suggestions/{listed['suggestion_id']}",
                json={"accept": False}, headers=_hdr(client))
    body = client.get("/api/v1/identity/customers/suggestions/pending",
                      headers=_hdr(client)).json()
    assert body["suggestions"] == []


def test_deciding_twice_is_refused(client):
    s = client.Maker()
    _zoho(s)
    _erpnext(s)
    s.commit()
    s.close()
    listed = client.get("/api/v1/identity/customers/suggestions/pending",
                        headers=_hdr(client)).json()["suggestions"][0]
    url = f"/api/v1/identity/customers/suggestions/{listed['suggestion_id']}"
    assert client.post(url, json={"accept": True}, headers=_hdr(client)).status_code == 200
    assert client.post(url, json={"accept": True}, headers=_hdr(client)).status_code == 400


# ── who may do what ─────────────────────────────────────────────────────────
def test_a_manager_may_review_but_only_an_owner_may_link(client):
    """Deciding two ERP records are one company reshapes every margin figure
    rolled up from them — the same class of act as editing the margin policy."""
    s = client.Maker()
    a = _zoho(s)
    b = _erpnext(s)
    s.commit()
    s.close()

    mgr = _hdr(client, MANAGER)
    assert client.get("/api/v1/identity/customers", headers=mgr).status_code == 200
    assert client.get("/api/v1/identity/customers", headers=mgr).json()["can_manage"] is False
    r = client.post("/api/v1/identity/customers/link", headers=mgr,
                    json={"record_id": b.record.record_id, "identity_id": a.identity_id})
    assert r.status_code == 403


def test_a_salesperson_sees_none_of_it(client):
    assert client.get("/api/v1/identity/customers",
                      headers=_hdr(client, SALES)).status_code == 403


def test_the_auto_link_setting_is_owner_only(client):
    assert client.patch("/api/v1/identity/settings/policy",
                        json={"auto_link_customers": True},
                        headers=_hdr(client, MANAGER)).status_code == 403
    r = client.patch("/api/v1/identity/settings/policy",
                     json={"auto_link_customers": True}, headers=_hdr(client))
    assert r.status_code == 200 and r.json()["auto_link_customers"] is True


def test_an_unknown_entity_kind_is_a_404_not_a_crash(client):
    assert client.get("/api/v1/identity/invoices",
                      headers=_hdr(client)).status_code == 404


# ── the architecture rule ───────────────────────────────────────────────────
def test_no_connector_is_named_anywhere_in_the_identity_layer():
    """Adding a connector must require an importer and nothing else. A
    connector name in the resolver is how that promise erodes, one commit at a
    time — so it is asserted rather than trusted."""
    import pathlib

    offenders = []
    for path in pathlib.Path("app/identity").glob("*.py"):
        text = path.read_text().lower()
        for name in ("zoho", "tally", "erpnext", "sap", "dynamics"):
            # Docstrings may name one as an example; code may not.
            import ast
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if len(node.value) > 200:      # a docstring
                        continue
                    if name in node.value.lower():
                        offenders.append(f"{path.name}:{node.lineno} {node.value[:40]!r}")
            if name in text.replace("#", "\n#").split("\n#")[0].lower():
                pass
    assert offenders == [], offenders
