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

             Key destruction reaches exactly the ciphertext written under the
             key, and this platform encrypts two field classes that way — the
             vaulted display names and the AI payload log (``DESTROYED``).
             Everything else, ``customers.name`` and ``products.name``
             included, was never encrypted and stays readable
             (``SURVIVES_PLAINTEXT``). The receipt enumerates both, because a
             receipt that says "crypto-shredding" and stops is a stronger
             claim than the mechanism behind it — the first customer security
             review to open ``vault.py`` would find the plaintext column and
             then distrust every other line on the trust screen.

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
#:
#: That reasoning is right and it used to have no teeth. This list was written
#: when the platform held customers, products and the analysis over them; the
#: whole supply and payables layer arrived afterwards and none of it was
#: decided about, so an export promising "everything this organization owns"
#: quietly omitted about half of it.
#: ``tests/decision_platform/test_trust_export_completeness.py`` now fails when
#: a tenant-scoped table appears in neither this list nor ``EXCLUDED``. The
#: decision stays a human one; only the *coverage* is automatic.
EXPORTED: tuple[tuple[str, Any], ...] = (
    # ── the read model ──────────────────────────────────────────────────────
    ("customers", models.Customer),
    ("products", models.Product),
    ("vendors", models.Vendor),
    ("sales_txns", models.SalesTxn),
    ("cost_records", models.CostRecord),
    ("customer_item_metrics", models.CustomerItemMetric),
    ("stock_snapshots", models.StockSnapshot),
    # Where the business trades from, and what each place held. Exported with
    # the rest of the read model: a branch is this tenant's own structure, and
    # a shelf history that could not say which shelf is a weaker record than the
    # one they gave us.
    ("locations", models.Location),
    ("stock_location_snapshots", models.StockLocationSnapshot),
    # ── the documents: what is owed, by whom, and what has settled ──────────
    ("bills", models.BillDoc),
    ("invoices", models.InvoiceDoc),
    ("purchase_orders", models.PurchaseOrderDoc),
    ("sales_orders", models.SalesOrderDoc),
    # Which orders each invoice billed against. Exported rather than excluded:
    # it is this customer's own trading record, and an export holding the orders
    # and the invoices but not the joins between them would hand back two lists
    # nobody can put back together — the many-to-many is precisely the part a
    # re-sync from a different system cannot rebuild.
    ("invoice_sales_orders", models.InvoiceSalesOrderLink),
    ("payment_receipts", models.PaymentReceipt),
    ("payment_applications", models.PaymentApplication),
    ("vendor_payments", models.VendorPaymentDoc),
    ("bill_payment_applications", models.BillPaymentApplication),
    # Credit given back, and which invoice each note was set against. Exported
    # rather than excluded for the same reason as the invoice it reduces: it is
    # this customer's own trading record, and an export that showed what they
    # were billed while withholding what was credited would overstate what they
    # were charged.
    ("credit_notes", models.CreditNoteDoc),
    ("credit_note_applications", models.CreditNoteApplication),
    # ── what a person typed, which no re-sync can rebuild ───────────────────
    #
    # The most important group here and the least obvious. Everything above is
    # recoverable from Zoho; these are judgements somebody made inside this
    # platform — a negotiated term Zoho's dropdown could not express, an item's
    # real category, a scheme slab, the approval policy. Leaving them out would
    # be the export that keeps you.
    ("vendor_targets", models.VendorTarget),
    ("vendor_scheme_slabs", models.VendorSchemeSlab),
    ("vendor_payment_terms", models.VendorPaymentTerm),
    ("vendor_msme_statuses", models.VendorMsmeStatus),
    ("item_category_overrides", models.ItemCategoryOverride),
    # The customer side of the same rule. Zoho holds no credit limit on a
    # contact, and its salesperson field is derived — the sync rewrites it from
    # whoever was on the last invoice. Both of these are the typed decision
    # beside that: the line somebody set, and the book somebody was given. A
    # departing customer whose export omitted them would get back every invoice
    # and no record of the terms they were actually traded on.
    ("customer_credit_limits", models.CustomerCreditLimit),
    ("customer_account_owners", models.CustomerAccountOwner),
    # Read off a published tender portal by hand and typed in. Nothing syncs
    # it, so an export without it hands back a book whose measured share of
    # wallet cannot be reconstructed — and the source URLs on these rows are
    # the only record of where those figures came from.
    ("tender_results", models.TenderResult),
    ("commercial_policies", models.CommercialPolicy),
    ("org_policies", models.OrgPolicy),
    ("identity_policies", models.IdentityPolicy),
    ("confirmed_code_mappings", models.ConfirmedCodeMapping),
    # Every document line a pull could not fully resolve — the part number as
    # written on the bill, the document it was on, the supplier and the value.
    # Exported rather than excluded with its parent `sync_runs`, and the split
    # is the point: the run row is our own plumbing ("how did the pull go"),
    # while these rows are the customer's own trading record with a note saying
    # what the item master was missing. It is also the one table that says which
    # of their documents are not fully represented in the read model, which is
    # exactly what somebody taking their data elsewhere needs to know.
    ("sync_skipped_rows", models.SyncSkip),
    # ── what the platform decided, and what a human did about it ────────────
    ("signals", models.Signal),
    ("decisions", models.Decision),
    ("approval_requests", models.ApprovalRequest),
    ("quote_drafts", models.QuoteDraft),
    ("quote_decisions", models.QuoteDecision),
    ("quote_outcomes", models.QuoteOutcome),
    ("outcomes", models.Outcome),
    # The baseline frozen when a recommendation was accepted. Exported rather
    # than excluded as "derived": the whole point of the capture is that a
    # re-sync *cannot* rebuild what the evidence looked like at the moment of
    # acceptance, so this table is the only record of what a human said yes to.
    ("outcome_snapshots", models.OutcomeSnapshot),
    # What the platform was measured to be worth, and what the business looked
    # like before it. Exported alongside the quote decisions they are computed
    # from, and deliberately not excluded as "derived": a value event carries
    # the operands and the evidence refs behind each amount, so it is the only
    # record of *why* a figure was claimed — and a departing customer arguing
    # about what they were charged for wants exactly that. The baseline is the
    # same rows read once, and an export holding the outcome without the
    # starting point hands back a comparison with one side missing.
    ("value_events", models.ValueEvent),
    ("evaluation_baselines", models.EvaluationBaseline),
    # ── the identity graph ──────────────────────────────────────────────────
    ("customer_identities", models.CustomerIdentity),
    ("customer_connector_records", models.CustomerConnectorRecord),
    ("item_identities", models.ItemIdentity),
    ("item_connector_records", models.ItemConnectorRecord),
    ("identity_suggestions", models.IdentitySuggestion),
    ("identity_events", models.IdentityEvent),
    # ── the record of what we did with their data ───────────────────────────
    #
    # Small, and exactly what a departing customer wants: who reached in, when
    # and why; what was sent to a model and how each call went. A trust surface
    # that is visible while you are a customer and gone the moment you leave is
    # a trust surface with an expiry date on it.
    ("ai_call_logs", models.AiCallLog),
    ("access_grants", models.AccessGrant),
    ("access_events", models.AccessEvent),
    ("erasure_receipts", models.ErasureReceipt),
    # What they asked us for, and what we did about it.
    #
    # Exported rather than excluded, and the neighbouring judgement is the one
    # worth reading against: ``intelligence_trials`` sits in EXCLUDED because it
    # is licensing bookkeeping with "no fact about your business in it". That
    # sentence would be false here. A plan request carries a free-text note the
    # owner wrote — "three companies, need it before the quarter" — which is
    # their own words about their own business, and an exclusion note claiming
    # otherwise would be untrue for every request that carries one.
    #
    # Small, and it is also the record of an ask this platform may not have
    # answered. A departing customer is entitled to the evidence of what they
    # requested and when, particularly where that is part of why they are
    # leaving.
    ("plan_change_requests", models.PlanChangeRequest),
)

