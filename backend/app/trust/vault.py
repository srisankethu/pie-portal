"""The name vault — display names, encrypted under the tenant's own data key.

Customer and item names are the only genuinely identifying data this platform
holds. Everything else is quantities, prices and dates, which are commercially
sensitive but not *identifying*: a table of margins with pseudonymous labels
tells a thief what a distributor's economics look like and not who they trade
with. Separating the names is therefore the single highest-value split
available, and it is cheap because nothing computes with them.

The vault is the authority for a display name. It is encrypted under the
tenant's DEK, so destroying that key takes the names with it — which is what
makes the erasure receipt meaningful rather than decorative.

**Known limitation, stated rather than hidden.** ``customers.name`` and
``products.name`` still hold plaintext, as a display cache for the many read
paths that join them. The vault is populated alongside and is authoritative;
the AI boundary is already pseudonymous, which is where the leak actually
mattered. Removing the plaintext columns is a follow-on migration touching every
read path, and doing it in the same change as introducing the vault would have
meant one commit that both adds a mechanism and rewrites its callers.

The same limitation is stated where it matters most: the signed erasure receipt
enumerates these columns (and the other plaintext survivors) under
``survives_plaintext`` — see ``trust/erasure.SURVIVES_PLAINTEXT``. If a
plaintext column here is ever encrypted or dropped, that list is where the
change must also land.
"""
from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain import models
from . import keys
from .pseudonym import label_for


def _get(session: Session, organization_id: str, entity_type: str,
         entity_id: str) -> Optional[models.NameVaultEntry]:
    return session.scalar(
        select(models.NameVaultEntry).where(
            models.NameVaultEntry.organization_id == organization_id,
            models.NameVaultEntry.entity_type == entity_type.upper(),
            models.NameVaultEntry.entity_id == entity_id))


def put(session: Session, organization_id: str, entity_type: str,
        entity_id: str, name: str,
        cipher: Optional[keys.TenantCipher] = None) -> None:
    """Store (or update) a display name. Idempotent, and safe concurrently.

    Read-then-insert is a race, and this is called from the pull — which now
    runs several connected companies at once. Two pulls vaulting the same entity
    together both saw no row, both inserted, and one died on
    ``UNIQUE constraint failed: name_vault.organization_id, ...``, taking its
    whole sync with it.

    The insert goes through a SAVEPOINT so losing that race costs one statement
    rather than the caller's entire transaction, and the loser then updates the
    winner's row — which is what it would have done had it read a moment later.
    """
    if not (name or "").strip():
        return
    # ``cipher`` when the caller is vaulting a batch: opening the key is the
    # expensive half of this function and it is the same key for every row.
    cipher = cipher or keys.cipher_for(session, organization_id)
    ciphertext = cipher.encrypt(name.strip())
    row = _get(session, organization_id, entity_type, entity_id)
    if row is not None:
        row.name_ciphertext = ciphertext
        session.flush()
        return

    fresh = models.NameVaultEntry(
        organization_id=organization_id, entity_type=entity_type.upper(),
        entity_id=entity_id, name_ciphertext=ciphertext)
    try:
        with session.begin_nested():
            session.add(fresh)
            session.flush()
    except IntegrityError:
        if fresh in session:
            session.expunge(fresh)
        winner = _get(session, organization_id, entity_type, entity_id)
        if winner is None:
            raise
        winner.name_ciphertext = ciphertext
        session.flush()


def resolve(session: Session, organization_id: str, entity_type: str,
            entity_id: str) -> str:
    """The display name, or the pseudonym if it cannot be produced.

    Falling back to the pseudonym rather than raising is deliberate: after a
    key destruction the name is *gone*, and a screen that cannot render a name
    should say "Customer C-9F42A1", not fail. An erased tenant's audit trail
    stays legible without becoming re-identifying.
    """
    fallback = label_for(organization_id, entity_type, entity_id)
    row = _get(session, organization_id, entity_type, entity_id)
    if row is None:
        return fallback
    try:
        return keys.decrypt_for(session, organization_id, row.name_ciphertext)
    except (keys.KeyDestroyed, keys.KeyUnavailable):
        return fallback


def resolve_many(session: Session, organization_id: str, entity_type: str,
                 entity_ids: Iterable[str]) -> dict[str, str]:
    """Names for a page of rows in one pass, rather than per row."""
    ids = list(dict.fromkeys(entity_ids))
    if not ids:
        return {}
    out = {i: label_for(organization_id, entity_type, i) for i in ids}
    rows = session.scalars(
        select(models.NameVaultEntry).where(
            models.NameVaultEntry.organization_id == organization_id,
            models.NameVaultEntry.entity_type == entity_type.upper(),
            models.NameVaultEntry.entity_id.in_(ids))).all()
    # One key for the page, not one per name. This loop used to call
    # ``decrypt_for`` per row, and that reads the key row and unwraps the DEK
    # every time — a SQL statement and a KEK decryption per customer on screen.
    try:
        cipher = keys.cipher_for(session, organization_id)
    except (keys.KeyDestroyed, keys.KeyUnavailable):
        return out       # every row here is unreadable; pseudonyms stand
    for row in rows:
        try:
            out[row.entity_id] = cipher.decrypt(row.name_ciphertext)
        except keys.KeyUnavailable:
            continue     # this one value will not open; the rest may
    return out


def backfill(session: Session, organization_id: str) -> dict[str, int]:
    """Populate the vault from the plaintext display columns.

    Safe to re-run. Called at the end of a sync so a name changed in the ERP
    reaches the vault without a separate job.
    """
    counts = {"CUSTOMER": 0, "PRODUCT": 0, "VENDOR": 0}
    # The whole book's names go under one key, so the key is opened once. This
    # runs at the end of every sync, over every customer, product and vendor:
    # per-row it was a statement and an unwrap per name, which is the sync's
    # own tail spent on nothing.
    cipher = keys.cipher_for(session, organization_id)
    for kind, model, id_attr in (
        ("CUSTOMER", models.Customer, "customer_id"),
        ("PRODUCT", models.Product, "product_id"),
        # Suppliers, for the same reason as customers: a supplier's name is
        # identifying and nothing computes with it. Their absence here was the
        # reason a destroyed key left a readable list of who this book buys
        # from.
        ("VENDOR", models.Vendor, "vendor_id"),
    ):
        rows = session.scalars(
            select(model).where(model.organization_id == organization_id)).all()
        for row in rows:
            name = getattr(row, "name", "") or ""
            if name.strip():
                put(session, organization_id, kind, getattr(row, id_attr), name,
                    cipher=cipher)
                counts[kind] += 1
    return counts
