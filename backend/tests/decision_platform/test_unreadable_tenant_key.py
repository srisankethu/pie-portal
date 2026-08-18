"""A tenant key that cannot be unwrapped must not cost a pull.

The incident these are written against: a live sync reported

    KeyUnavailable: Could not unwrap this organization's data key

after reading 2594 customers and 10000 items, having read **0 of 5 months** of
documents — and every retry did the same, because nothing about a retry changes
a key. Five months of invoices were being withheld by an encrypted copy of
names that are also sitting in plaintext one table away.

Two claims are pinned here, and the second is the one that makes the diagnosis
possible at all: the pull survives, and the two states behind an unwrap failure
stay distinguishable. Restoring an old master key is the right remedy for one
of them and destroys a working deployment in the other.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.config import settings
from app.domain import models
from app.ingestion.sync import SyncService
from app.trust import keys, rekey, vault

ORG = "org_key"


@pytest.fixture()
def org(session):
    session.add(models.Organization(organization_id=ORG, name="SLS Engineers",
                                    currency="INR", config={}))
    session.flush()
    return ORG


def _stale_key(session) -> None:
    """A key row wrapped under a master key this process does not have.

    Exactly the state a rotation leaves behind: the row is intact, undestroyed,
    and unreadable.
    """
    other = Fernet(Fernet.generate_key())
    session.add(models.TenantKey(
        organization_id=ORG,
        wrapped_dek=other.encrypt(Fernet.generate_key()).decode()))
    session.flush()


class _Source:
    """One customer, one item, one invoice — enough to prove documents landed."""

    def list_contacts(self):
        return [{"contact_id": "c1", "contact_name": "Acme", "status": "active"}]

    def list_items(self):
        return [{"item_id": "i1", "name": "Insert", "unit": "pcs",
                 "status": "active"}]

    def list_users(self):
        return []

    def list_invoices(self, skip=None):
        return [{"invoice_id": "inv1", "customer_id": "c1", "date": "2026-06-01",
                 "line_items": [{"line_item_id": "l1", "item_id": "i1",
                                 "quantity": 10, "rate": 500,
                                 "item_total": 5000}]}]

    def list_bills(self, skip=None):
        return []


# ── the pull ────────────────────────────────────────────────────────────────
def test_a_stale_key_does_not_stop_the_documents(session, org):
    _stale_key(session)

    report = SyncService(session, _Source(), ORG).run()

    # The point of the whole change: the trade landed.
    assert report.sales_txns == 1, "an unwrappable key must not cost the documents"
    assert report.customers == 1 and report.products == 1
    assert session.query(models.SalesTxn).count() == 1


def test_the_pull_reports_the_key_rather_than_swallowing_it(session, org):
    _stale_key(session)

    report = SyncService(session, _Source(), ORG).run()

    named = [s for s in report.skipped if s["code"] == "NAME_VAULT_UNAVAILABLE"]
    assert len(named) == 1, "a skipped vault must be visible, not silent"
    assert "KeyUnavailable" in named[0]["detail"]
    # Both remedies, so a screen does not send somebody to restore a master key
    # that is already the right one.
    assert "CREDENTIAL_ENCRYPTION_KEY" in named[0]["detail"]
    assert named[0]["context"]["fix"]
    assert report.unresolved()[0]["code"] == "NAME_VAULT_UNAVAILABLE"


def test_a_real_failure_here_still_stops_the_pull(session, org, monkeypatch):
    """The guard is for two key states, not a licence for this phase to fail."""
    def boom(*_a, **_kw):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(vault, "backfill", boom)
    with pytest.raises(RuntimeError):
        SyncService(session, _Source(), ORG).run()


def test_a_working_key_still_vaults_the_names(session, org):
    """The degraded path must not be the only path anybody exercises."""
    report = SyncService(session, _Source(), ORG).run()

    assert report.skipped == []
    customer = session.query(models.Customer).one()
    assert vault.resolve(session, ORG, "CUSTOMER", customer.customer_id) == "Acme"


# ── the states, kept apart ──────────────────────────────────────────────────
def test_inspect_names_the_state_without_creating_a_key(session, org):
    assert keys.inspect(session, ORG) == keys.KEY_ABSENT
    assert session.get(models.TenantKey, ORG) is None, "a look must not write"

    keys.encrypt_for(session, ORG, "Acme")
    assert keys.inspect(session, ORG) == keys.KEY_READY

    keys.destroy(session, ORG, reason="Customer requested erasure",
                 actor_user_id="u1")
    assert keys.inspect(session, ORG) == keys.KEY_DESTROYED


def test_a_stale_key_reads_as_unreadable_not_destroyed(session, org):
    _stale_key(session)
    assert keys.inspect(session, ORG) == keys.KEY_UNREADABLE
    assert not keys.is_destroyed(session, ORG), (
        "an erasure that never happened must not be reported as one")


# ── recovery, and its guards ────────────────────────────────────────────────
def test_reissue_recovers_a_stale_key(session, org):
    _stale_key(session)
    keys.reissue(session, ORG, reason="Master key rotated; old value gone",
                 actor_user_id="u1")

    assert keys.inspect(session, ORG) == keys.KEY_READY
    # And the vault works again, which is the reason to do it at all.
    assert keys.decrypt_for(session, ORG, keys.encrypt_for(session, ORG, "Acme")) == "Acme"

    row = session.get(models.TenantKey, ORG)
    assert row.reissued_at is not None
    assert row.reissue_reason == "Master key rotated; old value gone"
    assert row.reissued_by_user_id == "u1"


def test_reissue_will_not_resurrect_an_erased_tenant(session, org):
    keys.encrypt_for(session, ORG, "Acme")
    keys.destroy(session, ORG, reason="Customer requested erasure",
                 actor_user_id="u1")

    with pytest.raises(keys.KeyDestroyed):
        keys.reissue(session, ORG, reason="I would like it back",
                     actor_user_id="u2")
    assert keys.is_destroyed(session, ORG), "the tombstone must survive"


def test_reissue_will_not_shred_a_working_key(session, org):
    ciphertext = keys.encrypt_for(session, ORG, "Acme")

    with pytest.raises(ValueError):
        keys.reissue(session, ORG, reason="tidying up", actor_user_id="u1")
    assert keys.decrypt_for(session, ORG, ciphertext) == "Acme"


def test_reissue_needs_a_reason(session, org):
    _stale_key(session)
    with pytest.raises(ValueError):
        keys.reissue(session, ORG, reason="   ", actor_user_id="u1")


# ── the diagnosis the operator acts on ──────────────────────────────────────
def test_credentials_that_decrypt_are_evidence_the_master_key_is_current(session, org):
    assert rekey.master_key_is_current(session) is None, (
        "no credential stored is no evidence, not a comfortable yes")

    from app import crypto
    session.add(models.ZohoCredential(
        owner_organization_id=ORG, client_id="1000.X",
        client_secret_encrypted=crypto.encrypt("secret"),
        refresh_token_encrypted=crypto.encrypt("refresh")))
    session.flush()
    assert rekey.master_key_is_current(session) is True


def test_a_credential_written_under_another_master_key_reads_as_wrong(session, org,
                                                                     monkeypatch):
    monkeypatch.setattr(settings, "CREDENTIAL_ENCRYPTION_KEY",
                        Fernet.generate_key().decode())
    session.add(models.ZohoCredential(
        owner_organization_id=ORG, client_id="1000.X",
        client_secret_encrypted=Fernet(Fernet.generate_key()).encrypt(b"s").decode(),
        refresh_token_encrypted="x"))
    session.flush()
    assert rekey.master_key_is_current(session) is False


def test_the_survey_counts_what_a_reissue_would_cost(session, org):
    _stale_key(session)
    session.add(models.ModelPayload(
        organization_id=ORG, decision_type="QUOTE", provider="mock",
        model="mock", payload_ciphertext="x", disclosure_findings=[]))
    session.flush()

    row = [r for r in rekey.survey(session) if r["organization_id"] == ORG][0]
    assert row["state"] == keys.KEY_UNREADABLE
    # Counted from erasure.DESTROYED, so a third ciphertext class added there
    # is counted here without this test being edited.
    assert row["unreadable_if_reissued"]["model_payloads"] == 1
