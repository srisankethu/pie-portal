"""Closed enumerations for the domain.

Explicit, closed sets — the spec forbids a sixth decision type and pins the
lifecycle, roles, and data classes. Keeping these as ``str`` enums makes them
portable across the DB (stored as strings) and JSON APIs.
"""
from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    """User role (§2). Salesperson is additionally scoped to assigned customers."""

    SALESPERSON = "SALESPERSON"
    SALES_MANAGER = "SALES_MANAGER"
    OWNER = "OWNER"


class CustomerStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class SubjectEntityType(str, Enum):
    CUSTOMER = "CUSTOMER"
    PRODUCT = "PRODUCT"
    QUOTE = "QUOTE"
    # One customer's relationship with one item. The id is a composite,
    # ``customer_id::product_id`` — see ``commercial.subject``.
    CUSTOMER_ITEM = "CUSTOMER_ITEM"


class DecisionType(str, Enum):
    """The five V1 decision types (§9). No sixth type in V1."""

    CUSTOMER_DECLINE = "CUSTOMER_DECLINE"
    CUSTOMER_DORMANCY = "CUSTOMER_DORMANCY"
    MARGIN_DETERIORATION = "MARGIN_DETERIORATION"
    COST_PASS_THROUGH = "COST_PASS_THROUGH"
    QUOTE_CONTEXT = "QUOTE_CONTEXT"

    # Customer × Item grain. The four families above speak about a customer or a
    # product; these speak about one customer's relationship with one item,
    # which is the grain that can actually name what is eroding and why.
    CI_MARGIN_EROSION = "CI_MARGIN_EROSION"
    CI_COST_NOT_PASSED = "CI_COST_NOT_PASSED"
    CI_LOW_PEER_PRICING = "CI_LOW_PEER_PRICING"
    CI_MARGIN_DECLINE_NO_VOLUME = "CI_MARGIN_DECLINE_NO_VOLUME"
    CI_MARGIN_DECLINE_WITH_VOLUME = "CI_MARGIN_DECLINE_WITH_VOLUME"
    CI_MATERIAL_MARGIN_GAP = "CI_MATERIAL_MARGIN_GAP"


# Every Customer × Item decision type. All of them carry cost/margin.
CUSTOMER_ITEM_DECISION_TYPES = frozenset({
    DecisionType.CI_MARGIN_EROSION,
    DecisionType.CI_COST_NOT_PASSED,
    DecisionType.CI_LOW_PEER_PRICING,
    DecisionType.CI_MARGIN_DECLINE_NO_VOLUME,
    DecisionType.CI_MARGIN_DECLINE_WITH_VOLUME,
    DecisionType.CI_MATERIAL_MARGIN_GAP,
})

# Decision types that carry RESTRICTED economics and are never routed to a
# salesperson (§14 decision-type gating).
RESTRICTED_DECISION_TYPES = frozenset(
    {DecisionType.MARGIN_DETERIORATION, DecisionType.COST_PASS_THROUGH}
) | CUSTOMER_ITEM_DECISION_TYPES


class SignalType(str, Enum):
    """Detector families (§6). One per proactive decision family."""

    CUSTOMER_DECLINE = "CUSTOMER_DECLINE"
    CUSTOMER_DORMANCY = "CUSTOMER_DORMANCY"
    MARGIN_DETERIORATION = "MARGIN_DETERIORATION"
    COST_PASS_THROUGH = "COST_PASS_THROUGH"

    # Customer × Item grain (see DecisionType for why this grain exists).
    CI_MARGIN_EROSION = "CI_MARGIN_EROSION"
    CI_COST_NOT_PASSED = "CI_COST_NOT_PASSED"
    CI_LOW_PEER_PRICING = "CI_LOW_PEER_PRICING"
    CI_MARGIN_DECLINE_NO_VOLUME = "CI_MARGIN_DECLINE_NO_VOLUME"
    CI_MARGIN_DECLINE_WITH_VOLUME = "CI_MARGIN_DECLINE_WITH_VOLUME"
    CI_MATERIAL_MARGIN_GAP = "CI_MATERIAL_MARGIN_GAP"


class DecisionStatus(str, Enum):
    """Decision lifecycle states (§17)."""

    OPEN = "OPEN"
    VIEWED = "VIEWED"
    ACTIONED = "ACTIONED"
    DISMISSED = "DISMISSED"
    OVERRIDDEN = "OVERRIDDEN"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    RESOLVED = "RESOLVED"


