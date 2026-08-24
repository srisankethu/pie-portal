"""What reaches a model — declared as a schema, logged per call, and checkable.

"We only send aggregated facts" is a claim. A claim a customer cannot test is
worth nothing in a procurement conversation and, worse, tends to drift: someone
adds a field to a bundle a year later and the sentence on the website quietly
becomes false with no test failing.

So the statement is data. ``ALLOWED`` is the published contract — the complete
set of things permitted to cross to a provider. ``check`` measures a real
payload against it and names anything outside. The API serves the same constant
the checker uses, so the published statement and the enforced one cannot
disagree; and the AI contract tests run ``check`` over real bundles, so a new
field that leaks a name fails the suite rather than shipping.

The payload log is the second half. ``AiCallLog`` already records how a call
went — status, latency, tokens — and deliberately never held content, which is
right for operational telemetry and leaves the customer unable to see what was
actually said about them. ``ModelPayload`` holds the exact text sent, keyed to
the call, so "show me everything you sent to an AI about my business" has a
real answer. It is encrypted under the tenant's own data key, because a table
of everything-we-ever-sent is exactly the table an attacker would want.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..domain import models
from . import keys

#: The published statement. Every category permitted to reach a model provider,
#: with the reason it is needed — the reason is part of the contract, because a
#: field nobody can justify is a field that should not be sent.
ALLOWED: tuple[dict[str, str], ...] = (
    {"category": "Pseudonymous entity label",
     "example": "Customer C-9F42A1",
     "why": "The model must be able to refer to the subject in a sentence. The "
            "real name is never sent; it is held in the tenant's own vault."},
    {"category": "Computed metric values",
     "example": "margin_recent: 0.19",
     "why": "The figures the sentence is about. Computed deterministically "
            "before the call; the model reads them and never produces one."},
    {"category": "Signal type and severity",
     "example": "MARGIN_DETERIORATION, severity 3",
     "why": "What kind of situation is being described."},
    {"category": "Evidence sufficiency and unknowns",
     "example": "PARTIAL — cost coverage 0.4",
     "why": "So the wording can hedge where the evidence is thin instead of "
            "asserting through a gap."},
    {"category": "Policy names and threshold version",
     "example": "ci_4f2a91c0de",
     "why": "So an interpretation can be reproduced against the exact policy "
            "that produced its numbers."},
    {"category": "Dates and counts",
     "example": "last_order_date, transaction_count",
     "why": "Recency and volume change what a figure means."},
)

#: Never sent, and each one is checked for rather than merely promised.
NEVER: tuple[str, ...] = (
    "Customer or item names, or any free text from the ERP",
    "GSTIN, PAN, tax registrations or any government identifier",
    "Contact names, email addresses, phone numbers or postal addresses",
    "Invoice, bill or document numbers",
    "Bank, payment or credential data",
    "Any data belonging to another tenant",
)

# ── leak detection ──────────────────────────────────────────────────────────
# Patterns for identifiers that must never appear in a payload. Shapes rather
# than a blocklist of values: a blocklist only catches the examples someone
# thought of, and the point is to catch the field nobody thought about.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GSTIN", re.compile(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]{3}\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")),
    ("email address", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
    ("phone number", re.compile(r"(?<![\w.])(?:\+\d{1,3}[ -]?)?\d{10}(?![\w.])")),
    ("IFSC code", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
)


def check(payload: str) -> list[str]:
    """Anything in this payload that the published statement forbids.

    Returns findings rather than raising: the caller decides whether a leak
    blocks the call or is recorded and reported. An empty list is the whole
    claim, and it is a claim a test can make.
    """
    found: list[str] = []
    for name, pattern in _PATTERNS:
        match = pattern.search(payload)
        if match:
            found.append(f"{name} present in payload: {match.group(0)[:4]}…")
    return found


def statement() -> dict[str, Any]:
    """The published disclosure, served from the same constants ``check`` uses."""
    return {
        "provider": settings.AI_PROVIDER,
        "model": settings.AI_MODEL,
        # A commitment, expressed as configuration so it can be read back
        # rather than taken on trust — and asserted by a test.
        "training_on_customer_data": False,
        "zero_retention_requested": settings.AI_ZERO_RETENTION,
        "allowed": [dict(a) for a in ALLOWED],
        "never_sent": list(NEVER),
        "notes": (
            "The model receives computed facts and produces language. It never "
            "computes a price, a margin or a recommendation — those come from "
            "the deterministic layer and are stamped with the policy version "
            "that produced them. When the provider is unavailable the platform "
            "degrades to deterministic output rather than failing."),
    }


#: Names shorter than this are not searched for. "ACE" or "TVS" occur inside
#: ordinary words and inside base64, and a checker that cries wolf on every
#: payload is one whose findings stop being read.
_MIN_NAME = 5

#: Bound on how many names are scanned, so the check stays cheap next to the
#: network round trip it accompanies.
_MAX_NAMES = 5000


def check_names(session: Session, organization_id: str, payload: str) -> list[str]:
    """Any of this tenant's real entity names appearing in the payload.

    The pattern checker above catches identifiers by shape. A company name has
    no shape, so the only way to catch one is to know it — which is possible
    here because we hold the list. This is what caught a detector embedding
    ``top_declining_products[0].label``: the subject was pseudonymous and the
    payload still carried a real item name.

    Reads the plaintext display columns rather than the vault: no decryption,
    one query, and it is the same set of names either way.

    ``Vendor`` is here alongside the other two, and was missing until the
    statutory-timing work started assembling supplier rows. A checker that knew
    two thirds of this tenant's names would have reported clean on a payload
    naming a supplier, which is the failure mode the whole function exists to
    close.
    """
    found: list[str] = []
    for model in (models.Customer, models.Product, models.Vendor):
        names = session.scalars(
            select(model.name).where(model.organization_id == organization_id)
            .limit(_MAX_NAMES)).all()
        for name in names:
            if name and len(name) >= _MIN_NAME and name in payload:
                found.append(f"entity name present in payload: {name!r}")
    return found


# ── the per-call payload log ────────────────────────────────────────────────
def record(session: Session, *, organization_id: str, ai_call_log_id: Optional[str],
           decision_type: str, system: str, user: str,
           provider: str, model: str) -> models.ModelPayload:
    """Store exactly what was sent, encrypted under the tenant's key."""
    payload = f"{system}\n\n---\n\n{user}"
    findings = check(payload) + check_names(session, organization_id, payload)
    row = models.ModelPayload(
        organization_id=organization_id,
        ai_call_log_id=ai_call_log_id,
        decision_type=decision_type,
        provider=provider,
        model=model,
        payload_ciphertext=keys.encrypt_for(session, organization_id, payload),
        disclosure_findings=findings,
    )
    session.add(row)
    session.flush()
    return row


