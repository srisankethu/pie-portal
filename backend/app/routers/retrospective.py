"""The first-run look-back, over HTTP.

One read and no arithmetic. ``signals.retrospective`` assembles the answer from
the detectors' own results; this maps a role check onto it.

**Manager or owner, and there is no salesperson projection.** A count of
MARGIN_DETERIORATION findings is a count of products whose margin fell, and it
answers a margin question with the rupee figure already absent — the
``filterCounts.MFLOOR`` lesson in §1, where a *count* computed for every role
was the leak. Stripping the margin rows would leave a screen whose whole subject
is what is missing from it, so an honest 403 beats a hollowed-out answer, as
``insight``'s ``/weather`` already settled.

Plan gating is applied at ``include_router`` in ``main.py`` with the other
intelligence surfaces. That is the right default here, unlike the value ledger:
a book that has just connected is inside its free month, so the reader this
screen exists for has it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..authz import Principal, require_manager_or_owner
from ..db import get_session
from ..signals import retrospective

router = APIRouter(prefix="/api/v1/retrospective", tags=["retrospective"])


@router.get("")
def look_back(principal: Principal = Depends(require_manager_or_owner),
              session: Session = Depends(get_session)) -> dict:
    """What this organization's own synced history already contains.

    Coverage first, findings second — in the payload as well as on the screen.
    A reader who takes the finding count before the denominator has formed the
    wrong impression by the time they reach it.
    """
    return retrospective.look_back(session, principal.organization_id)
