"""Opening the tenant's data key once per batch, not once per value.

The cost this pins is invisible in a unit test and obvious in production:
``decrypt_for`` reads the key row and unwraps the DEK *every* call, so a page of
names, a payload list, or the vault backfill at the end of every sync paid a SQL
round trip and a KEK decryption per row. Measured on this codebase: 515 µs to
decrypt one name, of which 53 µs was the decryption.

Statements are what the tests assert on, because they are the part that does not
depend on the machine: N values must cost a constant number of key reads, not N.
"""
from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.domain import models
from app.trust import disclosure, keys, pseudonym, vault


@pytest.fixture()
def counting(engine):
    """Counts SQL statements issued against the test engine."""
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    yield seen
    event.remove(engine, "before_cursor_execute", record)


def _names(session, org, n):
    for i in range(n):
        vault.put(session, org, "CUSTOMER", f"c{i}", f"Customer {i} Pvt Ltd")
    session.commit()
    return [f"c{i}" for i in range(n)]


def test_a_page_of_names_opens_the_key_once(session, counting):
    """The property, stated as a count: 20 names must not cost 20 key reads."""
    ids = _names(session, "org1", 20)
    counting.clear()
    out = vault.resolve_many(session, "org1", "CUSTOMER", ids)

    assert out[ids[0]] == "Customer 0 Pvt Ltd"
    assert len(out) == 20
    key_reads = [s for s in counting if "tenant_keys" in s]
    assert len(key_reads) <= 1, (
        f"{len(key_reads)} key reads for 20 names — the key is being opened "
        "per row again")


def test_vaulting_a_book_opens_the_key_once(session, counting):
    """``backfill`` runs at the end of every sync, over every customer, product
    and vendor. Per-row it was a statement and an unwrap per name."""
    for i in range(15):
        session.add(models.Customer(organization_id="org1", customer_id=f"c{i}",
                                    external_id=f"zoho-{i}", name=f"Customer {i}"))
    keys.ensure_key(session, "org1")   # created lazily on first use; not the
    session.commit()                   # cost this test is about
    counting.clear()

    counts = vault.backfill(session, "org1")

    assert counts["CUSTOMER"] == 15
    key_reads = [s for s in counting if "tenant_keys" in s]
    assert len(key_reads) <= 1, f"{len(key_reads)} key reads to vault 15 names"


def test_a_page_of_payloads_opens_the_key_once(session, counting):
    """``GET /trust/payloads?reveal=true`` decrypts up to 200 rows."""
    cipher = keys.cipher_for(session, "org1")
    rows = []
    for i in range(10):
        row = models.ModelPayload(
            organization_id="org1", decision_type="TEST", provider="mock",
            model="mock-1", payload_ciphertext=cipher.encrypt(f"payload {i}"))
        session.add(row)
        rows.append(row)
    session.commit()
    counting.clear()

    out = disclosure.reveal_many(session, rows)

    assert out[rows[3].payload_id] == "payload 3"
    key_reads = [s for s in counting if "tenant_keys" in s]
    assert len(key_reads) <= 1


# ── the answers must not change ──────────────────────────────────────────────

def test_a_cipher_and_the_per_value_helpers_agree(session):
    """One implementation, two entry points: whatever ``cipher_for`` produces,
    ``decrypt_for`` must read, and the reverse."""
    cipher = keys.cipher_for(session, "org1")
    assert keys.decrypt_for(session, "org1", cipher.encrypt("Acme")) == "Acme"
    assert cipher.decrypt(keys.encrypt_for(session, "org1", "Acme")) == "Acme"


def test_one_tenant_s_cipher_cannot_read_another_s(session):
    """The whole point of a key per tenant."""
    ciphertext = keys.cipher_for(session, "org1").encrypt("Acme Precision")
    with pytest.raises(keys.KeyUnavailable):
        keys.cipher_for(session, "org2").decrypt(ciphertext)


def test_a_destroyed_key_is_refused_when_the_cipher_is_opened(session):
    """Not part-way through a page. A batch that starts decrypting and fails on
    row 40 has already answered 39 rows it should not have."""
    keys.cipher_for(session, "org1")           # make the key exist
    keys.destroy(session, "org1", reason="test", actor_user_id=None)
    session.commit()

    with pytest.raises(keys.KeyDestroyed):
        keys.cipher_for(session, "org1")


def test_names_fall_back_to_pseudonyms_after_the_key_is_destroyed(session):
    """Crypto-shredding is the product promise, and it is what the erasure
    receipt attests. Nothing here may keep a destroyed key usable — which is
    why the cipher is opened per batch and never cached."""
    ids = _names(session, "org1", 5)
    assert vault.resolve_many(session, "org1", "CUSTOMER", ids)[ids[0]] \
        == "Customer 0 Pvt Ltd"

    keys.destroy(session, "org1", reason="erasure", actor_user_id=None)
    session.commit()

    out = vault.resolve_many(session, "org1", "CUSTOMER", ids)
    expected = {i: pseudonym.label_for("org1", "CUSTOMER", i) for i in ids}
    assert out == expected, "a destroyed key must leave pseudonyms, not names"
    assert vault.resolve(session, "org1", "CUSTOMER", ids[0]) == expected[ids[0]]


def test_a_destroyed_key_reads_as_destroyed_in_a_payload_page(session):
    """The two unreadable states stay distinguishable in the batch path — one
    is a finished promise, the other an operational fault."""
    cipher = keys.cipher_for(session, "org1")
    row = models.ModelPayload(
        organization_id="org1", decision_type="TEST", provider="mock",
        model="mock-1", payload_ciphertext=cipher.encrypt("secret"))
    session.add(row)
    session.commit()

    keys.destroy(session, "org1", reason="erasure", actor_user_id=None)
    session.commit()

    assert disclosure.reveal_many(session, [row])[row.payload_id] \
        == disclosure.DESTROYED_NOTE


def test_a_page_spanning_two_tenants_opens_each_key(engine, session):
    """Nothing promises a page holds one organization, so the batch must not
    assume it — decrypting org2's rows under org1's key would answer
    'unreadable' for perfectly readable data."""
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    other = maker()
    rows = []
    for org in ("org1", "org2"):
        cipher = keys.cipher_for(session, org)
        row = models.ModelPayload(
            organization_id=org, decision_type="TEST", provider="mock",
            model="mock-1", payload_ciphertext=cipher.encrypt(f"{org} payload"))
        session.add(row)
        rows.append(row)
    session.commit()

    out = disclosure.reveal_many(other, rows)
    assert out[rows[0].payload_id] == "org1 payload"
    assert out[rows[1].payload_id] == "org2 payload"
    other.close()
