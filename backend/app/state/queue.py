"""How money becomes rank.

One function, and it is deliberately small enough to hold in your head — a
prioritisation nobody can recompute by hand is a prioritisation nobody argues
with, and a queue nobody argues with is a queue nobody reads.

    score = clamp(0, 100, money_points + urgency_points)

**Money is the base.** ``financial ÷ rupees_per_point``, capped. Absolute
rather than relative to the other rows in the build: a decision's score must
not move because an unrelated one appeared or was dismissed. A queue whose
order shifts for reasons nobody can point at gets ignored twice — once when it
is wrong, and permanently afterwards.

**Urgency is a modifier, never the base.** Days past a due date, or days a
promise has been broken, at a fixed rate. It is capped well below the money
term because a ₹2,000 bill 200 days overdue is an administrative annoyance and
a ₹20 lakh commitment is not, however fresh.

**Nothing here is a judgement.** Both terms are arithmetic over figures the
detector already published in its evidence, so a card can show its own ranking
working. The rupee scale is a versioned threshold, so re-tuning the queue does
not make last quarter's ranking unexplainable.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from ..domain.enums import PriorityBand
from .opportunities.base import DecisionPolicy, OpportunityDraft

#: How the 0–100 range is split between the two terms. Structural rather than
#: policy: it decides what *kind* of thing dominates the queue, and that is an
#: architectural statement (money first, lateness second), not a dial somebody
#: tunes per organization. The rupee-per-point rate is the tunable part.
MONEY_CAP = 75
URGENCY_CAP = 25

#: One urgency point per this many days past due. A fortnight, so a bill a
#: quarter overdue reaches the cap and no further — past a point, "more overdue"
#: stops being more urgent and starts being a different conversation.
DAYS_PER_URGENCY_POINT = Decimal(14)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def money_points(financial: Decimal, policy: DecisionPolicy) -> int:
    """Rupees at stake → priority points, capped.

    Guards a zero or negative rate rather than dividing by it: a misconfigured
    threshold should produce a flat queue somebody notices, not a crash in a
    background job.
    """
    if policy.rupees_per_point <= 0 or financial <= 0:
        return 0
    return _clamp(int(financial / policy.rupees_per_point), 0, MONEY_CAP)


def urgency_points(days_past: Optional[int]) -> int:
    """Days past a date somebody committed to → priority points, capped."""
    if not days_past or days_past <= 0:
        return 0
    return _clamp(int(Decimal(days_past) / DAYS_PER_URGENCY_POINT), 0, URGENCY_CAP)


def _days_past(draft: OpportunityDraft) -> Optional[int]:
    """The one operational field that means "somebody is already late".

    Read by name rather than by scanning for anything date-shaped: a detector
    that wants its situation treated as urgent says so explicitly, and one that
    reports an *age* without a promise behind it — an open purchase order in a
    book with no promised dates — correctly gets no urgency points at all.
    """
    value = draft.impact.operational.get("days_past_due")
    return int(value) if isinstance(value, int) else None


def score(draft: OpportunityDraft, policy: DecisionPolicy) -> int:
    """The deterministic priority of one situation. 0–100."""
    return _clamp(money_points(draft.impact.financial, policy)
                  + urgency_points(_days_past(draft)), 0, 100)


def band(value: int) -> str:
    """The band a score falls in.

    Deliberately *not* ``settings.PRIORITY_HIGH_AT`` / ``PRIORITY_MEDIUM_AT``,
    which are tuned for signal severity — a shape-of-the-evidence number on a
    different scale. Sharing them would make ₹1.5 lakh of dead stock and a
    moderately confident margin signal the same colour by coincidence.
    """
    if value >= 60:
        return PriorityBand.HIGH.value
    if value >= 30:
        return PriorityBand.MEDIUM.value
    return PriorityBand.LOW.value


def explain(draft: OpportunityDraft, policy: DecisionPolicy) -> dict:
    """The ranking, shown working.

    A card renders this so the reader can check the position of a row rather
    than trusting it. Every field is one of the two terms or an input to them.
    """
    money = money_points(draft.impact.financial, policy)
    urgency = urgency_points(_days_past(draft))
    return {
        "score": score(draft, policy),
        "money_points": money,
        "urgency_points": urgency,
        "financial": str(draft.impact.financial),
        "rupees_per_point": str(policy.rupees_per_point),
        "days_past_due": _days_past(draft),
        "days_per_urgency_point": str(DAYS_PER_URGENCY_POINT),
        "money_cap": MONEY_CAP,
        "urgency_cap": URGENCY_CAP,
    }
