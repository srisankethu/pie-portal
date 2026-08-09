"""Suppliers get the same treatment as customers, which they did not.

``vault`` argues that names are the only genuinely identifying data this
platform holds and that separating them is the highest-value split available.
Every word of that applies to a supplier — and suppliers were in none of it:
not vaulted, so a destroyed key left them readable; not in ``_PREFIX``, so
there was no pseudonym to put in front of a model; not in ``check_names``, so a
payload naming one passed the leak check clean.

It stopped being theoretical when the MSME watchlist arrived, which is a screen
of supplier names against amounts and dates.

These tests are written against those three absences rather than against the
happy path, so re-introducing any of them fails here rather than in production.
"""
from __future__ import annotations

from sqlalchemy import select

from app.domain import models
from app.trust import disclosure, keys, pseudonym, vault

ORG = "org_vendor_trust"
OTHER = "org_vendor_other"


def _org(session, oid=ORG):
    session.add(models.Organization(organization_id=oid, name=oid, erp="zoho",
                                    currency="INR", config={}))
    session.flush()
    return oid


def _vendor(session, oid=ORG, *, name="Sandvik Asia Private Limited",
            external_id="v-1"):
    row = models.Vendor(organization_id=oid, external_id=external_id, name=name,
                        connector="zoho")
    session.add(row)
    session.flush()
    return row


# ── the pseudonym ───────────────────────────────────────────────────────────
def test_vendor_label_reads_as_a_supplier():
    label = pseudonym.label_for(ORG, "VENDOR", "v-1")
    assert label.startswith("Supplier V-"), label


def test_vendor_label_is_stable():
    assert (pseudonym.label_for(ORG, "VENDOR", "v-1")
            == pseudonym.label_for(ORG, "VENDOR", "v-1"))


def test_vendor_label_is_scoped_to_one_tenant():
    """The same supplier id in two books must not be correlatable."""
    assert (pseudonym.label_for(ORG, "VENDOR", "v-1")
            != pseudonym.label_for(OTHER, "VENDOR", "v-1"))


def test_vendor_label_contains_no_digits():
    """Load-bearing, and the reason ``_ALPHABET`` exists.

    The AI output validator rejects any number in a model's text that is not
    grounded in the fact bundle, and it extracts numbers without requiring a
    word boundary. A label carrying digits would make a model politely echoing
    its subject fail grounding, and every interpretation would degrade to the
    deterministic fallback for a reason nobody could see.
    """
    label = pseudonym.label_for(ORG, "VENDOR", "v-1")
    assert not any(character.isdigit() for character in label), label


# ── the vault ───────────────────────────────────────────────────────────────
def test_vendor_names_are_vaulted_by_backfill(session):
    _org(session)
    vendor = _vendor(session)

    counts = vault.backfill(session, ORG)

    assert counts["VENDOR"] == 1
    assert vault.resolve(session, ORG, "VENDOR", vendor.vendor_id) == vendor.name


def test_a_vaulted_vendor_name_is_not_stored_in_the_clear(session):
    _org(session)
    vendor = _vendor(session)
    vault.backfill(session, ORG)

    row = session.scalar(
        select(models.NameVaultEntry).where(
            models.NameVaultEntry.organization_id == ORG,
            models.NameVaultEntry.entity_type == "VENDOR",
            models.NameVaultEntry.entity_id == vendor.vendor_id))
    assert row is not None
    assert vendor.name not in row.name_ciphertext


def test_vault_resolve_falls_back_to_the_supplier_pseudonym_after_destruction(session):
    """A screen that cannot render a name must say "Supplier V-…", not fail."""
    _org(session)
    vendor = _vendor(session)
    vault.backfill(session, ORG)

    keys.destroy(session, ORG, reason="Customer requested erasure",
                 actor_user_id="u1")

    resolved = vault.resolve(session, ORG, "VENDOR", vendor.vendor_id)
    assert resolved == pseudonym.label_for(ORG, "VENDOR", vendor.vendor_id)
    assert vendor.name not in resolved


# ── the leak check ──────────────────────────────────────────────────────────
def test_check_names_catches_a_vendor_name_in_a_payload(session):
    _org(session)
    _vendor(session)

    findings = disclosure.check_names(
        session, ORG,
        '{"subject": "Supplier V-ABCDEF", '
        '"top_overdue": "Sandvik Asia Private Limited"}')

    assert findings, "a supplier name in a payload must be reported"
    assert "Sandvik Asia Private Limited" in findings[0]


def test_check_names_passes_a_payload_that_only_uses_the_pseudonym(session):
    _org(session)
    vendor = _vendor(session)

    label = pseudonym.label_for(ORG, "VENDOR", vendor.vendor_id)
    assert disclosure.check_names(session, ORG, f'{{"subject": "{label}"}}') == []


def test_another_tenants_vendor_name_is_not_searched_for(session):
    """``check_names`` answers for one tenant, from that tenant's own list."""
    _org(session)
    _org(session, OTHER)
    _vendor(session, OTHER, name="Kennametal India Limited", external_id="v-9")

    assert disclosure.check_names(
        session, ORG, '{"note": "Kennametal India Limited"}') == []
