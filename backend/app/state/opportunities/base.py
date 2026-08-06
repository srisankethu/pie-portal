"""What a decision detector produces, and the registry it registers into.

The shape mirrors ``state/reducers/``: a detector declares which states it
reads, is registered rather than switched on, and is a pure function of its
inputs. Adding a decision category is a module here and a line in the package
``__init__``; nothing in the engine changes.

**A detector produces a fact, not a recommendation.** It says *this situation
exists, it is worth this much, here is why, and here is what you could do about
it*. It never picks one of those actions, and it never produces a number a
person could not reproduce by hand from the state row it names.

**Money is the ranking input, and it is Decimal.** Every impact figure comes
from a state value that was folded as an exact string; the arithmetic here
stays exact and only becomes a string again at the boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Optional, Protocol

# ── the actions a situation permits ──────────────────────────────────────────
#
# Presented, never chosen. The platform's job is to make the options and their
# consequences legible; deciding between them is the reason a person is paid.
# One vocabulary, so the same action means the same thing on every card.
DISCOUNT_TO_MOVE = "DISCOUNT_TO_MOVE"
BUNDLE_WITH_MOVING = "BUNDLE_WITH_MOVING"
RETURN_TO_SUPPLIER = "RETURN_TO_SUPPLIER"
PROPOSE_WRITE_OFF = "PROPOSE_WRITE_OFF"
PUSH_TO_PAST_BUYERS = "PUSH_TO_PAST_BUYERS"
STOP_REORDERING = "STOP_REORDERING"
REORDER_NOW = "REORDER_NOW"
DEFER_PURCHASE = "DEFER_PURCHASE"
SPLIT_PURCHASE = "SPLIT_PURCHASE"
CHASE_SUPPLIER = "CHASE_SUPPLIER"
CANCEL_PURCHASE_ORDER = "CANCEL_PURCHASE_ORDER"
RE_PROMISE_CUSTOMER = "RE_PROMISE_CUSTOMER"
EXPEDITE_INBOUND = "EXPEDITE_INBOUND"
PAY_NOW = "PAY_NOW"
NEGOTIATE_TERMS = "NEGOTIATE_TERMS"
SET_REORDER_POINT = "SET_REORDER_POINT"

#: Every action, with what it means on screen. A card renders from this rather
#: than inventing its own wording, so one action reads identically everywhere.
ACTIONS: dict[str, str] = {
    DISCOUNT_TO_MOVE: "Discount it to move",
    BUNDLE_WITH_MOVING: "Bundle it with what does sell",
    RETURN_TO_SUPPLIER: "Ask the supplier to take it back",
    PROPOSE_WRITE_OFF: "Propose a write-off",
    PUSH_TO_PAST_BUYERS: "Offer it to the customers who bought it before",
    STOP_REORDERING: "Stop reordering it",
    REORDER_NOW: "Reorder now",
    DEFER_PURCHASE: "Defer the purchase",
    SPLIT_PURCHASE: "Split the purchase into smaller lots",
    CHASE_SUPPLIER: "Chase the supplier",
    CANCEL_PURCHASE_ORDER: "Cancel the order",
    RE_PROMISE_CUSTOMER: "Re-promise the customer a date you can meet",
    EXPEDITE_INBOUND: "Expedite what is already on the way",
    PAY_NOW: "Pay it now",
    NEGOTIATE_TERMS: "Negotiate terms with the supplier",
    SET_REORDER_POINT: "Set a reorder point in Zoho",
}


class UnknownAction(ValueError):
    """An action no card knows how to render. Loud, because a card that showed
    a raw enum name to a person would be a defect nobody notices in tests."""


@dataclass(frozen=True)
class Impact:
    """What a situation is worth, in money and in operations.

    ``financial`` is the ranking input and the one number that must always be
    present — a situation nobody can size is a situation nobody can prioritise,
    and surfacing it above a quantified one would be guessing.

    ``basis`` says in words *what the number is*. Not decoration: ₹4,00,000 of
    capital locked and ₹4,00,000 of annual holding cost are different claims,
    and a card that shows the figure without the sentence invites the reader to
    assume the wrong one.
    """

    financial: Decimal
    basis: str
    #: A recurring bleed, where the situation has one. Dead stock costs money
    #: every month it sits; an oversold line does not.
    monthly: Optional[Decimal] = None
    #: Counts, days, quantities — whatever makes the situation concrete without
    #: being money.
    operational: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "financial": str(self.financial),
            "basis": self.basis,
            "monthly": (str(self.monthly) if self.monthly is not None else None),
            "operational": dict(self.operational),
        }


@dataclass(frozen=True)
class OpportunityDraft:
    """One situation, before it is persisted as a ``Decision``.

    A draft, not a row, for the same reason ``SignalDraft`` is: detectors stay
    pure functions of (state, policy, as_of) and are testable without a
    database. Persistence is the service's job.
    """

    decision_type: str
    subject_entity_type: str
    subject_entity_id: str
    impact: Impact
    #: Why this exists, in a sentence a person can check against the evidence.
    rationale: str
    #: The state fields that produced it, verbatim. The reader's audit trail:
    #: every number in ``impact`` must be recomputable from these.
    evidence: dict[str, Any]
    actions: tuple[str, ...]
    #: The ``BusinessState`` keys folded into this. Usually one — the subject —
    #: but a situation spanning two states names both.
    state_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        unknown = [a for a in self.actions if a not in ACTIONS]
        if unknown:
            raise UnknownAction(
                f"{self.decision_type} offers {unknown}, which no card can "
                "render. Add them to ACTIONS with the words a person reads.")


@dataclass(frozen=True)
class DecisionPolicy:
    """The thresholds a detector reads, lifted out of ``CommercialThresholds``.

    Built by the caller and passed in, exactly like ``stock.Carrying`` — so
    ``state/`` never imports ``commercial/`` and the arrow between them keeps
    pointing one way. ``version`` travels with it so every decision this
    produces can be stamped with the policy that produced it.
    """

    dead_days: int
    slow_days: int
    carrying_annual_pct: Decimal
    excess_cover_months: Decimal
    rupees_per_point: Decimal
    #: Below this, a situation is real but not worth a person's attention. The
    #: same materiality figure the commercial layer already uses, reused rather
    #: than duplicated as a second constant meaning the same thing.
    min_impact: Decimal
    version: str = ""

    @property
    def carrying_monthly_pct(self) -> Decimal:
        return self.carrying_annual_pct / Decimal(12)


class OpportunityDetector(Protocol):
    """One decision category. Registered, never switched on."""

    decision_type: str
    #: Which states this reads. Declared so the service loads each state once
    #: rather than every detector loading its own.
    states: frozenset[str]

    def detect(self, states: dict[str, dict[str, dict[str, Any]]],
               policy: DecisionPolicy, as_of: date) -> Iterable[OpportunityDraft]:
        """Situations present in this state, or nothing.

        ``states`` is ``{state_name: {key: value}}`` — already loaded, already
        for the right day. A detector never touches a session.
        """
        ...


#: Populated at import time by ``state.opportunities``. A dict rather than a
#: type-chain: adding a category must not mean editing the service.
DETECTORS: dict[str, OpportunityDetector] = {}


def register(detector: OpportunityDetector) -> OpportunityDetector:
    """Register a detector. Refuses a duplicate type and an empty state list —
    a detector reading no state would produce decisions from nothing, which is
    the one thing this layer exists not to do."""
    if detector.decision_type in DETECTORS:
        raise ValueError(
            f"two detectors claim {detector.decision_type!r}")
    if not detector.states:
        raise ValueError(
            f"{detector.decision_type} declares no states. Business State is "
            "the only source of decision intelligence; a detector reading "
            "none is producing decisions from nothing.")
    DETECTORS[detector.decision_type] = detector
    return detector


# ── shared reading helpers ───────────────────────────────────────────────────
#
# State values are JSON: money and quantity as strings, dates as ISO. Every
# detector parses the same way, so it is parsed in one place.

def number(value: dict[str, Any], name: str) -> Optional[Decimal]:
    raw = value.get(name)
    if raw is None or raw == "":
        return None
    return Decimal(str(raw))


def day(value: dict[str, Any], name: str) -> Optional[date]:
    raw = value.get(name)
    return date.fromisoformat(str(raw)) if raw else None


def money(amount: Decimal) -> Decimal:
    """Rupees, to the paisa. Quantised once at the point a figure becomes an
    impact, so two cards never disagree in the second decimal place."""
    return amount.quantize(Decimal("0.01"))