#: Never exported, and each one has a reason a customer can read. Keyed by
#: table name so ``EXCLUDED`` stays the flat tuple its callers expect while the
#: justification travels with it — an exclusion nobody can explain is one
#: nobody should trust.
EXCLUDED_REASONS: dict[str, str] = {
    "zoho_credentials": (
        "Your ERP credentials are yours to rotate at the source, and exporting "
        "them would put live secrets in a file that travels by email."),
    "zoho_connections": (
        "Holds the same credentials, encrypted. Same reason."),
    "ai_provider_keys": (
        "Your AI provider API keys, encrypted. Yours to rotate at the "
        "provider, and exporting them would put live secrets in a file that "
        "travels by email."),
    "users": (
        "Staff accounts and password hashes. Yours to administer, and not "
        "something a data export should carry."),
    "user_sessions": (
        "Who is currently signed in, and from what. Each row's id is the "
        "credential the session rides on, so exporting the table would put "
        "live sign-ins in a file that travels by email — the same reason the "
        "ERP credentials above are withheld. Ending them is a button in the "
        "app, not a download."),
    "tenant_keys": (
        "Your data key, wrapped by our master key. Exporting it would export "
        "nothing usable and weaken the thing that makes erasure provable."),
    "model_payloads": (
        "Stored encrypted under your data key. The readable version is served "
        "decrypted by /trust/payloads, which is where to take it from."),
    "name_vault": (
        "Encrypted display names. The plaintext of every one of them is "
        "already in customers, products and vendors above; a second, "
        "undecryptable copy would be noise."),
    "organizations": (
        "Your organization's own row is the header of this export rather than "
        "a table inside it."),
    "business_events": (
        "The append-only reading of every document, at line grain. Derived, "
        "not canonical — Zoho is the system of record and a complete re-sync "
        "rebuilds all of it — and two years of it would be a download in the "
        "hundreds of megabytes. The documents it was read from are exported "
        "above."),
    "business_states": (
        "Derived from business_events by replay, and rebuilt with them."),
    "state_transitions": (
        "Derived from business_events by replay, and rebuilt with them."),
    "sync_runs": (
        "Our own record of how each pull went. Operational plumbing with no "
        "fact about your business in it."),
    "sync_run_logs": (
        "The lines each pull wrote as it ran — phases, warnings, tracebacks. "
        "Our own diagnostics, kept so a failed sync can be explained to you. "
        "Not excluded because it is sensitive: it is ours, not yours, and a "
        "file of our stack traces is not part of your data. `sync_skipped_rows` "
        "is the half of a pull that *is* your trading record, and that one is "
        "exported."),
    "ingested_documents": (
        "Which document was last read at which timestamp — the bookkeeping "
        "that makes a resumed sync cheap. No business content."),
    "intelligence_trials": (
        "Whether your books have used their free month of Commercial "
        "Intelligence — our licensing bookkeeping, with no fact about your "
        "business in it beyond the connection date you already have."),
}

