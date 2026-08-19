"""The second producer: Business State → Decision, deterministically.

``DecisionService`` turns a ``Signal`` into a ``Decision`` by way of the AI
layer. This turns a state ``OpportunityDraft`` into the same ``Decision``
without one — same table, same lifecycle, same queue, same role scoping, same
audit trail. Everything downstream of a decision already works and is reused
unchanged; only the producer is new.

**No AI, by construction rather than by convention.** This module does not
import ``ai/``, so ``priority_ai_adjustment`` stays 0 and ``ai.status`` is
``NOT_APPLICABLE``. A state decision's score is its money, and a reader can
recompute it from the evidence on its own card.

**Idempotent on the fold, not on the week.** A signal decision's key buckets by
ISO week, because a detector firing twice in a week is one situation. A state
decision keys on (type, subject) alone: the fold is rebuilt at the end of every
sync, and a re-fold of an unchanged shelf must update the row it already made
rather than open a second one. What changes between folds is the *impact*, and
that is exactly what wants updating.

**A situation that has gone away is resolved, not left open.** An item that
sells is no longer dead stock, and a queue that still shows it is a queue
somebody has to hand-clean. Rows this producer made that no detector still
produces are closed as RESOLVED — never dismissed, which is a person's verb.
"""
from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import now as utc_now
from ..commercial.config import CommercialThresholds
from ..domain import models
from ..domain.enums import (AiStatus, DecisionOrigin, DecisionStatus, Role,
                            STATE_DECISION_TYPES)
from ..repositories import DecisionRepository
from ..state import queue
from ..state.engine import latest_as_of, load
from ..state.opportunities import DETECTORS, DecisionPolicy, OpportunityDraft

#: Statuses a person has already spoken about. A re-fold refreshes the numbers
#: on these but never reopens them: dismissing a decision and having it come
#: back tomorrow is how a queue teaches people to ignore it.
_SPOKEN_FOR = frozenset({
    DecisionStatus.DISMISSED.value,
    DecisionStatus.ACTIONED.value,
    DecisionStatus.OVERRIDDEN.value,
    DecisionStatus.RESOLVED.value,
})


def policy_from(th: CommercialThresholds) -> DecisionPolicy:
    """Lift the thresholds a detector reads out of the commercial policy.

    Built here, at the seam, exactly like ``stock.Carrying`` is built at the
    router — so ``state/`` never imports ``commercial/`` and the dependency
    arrow between them keeps pointing one way.

    ``min_impact`` reuses ``min_material_gap`` rather than adding a second
    materiality constant meaning the same thing: "worth a person's attention"
    and "a material gap" are one policy, and two of them would drift.
    """
    return DecisionPolicy(
        dead_days=th.dead_stock_days,
        slow_days=th.slow_stock_days,
        carrying_annual_pct=Decimal(str(th.carrying_cost_annual_pct)),
        excess_cover_months=Decimal(str(th.excess_cover_months)),
        rupees_per_point=Decimal(str(th.decision_rupees_per_point)),
        min_impact=Decimal(str(th.min_material_gap)),
        exposure_share=Decimal(str(th.receivable_exposure_share)),
        supplier_share=Decimal(str(th.supplier_spend_share)),
        version=th.version,
    )


def _key(org: str, draft: OpportunityDraft) -> str:
    """One decision per (organization, type, subject). See the module docstring
    for why this does not bucket by time the way a signal decision does."""
    blob = f"{org}|{draft.decision_type}|{draft.subject_entity_id}".encode()
    return "dks_" + hashlib.sha256(blob).hexdigest()[:16]


def _open_state_decisions(session: Session, org: str) -> dict[str, models.Decision]:
    rows = session.scalars(
        select(models.Decision).where(
            models.Decision.organization_id == org,
            models.Decision.origin == DecisionOrigin.STATE.value)).all()
    return {d.decision_key: d for d in rows}


