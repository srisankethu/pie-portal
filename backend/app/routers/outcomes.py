"""Realised outcomes of accepted decisions — the Outcome Tracker, over HTTP.

Thin by rule: every number here was computed in ``commercial/outcome_tracker``
from persisted rows; this module maps the rows and enforces scope. A realised
margin summed or re-derived here would be a second owner for the one answer.

Role scope, server-side (§1 — absent from the response, not hidden):

- A salesperson sees only outcomes of decisions assigned to them, and never an
  outcome whose category is a RESTRICTED decision type — the same two rules the
  decision queue applies, because an outcome is that decision's afterlife and
  must not be a side door into it.
- Field-level, as defence in depth on what remains: any metric key
  ``context.assembler._is_restricted`` would redact from a fact list is omitted
  from a salesperson's baseline/realised/delta dicts before serialization.
  Today the surviving categories carry no such key; the filter is what keeps
  that true when a detector grows one.
- Management and owner responses carry everything, including the margin and
  cost economics the restricted categories embed.

Plan gating is not repeated here: ``main.py`` includes this router behind
``require_feature("intelligence")``, the same single declaration the decisions
and insight surfaces use.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..authz import Principal, current_principal
from ..commercial import outcome_tracker
from ..context.assembler import _is_restricted
from ..db import get_session
from ..domain import models
from ..domain.enums import RESTRICTED_DECISION_TYPES, OutcomeStatus

router = APIRouter(prefix="/api/v1/outcomes", tags=["outcomes"])


def _redact(metrics: dict[str, Any]) -> dict[str, Any]:
    """Restricted keys removed, recursively — omitted, never blanked.

    Recurses through lists as well as dicts: ``context/assembler`` flattens a
    fact list into ``path[i].key`` entries before checking, so a restricted
    key nested inside a list of dicts is redacted there — this filter must
    reach the same keys or its parity claim is false. A detector that grows a
    ``top_…: [{unit_cost: …}]`` breakdown must not become a leak here.
    """
    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items()
                    if not _is_restricted(k)}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    return scrub(metrics)


def _status_filter(raw: Optional[str]) -> Optional[OutcomeStatus]:
    """Validated rather than passed through: a typo that silently matched
    nothing would read as "no outcomes", the benign-default shape §1 forbids."""
    if raw is None:
        return None
    try:
        return OutcomeStatus(raw)
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown status {raw!r}. Expected one of: "
            + ", ".join(m.value for m in OutcomeStatus)) from None


@router.get("")
def list_outcomes(
    status_filter: Optional[str] = None,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    """Every visible outcome snapshot with its evaluation, computed on read.

    Evaluation is not stored, so this list can never disagree with the rows it
    is computed from — a re-sync that lands late invoices changes the answer
    here on the next request, which is the behaviour an evaluator over derived
    state must have.
    """
    wanted = _status_filter(status_filter)
    is_sales = principal.is_salesperson
    restricted = {t.value for t in RESTRICTED_DECISION_TYPES}

    stmt = (select(models.OutcomeSnapshot)
            .where(models.OutcomeSnapshot.organization_id
                   == principal.organization_id)
            .order_by(models.OutcomeSnapshot.accepted_at.desc()))
    snaps = list(session.scalars(stmt).all())

    if is_sales:
        snaps = [r for r in snaps if r.category not in restricted]
        if snaps:
            owned = set(session.scalars(
                select(models.Decision.decision_id).where(
                    models.Decision.organization_id == principal.organization_id,
                    models.Decision.decision_id.in_(
                        [r.decision_id for r in snaps]),
                    models.Decision.assigned_user_id == principal.user_id)))
            snaps = [r for r in snaps if r.decision_id in owned]

    # The business's current date, in the organization's own zone — the same
    # call every insight route makes. The zone also reaches evaluate(), where
    # the acceptance *day* and the horizon boundary are computed.
    tz = getattr(session.get(models.Organization,
                             principal.organization_id), "timezone", None)
    as_of = clock.today(tz)
    outcomes = []
    for snap in snaps:
        ev = outcome_tracker.evaluate(session, snap, as_of=as_of, tz=tz)
        if wanted is not None and ev.status is not wanted:
            continue
        evaluation = ev.to_dict()
        baseline = dict(snap.baseline_metrics or {})
        if is_sales:
            evaluation["realised"] = _redact(evaluation["realised"])
            evaluation["delta"] = _redact(evaluation["delta"])
            baseline = _redact(baseline)
        outcomes.append({
            "outcome_snapshot_id": snap.outcome_snapshot_id,
            "decision_id": snap.decision_id,
            "signal_id": snap.signal_id,
            "category": snap.category,
            "subject_entity_type": snap.subject_entity_type,
            "subject_entity_id": snap.subject_entity_id,
            "accepted_at": clock.iso(snap.accepted_at),
            "horizon_days": snap.horizon_days,
            "thresholds_version": snap.thresholds_version,
            "baseline_window": snap.baseline_window or {},
            "baseline_metrics": baseline,
            "evaluation": evaluation,
        })

    return {
        "as_of": as_of.isoformat(),
        "count": len(outcomes),
        "outcomes": outcomes,
        "caveats": [
            "An evaluation is computed from persisted rows on every read and "
            "is never stored; a sync that lands late documents changes it.",
            "PENDING asserts nothing in either direction — the horizon set at "
            "acceptance has not elapsed.",
            "UNKNOWN names exactly what is missing; it is never a zero and "
            "never sums with anything.",
            "One snapshot per decision, anchored at the first acceptance; "
            "re-accepting after an undo does not restart the horizon.",
        ],
    }