EXCLUDED = tuple(EXCLUDED_REASONS)

#: Every tenant-scoped table, exported or not. The manifest is a different
#: question from the export and needs a different list: the export says what
#: you may take, the receipt says what *existed* when the key was destroyed.
#: A receipt built from ``EXPORTED`` would silently under-report precisely the
#: tables somebody decided not to hand over — which is the half a customer
#: verifying an erasure would most want counted.
MANIFESTED: tuple[tuple[str, Any], ...] = EXPORTED + (
    ("name_vault", models.NameVaultEntry),
    ("model_payloads", models.ModelPayload),
    ("organizations", models.Organization),
    ("business_events", models.BusinessEvent),
    ("business_states", models.BusinessState),
    ("state_transitions", models.StateTransition),
    ("sync_runs", models.SyncRun),
    ("sync_run_logs", models.SyncRunLog),
    ("ingested_documents", models.IngestedDocument),
    ("zoho_connections", models.ZohoConnection),
    ("ai_provider_keys", models.AIProviderKey),
    ("intelligence_trials", models.IntelligenceTrial),
    ("tenant_keys", models.TenantKey),
    ("users", models.User),
    ("user_sessions", models.UserSession),
)

# ── what the receipt attests ────────────────────────────────────────────────
#
# The receipt used to say ``method: "data key destroyed (crypto-shredding)"``
# and nothing else, which read as "your data is gone" while ``vault.py``
# conceded in its own docstring that ``customers.name`` and ``products.name``
# sit in plaintext two tables away. A signed document that overstates what an
# operation did is worse than no document: the reviewer who finds the plaintext
# column — minutes of work — then distrusts every honest claim beside it.
#
# So the receipt enumerates both halves. ``DESTROYED`` is what key destruction
# reached; ``SURVIVES_PLAINTEXT`` is what it could not touch and why each item
# is in the clear. Making these lists shorter is an encryption project
# (see ``vault.py`` on why the plaintext display columns exist), not a wording
# choice — do not trim an entry unless the column itself is gone or encrypted.
#
# Both lists are stamped onto the receipt row at erase time and covered by its
# signature, not re-read from these constants at display time: what an erasure
# did is a fact about that moment, and a receipt rebuilt from today's code
# would either change its story or stop verifying the day either list is
# edited.

RECEIPT_METHOD = (
    "tenant data key (DEK) destroyed; ciphertext written under it is "
    "permanently unreadable, in live tables, replicas and backups alike — "
    "plaintext columns are NOT affected and are listed under "
    "survives_plaintext")

#: The complete set of field classes encrypted under the tenant DEK — which is
#: exactly what destroying the key unreads. Two entries because exactly two
#: call sites use ``keys.encrypt_for``: ``vault.put`` and ``disclosure.record``.
#: A new field class encrypted under the DEK must be added here in the same
#: change, or every later receipt under-reports what its erasure destroyed.
DESTROYED: tuple[dict[str, str], ...] = (
    {"table": "name_vault", "column": "name_ciphertext",
     "holds": "customer, product and supplier display names — the vault's "
              "authoritative copy"},
    {"table": "model_payloads", "column": "payload_ciphertext",
     "holds": "the exact text of every payload sent to an AI provider about "
              "this organization"},
)