class HumanAction(str, Enum):
    """Actions a human can take on a decision card (§12 POST /decisions/{id}/action)."""

    VIEW = "VIEW"
    ACT = "ACT"
    DISMISS = "DISMISS"
    SNOOZE = "SNOOZE"
    OVERRIDE = "OVERRIDE"
    # Undo a human action taken by mistake. The reopen is itself recorded, so
    # the audit trail shows both the original action and its reversal.
    REOPEN = "REOPEN"


class PriorityBand(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AiStatus(str, Enum):
    """AI interpretation status (§5, §16). PENDING = not yet interpreted (Phase 1)."""

    PENDING = "PENDING"
    OK = "OK"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    SUPPRESSED = "SUPPRESSED"


class EvidenceSufficiency(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class AiFailureReason(str, Enum):
    """Why an AI call did not yield a usable, grounded recommendation (WS3).

    Split into two tiers so a prompt problem is distinguishable from a schema
    problem, and both from an infrastructure problem:

    - *Rejections* raise from the validation gate and degrade the decision:
      SCHEMA_INVALID, UNKNOWN_FACT_LABEL, UNKNOWN_SIGNAL_ID, UNGROUNDED_NUMBER,
      SCALE_VIOLATION.
    - *Corrections* are repaired deterministically and recorded, not raised
      (the output stays usable): PRIORITY_OUT_OF_RANGE, ACTION_TEXT_ON_WITHHELD.
    - *Provider* failures never reach the gate at all.
    """

    # gate rejections
    SCHEMA_INVALID = "SCHEMA_INVALID"
    UNKNOWN_FACT_LABEL = "UNKNOWN_FACT_LABEL"
    UNKNOWN_SIGNAL_ID = "UNKNOWN_SIGNAL_ID"
    UNGROUNDED_NUMBER = "UNGROUNDED_NUMBER"
    SCALE_VIOLATION = "SCALE_VIOLATION"
    # deterministic corrections (recorded, not fatal)
    PRIORITY_OUT_OF_RANGE = "PRIORITY_OUT_OF_RANGE"
    ACTION_TEXT_ON_WITHHELD = "ACTION_TEXT_ON_WITHHELD"
    # provider-side failures
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_ERROR = "PROVIDER_ERROR"


# Reasons that cause the gate to reject the model output (vs. repair it).
GATE_REJECTION_REASONS = frozenset({
    AiFailureReason.SCHEMA_INVALID,
    AiFailureReason.UNKNOWN_FACT_LABEL,
    AiFailureReason.UNKNOWN_SIGNAL_ID,
    AiFailureReason.UNGROUNDED_NUMBER,
    AiFailureReason.SCALE_VIOLATION,
})


class OutcomeStatus(str, Enum):
    PENDING = "PENDING"
    MEASURED = "MEASURED"
    NOT_MEASURABLE = "NOT_MEASURABLE"


class QuoteOutcomeStatus(str, Enum):
    """A quote's commercial lifecycle.

    Deliberately narrow. This is not a CRM pipeline: it exists so that a priced
    decision can later be joined to whether the customer accepted it, which is
    the only way to tell a disciplined price from a lost order.
    """

    DRAFT = "DRAFT"
    SENT = "SENT"
    WON = "WON"
    LOST = "LOST"


# Legal transitions. A quote may be re-sent (revised) while still SENT, and a
# decided quote is terminal — reopening one would silently rewrite history that
# a margin analysis has already counted.
QUOTE_OUTCOME_TRANSITIONS: dict[QuoteOutcomeStatus, frozenset] = {
    QuoteOutcomeStatus.DRAFT: frozenset({QuoteOutcomeStatus.SENT,
                                         QuoteOutcomeStatus.LOST}),
    QuoteOutcomeStatus.SENT: frozenset({QuoteOutcomeStatus.SENT,
                                        QuoteOutcomeStatus.WON,
                                        QuoteOutcomeStatus.LOST}),
    QuoteOutcomeStatus.WON: frozenset(),
    QuoteOutcomeStatus.LOST: frozenset(),
}


# Data classes for permission redaction (§14). RESTRICTED fields are visible to
# SALES_MANAGER and OWNER only. Enforced downstream (context assembly / API);
# defined here so every layer references one source of truth.
RESTRICTED_FACT_FIELDS = frozenset(
    {"unit_cost", "cost", "margin", "margin_pct", "cost_delta", "cost_delta_pct",
     "baseline_margin_pct", "current_margin_pct", "prior_unit_cost", "latest_unit_cost"}
)
