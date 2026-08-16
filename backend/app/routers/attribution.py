"""What PIE changed — the value ledger, over HTTP.

Three reads and no arithmetic. Every number on this surface was computed in
``attribution/`` from rows the Quote Desk already wrote; this module maps query
parameters onto those calls and shapes what comes back. A router that added up a
class total here would be a second answer to the question the ledger exists to
answer once.

**Every route is manager-or-owner, and the report is owner-only.** That is
stricter than "hide the amounts", and the reason is the ``filterCounts.MFLOOR``
lesson in §1 rather than caution: on this surface the amount is not the only
thing that answers a margin question. A ``MARGIN_PROTECTED`` event *names a quote
line that was priced below the floor* — the event type is the below-floor flag,
the event count is the below-floor count, and both survive stripping the rupee
figure. There is no salesperson-safe projection of a ledger whose every row is
gross-profit arithmetic, so removing the amounts would leave a screen showing
only currency codes and evidence gaps.

``insight.py`` already settled this trade for ``/weather`` and
``/opportunities``: where a surface is *entirely* margin, an honest 403 beats an
empty screen. So a salesperson gets no amount from any route here because they
get no response body from any route here — the strongest available reading of
"absent, not hidden in the browser".

Plan gating is not repeated in this file. ``main.py`` applies
``require_feature("intelligence")`` at ``include_router``, the same single visible
declaration the decisions and insight surfaces use.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any, Optional, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import attribution
from ..attribution.evaluator import NO_EVENTS_RECORDED, NO_TRIAL_ON_RECORD
from ..authz import Principal, require_manager_or_owner, require_owner
from ..db import get_session
from ..domain.enums import ValueClass, ValueEventType

router = APIRouter(prefix="/api/attribution", tags=["attribution"])

#: One page of the ledger. Small by default because the drill-down is read
#: before it is audited, and capped because "show me everything" over an
#: append-only table grows without limit.
_PAGE = 100
_MAX_PAGE = 500

_E = TypeVar("_E", bound=Enum)


def _enum(raw: Optional[str], enum_type: type[_E], field: str) -> Optional[_E]:
    """A query parameter as its enum, or 400 naming what was allowed.

    Validated rather than passed through to the query, so an unrecognised filter
    is refused instead of silently matching nothing — an empty list is how a
    typo in ``value_class=ATRIBUTED`` would otherwise read as "no value was
    attributed", which is the benign-default shape §1 exists to catch.
    """
    if raw is None:
        return None
    try:
        return enum_type(raw)
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown {field} {raw!r}. Expected one of: "
            + ", ".join(m.value for m in enum_type)) from None


def _empty_reason(gaps: list[dict[str, Any]]) -> Optional[str]:
    """The one sentence a screen shows instead of a blank panel.

    Read off the gaps the evaluator already named rather than inferred from the
    numbers: "no trial on record" and "no events recorded" are different empty
    states with different answers, and a screen that cannot tell them apart tells
    somebody to wait when they should be connecting their books.
    """
    reasons = {gap.get("reason") for gap in gaps}
    if NO_TRIAL_ON_RECORD in reasons:
        return ("This organization has no intelligence trial on record, so there "
                "is no window to measure. Connect a Zoho company to start one.")
    if NO_EVENTS_RECORDED in reasons:
        return ("No value events have been recorded in this window. That is not a "
                "measured zero — it means no detection run is on record, and the "
                "two cannot be told apart from here.")
    return None


@router.get("/summary")
def summary(principal: Principal = Depends(require_manager_or_owner),
            session: Session = Depends(get_session)) -> dict:
    """The "What PIE Changed" figures for the live trial window.

    The headline is ATTRIBUTED alone. POTENTIAL and REALIZED come back in their
    own fields and the payload carries the evaluator's own note saying they must
    never be added together — the classes describe overlapping facts
    about the same line on purpose, so a caller that sums them double counts it.

    Passed through unchanged apart from the empty-state sentence. Re-shaping the
    rollup here would put a second opinion about the headline in a router.
    """
    result = attribution.trial_progress(session, principal.organization_id)
    return {**result,
            "empty_reason": _empty_reason(result.get("evidence_gaps") or [])}


@router.get("/events")
def events(event_type: Optional[str] = Query(None),
           value_class: Optional[str] = Query(None),
           limit: int = Query(_PAGE, ge=1, le=_MAX_PAGE),
           offset: int = Query(0, ge=0),
           principal: Principal = Depends(require_manager_or_owner),
           session: Session = Depends(get_session)) -> dict:
    """The ledger, filterable, each row drilling to its own evidence and basis.

    This is the audit surface for the summary: ``basis`` holds the operands the
    amount was computed from and ``evidence_refs`` names the rows it was computed
    over, so the figure is re-derivable rather than asserted.

    ``event_type`` and ``value_class`` are validated against the enums — see
    ``_enum`` for why an unknown filter is a 400 and not an empty list.
    """
    result = attribution.list_events(
        session, principal.organization_id,
        event_type=_enum(event_type, ValueEventType, "event_type"),
        value_class=_enum(value_class, ValueClass, "value_class"),
        limit=limit, offset=offset)
    return {
        **result,
        "filters": {"event_type": event_type, "value_class": value_class},
        # What could have been asked for, so a filter control is built from the
        # server's own vocabulary rather than a list copied into the client that
        # then drifts when a sixth event type lands.
        "event_types": [m.value for m in ValueEventType],
        "value_classes": [m.value for m in ValueClass],
        "empty_reason": (None if result["total"] else
                         "No value event matches this filter. An empty ledger is "
                         "not a measured zero — check the summary's evidence gaps "
                         "for whether detection has run at all."),
    }


@router.get("/evaluation")
def evaluation(pie_cost: Optional[Decimal] = Query(None, ge=0),
               principal: Principal = Depends(require_owner),
               session: Session = Depends(get_session)) -> dict:
    """Trial progress against the pre-trial baseline — the 30-day report.

    Owner-only: this is the renewal conversation, and it sets one book's
    performance before the platform against its performance during.

    ``pie_cost`` is supplied by the caller because the platform holds no price
    for its own plans. Left out, ``roi`` is ``None`` and ``roi_is_unknown`` is
    true — **render that as UNKNOWN, never as 0x**. A default cost here would put
    a return figure nobody entered on a screen somebody signs against.
    """
    result = attribution.thirty_day_report(
        session, principal.organization_id, pie_cost=pie_cost)
    return {**result,
            "empty_reason": _empty_reason(result.get("evidence_gaps") or [])}
