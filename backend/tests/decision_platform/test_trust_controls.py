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
                                        currency="INR", config={}))
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

    Asserted through ``numbers_in`` — the same function the gate calls, which
    now lives beside ``allowed_numbers`` in ``context/bundle.py`` so the two
    halves of the grounding contract cannot drift apart.
    """
    from app.context.bundle import numbers_in

    for entity_id in (f"c{i}" for i in range(200)):
        label = pseudonym.label_for(ORG, "CUSTOMER", entity_id)
        assert not numbers_in(label), (
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


def test_a_receipt_still_verifies_after_it_has_been_stored(session, org):
    """The receipt has to verify *later*, which is the only time anyone checks.

    The test above verifies the row `erase` just returned, in the transaction
    that made it — and that passed while the receipt was unverifiable from the
    moment it hit the disk. `_sign` covers an aware `erased_at`; a
    `DateTime(timezone=True)` column round-trips from SQLite as naive, so
    `receipt_body` re-read a timestamp with no offset, built a different blob and
    reported a valid receipt as tampered with. `GET /trust/erasure` — the only
    way an owner ever looks at one — therefore answered `verified: false` for
    every receipt ever issued, which on the trust screen reads as an accusation.

    `clock.iso` is what makes it hold, and that is not obvious from the call
    site: it was applied there as part of making timestamps unambiguous for the
    *browser*, so nothing recorded that a signature depends on it. Anyone
    "simplifying" it back to `.isoformat()` would break this silently. Hence a
    test about the signature rather than about the display.
    """
    row = erasure.erase(session, ORG, reason="Customer requested erasure",
                        actor_user_id="u1")
    session.commit()
    # Forces the reload the API path gets for free, and the naive datetime with
    # it. Without this the object under test is still the one built on the way
    # in, which is exactly the case that already passed.
    session.expire(row)

    assert erasure.verify_receipt(row), (
        "A receipt that only verifies before it is stored verifies never.")
    assert erasure.status(session, ORG)["receipt"]["verified"] is True


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


# ── receipt honesty ─────────────────────────────────────────────────────────
#
# The receipt once said ``method: "data key destroyed (crypto-shredding)"`` and
# stopped, while `vault.py` conceded `customers.name` and `products.name` stay
# plaintext — a signed overstatement, and the finding most likely to fail a
# customer security review. These tests pin the honest shape: the receipt must
# enumerate what key destruction reached and what survives in the clear, and
# removing either enumeration must fail here, not in a procurement call.

def test_the_receipt_no_longer_claims_crypto_shredding(session, org):
    row = erasure.erase(session, ORG, reason="Customer requested erasure",
                        actor_user_id="u1")
    method = erasure.receipt_body(row)["method"]
    assert "shred" not in method.lower(), (
        "'crypto-shredding' reads as 'your data is gone'; the method must say "
        "what was actually destroyed — the key.")
    assert "data key" in method.lower() or "dek" in method.lower()
    assert "plaintext" in method.lower(), (
        "the method line itself must point at the surviving plaintext, because "
        "it is the one sentence everyone quotes without the rest of the receipt")


def test_the_receipt_enumerates_what_key_destruction_reached(session, org):
    """`destroyed` must be the DEK-encrypted field classes — exactly those.

    Two entries because exactly two call sites use `keys.encrypt_for`
    (`vault.put`, `disclosure.record`). A third encrypted field class must be
    added to `erasure.DESTROYED` in the same change, and this assertion is the
    reminder.
    """
    row = erasure.erase(session, ORG, reason="Customer requested erasure",
                        actor_user_id="u1")
    destroyed = erasure.receipt_body(row)["destroyed"]
    assert {(d["table"], d["column"]) for d in destroyed} == {
        ("name_vault", "name_ciphertext"),
        ("model_payloads", "payload_ciphertext"),
    }
    assert all(d["holds"] for d in destroyed), (
        "an entry that does not say what the ciphertext held tells the "
        "customer nothing")


def test_the_receipt_enumerates_the_plaintext_that_survives(session, org):
    """The uncomfortable half, and the one this receipt exists to state.

    If someone trims `SURVIVES_PLAINTEXT` — because it reads badly in a demo —
    this fails. The enumeration may only shrink when a column is actually
    encrypted or dropped.
    """
    row = erasure.erase(session, ORG, reason="Customer requested erasure",
                        actor_user_id="u1")
    survives = erasure.receipt_body(row)["survives_plaintext"]

    assert survives, "the surviving-plaintext enumeration must not be removed"
    named = {(s["table"], s["column"]) for s in survives}
    # The two columns vault.py concedes, plus the identifiers the positioning
    # review found beyond them. Named individually so dropping any one of them
    # from the receipt is a test failure with that row in the diff.
    for must_name in (("customers", "name"),
                      ("products", "name"),
                      ("vendors", "name"),
                      ("vendors", "gstin, pan"),
                      ("customer_connector_records", "name, gstin")):
        assert must_name in named, f"receipt no longer discloses {must_name}"
    assert all(s["why"] for s in survives), (
        "an exclusion nobody can explain is one nobody should trust — same "
        "rule as EXCLUDED_REASONS")


def test_the_enumerations_are_covered_by_the_signature(session, org):
    """Honesty that is not signed is a webpage, not a receipt."""
    row = erasure.erase(session, ORG, reason="Customer requested erasure",
                        actor_user_id="u1")
    assert erasure.verify_receipt(row)
    doctored = dict(row.attestation)
    doctored["survives_plaintext"] = []          # "nothing survives" — the lie
    row.attestation = doctored
    assert not erasure.verify_receipt(row), (
        "stripping the surviving-plaintext disclosure must break the signature")


def test_the_receipt_keeps_its_story_when_the_code_moves_on(session, org, monkeypatch):
    """The attestation is a fact about the moment of erasure, kept on the row.

    A receipt that rebuilt its claims from the module constants would change
    its story — or stop verifying — the day `SURVIVES_PLAINTEXT` is edited.
    Simulate that day and require the stored receipt to stand unchanged.
    """
    row = erasure.erase(session, ORG, reason="Customer requested erasure",
                        actor_user_id="u1")
    original = erasure.receipt_body(row)

    monkeypatch.setattr(erasure, "RECEIPT_METHOD", "a different claim")
    monkeypatch.setattr(erasure, "SURVIVES_PLAINTEXT",
                        ({"table": "x", "column": "y", "why": "edited later"},))

    assert erasure.receipt_body(row) == original
    assert erasure.verify_receipt(row), (
        "editing the constants must not invalidate receipts already issued")


def test_the_status_payload_carries_the_enumerations_to_the_owner(session, org):
    """`status` is what GET /trust/erasure serves — the enumeration has to
    reach the screen, not sit in a column nobody renders."""
    erasure.erase(session, ORG, reason="Customer requested erasure",
                  actor_user_id="u1")
    receipt = erasure.status(session, ORG)["receipt"]
    assert receipt["survives_plaintext"]
    assert receipt["destroyed"]
    assert receipt["verified"] is True


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
