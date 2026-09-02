"""The quote-level fields an organization asks for, and which are mandatory.

A quote used to be a customer and a list of lines. Every distributor's quote
also carries a handful of details the document needs — the customer's own
reference, how long the price holds, payment and delivery terms, a note — and
some businesses will not let one go out without them. This module is where
that is decided: the organization's list of fields (``definitions_for``), the
built-in handful it starts with, what a value of each kind must look like
(``normalise``), and which required fields a draft has not answered
(``missing_required``) — the last of which is what the send enforces and the
workspace list reports.

Deterministic and session-passing. Nothing here is a number the platform
computes about the business; it is the shape of a form and a rule about
completeness, so it sits outside ``commercial/`` and imports nothing from
``ai/``.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .domain import models

#: What a field can hold. ``MULTILINE`` is ``TEXT`` with a taller box; the
#: distinction is the screen's, kept here so the definition says how to render.
KINDS = ("TEXT", "MULTILINE", "NUMBER", "DATE", "CHOICE")

#: The fields every organization starts with. Not required by default: which
#: of them are mandatory is the organization's decision, and a platform that
#: refused every first quote for want of a delivery term would be switched off.
#: ``builtin`` rows cannot be removed — only hidden by ``active`` — because a
#: draft may already hold a value under the key.
BUILTIN: tuple[dict[str, Any], ...] = (
    {"key": "customer_reference", "label": "Customer reference", "kind": "TEXT"},
    {"key": "valid_until", "label": "Valid until", "kind": "DATE"},
    {"key": "payment_terms", "label": "Payment terms", "kind": "TEXT"},
    {"key": "delivery_terms", "label": "Delivery terms", "kind": "TEXT"},
    {"key": "notes", "label": "Notes to the customer", "kind": "MULTILINE"},
)

_KEY = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class FieldError(ValueError):
    """A definition or a value that cannot be accepted, with the reason."""


# ── definitions ──────────────────────────────────────────────────────────────
def definitions_for(session: Session, org: str, *,
                    active_only: bool = True) -> list[models.QuoteFieldDefinition]:
    """This organization's fields, in position order, seeded on first read.

    Seeding here rather than at organization creation means an organization
    that predates this table gets the built-in fields the first time anybody
    asks — the same first-read arrangement ``approvals.get_policy`` uses.
    """
    rows = _rows(session, org)
    if not any(r.builtin for r in rows):
        now = clock.now()
        for position, spec in enumerate(BUILTIN):
            session.add(models.QuoteFieldDefinition(
                organization_id=org, key=spec["key"], label=spec["label"],
                kind=spec["kind"], required=False, choices=[],
                position=position, builtin=True, active=True,
                created_at=now, updated_at=now))
        session.flush()
        rows = _rows(session, org)
    return [r for r in rows if r.active or not active_only]


def _rows(session: Session, org: str) -> list[models.QuoteFieldDefinition]:
    return list(session.scalars(
        select(models.QuoteFieldDefinition)
        .where(models.QuoteFieldDefinition.organization_id == org)
        .order_by(models.QuoteFieldDefinition.position,
                  models.QuoteFieldDefinition.created_at)))


def replace_definitions(session: Session, org: str,
                        specs: list[dict[str, Any]]) -> list[models.QuoteFieldDefinition]:
    """Make the organization's fields exactly ``specs``, in that order.

    Each spec: ``key`` (a slug; minted from the label when absent), ``label``,
    ``kind``, ``required``, ``choices``. A built-in field absent from the list
    is deactivated rather than deleted, and so is a custom field a draft may
    already hold a value for — the row keeps the label so the value stays
    readable. A new key becomes a new row. Refuses, naming the field, rather
    than storing a definition the builder could not render.
    """
    existing = {r.key: r for r in definitions_for(session, org, active_only=False)}
    seen: set[str] = set()
    now = clock.now()
    for position, spec in enumerate(specs):
        label = str(spec.get("label") or "").strip()
        if not label:
            raise FieldError("Every field needs a label.")
        key = str(spec.get("key") or slug(label)).strip()
        if not _KEY.match(key):
            raise FieldError(f"{label!r}: the key {key!r} must be lower-case letters, "
                             "digits and underscores, starting with a letter.")
        if key in seen:
            raise FieldError(f"Two fields share the key {key!r}.")
        seen.add(key)
        kind = str(spec.get("kind") or "TEXT").upper()
        if kind not in KINDS:
            raise FieldError(f"{label!r}: {kind!r} is not a kind of field "
                             f"({', '.join(KINDS)}).")
        choices = [str(c).strip() for c in (spec.get("choices") or []) if str(c).strip()]
        if kind == "CHOICE" and not choices:
            raise FieldError(f"{label!r}: a choice field needs at least one option.")
        row = existing.get(key)
        if row is None:
            row = models.QuoteFieldDefinition(
                organization_id=org, key=key, builtin=False, created_at=now)
            session.add(row)
            existing[key] = row
        elif row.builtin:
            # The kind of a built-in is what the platform reads it as — a date
            # for validity — so the label and the requirement move, the kind
            # does not.
            kind = row.kind
        row.label, row.kind, row.choices = label, kind, choices
        row.required = bool(spec.get("required"))
        row.position, row.active, row.updated_at = position, True, now
    for key, row in existing.items():
        if key not in seen and row.active:
            row.active, row.updated_at = False, now
    session.flush()
    return definitions_for(session, org)


def slug(label: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    key = key[:48] or "field"
    return key if key[0].isalpha() else f"f_{key}"[:48]


def to_dict(row: models.QuoteFieldDefinition) -> dict[str, Any]:
    return {"key": row.key, "label": row.label, "kind": row.kind,
            "required": bool(row.required), "choices": list(row.choices or []),
            "builtin": bool(row.builtin)}


# ── values ───────────────────────────────────────────────────────────────────
def normalise(defs: list[models.QuoteFieldDefinition],
              values: dict[str, Any]) -> dict[str, Any]:
    """The values a draft may store, checked against the definitions.

    Unknown keys are dropped — a value for a field the organization no longer
    has is not an error, it is stale. An empty string clears the field. A
    number must parse, a date must be ISO ``YYYY-MM-DD``, a choice must be one
    of the options; anything else refuses with the field's label.
    """
    by_key = {d.key: d for d in defs}
    out: dict[str, Any] = {}
    for key, raw in (values or {}).items():
        d = by_key.get(key)
        if d is None:
            continue
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        if d.kind == "NUMBER":
            try:
                out[key] = float(raw)
            except (TypeError, ValueError):
                raise FieldError(f"{d.label} must be a number.") from None
        elif d.kind == "DATE":
            try:
                out[key] = date.fromisoformat(str(raw).strip()).isoformat()
            except ValueError:
                raise FieldError(f"{d.label} must be a date (YYYY-MM-DD).") from None
        elif d.kind == "CHOICE":
            text = str(raw).strip()
            if text not in (d.choices or []):
                raise FieldError(
                    f"{d.label} must be one of: {', '.join(d.choices or [])}.")
            out[key] = text
        else:
            out[key] = str(raw).strip()
    return out


def missing_required(defs: list[models.QuoteFieldDefinition],
                     values: Optional[dict[str, Any]]) -> list[str]:
    """The labels of required fields this draft has not answered, in order."""
    held = values or {}
    return [d.label for d in defs
            if d.required and (held.get(d.key) is None
                               or (isinstance(held.get(d.key), str)
                                   and not str(held.get(d.key)).strip()))]
