"""The trust controls, tested as the promises they are meant to make.

Each promise here is one a customer is asked to believe. A test that only
exercises the happy path would let every one of them quietly become false, so
these are written against the failure the control exists to prevent:

  * a destroyed key must make data *unreadable*, not merely absent from a query;
  * a name must not reach a model, whatever path the bundle was built by;
  * staff access without a grant must raise, not return False and be ignored;
  * a receipt must fail verification if anyone edits it.
"""
from __future__ import annotations

import pytest

from app.context.bundle import ContextBundle
from app.domain import models
from app.trust import access, disclosure, erasure, keys, pseudonym, rehydrate, vault

ORG = "org_trust"
OTHER = "org_other"


@pytest.fixture()
def org(session):
    for oid in (ORG, OTHER):
        session.add(models.Organization(organization_id=oid, name=oid,
                                        erp="zoho", currency="INR", config={}))
    session.flush()
    return ORG


# ── per-tenant keys ─────────────────────────────────────────────────────────
def test_round_trip(session, org):
    ct = keys.encrypt_for(session, ORG, "Bharat Forge")
    assert ct != "Bharat Forge"
    assert keys.decrypt_for(session, ORG, ct) == "Bharat Forge"


def test_one_tenant_cannot_read_another(session, org):
    ct = keys.encrypt_for(session, ORG, "Bharat Forge")
    with pytest.raises(keys.KeyUnavailable):
        keys.decrypt_for(session, OTHER, ct)


def test_destroying_the_key_makes_ciphertext_unreadable(session, org):
    ct = keys.encrypt_for(session, ORG, "Bharat Forge")
    keys.destroy(session, ORG, reason="Customer requested erasure", actor_user_id="u1")

    with pytest.raises(keys.KeyDestroyed):
        keys.decrypt_for(session, ORG, ct)
    # The ciphertext is still sitting there. That is the point: it survives in
    # backups too, and is inert in all of them.
    assert ct


def test_destruction_needs_a_reason(session, org):
    with pytest.raises(ValueError):
        keys.destroy(session, ORG, reason="   ", actor_user_id="u1")


def test_destruction_leaves_a_tombstone_not_a_hole(session, org):
    keys.destroy(session, ORG, reason="Customer requested erasure", actor_user_id="u1")
    row = session.get(models.TenantKey, ORG)
    assert row is not None and row.destroyed_at is not None
    assert row.destroy_reason == "Customer requested erasure"
    assert keys.is_destroyed(session, ORG)


def test_destroying_twice_is_harmless(session, org):
    first = keys.destroy(session, ORG, reason="Customer requested erasure",
                         actor_user_id="u1")
    again = keys.destroy(session, ORG, reason="second attempt", actor_user_id="u2")
    assert again.destroyed_at == first.destroyed_at, "must not re-stamp"


# ── pseudonyms ──────────────────────────────────────────────────────────────
def test_pseudonym_is_stable():
    a = pseudonym.label_for(ORG, "CUSTOMER", "c1")
    b = pseudonym.label_for(ORG, "CUSTOMER", "c1")
    assert a == b, "an unstable label would break interpretation caching"


def test_pseudonym_does_not_correlate_across_tenants():
    assert (pseudonym.label_for(ORG, "CUSTOMER", "c1")
            != pseudonym.label_for(OTHER, "CUSTOMER", "c1"))


def test_pseudonym_carries_no_trace_of_the_name():
    label = pseudonym.label_for(ORG, "CUSTOMER", "c1")
    assert "c1" not in label.replace("Customer C-", "")


def test_pseudonym_contains_no_digits():
    """Load-bearing, and the reason is not obvious.

    ``ai/contract.py`` rejects any number in a model's output that is not
    grounded in the fact bundle, and it finds numbers with a regex that does not
    require a word boundary. A hex label like ``C-A67EF1`` reads to that check
    as the numbers 67 and 1, so a model that politely names its subject would
    fail grounding and every interpretation would degrade to the deterministic
    fallback — quietly, since degrading is a supported outcome.
    """
    from app.ai.contract import _NUMBER_RE

    for entity_id in (f"c{i}" for i in range(200)):
        label = pseudonym.label_for(ORG, "CUSTOMER", entity_id)
        assert not _NUMBER_RE.findall(label), (
            f"{label!r} parses as a number and would break output grounding")