#: What key destruction does not reach: columns held in plaintext, each with
#: the reason it is in the clear. These rows remain readable after erasure and
#: are removed only by row deletion, which does not reach backups. Honest and
#: uncomfortable by design — see the block comment above.
SURVIVES_PLAINTEXT: tuple[dict[str, str], ...] = (
    {"table": "customers", "column": "name",
     "why": "plaintext display cache for the many read paths that join it; "
            "the vault's encrypted copy is destroyed, this one is not"},
    {"table": "products", "column": "name",
     "why": "plaintext display cache, same as customers.name"},
    {"table": "vendors", "column": "name",
     "why": "plaintext display cache, same as customers.name"},
    {"table": "vendors", "column": "gstin, pan",
     "why": "government identifiers, stored plaintext"},
    {"table": "customer_connector_records", "column": "name, gstin",
     "why": "identity-matching keys — gstin is plaintext and indexed because "
            "linking matches on it"},
    {"table": "item_connector_records", "column": "sku, description",
     "why": "item text as each source system holds it, used for matching"},
    {"table": "locations", "column": "name, tax_reg_no",
     "why": "branch names and their registered GSTINs"},
    {"table": "organizations", "column": "name",
     "why": "the tenant's own name, which also heads this receipt"},
    {"table": "users", "column": "name, email",
     "why": "staff account identities, needed to keep the audit trail "
            "attributable"},
    {"table": "every transactional table", "column":
     "quantities, prices, dates, document and reference numbers",
     "why": "the analytical layer computes on plaintext rows by design; only "
            "identity was split out and encrypted, never the economics"},
)


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
        # One reason per exclusion rather than one sentence about credentials.
        # The old note explained the three things a reader would have guessed
        # and said nothing about the rest, which is how a list grows entries
        # nobody can account for.
        "excluded_note": dict(EXCLUDED_REASONS),
        "counts": {name: len(rows) for name, rows in data.items()},
        "data": data,
    }


# ── the receipt ─────────────────────────────────────────────────────────────
def _manifest(session: Session, organization_id: str) -> dict[str, int]:
    """What existed at the moment of erasure, per table.

    Built from ``MANIFESTED`` — every tenant-scoped table — and not from the
    export list. The two used to be the same list plus two hand-added counts,
    which meant the receipt could only ever attest to the subset somebody had
    remembered to make exportable.
    """
    return {
        name: int(session.scalar(
            select(func.count()).select_from(model)
            .where(model.organization_id == organization_id)) or 0)
        for name, model in MANIFESTED
    }


def _sign(body: dict[str, Any]) -> str:
    blob = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(settings.CREDENTIAL_ENCRYPTION_KEY.encode(),
                    blob, hashlib.sha256).hexdigest()


def erase(session: Session, organization_id: str, *, reason: str,
          actor_user_id: Optional[str]) -> models.ErasureReceipt:
    """Destroy the tenant's data key and issue a signed receipt.

    Irreversible. Every row encrypted under the key stays where it is and
    becomes unreadable — the only form of deletion that also reaches the
    backups. The receipt says exactly that and no more: what the key loss
    destroyed (``DESTROYED``) and what remains readable in plaintext
    (``SURVIVES_PLAINTEXT``), both stamped onto the row and covered by the
    signature.
    """
    manifest = _manifest(session, organization_id)
    keys.destroy(session, organization_id, reason=reason, actor_user_id=actor_user_id)

    attestation = {
        "method": RECEIPT_METHOD,
        "destroyed": [dict(entry) for entry in DESTROYED],
        "survives_plaintext": [dict(entry) for entry in SURVIVES_PLAINTEXT],
    }
    body = {
        "organization_id": organization_id,
        "erased_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason.strip(),
        "actor_user_id": actor_user_id,
        "manifest": manifest,
        **attestation,
    }
    row = models.ErasureReceipt(
        organization_id=organization_id,
        manifest=manifest,
        attestation=attestation,
        reason=body["reason"],
        actor_user_id=actor_user_id,
        erased_at=datetime.fromisoformat(body["erased_at"]),
        signature=_sign(body),
    )
    session.add(row)
    session.flush()
    return row


def receipt_body(row: models.ErasureReceipt) -> dict[str, Any]:
    """The exact structure the signature covers — used to re-verify.

    The attestation is read from the row, never rebuilt from the module
    constants: a receipt is a record of what was claimed when the key was
    destroyed, and one that re-read today's ``SURVIVES_PLAINTEXT`` would either
    change its story or fail verification the day that list is edited.
    """
    attestation = row.attestation or {}
    return {
        "organization_id": row.organization_id,
        "erased_at": clock.iso(row.erased_at),
        "reason": row.reason,
        "actor_user_id": row.actor_user_id,
        "manifest": row.manifest,
        "method": attestation.get("method", ""),
        "destroyed": attestation.get("destroyed", []),
        "survives_plaintext": attestation.get("survives_plaintext", []),
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
