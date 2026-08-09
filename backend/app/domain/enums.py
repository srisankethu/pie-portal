"""Closed enumerations for the domain.

Explicit, closed sets — the spec forbids a sixth decision type and pins the
lifecycle, roles, and data classes. Keeping these as ``str`` enums makes them
portable across the DB (stored as strings) and JSON APIs.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


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
    # A supplier. Its own member rather than borrowing CUSTOMER: the two are
    # different parties with different screens and different scoping, and a
    # supplier card filed under CUSTOMER would resolve to the wrong name.
    VENDOR = "VENDOR"


class DecisionOrigin(str, Enum):
    """Which producer made a decision, and therefore what its score means.

    A decision has always been derived from a ``Signal`` — a detector over
    sales and cost lines, interpreted by the AI layer. Business State added a
    second, deterministic producer over folded state, and the two rank on
    different scales: a signal's severity is a shape-of-the-evidence number,
    a state opportunity's is money. The queue orders on the deterministic base
    for exactly this reason (see ``DecisionRepository.list``).
    """

    SIGNAL = "SIGNAL"
    STATE = "STATE"


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

    # ── derived from Business State, deterministically ──────────────────────
    #
    # Seven types for the seven situations the two folded states can actually
    # describe. Each is a *distinct situation with a distinct action set* —
    # deliberately not one type per phrase in the brief, because dead stock and
    # "capital locked" are one situation measured two ways, and two cards for
    # one situation is two things to dismiss.
    INV_DEAD_STOCK = "INV_DEAD_STOCK"
    INV_SLOW_MOVING = "INV_SLOW_MOVING"
    INV_EXCESS_COVER = "INV_EXCESS_COVER"
    INV_BELOW_REORDER = "INV_BELOW_REORDER"
    INV_OVERSOLD = "INV_OVERSOLD"
    SUP_OPEN_COMMITMENT = "SUP_OPEN_COMMITMENT"
    CASH_PAYABLE_OVERDUE = "CASH_PAYABLE_OVERDUE"
    # The receivable side. Two situations, not one measured twice: money past
    # its date is a collection you can act on today, and a large share of the
    # book owed by one customer is an exposure that is true even when every
    # invoice is current.
    CASH_RECEIVABLE_OVERDUE = "CASH_RECEIVABLE_OVERDUE"
    CASH_CREDIT_EXPOSURE = "CASH_CREDIT_EXPOSURE"
    # The supply side of the same two questions. Concentration is about money
    # — how much of our purchasing rests on one relationship. Sole source is
    # about substitutability, which is a different exposure: a small supplier
    # can be irreplaceable and a large one easy to replace.
    SUP_SPEND_CONCENTRATION = "SUP_SPEND_CONCENTRATION"
    SUP_SOLE_SOURCE = "SUP_SOLE_SOURCE"


#: Everything derived from Business State. All of them quantify impact from a
#: purchase rate or a payable balance, which is cost information — so all of
#: them are management decisions, and none reaches a salesperson.
STATE_DECISION_TYPES = frozenset({
    DecisionType.INV_DEAD_STOCK,
    DecisionType.INV_SLOW_MOVING,
    DecisionType.INV_EXCESS_COVER,
    DecisionType.INV_BELOW_REORDER,
    DecisionType.INV_OVERSOLD,
    DecisionType.SUP_OPEN_COMMITMENT,
    DecisionType.CASH_PAYABLE_OVERDUE,
    # Receivables are not cost information — what a customer owes is revenue
    # already billed. These are here because of how state decisions are
    # *routed*, not because of what they disclose: `decisions/opportunities`
    # assigns every state decision to no individual and to SALES_MANAGER, and
    # PIE has no model of which salesperson owns a collection. Routing them to
    # a person needs that model first; until then they sit with management,
    # which is also where collections are run in this business.
    DecisionType.CASH_RECEIVABLE_OVERDUE,
    DecisionType.CASH_CREDIT_EXPOSURE,
    # Both are sized in purchase spend, which is cost information outright.
    DecisionType.SUP_SPEND_CONCENTRATION,
    DecisionType.SUP_SOLE_SOURCE,
})


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
RESTRICTED_DECISION_TYPES = (
    frozenset({DecisionType.MARGIN_DETERIORATION, DecisionType.COST_PASS_THROUGH})
    | CUSTOMER_ITEM_DECISION_TYPES
    | STATE_DECISION_TYPES
)


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
    # Waiting on someone with more authority. Deliberately *not* a closing
    # state: escalation previously reused OVERRIDDEN, which closed the decision
    # the moment it was handed upward, so the queue looked dealt with while
    # nobody had actually looked at it.
    ESCALATED = "ESCALATED"


# Statuses that mean the decision is finished and off the queue.
CLOSED_DECISION_STATUSES = frozenset({
    DecisionStatus.ACTIONED, DecisionStatus.DISMISSED, DecisionStatus.OVERRIDDEN,
    DecisionStatus.EXPIRED, DecisionStatus.SUPERSEDED, DecisionStatus.RESOLVED,
})


class HumanAction(str, Enum):
    """Actions a human can take on a decision card (§12 POST /decisions/{id}/action)."""

    VIEW = "VIEW"
    ACT = "ACT"
    DISMISS = "DISMISS"
    SNOOZE = "SNOOZE"
    OVERRIDE = "OVERRIDE"
    # Hand upward for a judgement this person is not authorized to make. Raises
    # an ApprovalRequest and parks the decision in ESCALATED; it is not a way to
    # close a decision, which is what reusing OVERRIDE made it.
    ESCALATE = "ESCALATE"
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
    # No interpretation was ever attempted, and none is coming. A decision
    # derived from Business State is arithmetic over folded facts; the module
    # that makes one does not import ``ai/`` at all. Distinct from SUPPRESSED,
    # which means a model ran and its output was withheld.
    NOT_APPLICABLE = "NOT_APPLICABLE"


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
      SCALE_VIOLATION, NO_GROUNDED_FIGURE.
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
    #: Nothing was invented — nothing was said either. A surfaced reading of a
    #: bundle that carried figures quoted none of them, which is a caption, not
    #: an interpretation. Distinguished from the rejections above because it
    #: points at the prompt rather than at the model's arithmetic.
    NO_GROUNDED_FIGURE = "NO_GROUNDED_FIGURE"
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


class ApprovalKind(str, Enum):
    """What is being asked for.

    Each kind names a specific thing a person could not do on their own
    authority. A generic "approval" with a free-text subject would be
    unenforceable: the gate has to know what it is gating.
    """

    # A quote line priced below the margin the business set for it.
    QUOTE_LINE_PRICE = "QUOTE_LINE_PRICE"
    # The whole quote going out, when any line on it needed approval.
    QUOTE_SUBMISSION = "QUOTE_SUBMISSION"
    # A decision handed upward because the person cannot judge it alone.
    DECISION_ESCALATION = "DECISION_ESCALATION"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    # Approver wants a different price or more information — distinct from
    # rejection, which ends the request. This one returns it to the requester
    # with the thread intact.
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    # Requester no longer needs it (they re-priced above the floor).
    WITHDRAWN = "WITHDRAWN"


OPEN_APPROVAL_STATUSES = frozenset({ApprovalStatus.PENDING,
                                    ApprovalStatus.CHANGES_REQUESTED})

# Only an approver may set these; the requester may only WITHDRAW.
APPROVER_DECISIONS = frozenset({ApprovalStatus.APPROVED, ApprovalStatus.REJECTED,
                                ApprovalStatus.CHANGES_REQUESTED})


class ApprovalAuthority(str, Enum):
    """The minimum role that may decide a given request.

    Two levels, because there are genuinely two kinds of ask. A thin margin is a
    commercial judgement a sales manager is paid to make. Selling below what the
    item cost is a decision about whether the business loses money on purpose,
    and that is the owner's.
    """

    MANAGER = "MANAGER"
    OWNER = "OWNER"


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


class QuoteLossReason(str, Enum):
    """Why a quote was lost — and specifically, whether the money went anywhere.

    The distinction this exists to draw is not a CRM nicety. "They bought it
    from somebody else" and "nobody bought it at all" look identical in a
    `LOST` row and mean opposite things about the customer's spending: the
    first is a directly observed piece of a competitor's share of that
    customer, the second is not evidence of any purchase. Anything that
    reasons about what a customer buys elsewhere has to be able to tell them
    apart, and a free-text note cannot be aggregated.

    ``went_elsewhere`` is the property that carries that, rather than callers
    re-deciding which members count — one definition, extended here when a
    member is added, instead of a set literal copied into every consumer.

    ``UNKNOWN`` exists because a quote lost before this field did still has to
    be representable. It is never a default a *new* row may take: recording a
    loss requires naming which of these it was, and `set_outcome` refuses
    otherwise. An unknown reason is excluded from every denominator rather
    than assumed benign — a lost quote silently counted as "not bought" would
    understate what a customer spends elsewhere, which is the direction that
    makes a share estimate flattering.
    """

    #: Beaten on price by another supplier.
    LOST_ON_PRICE = "LOST_ON_PRICE"
    #: Another supplier could deliver and we could not, or not in time.
    LOST_ON_DELIVERY = "LOST_ON_DELIVERY"
    #: Our brand or grade was not approved for the application; theirs was.
    LOST_ON_APPROVAL = "LOST_ON_APPROVAL"
    #: The requirement went away — project shelved, part cancelled, budget
    #: pulled. Nobody supplied it, so nobody gained the spend.
    NOT_BOUGHT = "NOT_BOUGHT"
    #: Still nobody's, and not obviously coming back. Distinct from
    #: ``NOT_BOUGHT``: the requirement may be live and simply stalled, so it
    #: cannot be counted as a competitor's and cannot be counted as dead.
    NO_DECISION = "NO_DECISION"
    #: Recorded before a reason was asked for, or genuinely not known.
    UNKNOWN = "UNKNOWN"

    @property
    def went_elsewhere(self) -> Optional[bool]:
        """Whether this loss is evidence somebody else supplied the line.

        Three-valued on purpose. ``True`` means a competitor took it, ``False``
        means the requirement died, and ``None`` means the record does not say
        — which is not the same as "no", and must not be folded into it.
        """
        if self in (QuoteLossReason.LOST_ON_PRICE,
                    QuoteLossReason.LOST_ON_DELIVERY,
                    QuoteLossReason.LOST_ON_APPROVAL):
            return True
        if self is QuoteLossReason.NOT_BOUGHT:
            return False
        return None


#: Reasons a person may choose when recording a loss. ``UNKNOWN`` is absent
#: deliberately: it is a state history can be in, never a state a new record
#: may be created in.
SELECTABLE_LOSS_REASONS: tuple[QuoteLossReason, ...] = tuple(
    r for r in QuoteLossReason if r is not QuoteLossReason.UNKNOWN)


# Data classes for permission redaction (§14). RESTRICTED fields are visible to
# SALES_MANAGER and OWNER only. Enforced downstream (context assembly / API);
# defined here so every layer references one source of truth.
RESTRICTED_FACT_FIELDS = frozenset(
    {"unit_cost", "cost", "margin", "margin_pct", "cost_delta", "cost_delta_pct",
     "baseline_margin_pct", "current_margin_pct", "prior_unit_cost", "latest_unit_cost"}
)