# ── the vault ───────────────────────────────────────────────────────────────
def test_vault_round_trip(session, org):
    vault.put(session, ORG, "CUSTOMER", "c1", "Bharat Forge")
    assert vault.resolve(session, ORG, "CUSTOMER", "c1") == "Bharat Forge"


def test_vault_stores_ciphertext_not_the_name(session, org):
    vault.put(session, ORG, "CUSTOMER", "c1", "Bharat Forge")
    row = session.query(models.NameVaultEntry).one()
    assert "Bharat" not in row.name_ciphertext


def test_an_unknown_entity_resolves_to_its_pseudonym(session, org):
    got = vault.resolve(session, ORG, "CUSTOMER", "nope")
    assert got == pseudonym.label_for(ORG, "CUSTOMER", "nope")


def test_after_erasure_names_degrade_to_pseudonyms_rather_than_failing(session, org):
    vault.put(session, ORG, "CUSTOMER", "c1", "Bharat Forge")
    keys.destroy(session, ORG, reason="Customer requested erasure", actor_user_id="u1")

    got = vault.resolve(session, ORG, "CUSTOMER", "c1")
    assert got == pseudonym.label_for(ORG, "CUSTOMER", "c1")
    assert "Bharat" not in got


def test_resolve_many_matches_resolve(session, org):
    vault.put(session, ORG, "CUSTOMER", "c1", "Bharat Forge")
    vault.put(session, ORG, "CUSTOMER", "c2", "Kirloskar")
    many = vault.resolve_many(session, ORG, "CUSTOMER", ["c1", "c2", "c3"])
    assert many["c1"] == "Bharat Forge"
    assert many["c2"] == "Kirloskar"
    assert many["c3"] == vault.resolve(session, ORG, "CUSTOMER", "c3")


def test_backfill_is_idempotent(session, org):
    session.add(models.Customer(customer_id="c1", organization_id=ORG,
                                external_id="z1", name="Bharat Forge"))
    session.flush()
    vault.backfill(session, ORG)
    vault.backfill(session, ORG)
    assert session.query(models.NameVaultEntry).count() == 1


# ── re-hydration ────────────────────────────────────────────────────────────
def test_rehydrate_replaces_every_occurrence():
    label = pseudonym.label_for(ORG, "CUSTOMER", "c1")
    text = f"{label} is declining. Contact {label} this week."
    assert rehydrate.text(text, {label: "Bharat Forge"}) == (
        "Bharat Forge is declining. Contact Bharat Forge this week.")


def test_rehydrate_leaves_text_alone_when_there_is_nothing_to_do():
    assert rehydrate.text("no labels here", {}) == "no labels here"


def test_rehydrate_updates_every_user_facing_field():
    class Result:
        concise_title = "X down"
        explanation = "X has slipped"
        recommended_action = "Call X"
        caveat = "X is thin on evidence"

    r = rehydrate.result(Result(), {"X": "Bharat Forge"})
    assert r.concise_title == "Bharat Forge down"
    assert r.explanation == "Bharat Forge has slipped"
    assert r.recommended_action == "Call Bharat Forge"
    assert r.caveat == "Bharat Forge is thin on evidence"


# ── disclosure ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("leak", [
    "29ABCDE1234F1Z5",              # GSTIN
    "ABCDE1234F",                   # PAN
    "buyer@bharatforge.co.in",      # email
    "9876543210",                   # phone
    "HDFC0001234",                  # IFSC
])
def test_the_checker_catches_identifiers(leak):
    assert disclosure.check(f'{{"fact": "{leak}"}}'), f"{leak} slipped through"


def test_a_clean_payload_has_no_findings():
    payload = ('{"subject": "Customer C-9F42A1", "facts": '
               '[{"label": "margin_recent", "value": 0.19}]}')
    assert disclosure.check(payload) == []


