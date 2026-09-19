"""Options a person might weigh on a line, where the evidence already carries one.

**An option, never an instruction.** The engine does not make the pricing
decision and nothing here is phrased as though it did: "review the purchase
source", never "buy from Supplier X". That is not politeness. A recommendation
names a course of action the platform has evidence for, and the evidence in
these books says what an item has cost and what a customer has paid — it does
not say that another supplier would be cheaper or that this customer would have
accepted more. Naming a supplier would be a claim about a record that does not
exist, and the first time somebody follows one and it is wrong, every other
sentence this engine writes gets read differently.

**Nothing here is a new finding.** Every consideration rests on a conclusion the
engine has already drawn and published — a price-band code, a context qualifier,
a recorded-reason code, a cost-side code, the working-capital reading — and adds
one thing to it: what a person could do about it. A consideration that
introduced its own judgement about the business would be a second diagnosis
wearing a helpful voice, and it would be the one nobody could replay.

**A consideration on every quote is alert fatigue with a new name.** So each one
is gated, and the gate is never this module's own: it is the gate belonging to
the finding it rests on. Concretely, ``surfaces`` here implies
``OwnerDiagnosis.surfaces`` on every path — no consideration interrupts a reader
on a line the engine decided not to interrupt them about. That is the same
property ``render.render_working_capital`` holds (``interrupts <= surfaces``) and
it is pinned by a test rather than by this paragraph. Everything is still
*computed* on every line, because the gate governs interruption and not
calculation — the rule ``rules._surfaces`` states and this module inherits.

**The vocabulary is a code → label dict, the shape ``render.DISMISS_REASONS``
already has**, and there is deliberately no second dismissal vocabulary here.
A consideration a person rejects is the cheapest labelled feedback this engine
will ever get, and it reaches the existing path unchanged: a consideration hangs
off the line whose diagnosis produced it — ``line_id`` is on every one of them —
and that line's stored diagnosis is what ``POST /{quote_diagnosis_id}/dismiss``
already points at, with a reason from ``render.DISMISS_REASONS``. Rejecting the
finding rejects the option resting on it, because there is no option here that
survives its finding being wrong. Two of the reasons in that dict —
``COMPARISON_IS_WRONG`` and ``COST_HAS_CHANGED`` — are already the exact refusals
two of the considerations below invite. A second vocabulary would split the one
labelled dataset this engine has into two nobody can join.

``render`` is named in this docstring and imported nowhere in this module: the
renderer reads considerations, so the dependency has to run one way.

── which reader gets which ──────────────────────────────────────────────────

``OPERATIONS_CONSIDERATIONS`` is an **allowlist**, exactly as
``rules.OPERATIONS_CODES`` is and for its reason: a consideration added later is
silent to the desk until somebody decides otherwise, which is the opposite
failure direction from a denylist. Membership is decided by the same question —
can a caller who varies the quoted price and watches this appear and disappear
learn a cost? The three that cross rest on the price band, on a context
qualifier about that band, and on a field of the quote document. The two that do
not rest on the purchase ledger and on the cash cycle, and both of those are
RESTRICTED where they are computed.

One structural consequence is worth stating because a test holds it: **every
allowlisted consideration carries no ``severity``.** Severity here is
``drivers.severity``, which grades a movement in margin points, and a margin
grade beside the price the caller sent is the cost in one step — the shape
``filterCounts.MFLOOR`` had. The desk is told how much to *believe* a
consideration (``strength``, the four-grade ladder it already sees as
``strength_word``) and not how much money is behind it; the magnitude it can act
on is the deviation its own card already prints.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from ..config import CommercialThresholds
from . import drivers
from .rules import (ABOVE_HISTORICAL_RANGE, BELOW_HISTORICAL_RANGE,
                    COST_DRIVEN_MARGIN_RISK, EVIDENCE_WITHHELD,
                    KNOWN_COST_CHANGE, MODERATE, NO_PRICING_REASON_RECORDED,
                    OwnerDiagnosis, POSSIBLE_EXCEPTIONAL_PRICE,
                    PRICE_RESISTANCE_OBSERVED, _RANK)

#: The two context qualifiers that say something about the *comparable set*
#: rather than about the price. Both are on ``rules.OPERATIONS_CODES`` and both
#: are already printed as a note; what they support is the step a person can
#: take about the note, which is why one consideration covers the pair.
#:
#: ``POSSIBLE_EXCEPTIONAL_PRICE`` on its own can never interrupt anybody —
#: ``rules.strength`` caps a band that lost more than the trusted share of its
#: own evidence at WEAK, and ``_surfaces`` requires MODERATE. It is still listed
#: here, and the consideration is still computed on those lines, because the
#: gate governs interruption and not calculation; a reader who opens the line
#: finds the option. ``EVIDENCE_WITHHELD`` caps nothing, so it is the half that
#: can reach somebody unprompted.
_COMPARISON_DOUBT = (POSSIBLE_EXCEPTIONAL_PRICE, EVIDENCE_WITHHELD)

_ZERO = Decimal("0")


# ── the vocabulary ───────────────────────────────────────────────────────────
#
# Stable strings. They are published to a front end and read back in telemetry,
# so they are codes and the label is a separate thing a screen may change.

#: This organization records a pricing reason on a quote and this one is blank.
#: The one consideration on this list a salesperson can close by themselves.
RECORD_THE_PRICING_REASON = "RECORD_THE_PRICING_REASON"

#: Some of the comparables behind the band were set aside — trimmed as outliers,
#: or withheld because it is not clear when they became visible — so the band
#: may not be describing a population. Saying it does not is already a dismissal
#: reason, which is why this one's rejection is worth the most.
CHECK_THE_COMPARISON = "CHECK_THE_COMPARISON"

#: Quoted above the supported range, to a customer who has declined quotes at or
#: below the top of it. The engine does not know whether a high price wins the
#: order and says so; checking is the option.
CHECK_THE_PRICE_IS_WINNABLE = "CHECK_THE_PRICE_IS_WINNABLE"

#: The cost level this line faces has moved above what its purchase history
#: supports. RESTRICTED — it rests on the purchase ledger.
REVIEW_THE_PURCHASE_SOURCE = "REVIEW_THE_PURCHASE_SOURCE"

#: The money on this line is out materially longer than the supplier funds it,
#: and the wait is a major drag on what the line earns. RESTRICTED.
REVIEW_THE_PAYMENT_TERMS = "REVIEW_THE_PAYMENT_TERMS"

#: Code → the option in a reader's own words. The same shape
#: ``render.DISMISS_REASONS`` has, so a front end offers both the same way and
#: neither can be a dead button the other does not know about.
#:
#: Every label is an option somebody may take, in the imperative of a *choice*.
#: None of them names a supplier, a price or a number, and the module docstring
#: says why at length.
CONSIDERATIONS: dict[str, str] = {
    RECORD_THE_PRICING_REASON: "Record why this price was set",
    CHECK_THE_COMPARISON: "Check whether these past transactions are comparable",
    CHECK_THE_PRICE_IS_WINNABLE: "Check this price is still winnable here",
    REVIEW_THE_PURCHASE_SOURCE: "Review the purchase source for this item",
    REVIEW_THE_PAYMENT_TERMS: "Review the payment terms behind this line",
}

# ── why a line carries the options it carries ────────────────────────────────
#
# The machine-readable half of ``Considerations.basis``, and the same shape
# ``working_capital.WorkingCapital.reason``, ``intent.Reading.reason`` and
# ``drivers.Attribution.reason`` have. Two values, not three: ``_basis`` words
# the two empty cases differently — a quiet line and a line whose finding
# supports no option — but both are one answer to "what is on offer here",
# which is nothing. A third code would be a distinction no caller has asked
# for, readable from ``line_surfaces`` already.
#
# ``render.NOT_ON_STORED_RECORD`` is the third value this field takes and is
# deliberately not named here: it is a fact about a stored row rather than
# about a line's evidence, and ``render`` owns it for the same reason it owns
# the other three blocks' stored refusals. This module imports nothing from
# there — the renderer reads considerations, so the dependency runs one way.

#: At least one option rests on what this line's evidence already showed.
OFFERED = "OFFERED"

#: The line was weighed and its evidence supports no option.
NOTHING_TO_WEIGH = "NOTHING_TO_WEIGH"


#: The considerations an operations reader may be offered. An allowlist — see
#: the module docstring for what decides membership.
OPERATIONS_CONSIDERATIONS = frozenset({
    RECORD_THE_PRICING_REASON, CHECK_THE_COMPARISON, CHECK_THE_PRICE_IS_WINNABLE,
})

#: The order considerations are emitted in, fixed here rather than left to the
#: order the branches happen to run in. Two runs over one line must produce the
#: same bytes, and a reader scanning two lines of one quote should find the same
#: option in the same place on both.
ORDER: tuple[str, ...] = (
    RECORD_THE_PRICING_REASON, CHECK_THE_COMPARISON, CHECK_THE_PRICE_IS_WINNABLE,
    REVIEW_THE_PURCHASE_SOURCE, REVIEW_THE_PAYMENT_TERMS,
)


# ── the output ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Consideration:
    """One option, what it rests on, and how much to believe it.

    Frozen, like every other record in this package: a consideration is shown to
    somebody and may be rejected, and one that could be edited afterwards would
    make the rejection a record of something else.
    """

    #: One of ``CONSIDERATIONS``.
    code: str
    #: ``CONSIDERATIONS[code]`` — the option in a reader's words. Carried rather
    #: than left for a renderer to look up, so the wording travels with the
    #: thing it describes and a screen cannot offer an option under a name the
    #: engine never chose.
    label: str
    #: One sentence saying what this rests on and what the option is. Never a
    #: money figure: the figures are on the card this sits beside, and a number
    #: spelled here as well would be a second rounding of one value.
    detail: str
    #: The line whose diagnosis produced it — and therefore the line whose
    #: stored diagnosis a rejection is posted against. See the module docstring.
    line_id: str
    #: The engine codes this rests on, so "extends rather than invents" is
    #: something a reader can check instead of a claim in a docstring.
    rests_on: tuple[str, ...]
    #: ``STRONG`` / ``MODERATE`` / ``WEAK`` / ``INSUFFICIENT`` — the grade of the
    #: finding this rests on, never a second grader's.
    strength: str
    #: ``MAJOR`` / ``MINOR`` / ``NEGLIGIBLE`` where the finding published a
    #: magnitude, and ``None`` where it publishes none. **Not ``NEGLIGIBLE`` in
    #: that case**: "this movement is small" and "this finding is not a movement"
    #: are different answers, and collapsing them would report a fact about a
    #: field on a document as a margin of nearly zero. Every allowlisted
    #: consideration has ``None`` here, which is a test rather than a habit.
    severity: Optional[str]
    #: Whether this interrupts anybody. ``True`` implies the line's own
    #: ``surfaces``; calculation happens regardless.
    surfaces: bool

    def to_dict(self) -> dict:
        return {"code": self.code, "label": self.label, "detail": self.detail,
                "line_id": self.line_id, "rests_on": list(self.rests_on),
                "strength": self.strength, "severity": self.severity,
                "surfaces": self.surfaces}


@dataclass(frozen=True)
class Considerations:
    """Every option this line carries, and what was weighed when there is none.

    Never a bare tuple. An empty list rendered on its own reads as "nothing to
    do here", which is indistinguishable from "nothing was looked at" — the
    failure CLAUDE.md §1 names three times. ``basis`` says which it is.
    """

    line_id: str
    #: Whether the line itself interrupts anybody. Carried because it is what
    #: ``basis`` turns on and what a renderer needs to lay this out, and because
    #: ``for_operations`` has to rebuild ``basis`` from the desk's own list.
    line_surfaces: bool
    items: tuple[Consideration, ...]
    #: ``OFFERED`` / ``NOTHING_TO_WEIGH``, or ``render.NOT_ON_STORED_RECORD``
    #: on a row read back from the store. The code a caller reads, beside the
    #: sentence a person reads — kept apart so neither has to be recovered from
    #: the other, which is the lesson CLAUDE.md §1 draws from
    #: ``_identity_candidate``.
    reason: str
    #: What was weighed, or why nothing is offered, in words. Never empty, and
    #: never prefixed with its own code.
    basis: str

    @property
    def surfacing(self) -> tuple[Consideration, ...]:
        """The subset that interrupts. The rest are computed and available."""
        return tuple(c for c in self.items if c.surfaces)

    def to_dict(self) -> dict:
        return {"line_id": self.line_id, "line_surfaces": self.line_surfaces,
                "items": [c.to_dict() for c in self.items],
                "reason": self.reason, "basis": self.basis}


# ── generation ───────────────────────────────────────────────────────────────

def propose(owner: OwnerDiagnosis, *,
            th: CommercialThresholds) -> Considerations:
    """Every option this line's own evidence supports. Pure, total, deterministic.

    Returns a ``Considerations`` on every path, including the ordinary one where
    there is nothing to offer, so a caller is always told either the options or
    what was weighed instead.

    ``th`` is read for exactly one thing: ``drivers.cost_strength``, the grade of
    the purchase evidence behind the cost-side consideration. It is not a second
    grader — it is the function the attribution already grades its cost half
    with, asked again for the same line, so the two cannot disagree on the rows
    nobody looks at.

    Nothing is re-derived. Every condition below reads a code, a context
    qualifier or a published grade that ``rules`` has already decided; this
    function makes no finding of its own, and a branch here that computed one
    would be a second diagnosis.
    """
    items: list[Consideration] = []
    codes = set(owner.codes)
    context = set(owner.context)
    reading = owner.intent.reading

    # ── record the reason ───────────────────────────────────────────────────
    # Gated on the line surfacing, and that gate is load-bearing rather than
    # tidy: an organization with a declared pricing-reason field has a blank one
    # on most quotes, so an ungated version of this would appear on nearly every
    # line of every quote. That is the alert fatigue this package exists to
    # avoid, and it is worth less than nothing — a prompt nobody reads trains a
    # desk to skip the panel it sits in.
    if NO_PRICING_REASON_RECORDED in reading.codes:
        items.append(_one(
            RECORD_THE_PRICING_REASON, owner,
            rests_on=(NO_PRICING_REASON_RECORDED,) + _price_code(codes),
            detail=("This organization records a pricing reason on a quote and "
                    "this one is blank, so nothing on the record says why this "
                    "price was set. Writing it down is the option — an "
                    "unrecorded reason is not an absent one, and next quarter "
                    "nobody will remember."),
            strength=owner.strength, surfaces=owner.surfaces))

    # ── check the comparison ────────────────────────────────────────────────
    # Either qualifier about the comparable set: rows trimmed past the trusted
    # share, or rows withheld because their visibility could not be read. The
    # engine already prints both as a note; the option is the step a person can
    # take about it, and ``COMPARISON_IS_WRONG`` in ``render.DISMISS_REASONS``
    # is what saying "they are not comparable" already looks like. This is the
    # consideration whose rejection is worth the most — it is the only one whose
    # refusal says something about the evidence rather than about the price.
    doubt = tuple(c for c in _COMPARISON_DOUBT if c in context)
    if doubt:
        items.append(_one(
            CHECK_THE_COMPARISON, owner,
            rests_on=doubt + _price_code(codes),
            detail=("Some of the past transactions behind this range were set "
                    "aside — as outliers, or because it is not clear when they "
                    "became visible — so the range may not be describing a "
                    "population. Checking whether they are comparable at all is "
                    "the option; if they are not, saying so is what tunes this."),
            strength=owner.strength, surfaces=owner.surfaces))

    # ── check it is winnable ────────────────────────────────────────────────
    # Both halves are needed. Above the band on its own is as much a win as a
    # risk and the engine says it does not know which; above the band *to a
    # customer who has declined quotes at or below the top of it* is a fact
    # about this account, and it is the one a desk can act on before sending.
    if ABOVE_HISTORICAL_RANGE in codes and PRICE_RESISTANCE_OBSERVED in context:
        items.append(_one(
            CHECK_THE_PRICE_IS_WINNABLE, owner,
            rests_on=(ABOVE_HISTORICAL_RANGE, PRICE_RESISTANCE_OBSERVED),
            detail=("This line is quoted above the range this customer's own "
                    "history supports, and this customer has already declined "
                    "quotes at or below the top of that range. The engine does "
                    "not know whether a high price wins the order — checking "
                    "before it goes out is the option."),
            strength=owner.strength, surfaces=owner.surfaces))

    # ── review the purchase source ──────────────────────────────────────────
    #
    # RESTRICTED: it rests on the purchase ledger, and ``KNOWN_COST_CHANGE`` is
    # outside ``rules.OPERATIONS_CODES`` because its boundary *is* a cost.
    #
    # Two codes, and both are already the engine's conclusion that the cost side
    # moved: ``COST_DRIVEN_MARGIN_RISK`` where the price is not the problem, and
    # ``KNOWN_COST_CHANGE`` where a rise was on record before the quote was
    # written. The first never surfaces — it requires ``WITHIN_HISTORICAL_RANGE``
    # and ``rules._surfaces`` refuses that line — so this is computed there and
    # interrupts nobody, which is correct and is why the code below does not
    # special-case it. The second rides on whichever price finding the line
    # already made, so a below-band line with a recorded cost rise is offered
    # both options and the engine chooses between them for nobody.
    #
    # No supplier is named, ever. ``CostBaseline`` cites the purchases behind
    # the level and every one of them carries a vendor, so naming the vendor
    # would be easy and would be a different claim: that another source exists
    # and is cheaper. Nothing in these books records that.
    if COST_DRIVEN_MARGIN_RISK in codes or KNOWN_COST_CHANGE in codes:
        cost_grade = drivers.cost_strength(owner.cost, th)
        items.append(_one(
            REVIEW_THE_PURCHASE_SOURCE, owner,
            rests_on=tuple(c for c in (COST_DRIVEN_MARGIN_RISK,
                                       KNOWN_COST_CHANGE) if c in codes),
            detail=("The cost level this line faces sits above what its own "
                    "purchase history supports, and that was on record before "
                    "this quote was written. Reviewing where this item is "
                    "bought is the option. No supplier is named: these books "
                    "record what was paid, not that another source would be "
                    "cheaper."),
            strength=cost_grade,
            severity=_driver_severity(owner, drivers.COST_LEVEL_EFFECT),
            surfaces=owner.surfaces and _RANK[cost_grade] >= _RANK[MODERATE]))

    # ── review the payment terms ────────────────────────────────────────────
    #
    # RESTRICTED in its entirety, like the reading it rests on.
    #
    # Only where the wait *hurt*. ``effect_pp`` negative is this codebase's
    # "this cost margin", not a second convention, and it is the condition
    # rather than the day count because ``drivers.severity`` is symmetric on
    # purpose — it grades a movement in either direction, and a term review
    # offered to somebody whose terms are already good is the kind of
    # suggestion that gets a panel switched off.
    #
    # Belt and braces today: ``insight/financing.financing_cost`` floors the
    # funded window at zero days, so a supplier who covers the wait outright
    # produces a charge of exactly nought and a NEGLIGIBLE grade rather than a
    # favourable MAJOR one. The guard reads the effect anyway, so this stays
    # correct if that floor is ever revisited — which its own docstring
    # contemplates, since pricing an advance is a different feature.
    capital = owner.working_capital
    if (capital.assessed and capital.effect_pp is not None
            and capital.effect_pp < _ZERO):
        items.append(_one(
            REVIEW_THE_PAYMENT_TERMS, owner,
            # The working-capital reading has no code in ``rules``' vocabulary
            # — it publishes its own outcome, deliberately, because a reading
            # that graded itself into the price-side ladder would be a second
            # answer to how believable this line's band is. So what this rests
            # on is that outcome, carried rather than restated.
            rests_on=(capital.reason,),
            detail=("The money behind this line is out for materially longer "
                    "than the credit its supplier gives, and funding that wait "
                    "is a major drag on what the line earns. Reviewing the "
                    "terms on either side is the option; the days and the "
                    "charge are on the working-capital block."),
            strength=capital.strength, severity=capital.severity,
            # ``capital.surfaces`` already applies the MODERATE floor and the
            # MAJOR boundary; the line's own gate narrows it, never widens it,
            # which is exactly what ``render.render_working_capital`` does.
            surfaces=owner.surfaces and capital.surfaces))

    ordered = tuple(sorted(items, key=lambda c: ORDER.index(c.code)))
    return Considerations(line_id=owner.line_id, line_surfaces=owner.surfaces,
                          items=ordered, reason=_reason(ordered),
                          basis=_basis(ordered, line_surfaces=owner.surfaces))


def for_operations(proposed: Considerations) -> Considerations:
    """The desk's options, from the allowlist. A construction, not a redaction.

    The same shape ``rules.operations_view`` has and for its reason: the codes
    are intersected with an allowlist, so a consideration resting on the
    purchase ledger or on the cash cycle simply is not present — there is
    nothing here that was removed and could have been forgotten.

    ``basis`` is rebuilt over the surviving list rather than carried across, and
    it names nothing that did not survive. A sentence saying "one further option
    is not shown to you" would be a flag that answers a margin question, which is
    the one thing CLAUDE.md §1 rules out by name; it would also be untrue half
    the time, since the two restricted considerations fire on different evidence
    from each other.
    """
    kept = tuple(c for c in proposed.items
                 if c.code in OPERATIONS_CONSIDERATIONS)
    return Considerations(
        line_id=proposed.line_id, line_surfaces=proposed.line_surfaces,
        items=kept, reason=_reason(kept),
        basis=_basis(kept, line_surfaces=proposed.line_surfaces))


# ── helpers ──────────────────────────────────────────────────────────────────

def _one(code: str, owner: OwnerDiagnosis, *, rests_on: tuple[str, ...],
         detail: str, strength: str, surfaces: bool,
         severity: Optional[str] = None) -> Consideration:
    """One consideration, with its label read from the single vocabulary."""
    return Consideration(
        code=code, label=CONSIDERATIONS[code], detail=detail,
        line_id=owner.line_id, rests_on=rests_on, strength=strength,
        severity=severity, surfaces=surfaces)


def _price_code(codes: set[str]) -> tuple[str, ...]:
    """The price-side finding this consideration is riding on, if there is one.

    Recorded in ``rests_on`` rather than inferred later: the reason a
    record-the-reason or check-the-comparison option is in front of somebody at
    all is that the line already carried a price finding, and an option whose
    provenance a reader has to reconstruct is one they will read as the engine's
    own idea.
    """
    return tuple(c for c in (BELOW_HISTORICAL_RANGE, ABOVE_HISTORICAL_RANGE)
                 if c in codes)


def _driver_severity(owner: OwnerDiagnosis, code: str) -> Optional[str]:
    """The published severity of one driver, or ``None`` where there is none.

    ``None`` rather than ``NEGLIGIBLE``: the attribution refuses on a thin band
    or an unknowable cost and publishes no drivers at all, and reporting that
    refusal as a negligible movement would turn "we did not say" into "we said
    it was small".
    """
    for driver in owner.attribution.drivers:
        if driver.code == code:
            return driver.severity
    return None


def _reason(items: Sequence[Consideration]) -> str:
    """Whether anything is on offer, as a code.

    Read off the same list ``_basis`` words, and rebuilt by ``for_operations``
    over the desk's own list for that function's reason: a desk that was handed
    nothing has nothing to weigh, whatever the owner's copy holds. Saying
    otherwise would be a flag answering a margin question.
    """
    return OFFERED if items else NOTHING_TO_WEIGH


def _basis(items: Sequence[Consideration], *, line_surfaces: bool) -> str:
    """What was weighed, in one sentence. Three cases and no empty fourth."""
    if not items:
        if not line_surfaces:
            return ("Nothing on this line was found worth interrupting anybody "
                    "about, so no option is put in front of you. What was "
                    "checked, and what could not be, is on the card.")
        return ("This line carries a finding and the evidence behind it "
                "supports no option beyond reading the finding itself.")
    surfacing = sum(1 for c in items if c.surfaces)
    named = ", ".join(c.code for c in items)
    verb = "rests" if len(items) == 1 else "rest"
    noun = "option" if len(items) == 1 else "options"
    return (f"{len(items)} {noun} {verb} on what this line's evidence already "
            f"showed ({named}); {surfacing} put in front of a reader. Each is "
            f"an option to weigh and none of them is an instruction — the "
            f"pricing decision is not the engine's to make.")
