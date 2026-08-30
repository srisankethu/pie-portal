"""Closed enumerations for the domain.

Explicit, closed sets — the spec forbids a sixth decision type and pins the
lifecycle, roles, and data classes. Keeping these as ``str`` enums makes them
portable across the DB (stored as strings) and JSON APIs.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class Role(str, Enum):
    """What a person may do **inside one organization** (§2).

    A role is a property of a *membership*, not of a person: the same identity
    can be an owner of the company they run and a salesperson in a group
    company, and one column on ``users`` cannot say that. It is stored on
    ``organization_memberships.role`` and resolved per request from the
    organization the session is acting for — see ``app/memberships.py``.

    Salesperson is additionally scoped to assigned customers.
    """

    SALESPERSON = "SALESPERSON"
    SALES_MANAGER = "SALES_MANAGER"
    OWNER = "OWNER"


class MembershipStatus(str, Enum):
    """Whether a membership currently grants access to its organization.

    Only ``ACTIVE`` does. The other two are kept rather than deleted because a
    membership is the record of a relationship, and "Sarah replaced John" is a
    fact an audit asks about long after John's laptop went back.

    ``INVITED`` is a membership that exists before its holder has accepted —
    the row is created with the invitation so the seat, the role and who
    offered it are decided in one place, and acceptance flips one column
    instead of inventing the membership from an email.

    ``REMOVED`` is a membership that was ended. It is never deleted and never
    reused: re-adding somebody writes a new row, because the question six
    months from now is *when* they had access, and a row edited back to ACTIVE
    answers that wrongly.
    """

    ACTIVE = "ACTIVE"
    INVITED = "INVITED"
    REMOVED = "REMOVED"


class SubscriptionStatus(str, Enum):
    """The organization's commercial relationship with PIE, as a state.

    ``TRIALING`` and ``EXPIRED`` are the two halves of one trial: every new
    organization starts in the first and falls to the second when
    ``trial_ends_at`` passes. **Expiry is derived from that timestamp rather
    than written by a job** — a scheduled sweep that misses a run would leave
    an organization entitled to something it has stopped paying for, and the
    absence of the sweep would look exactly like the absence of a problem.

    ``ACTIVE`` is a paid subscription; ``CANCELLED`` is one that ended. Neither
    is reachable from the API — an organization that could set its own status
    would not have one — and both are set through ``entitlements.set_plan`` /
    ``decide_request``.
    """

    TRIALING = "TRIALING"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class PlanTier(str, Enum):
    """What an organization is licensed to use (see ``app/entitlements.py``).

    FREE is **not a product**. It is the floor an organization sits on when it
    is paying for nothing — before a trial has been granted, and again once one
    has run out. It keeps the quote desk, margin floors and approvals working,
    because taking away the screens somebody has their working week in would be
    deleting their business rather than ending a subscription, and the trial's
    own promise is that the data survives it. What it does not include is the
    decision layer, which is the thing being sold.

    That reading is a change: FREE used to be marketed as "the Quote Desk, free
    forever", a tier a business could deliberately choose and stay on. There is
    no always-free plan any more — a new organization gets a 30-day trial of
    Commercial Intelligence and then either subscribes or lands here. The value
    is unchanged so that no stored row, no ``DEFAULT_PLAN`` and no operator
    command has to be rewritten to mean what it already meant mechanically.

    INTELLIGENCE adds the decision layer — signals, decision cards, the insight
    screens. PLATFORM adds multi-company groups. Stored lowercase because the
    value travels through config (``DEFAULT_PLAN``) and a CLI, where lowercase
    is what people type.
    """

    FREE = "free"
    INTELLIGENCE = "intelligence"
    PLATFORM = "platform"


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
    """Every situation the platform raises a card about — 22 of them, in three
    grains.

    V1 shipped five and the brief (§9) said there would be no sixth. That held
    while a customer or a product was the only thing a decision could be about,
    and stopped holding when the grain did: the 6 Customer × Item types speak
    about one customer's relationship with one item, and the 11 derived from
    Business State speak about stock, supply and cash. Each grain has its own
    frozenset below, and the routing and disclosure rules key on those rather
    than on this list.

    The V1 sentence is kept in the past tense rather than deleted — the
    constraint is what explains the shape of the first five. It is not what
    this file has held for a long time, and until now the docstring still said
    it was. `test_enum_prose_counts` pins the three counts above to the members
    below, so the next one to go stale fails instead of misleading.
    """

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
    # One type per situation the two folded states can actually describe. Each
    # is a *distinct situation with a distinct action set* — deliberately not
    # one type per phrase in the brief, because dead stock and "capital locked"
    # are one situation measured two ways, and two cards for one situation is
    # two things to dismiss.
    #
    # How many there are is `STATE_DECISION_TYPES` below, and deliberately not
    # written out here as well: this comment read "Seven types for the seven
    # situations" for a long time after there were eleven, because a count
    # restated beside the members it counts has nothing keeping it honest.
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
    """How the evaluation of an accepted decision's realised impact stands.

    The Outcome Tracker's vocabulary (``commercial/outcome_tracker.py``).
    Three states, and the third is the load-bearing one:

    - ``PENDING`` — the evaluation horizon has not elapsed. Nothing is
      asserted, in either direction.
    - ``REALISED`` — the horizon has passed and the same metrics the signal
      carried were recomputed from persisted rows over the post-decision
      window; the delta is a measurement.
    - ``UNKNOWN`` — the horizon has passed but the evidence needed is missing,
      and the evaluation names exactly what is missing. Never a benign default
      (§1): a snapshot with no cost record behind it yields UNKNOWN, not a
      margin fabricated from partial rows.

    This enum predates the tracker (spec §8 reserved it, nothing ever read or
    wrote it); its unused MEASURED/NOT_MEASURABLE members were renamed to the
    tracker's vocabulary rather than shipping a second enum for the same fact.
    """

    PENDING = "PENDING"
    REALISED = "REALISED"
    UNKNOWN = "UNKNOWN"


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
    """Why a quote was lost, from a list short enough that people use it.

    Five entries, because the owner's question has four answers and one of them
    is "they never came back": *losing at 8% below my quote is a pricing
    problem; losing on delivery is a stock problem*. A free-text field alone
    could not separate those two — every loss would be a sentence, and nobody
    counts sentences. The note beside this is what stops the list from lying
    when reality does not fit one of the five.

    Deliberately about *the customer's reason*, not ours. "Priced too high" and
    "cost too high" are the same loss to the person recording it and two
    different problems to the person fixing it, so this records only what was
    heard and leaves the diagnosis to the modules that read it.

    **This vocabulary is deliberately not mine.** It is `claude/quote-win-loss`'s
    (PR #42), adopted here verbatim rather than shipped alongside a second set of
    names for the same fact. That branch carries ~1,100 lines of analysis and UI
    reading these exact values; a competing enum would have made whichever landed
    second a rename across all of it, for no gain. What is added below is the one
    question that branch does not answer and `insight/wallet.py` needs. If both
    land, the reconciliation is deleting one migration file — not a vocabulary
    argument.
    """

    PRICE = "PRICE"
    DELIVERY = "DELIVERY"
    COMPETITOR = "COMPETITOR"
    CUSTOMER_CANCELLED = "CUSTOMER_CANCELLED"
    NO_DECISION = "NO_DECISION"

    @property
    def went_elsewhere(self) -> Optional[bool]:
        """Whether this loss is evidence somebody else supplied the line.

        Three-valued on purpose, and the third value is the point. ``True`` a
        competitor took it, ``False`` the requirement died, ``None`` the record
        cannot say. ``None`` is **not** "no" and must never be folded into it:
        anything reasoning about what a customer buys elsewhere has to exclude
        the unknowns from both sides, because counting them as "nobody bought
        it" shrinks the competitor's side and overstates our own share.

        A property on the enum rather than a set literal in each consumer, so
        adding a sixth reason forces the question to be answered once, here,
        instead of being silently defaulted in three places.

        ``NO_DECISION`` is ``None`` rather than ``False`` deliberately: a stalled
        requirement may still land with somebody, so it is neither ours nor
        theirs yet.
        """
        if self in (QuoteLossReason.PRICE, QuoteLossReason.DELIVERY,
                    QuoteLossReason.COMPETITOR):
            return True
        if self is QuoteLossReason.CUSTOMER_CANCELLED:
            return False
        return None


#: Losses recorded before the vocabulary existed. Not a member of the enum —
#: nothing may ever be *written* with it — but a reader has to be able to name
#: the bucket rather than quietly dropping those quotes out of a denominator.
#: Distinct from a recorded ``NO_DECISION``: one is "nobody asked", the other is
#: "we asked and the customer has not decided".
LOSS_REASON_NOT_RECORDED = "NOT_RECORDED"

#: Reasons a person may choose when recording a loss — every member, since the
#: not-recorded sentinel is deliberately outside the enum and so cannot be
#: offered by construction.
SELECTABLE_LOSS_REASONS: tuple[QuoteLossReason, ...] = tuple(QuoteLossReason)


class QuoteDocOutcome(str, Enum):
    """How a quote raised in an ERP ended, according to that ERP's own record.

    Three values, and the third is the one this exists for. ``UNRECORDED`` is a
    real member rather than a sentinel kept outside the enum — deliberately the
    opposite choice from ``LOSS_REASON_NOT_RECORDED`` above. Nothing may ever
    *write* "not recorded" as a loss reason, because that would be a person's
    answer invented on their behalf; ``UNRECORDED`` is the correct and honest
    thing to write for the roughly 215 of this book's ~290 estimates whose
    status says only that nobody ever closed them.

    That silence spans three different facts — nobody worked it, the customer
    never answered, and we lost it to a competitor — and only the third is a
    loss. Reading an ``expired`` estimate as LOST would manufacture two hundred
    labels out of it, every one of them defensible-looking and most of them
    wrong, and anything learned from those labels would be confidently wrong
    about why this business loses work. So the unknown is named, stored and
    counted, and never folded into either decided value.

    Not the same thing as ``QuoteOutcomeStatus``, and not interchangeable with
    it: that one is the platform's own lifecycle (DRAFT → SENT → WON/LOST),
    driven by a person through transitions that refuse to reopen a decided
    quote. This one is a *reading* of somebody else's record, rewritten from
    the payload on every sync, with no lifecycle and no transitions at all.
    """

    WON = "WON"
    LOST = "LOST"
    UNRECORDED = "UNRECORDED"

class MsmeClassification(str, Enum):
    """A supplier's registered size under the MSMED Act, as somebody saw it.

    ``UNKNOWN`` is the default and is not a synonym for ``NOT_REGISTERED``.
    Section 43B(h) bites on micro and small suppliers only, so the difference
    between "we checked and they are not registered" and "nobody has checked"
    decides whether a bill is safe or merely unexamined — and only one of those
    is a fact. Nothing infers this from turnover, bill size or a supplier's
    name; see ``Customer.incentive_eligibility`` for the same rule applied to
    the same temptation.
    """

    MICRO = "MICRO"
    SMALL = "SMALL"
    # Outside 43B(h) entirely. Recorded rather than filed under NOT_REGISTERED
    # because "registered, and out of scope" is a checked answer.
    MEDIUM = "MEDIUM"
    NOT_REGISTERED = "NOT_REGISTERED"
    UNKNOWN = "UNKNOWN"


#: The classifications section 43B(h) actually reaches.
MSME_PROTECTED_CLASSES = frozenset({MsmeClassification.MICRO,
                                    MsmeClassification.SMALL})


class EnterpriseActivity(str, Enum):
    """What the supplier does, which decides whether registration means anything.

    Not decoration, and the most load-bearing field on the record for a
    distributor. Wholesale and retail traders hold Udyam registration for
    priority-sector lending, and that registration does not carry the section
    15 payment protection 43B(h) enforces. A cutting-tool distributor buys a
    large share of its stock from dealers, so a watchlist that ignored this
    would raise most of its rows against suppliers who are not in scope — and a
    list that is usually wrong is a list people learn to scroll past.
    """

    MANUFACTURER = "MANUFACTURER"
    SERVICE = "SERVICE"
    TRADER = "TRADER"
    UNKNOWN = "UNKNOWN"


#: Activities for which registration carries the section 15 benefit.
MSME_PROTECTED_ACTIVITIES = frozenset({EnterpriseActivity.MANUFACTURER,
                                       EnterpriseActivity.SERVICE})


class MsmeEvidence(str, Enum):
    """What was actually seen. A status nobody can source is one nobody can
    defend when somebody asks where the 45 days came from."""

    UDYAM_CERT = "UDYAM_CERT"
    # Most registered suppliers print their Udyam number on the tax invoice.
    # The cheapest evidence available, and already in the document set.
    INVOICE_DECLARATION = "INVOICE_DECLARATION"
    VENDOR_EMAIL = "VENDOR_EMAIL"
    PORTAL_LOOKUP = "PORTAL_LOOKUP"
    NONE = "NONE"

class ValueEventType(str, Enum):
    """What kind of business fact a ``ValueEvent`` records.

    Every member names something that already leaves evidence in this schema —
    a priced line, a quote outcome, a resolved equivalent — because an event
    type with no evidence behind it is a number the platform would be inventing
    rather than measuring. Adding a member therefore means naming the rows it is
    computed from first; if there are none, the answer is that the value is not
    measurable yet, not that it is zero.

    Deliberately absent: anything that values *time*. Approvals turned round and
    lines priced are real and worth reporting, but this business holds no hourly
    rate, so converting them to rupees would be a fabricated number that happens
    to be computed deterministically. Productivity is counted, never valued.
    """

    MARGIN_PROTECTED = "MARGIN_PROTECTED"
    DISCOUNT_LEAKAGE_PREVENTED = "DISCOUNT_LEAKAGE_PREVENTED"
    EQUIVALENT_SAVING = "EQUIVALENT_SAVING"
    LOST_SALE_RECOVERED = "LOST_SALE_RECOVERED"
    PROCUREMENT_OPPORTUNITY = "PROCUREMENT_OPPORTUNITY"


class ValueClass(str, Enum):
    """How strong the evidence behind an event's amount is.

    The whole reason this is a separate axis from ``ValueEventType``: the same
    kind of fact can be an opportunity nobody acted on or money that demonstrably
    moved, and a total that adds those together is a claim the evidence does not
    support. **These classes never sum with each other.** The headline attributed
    figure is the sum of ``ATTRIBUTED`` alone; the others are reported on their
    own rows under their own labels, and an honest zero stays zero rather than
    being topped up from a weaker class.

    ``ATTRIBUTED`` is the narrowest and the only one that may be called what PIE
    is worth: it requires the money to have moved *and* an intervention that
    precedes the outcome. ``REALIZED`` without that ordering is money the
    business would have made anyway as far as anyone can prove.
    """

    #: An opportunity that was identified. Nothing has happened yet.
    POTENTIAL = "POTENTIAL"
    #: The money moved and the evidence says so — a won quote, an order, an
    #: invoice. Says nothing about who caused it.
    REALIZED = "REALIZED"
    #: REALIZED, and the intervention is on record as preceding the outcome.
    ATTRIBUTED = "ATTRIBUTED"
    #: Modelled rather than observed. Shown separately, never in the headline.
    ESTIMATED = "ESTIMATED"


#: The one class that may be summed into a headline "value delivered" figure.
#: A constant rather than a literal at each call site, so a sixth class cannot
#: be quietly folded into the total by whichever module adds it.
HEADLINE_VALUE_CLASSES = frozenset({ValueClass.ATTRIBUTED})


class InboundChannel(str, Enum):
    """How an enquiry reached us.

    Closed, and deliberately about the *arrival path* rather than the medium's
    vendor: WHATSAPP rather than a messaging-app name, PHONE_NOTE rather than
    "call", because what a coverage report asks is which route a customer used,
    not which application was open. A seventh route is a schema decision — a
    new member here and nothing else — and until it is made, an enquiry that
    arrived some other way has no honest value to store.
    """

    EMAIL = "EMAIL"
    WHATSAPP = "WHATSAPP"
    PDF = "PDF"
    PORTAL = "PORTAL"
    PHONE_NOTE = "PHONE_NOTE"


class LineDisposition(str, Enum):
    """How an inbound enquiry line ended.

    Terminal, and every member is an *ending* — there is no PENDING. A line
    whose fate is not yet known has no disposition row at all, which is what
    keeps "not answered yet" distinguishable from "answered with nothing".
    Inventing an OPEN member would put those two in one bucket and make the
    unquoted-demand report unreadable.

    ``QUOTED`` is the only success, and it is a *coverage* fact rather than a
    commercial one: it says a price went back, not that it was accepted or that
    it was any good. ``LOST`` records a quote that did not convert, which is a
    different question and belongs to the quote's own outcome — it is here so
    that a line's story can be told without a join.

    The four failures are separated because each names a different thing to fix:
    ABSTAINED is ours (we could not read the requirement), NO_STOCK and NO_PRICE
    are the book's, and NO_RESPONSE is the customer's. Collapsing them into one
    "not quoted" would leave the report unable to say which.

    None of these is a number, and none may become one. A disposition says what
    happened to a line; the economics of the line it became live where economics
    live (§1).
    """

    QUOTED = "QUOTED"
    ABSTAINED = "ABSTAINED"
    NO_STOCK = "NO_STOCK"
    NO_PRICE = "NO_PRICE"
    LOST = "LOST"
    NO_RESPONSE = "NO_RESPONSE"


# Data classes for permission redaction (§14). RESTRICTED fields are visible to
# SALES_MANAGER and OWNER only. Enforced downstream (context assembly / API);
# defined here so every layer references one source of truth.

#: The two data classes themselves — the tag carried on every price reference,
#: quote exception and context fact.
#:
#: They lived as three independent pairs of string literals:
#: ``commercial/references``, ``signals/quote_context`` and
#: ``context/quote_bundle``. That is the shape the redaction rule is least able
#: to survive, because the first two packages *write* the tag and the third
#: *reads* it to decide what a salesperson never sees. A rename or a typo in any
#: single copy fails nothing — the comparison in ``quote_bundle`` simply stops
#: matching, and a RESTRICTED cost fact is emitted to a salesperson with no test
#: red anywhere. One declaration is what makes the write and the read the same
#: string by construction.
#:
#: ``commercial/incentive.ELIGIBILITY_RESTRICTED`` is a different concept under a
#: colliding name and stays where it is; see the note on it there.
OPERATIONAL = "OPERATIONAL"
RESTRICTED = "RESTRICTED"

#: Field names that are RESTRICTED wherever they appear, for the surfaces that
#: redact by name rather than by tag.
RESTRICTED_FACT_FIELDS = frozenset(
    {"unit_cost", "cost", "margin", "margin_pct", "cost_delta", "cost_delta_pct",
     "baseline_margin_pct", "current_margin_pct", "prior_unit_cost", "latest_unit_cost",
     # Profit is cost by subtraction the moment revenue sits beside it, so it
     # belongs to the same class: no salesperson-visible fact may carry it.
     "gross_profit", "gross_profit_delta"}
)