def test_the_statement_is_served_from_what_the_checker_enforces():
    s = disclosure.statement()
    assert s["training_on_customer_data"] is False
    assert len(s["allowed"]) == len(disclosure.ALLOWED)
    assert len(s["never_sent"]) == len(disclosure.NEVER)


def test_a_logged_payload_is_encrypted_and_readable_by_its_owner(session, org):
    row = disclosure.record(
        session, organization_id=ORG, ai_call_log_id=None,
        decision_type="MARGIN_DETERIORATION", system="SYS",
        user='{"subject": "Customer C-9F42A1"}', provider="mock", model="m")

    assert "Customer C-9F42A1" not in row.payload_ciphertext
    assert "Customer C-9F42A1" in disclosure.reveal(session, row)
    assert row.disclosure_findings == []


def test_a_leaking_payload_is_recorded_as_a_finding(session, org):
    row = disclosure.record(
        session, organization_id=ORG, ai_call_log_id=None, decision_type="X",
        system="", user='{"gstin": "29ABCDE1234F1Z5"}', provider="mock", model="m")
    assert row.disclosure_findings, (
        "A payload carrying a GSTIN must be flagged where someone will see it, "
        "not merely fail a test that nobody is running in production.")


def test_payloads_are_scoped_to_their_organization(session, org):
    disclosure.record(session, organization_id=ORG, ai_call_log_id=None,
                      decision_type="X", system="", user="mine",
                      provider="mock", model="m")
    assert disclosure.payloads_for(session, OTHER) == []


# ── break-glass ─────────────────────────────────────────────────────────────
def test_access_without_a_grant_raises(session, org):
    with pytest.raises(access.AccessDenied):
        access.record_use(session, organization_id=ORG, staff_user_id="staff1",
                          resource="customer portfolio")


def test_a_grant_needs_a_real_justification(session, org):
    with pytest.raises(ValueError):
        access.grant(session, organization_id=ORG, staff_user_id="s1",
                     justification="debug")


def test_a_grant_and_every_use_are_logged(session, org):
    access.grant(session, organization_id=ORG, staff_user_id="s1",
                 justification="TICKET-4471: investigating a missing cost record")
    access.record_use(session, organization_id=ORG, staff_user_id="s1",
                      resource="customer portfolio")
    access.record_use(session, organization_id=ORG, staff_user_id="s1",
                      resource="quote drawer")

    actions = [e.action for e in access.events_for(session, ORG)]
    assert actions.count("ACCESSED") == 2, (
        "Per-use logging is the point: one reach and fifty reaches must be "
        "distinguishable.")
    assert "GRANTED" in actions


def test_the_justification_is_kept_verbatim(session, org):
    reason = "TICKET-4471: investigating a missing cost record"
    access.grant(session, organization_id=ORG, staff_user_id="s1", justification=reason)
    granted = [e for e in access.events_for(session, ORG) if e.action == "GRANTED"]
    assert granted[0].detail == reason


def test_a_revoked_grant_stops_working(session, org):
    row = access.grant(session, organization_id=ORG, staff_user_id="s1",
                       justification="TICKET-4471: investigating a cost record")
    access.revoke(session, row.grant_id, actor_user_id="admin")
    with pytest.raises(access.AccessDenied):
        access.record_use(session, organization_id=ORG, staff_user_id="s1",
                          resource="anything")


def test_an_expired_grant_stops_working(session, org):
    from datetime import timedelta
    row = access.grant(session, organization_id=ORG, staff_user_id="s1",
                       justification="TICKET-4471: investigating a cost record",
                       ttl=timedelta(minutes=1))
    row.expires_at = row.granted_at - timedelta(minutes=1)   # already past
    session.flush()
    with pytest.raises(access.AccessDenied):
        access.record_use(session, organization_id=ORG, staff_user_id="s1",
                          resource="anything")


