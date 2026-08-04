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
"""
from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy import select
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
        entity_id: str, name: str) -> None:
    """Store (or update) a display name. Idempotent."""
    if not (name or "").strip():
        return
    row = _get(session, organization_id, entity_type, entity_id)
    ciphertext = keys.encrypt_for(session, organization_id, name.strip())
    if row is None:
        session.add(models.NameVaultEntry(
            organization_id=organization_id, entity_type=entity_type.upper(),
            entity_id=entity_id, name_ciphertext=ciphertext))
    else:
        row.name_ciphertext = ciphertext
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
    for row in rows:
        try:
            out[row.entity_id] = keys.decrypt_for(
                session, organization_id, row.name_ciphertext)
        except (keys.KeyDestroyed, keys.KeyUnavailable):
            break        # one destroyed key means every row here is unreadable
    return out


def backfill(session: Session, organization_id: str) -> dict[str, int]:
    """Populate the vault from the plaintext display columns.

    Safe to re-run. Called at the end of a sync so a name changed in the ERP
    reaches the vault without a separate job.
    """
    counts = {"CUSTOMER": 0, "PRODUCT": 0}
    for kind, model, id_attr in (
        ("CUSTOMER", models.Customer, "customer_id"),
        ("PRODUCT", models.Product, "product_id"),
    ):
        rows = session.scalars(
            select(model).where(model.organization_id == organization_id)).all()
        for row in rows:
            name = getattr(row, "name", "") or ""
            if name.strip():
                put(session, organization_id, kind, getattr(row, id_attr), name)
                counts[kind] += 1
    return counts