def log_result(session: Session, *, organization_id: str, decision_type: str,
               result: Any,
               ai_call_log_id: Optional[str] = None
               ) -> Optional[models.ModelPayload]:
    """Record what an interpretation actually sent, if it sent anything.

    Takes the ``AIResult`` rather than the raw strings so both call sites — the
    proactive decision run and the quote drawer — share one rule about when a
    payload is worth storing. ``prompt_payload`` is None on every path that
    never reached a provider, and "nothing was sent" needs no row.

    Guarded by settings because this is a disclosure feature with a storage
    cost; a deployment that has not enabled it should not quietly accumulate a
    table of everything it ever sent.

    ``ai_call_log_id`` ties the payload to the call it belongs to, and used to
    be hard-coded ``None`` at both call sites — so the column existed, the
    foreign key existed, and every row was orphaned. What that cost is specific:
    ``AiCallLog`` holds the status, latency, tokens and cost of a call and
    deliberately holds no content; ``ModelPayload`` holds the content and none
    of the operational facts. Answering "what did we send on the call that
    failed" meant matching two tables on organization, decision type and a
    timestamp and hoping the run was quiet. Both callers now record telemetry
    first and pass the id.

    Still optional, and ``None`` is still a real state rather than a defect: a
    deployment with ``AI_TELEMETRY_ENABLED`` off writes no call log at all, and
    a payload is worth keeping even when the operational half was not.
    """
    payload = getattr(result, "prompt_payload", None)
    if not settings.AI_LOG_PAYLOADS or not payload:
        return None
    return record(
        session, organization_id=organization_id,
        ai_call_log_id=ai_call_log_id,
        decision_type=decision_type, system="", user=payload,
        provider=getattr(result, "provider", ""), model=getattr(result, "model", ""))


def payloads_for(session: Session, organization_id: str,
                 limit: int = 100) -> list[models.ModelPayload]:
    return list(session.scalars(
        select(models.ModelPayload)
        .where(models.ModelPayload.organization_id == organization_id)
        .order_by(models.ModelPayload.created_at.desc())
        .limit(limit)).all())


def reveal(session: Session, row: models.ModelPayload) -> str:
    """The plaintext of one logged payload, for the tenant that owns it.

    The two unreadable states are reported apart. Both look identical from
    here — ciphertext that will not open — and saying "destroyed" for either
    tells a customer their erasure completed when what actually happened is
    that a master key was rotated out from under a key row. One of those is a
    finished promise; the other is an operational fault with a remedy, and
    printing the first over the second is how a fault stays unreported.
    """
    try:
        return keys.decrypt_for(session, row.organization_id, row.payload_ciphertext)
    except keys.KeyDestroyed:
        return "(unavailable — this organization's data key has been destroyed)"
    except keys.KeyUnavailable:
        return ("(unavailable — this payload was encrypted under a data key "
                "that can no longer be read; the record survives, its contents "
                "do not)")


def findings_summary(rows: Iterable[models.ModelPayload]) -> dict[str, int]:
    """How many logged payloads tripped the checker. Should be zero; a non-zero
    number is a defect report, not a statistic to display and forget."""
    total = flagged = 0
    for row in rows:
        total += 1
        if row.disclosure_findings:
            flagged += 1
    return {"payloads": total, "flagged": flagged}