def test_a_grant_cannot_be_open_ended(session, org):
    from datetime import timedelta
    with pytest.raises(ValueError):
        access.grant(session, organization_id=ORG, staff_user_id="s1",
                     justification="TICKET-4471: investigating a cost record",
                     ttl=timedelta(days=30))


def test_one_tenants_events_are_not_anothers(session, org):
    access.grant(session, organization_id=ORG, staff_user_id="s1",
                 justification="TICKET-4471: investigating a cost record")
    assert access.events_for(session, OTHER) == []


# ── export and erasure ──────────────────────────────────────────────────────
def test_export_excludes_credentials(session, org):
    out = erasure.export(session, ORG)
    assert "zoho_credentials" not in out["data"]
    assert "zoho_credentials" in out["excluded"]


def test_export_includes_the_identity_graph(session, org):
    """The joined view is the thing they cannot rebuild elsewhere."""
    for table in ("customer_identities", "customer_connector_records",
                  "item_identities", "item_connector_records"):
        assert table in erasure.export(session, ORG)["data"]


def test_erasure_produces_a_verifiable_receipt(session, org):
    session.add(models.Customer(customer_id="c1", organization_id=ORG,
                                external_id="z1", name="Bharat Forge"))
    session.flush()

    row = erasure.erase(session, ORG, reason="Customer requested erasure",
                        actor_user_id="u1")
    assert erasure.verify_receipt(row)
    assert row.manifest["customers"] == 1, "the receipt must say what was destroyed"


def test_an_altered_receipt_fails_verification(session, org):
    row = erasure.erase(session, ORG, reason="Customer requested erasure",
                        actor_user_id="u1")
    row.manifest = {**row.manifest, "customers": 999}
    assert not erasure.verify_receipt(row), (
        "A receipt that still verifies after being edited proves nothing.")


def test_erasure_actually_makes_the_names_unreadable(session, org):
    vault.put(session, ORG, "CUSTOMER", "c1", "Bharat Forge")
    erasure.erase(session, ORG, reason="Customer requested erasure", actor_user_id="u1")

    assert "Bharat" not in vault.resolve(session, ORG, "CUSTOMER", "c1")
    assert erasure.status(session, ORG)["erased"] is True


def test_status_is_false_before_any_erasure(session, org):
    assert erasure.status(session, ORG) == {"erased": False, "receipt": None}


# ── the bundle contract ─────────────────────────────────────────────────────
def test_display_names_never_reach_the_prompt():
    bundle = ContextBundle(
        decision_type="MARGIN_DETERIORATION", organization_id=ORG,
        subject_ref={"entity_type": "CUSTOMER", "entity_id": "c1",
                     "label": "Customer C-9F42A1"},
        recipient_role="OWNER", permitted_data_classes=["OPERATIONAL"],
        redactions_applied=[], signals=[], facts=[],
        evidence_sufficiency={"level": "SUFFICIENT", "reasons": []},
        unknowns=[], policies=[], evidence_refs=[],
        display_names={"Customer C-9F42A1": "Bharat Forge"})

    import json
    payload = json.dumps(bundle.to_prompt_json(), default=str)
    assert "Bharat Forge" not in payload
    assert "display_names" not in payload


def test_display_names_do_not_change_the_context_hash():
    """A renamed customer must not invalidate a cached interpretation whose
    facts are identical — and the mapping must not be part of the identity of
    the call."""
    def build(names):
        return ContextBundle(
            decision_type="MARGIN_DETERIORATION", organization_id=ORG,
            subject_ref={"entity_type": "CUSTOMER", "entity_id": "c1",
                         "label": "Customer C-9F42A1"},
            recipient_role="OWNER", permitted_data_classes=["OPERATIONAL"],
            redactions_applied=[], signals=[], facts=[],
            evidence_sufficiency={"level": "SUFFICIENT", "reasons": []},
            unknowns=[], policies=[], evidence_refs=[], display_names=names)

    assert (build({"Customer C-9F42A1": "Bharat Forge"}).context_hash()
            == build({"Customer C-9F42A1": "Renamed Ltd"}).context_hash())
