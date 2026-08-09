"""Take everything with you, then have the deletion proved.

MSME buyers worry about lock-in more than about breaches and rarely say so out
loud. A one-click export plus a deletion that produces a receipt does more for
conversion than a security page, because it answers the unspoken question —
"what happens when I want to leave?" — before it has to be asked.

Two operations, in that order, and the order is enforced:

``export``   everything the tenant owns, as plain JSON. Includes the identity
             graph, which matters because the joined cross-connector view is
             the one thing PIE holds that no single source system does: leaving
             without it means losing something they cannot rebuild.

``erase``    destroy the tenant's data key and issue a signed receipt. The
             signature is over a manifest of what existed at that moment, so
             the receipt says *what* was destroyed rather than merely that
             something was. Row deletion alone could never be proved — backups
             are not selectively editable — so the proof is the key: the
             ciphertext survives wherever it survives and is inert.

The receipt is verifiable with ``verify_receipt`` and, deliberately, is itself
stored unencrypted. A receipt sealed under the key it certifies the destruction
of would be unreadable exactly when it is needed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings
from ..domain import models
from . import keys, vault

#: Tables that belong to a tenant, in the export. Explicit rather than derived
#: from the metadata: a new table must be a deliberate decision to include or
#: to leave out, and a silent "everything with an organization_id" would sweep
#: in credential ciphertext the moment someone adds a column.
EXPORTED: tuple[tuple[str, Any], ...] = (
    ("customers", models.Customer),
    ("products", models.Product),
    ("sales_txns", models.SalesTxn),
    ("cost_records", models.CostRecord),
    ("customer_item_metrics", models.CustomerItemMetric),
    ("signals", models.Signal),
    ("decisions", models.Decision),
    ("approval_requests", models.ApprovalRequest),
    ("quote_decisions", models.QuoteDecision),
    ("outcomes", models.Outcome),
    ("customer_identities", models.CustomerIdentity),
    ("customer_connector_records", models.CustomerConnectorRecord),
    ("item_identities", models.ItemIdentity),
    ("item_connector_records", models.ItemConnectorRecord),
    ("identity_events", models.IdentityEvent),
)

#: Never exported: it is either a secret of ours or a secret of theirs that a
#: JSON file has no business carrying.
EXCLUDED = ("zoho_credentials", "zoho_connections", "users", "tenant_keys",
            "model_payloads")


def _rows(session: Session, model, organization_id: str) -> list[dict[str, Any]]:
    out = []
    for row in session.scalars(
            select(model).where(model.organization_id == organization_id)).all():
        record = {}
        for column in row.__table__.columns:
            value = getattr(row, column.name)
            if isinstance(value, datetime):
                value = value.isoformat()
            elif hasattr(value, "isoformat"):
                value = value.isoformat()
            elif isinstance(value, (int, float, str, bool, type(None), dict, list)):
                pass
            else:
                value = str(value)          # Decimal and friends
            record[column.name] = value
        out.append(record)
    return out


def export(session: Session, organization_id: str) -> dict[str, Any]:
    """Everything this tenant owns, as JSON-safe structures."""
    org = session.get(models.Organization, organization_id)
    data = {name: _rows(session, model, organization_id)
            for name, model in EXPORTED}
    return {
        "organization": {
            "organization_id": organization_id,
            "name": getattr(org, "name", None),
            "currency": getattr(org, "currency", None),
        },
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "excluded": list(EXCLUDED),
        "excluded_note": (
            "Credentials and staff accounts are deliberately absent. Your ERP "
            "credentials are yours to rotate at the source, and exporting them "
            "would put live secrets in a file that travels by email."),
        "counts": {name: len(rows) for name, rows in data.items()},
        "data": data,
    }


# ── the receipt ─────────────────────────────────────────────────────────────
def _manifest(session: Session, organization_id: str) -> dict[str, int]:
    """What existed at the moment of erasure, per table."""
    counts = {}
    for name, model in EXPORTED:
        counts[name] = int(session.scalar(
            select(func.count()).select_from(model)
            .where(model.organization_id == organization_id)) or 0)
    counts["name_vault_entries"] = int(session.scalar(
        select(func.count()).select_from(models.NameVaultEntry)
        .where(models.NameVaultEntry.organization_id == organization_id)) or 0)
    counts["model_payloads"] = int(session.scalar(
        select(func.count()).select_from(models.ModelPayload)
        .where(models.ModelPayload.organization_id == organization_id)) or 0)
    return counts


def _sign(body: dict[str, Any]) -> str:
    blob = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(settings.CREDENTIAL_ENCRYPTION_KEY.encode(),
                    blob, hashlib.sha256).hexdigest()


def erase(session: Session, organization_id: str, *, reason: str,
          actor_user_id: Optional[str]) -> models.ErasureReceipt:
    """Destroy the tenant's data key and issue a signed receipt.

    Irreversible. The rows stay where they are and become unreadable, which is
    the only form of deletion that also reaches the backups.
    """
    manifest = _manifest(session, organization_id)
    keys.destroy(session, organization_id, reason=reason, actor_user_id=actor_user_id)

    body = {
        "organization_id": organization_id,
        "erased_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason.strip(),
        "actor_user_id": actor_user_id,
        "manifest": manifest,
        "method": "data key destroyed (crypto-shredding)",
    }
    row = models.ErasureReceipt(
        organization_id=organization_id,
        manifest=manifest,
        reason=body["reason"],
        actor_user_id=actor_user_id,
        erased_at=datetime.fromisoformat(body["erased_at"]),
        signature=_sign(body),
    )
    session.add(row)
    session.flush()
    return row


def receipt_body(row: models.ErasureReceipt) -> dict[str, Any]:
    """The exact structure the signature covers — used to re-verify."""
    return {
        "organization_id": row.organization_id,
        "erased_at": clock.iso(row.erased_at),
        "reason": row.reason,
        "actor_user_id": row.actor_user_id,
        "manifest": row.manifest,
        "method": "data key destroyed (crypto-shredding)",
    }


def verify_receipt(row: models.ErasureReceipt) -> bool:
    """True when the receipt has not been altered since it was issued."""
    return hmac.compare_digest(_sign(receipt_body(row)), row.signature or "")


def status(session: Session, organization_id: str) -> dict[str, Any]:
    """Whether this tenant has been erased, and the receipt if so."""
    row = session.scalar(
        select(models.ErasureReceipt)
        .where(models.ErasureReceipt.organization_id == organization_id)
        .order_by(models.ErasureReceipt.erased_at.desc()))
    if row is None:
        return {"erased": False, "receipt": None}
    return {
        "erased": True,
        "receipt": {**receipt_body(row), "signature": row.signature,
                    "verified": verify_receipt(row)},
    }


def vault_backfill(session: Session, organization_id: str) -> dict[str, int]:
    """Re-exported so callers have one import for tenant-lifecycle chores."""
    return vault.backfill(session, organization_id)