def generate_from_state(session: Session, org: str, *,
                        thresholds: CommercialThresholds,
                        as_of: Optional[date] = None) -> dict[str, Any]:
    """Fold state into decisions. Returns what it did.

    ``as_of`` selects which fold to read; the latest built one by default. A
    caller that passes a date is asking "what would the queue have said on that
    day", which the state series makes answerable and which nothing else in the
    platform can do.

    ``states_missing`` names the states this run could not read — always
    present, empty on a clean run. A book whose bills have not been pulled has
    no supplier fold on record, and that used to return ``None`` for the whole
    set and take every detector down with it: no decisions, no error, nothing
    on the run to say why. What a detector that could not run would have found
    is unknown, so the states it reads are named and the detectors that *can*
    see carry on.
    """
    report: dict[str, Any] = {"organization_id": org, "created": 0, "refreshed": 0,
                              "resolved": 0, "by_type": {}, "as_of": None,
                              "states_missing": []}
    if not DETECTORS:
        return report

    wanted_states = set().union(*(d.states for d in DETECTORS.values()))
    if as_of is not None:
        on, missing = as_of, []
    else:
        on, missing = _latest_common(session, org, wanted_states)
    report["states_missing"] = missing
    if on is None:
        # Not one state has been folded. Nothing to report and nothing to
        # invent — the Data screen already says a sync builds it, and
        # ``states_missing`` now says which states are waiting for one.
        return report
    report["as_of"] = on.isoformat()

    states = {name: load(session, org, name, on) for name in sorted(wanted_states)}
    policy = policy_from(thresholds)
    repo = DecisionRepository(session, org)
    existing = _open_state_decisions(session, org)
    seen: set[str] = set()

    for detector in DETECTORS.values():
        for draft in detector.detect(states, policy, on):
            key = _key(org, draft)
            seen.add(key)
            row = existing.get(key)
            if row is None:
                row = models.Decision(organization_id=org, decision_key=key)
                repo.add(row)
                report["created"] += 1
            else:
                report["refreshed"] += 1
                # A situation that recurs after somebody dealt with it is a new
                # conversation, so a spoken-for row reopens only when its
                # numbers move. Refreshing them in place is what keeps a
                # dismissed row honest without putting it back in the queue.
                if row.status not in _SPOKEN_FOR:
                    row.status = DecisionStatus.OPEN.value
            _apply(row, draft, policy, on)
            report["by_type"][draft.decision_type] = (
                report["by_type"].get(draft.decision_type, 0) + 1)

    # Situations that no longer exist. Resolved by the platform, which is a
    # different fact from a person dismissing them — and only ever rows this
    # producer made.
    #
    # Except where the state a card came from could not be read. A detector
    # that did not run produced nothing, and producing nothing is exactly what
    # a detector does when the situation has gone away — so sweeping on that
    # would tell a person a supplier concentration cleared itself while the
    # platform was not looking. Left as it stands until the state comes back.
    blind = {t for t, d in DETECTORS.items() if d.states & set(missing)}
    for key, row in existing.items():
        if key in seen or row.status in _SPOKEN_FOR or row.decision_type in blind:
            continue
        row.status = DecisionStatus.RESOLVED.value
        row.updated_at = utc_now()
        report["resolved"] += 1

    session.flush()
    return report


def _latest_common(session: Session, org: str, states: set[str],
                   ) -> tuple[Optional[date], list[str]]:
    """The most recent day every readable state was folded for, and the states
    with no fold on record at all.

    The minimum rather than the maximum: reading one state from Tuesday and
    another from Friday would produce a decision describing a business that
    never existed on either day.

    A state with no rows on any day is not that problem — it is a missing
    input, and it is missing for either of two reasons this cannot tell apart:
    the fold has never run for it, or it ran and found nothing. Treating the
    second as the first silenced the whole producer; treating the first as the
    second would have every card that state feeds swept away as resolved. So
    it is named, and the caller decides what may be concluded without it.
    """
    days = {name: latest_as_of(session, org, name) for name in sorted(states)}
    folded = [d for d in days.values() if d is not None]
    missing = [name for name, day in days.items() if day is None]
    return (min(folded) if folded else None), missing


def _apply(row: models.Decision, draft: OpportunityDraft,
           policy: DecisionPolicy, on: date) -> None:
    """Write a draft onto a decision row. Every field, every time.

    Assigning unconditionally rather than only-if-changed: a partially updated
    row would carry this fold's impact beside last fold's rationale, and the
    card would explain a number that is no longer there.
    """
    base = queue.score(draft, policy)
    row.decision_type = draft.decision_type
    row.origin = DecisionOrigin.STATE.value
    row.subject_entity_type = draft.subject_entity_type
    row.subject_entity_id = draft.subject_entity_id
    # Nobody owns an inventory or supplier decision personally. These carry
    # cost information and are management decisions; a salesperson's queue is
    # scoped by assignment and never sees them.
    row.assigned_user_id = None
    row.assigned_role = Role.SALES_MANAGER.value
    row.detected_at = row.detected_at or utc_now()
    row.signal_ids = []
    row.evidence_refs = []
    row.impact = draft.impact.to_dict()
    row.rationale = draft.rationale
    row.actions = list(draft.actions)
    row.state_keys = list(draft.state_keys)
    row.state_as_of = on
    # The ranking, shown working, so a card can justify its own position.
    row.confidence = {"evidence_sufficiency": "DETERMINISTIC",
                      "reasons": [], "ranking": queue.explain(draft, policy),
                      "evidence": draft.evidence,
                      "thresholds_version": policy.version}
    row.ai = {"status": AiStatus.NOT_APPLICABLE.value}
    row.priority_deterministic_base = base
    row.priority_ai_adjustment = 0
    row.priority_score = base
    row.priority_band = queue.band(base)
    if row.status is None:
        row.status = DecisionStatus.OPEN.value


#: Re-exported so a caller can assert this producer only ever writes its own
#: types, without importing the enum module directly.
STATE_TYPES = frozenset(t.value for t in STATE_DECISION_TYPES)
