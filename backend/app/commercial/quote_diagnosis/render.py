"""Two views of one diagnosis, in words. Template-based and deterministic.

Every sentence here is assembled from values that were already computed. Nothing
in this module estimates, infers, rounds creatively, or decides anything — it is
a rendering of a diagnosis, not an analysis of one, which is the same line
``commercial/diagnosis.py`` draws for the relationship screen.

**No model renders any of this, and there is no flag that makes one.** The
specification allows an LLM prose renderer over an already-complete structured
object, behind a flag; v1 ships without it because a flag toggling nothing is
worse than an absence. The contract it would have to meet is written down here
so the next person does not have to infer it: a model renderer receives a
finished card, may rephrase its sentences, and **may not introduce any number
that is not already on the card**. The platform's grounding gate in ``ai/``
exists for exactly that check, and `decisions/` is the only seam allowed to
call it.

**The operations renderer takes ``OperationsDiagnosis`` and nothing else.** Not
an owner diagnosis it filters, not a pair of objects it chooses between — the
only input is the type that structurally cannot carry cost. If it needed the
owner object for anything, that would be the bug.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from ..config import CommercialThresholds
from .drivers import (Attribution, COST_LEVEL_EFFECT, Driver,
                      PRICE_POSITION_EFFECT)
from .intent import PricingIntent, Reading
from .opportunity import Opportunity
from .working_capital import ASSESSED, WorkingCapital
from .rules import (ABOVE_HISTORICAL_RANGE, BELOW_HISTORICAL_RANGE,
                    BELOW_PEER_BAND_STRUCTURAL, COST_DRIVEN_MARGIN_RISK,
                    EVIDENCE_WITHHELD, INSUFFICIENT_EVIDENCE, KNOWN_COST_CHANGE,
                    had_enough_to_compare,
                    MODERATE, NO_COST_EVIDENCE, OperationsDiagnosis,
                    OwnerDiagnosis, POSSIBLE_COST_DRIVEN,
                    POSSIBLE_EXCEPTIONAL_PRICE, PRICE_RESISTANCE_OBSERVED,
                    STRONG, WEAK, WITHIN_HISTORICAL_RANGE)

#: What a reader may do about a card. Codes rather than labels, because the
#: button text is the front end's business and the *action* is a contract: a
#: dismissal has to come back with one of the reasons below.
REVIEW_PRICE = "REVIEW_PRICE"
DISMISS = "DISMISS"

#: Why somebody dismissed a card. Every dismissal captures one — it is the
#: cheapest route to labelled data this engine will ever have, and the only
#: honest way to find out which rules are noise. Free text is offered alongside
#: and is not a substitute: a reason nobody can aggregate tunes nothing.
DISMISS_REASONS: dict[str, str] = {
    "PRICE_IS_CORRECT": "The price is right for this deal",
    "VOLUME_COMMITMENT": "Priced for a volume or contract commitment",
    "COMPETITIVE_PRESSURE": "Matched to a competitor",
    "RELATIONSHIP": "Deliberate, for the relationship",
    "COMPARISON_IS_WRONG": "The comparable transactions are not comparable",
    "COST_HAS_CHANGED": "Our cost has changed and the history is stale",
    "OTHER": "Something else",
}

_STRENGTH_WORD = {STRONG: "Strong", MODERATE: "Moderate", WEAK: "Weak"}


def strength_word(strength: str) -> str:
    """The grade as a reader sees it, for either projection's card.

    One definition because it is one word on two screens. INSUFFICIENT has no
    entry on purpose: "Not enough" is what it is called everywhere it appears,
    and a second spelling of it would be a second answer.
    """
    return _STRENGTH_WORD.get(strength, "Not enough")

#: Printed on every card, without exception. A reader who is not told that
#: history can contain unrecorded exceptional pricing will read a band as a rule,
#: and these books genuinely hold deals that were negotiated outside the system.
QUALIFICATION = ("Historical prices may include exceptional deals not recorded "
                 "in the ERP.")


@dataclass(frozen=True)
class OperationsCard:
    """The card a salesperson sees. Strings and codes; no economics anywhere.

    ``renders`` is carried rather than assumed: a caller that ignored it and
    drew the card anyway would be the thing §11's default-silent rule exists to
    prevent, so the answer travels with the content.
    """

    line_id: str
    renders: bool
    #: Whether the engine had enough comparable evidence to say anything about
    #: this price at all.
    #:
    #: Distinct from ``renders``, and the distinction is the whole reason this
    #: field exists. A line that does not render covers three different facts —
    #: the price sat inside the supported range, the deviation was too small to
    #: interrupt anybody about, or there was nothing to compare against — and a
    #: screen with only ``renders`` has to report all three as "nothing stood
    #: out". The first two are good news and the third is the absence of any
    #: news, which CLAUDE.md §1 is explicit must never read as a pass.
    #:
    #: A reader can already see "Not enough" in ``evidence``, but that is a word
    #: chosen for display and matching on it downstream would be a guess about
    #: what this module meant. The producer knows; the output shape carries it.
    comparable: bool
    headline: str
    quoted: str
    historical: str
    evidence: str
    evidence_detail: str
    why: str
    note: str
    #: What this quote's own record says about why it was priced as it was.
    #: Required and never defaulted, for the reason ``OperationsDiagnosis.intent``
    #: is: a card that inherited a silence would be telling a salesperson nothing
    #: was recorded when nothing was read.
    intent: "IntentView"
    qualification: str = QUALIFICATION
    actions: tuple[str, ...] = (REVIEW_PRICE, DISMISS)


# ── driver attribution, in words ─────────────────────────────────────────────
#
# RESTRICTED in its entirety. Every figure below is a margin movement, and a
# margin beside the price the caller sent is the cost in one step. None of it
# has a counterpart on the operations card and none of it may grow one.

#: The noun a reader sees for each driver code. A map rather than a chain of
#: ``if``s, so a code this renderer has never heard of comes out as itself
#: rather than vanishing — a driver dropped in the rendering is a split that no
#: longer adds up, presented as though it did.
_DRIVER_LABEL = {PRICE_POSITION_EFFECT: "price", COST_LEVEL_EFFECT: "cost level"}

#: Compared against rather than tested for falsehood, so a genuine zero charge —
#: a line whose supplier credit covers the wait — is told apart from a missing
#: one. ``Decimal("0")`` and ``None`` are different answers here.
_CAPITAL_ZERO = Decimal("0")

#: Why a *stored* diagnosis carries no split. Its own reason rather than one of
#: ``drivers``' — those name something about the evidence, and this names
#: something about the record: ``quote_diagnoses`` has no attribution column, so
#: a row read back cannot answer the question and must say so instead of
#: answering it with silence.
NOT_ON_STORED_RECORD = "NOT_ON_STORED_RECORD"

#: An ``Attribution`` in its refusal shape — empty ``drivers``, ``reconciles``
#: false, and a basis that says what stopped it — built here so the stored-row
#: projection renders through the same function as a live one. A second renderer
#: for the refusal case would be a second answer to what a refusal looks like.
NOT_STORED = Attribution(
    movement_pp=None, drivers=(), reconciles=False, residual_pp=None,
    basis=(f"{NOT_ON_STORED_RECORD}: this is the diagnosis as it was stored, "
           "and the split between the price decision and the cost level is not "
           "one of its columns — it is computed when the engine runs over the "
           "evidence. Re-assess this line to see it."))


@dataclass(frozen=True)
class DriverLine:
    """One attributed factor, already in words and already in money.

    ``effect`` is formatted here and not downstream. The front end may not
    format money or compute a number (CLAUDE.md §3), and a percentage point
    rendered in two places is two roundings of one figure.
    """

    #: ``drivers.PRICE_POSITION_EFFECT`` / ``COST_LEVEL_EFFECT``.
    code: str
    #: ``MAJOR`` / ``MINOR`` / ``NEGLIGIBLE`` — how much it matters.
    severity: str
    #: How much to believe it, in the one spelling ``strength_word`` gives.
    strength_word: str
    #: The margin effect and the money behind it, spelled: ``-3.16 pp
    #: (-₹50 per unit)``.
    effect: str
    #: The counterfactual this number answers, in ``drivers``' own words.
    basis: str


@dataclass(frozen=True)
class AttributionView:
    """The split as an owner reads it, or the refusal to assert one. RESTRICTED.

    Two shapes and no third. Either ``drivers`` holds both factors and
    ``headline`` says how they divide the movement, or ``drivers`` is empty,
    ``headline`` is blank and ``note`` names what stopped it. There is no state
    where this renders as an empty panel: ``renders`` is false unless there is
    something written in one of the two fields, which is what keeps a refusal
    from reading as "nothing to report" (CLAUDE.md §1).
    """

    #: Whether this block draws. **Not a second surfacing gate.** It is
    #: ``OwnerDiagnosis.surfaces`` — the one ``rules._surfaces`` decided —
    #: narrowed by whether there is anything written here at all. It can only
    #: ever be more silent than that gate, never less, so "should this interrupt
    #: somebody" still has exactly one answer.
    renders: bool
    #: The split in one sentence, or ``""`` when nothing is asserted.
    headline: str
    drivers: tuple[DriverLine, ...]
    #: The counterfactual the split was taken in, or the refusal and its reason
    #: — ``drivers``' own basis either way, verbatim. Not re-worded here: it
    #: carries the residual and the observation counts, and a second spelling of
    #: "why there is no split" would drift from the one the engine states.
    note: str


# ── working capital, in words ────────────────────────────────────────────────
#
# RESTRICTED in its entirety, and the most direct of the three blocks on this
# card: ``capital_per_unit`` **is** the purchase cost and the charge divides
# straight back to it against one organization-wide rate. None of it has a
# counterpart on the operations card and none of it may grow one.

#: An unassessed reading for a row read back from the store. The same
#: ``NOT_ON_STORED_RECORD`` the attribution uses, because it is the same fact
#: about the same record: ``quote_diagnoses`` has no column for either, so a
#: stored row cannot answer and must say so rather than answering with silence.
#: Built here so the stored projection refuses through the same function a live
#: reading renders through — a second renderer for the refusal case would be a
#: second answer to what a refusal looks like.
WC_NOT_STORED = WorkingCapital(
    assessed=False, reason=NOT_ON_STORED_RECORD,
    funded_days=None, receivable_days=None, supplier_credit_days=None,
    days_source=None, terms_source=None, settlements=0,
    rate=None, capital_per_unit=None, capital_at_risk=None,
    charge_per_unit=None, line_charge=None, effect_pp=None,
    severity="", strength="", days_strength="", cost_strength="",
    surfaces=False, cited=(), unavailable=(),
    basis=(f"{NOT_ON_STORED_RECORD}: this is the diagnosis as it was stored, "
           "and what the cash on this line costs is not one of its columns — it "
           "is computed when the engine runs over this account's settled "
           "invoices and this supplier's terms. Re-assess this line to see it."))


@dataclass(frozen=True)
class WorkingCapitalView:
    """What the line's cash costs as an owner reads it, or the refusal. RESTRICTED.

    Two shapes and no third, which is the shape ``WorkingCapital`` itself has.
    Either ``figures`` holds the reading and ``headline`` states the money, or
    ``figures`` is empty, ``headline`` is blank and ``note`` names what stopped
    it. There is no state where this renders as an empty panel.

    ``assessed`` is **carried from the engine, never re-derived** from whether a
    figure happens to be present. A predicate rebuilt downstream from published
    fields is a guess about what the producer meant, which is the lesson
    CLAUDE.md §1 draws from ``_identity_candidate`` — and here the producer knows
    something the figures do not say, namely that a reading of zero days is a
    real answer rather than an absent one.
    """

    #: Whether a figure was produced at all — ``WorkingCapital.assessed``.
    assessed: bool
    #: Whether this reading should interrupt somebody — ``rules._surfaces``'
    #: answer for the line, narrowed by ``working_capital._surfaces``' answer for
    #: the reading. **A narrowing, never a widening**: it can only ever be more
    #: silent than either gate already decided, so "should this interrupt
    #: somebody" keeps exactly one answer. It decides the register the headline
    #: is printed in and nothing else — the figures are on the card either way,
    #: because a card somebody opened is not an interruption and
    #: ``working_capital._surfaces`` says in as many words that a ``MINOR``
    #: reading still belongs on it.
    interrupts: bool
    #: Whether this block draws at all. The line's own gate, narrowed by whether
    #: there is anything written here — never widened, for the reason
    #: ``AttributionView.renders`` is not.
    renders: bool
    #: The money in one sentence, or ``""`` when nothing is asserted.
    headline: str
    #: Label and value, already formatted. A fixed handful of rows, which is the
    #: case ``ui-standards`` §3 keeps a ``<table>`` for.
    figures: tuple[tuple[str, str], ...]
    #: ``MAJOR`` / ``MINOR`` / ``NEGLIGIBLE``, or ``""`` on a refusal — a refusal
    #: has no effect to grade and a grade printed beside one would be read as a
    #: verdict on a number nobody asserted.
    severity: str
    #: How much to believe it, in the one spelling ``strength_word`` gives, or
    #: ``""`` on a refusal.
    strength_word: str
    #: The engine's own sentence — what the number answers, or the refusal and
    #: the field that would finish it. Verbatim, never re-worded: it names a
    #: Settings field by the label that screen actually uses, and a second
    #: spelling of "why there is no figure" would drift from the one the engine
    #: states.
    note: str


# ── what the record says, in words ───────────────────────────────────────────
#
# **Not restricted, with one exception that is not declared here.** Every
# sentence this block prints is a fact about a field on a document — what was
# written down, or that nothing was — so it reaches both roles under one
# wording. The owner's card carries one more sentence, the potential-leakage
# one, and it is not a field on any type this module declares: it arrives on
# ``intent.PricingIntent``, which only the owner's path has. See
# ``render_intent``.

#: An unread reading for a row read back from the store. The same
#: ``NOT_ON_STORED_RECORD`` the attribution and the working-capital reading use,
#: because it is the same fact about the same record: ``quote_diagnoses`` has no
#: column for any of the three, so a stored row cannot answer and must say so
#: rather than answering with silence. Why there is no column is argued at
#: ``rules.ENGINE_VERSION`` — a taxonomy can move retroactively under a stored
#: diagnosis, so a stored reading would be a claim about a mapping that has since
#: been corrected.
INTENT_NOT_STORED = Reading(
    read=False, reason=NOT_ON_STORED_RECORD, reasons=(), codes=(), headline="",
    basis=(f"{NOT_ON_STORED_RECORD}: this is the diagnosis as it was stored, "
           "and what the record says about why this quote was priced is not one "
           "of its columns — it is read when the engine runs, against the "
           "declarations in force at the moment the quote was written. "
           "Re-assess this line to see it."))


@dataclass(frozen=True)
class IntentView:
    """What the record says, as either reader sees it, or the refusal to say.

    Two shapes and no third, which is the shape ``intent.Reading`` itself has.
    Either ``lines`` holds a sentence per concept and ``headline`` says what was
    recorded, or ``lines`` is empty, ``headline`` is blank and ``note`` names
    what stopped it. There is no state where this renders as an empty panel.

    ``read`` is **carried from the engine, never re-derived** from whether a
    sentence happens to be present. A predicate rebuilt downstream from published
    fields is a guess about what the producer meant, which is the lesson
    CLAUDE.md §1 draws from ``_identity_candidate``.
    """

    #: Whether the record was read at all — ``intent.Reading.read``.
    read: bool
    #: Whether this block draws. The line's own gate, narrowed by whether there
    #: is anything written here — never widened, for the reason
    #: ``AttributionView.renders`` is not.
    renders: bool
    #: What was recorded in one sentence, or that nothing was. ``""`` on a
    #: refusal.
    headline: str
    #: One sentence per concept, in the engine's own words — and, on an owner's
    #: card only, the potential-leakage sentence first. Four different statuses
    #: are four different sentences here and are never collapsed: "no field was
    #: declared for this" is not "the field is empty" is not "the field holds
    #: something nobody declared a meaning for".
    lines: tuple[str, ...]
    #: The status codes, from ``rules``' one vocabulary.
    codes: tuple[str, ...]
    #: The standing qualification — that this is read from recorded fields and
    #: that an unrecorded reason is not an absent one — or the refusal and its
    #: reason. Verbatim from the engine, never re-worded: a second spelling of
    #: "why there is nothing here" would drift from the one the engine states.
    note: str


@dataclass(frozen=True)
class OwnerReport:
    """The full picture, economics included. RESTRICTED.

    Sentences rather than a blob, so a screen can lay them out and a test can
    assert on one claim — the same reason ``commercial/diagnosis.py`` returns a
    list.
    """

    line_id: str
    headline: str
    lines: tuple[str, ...]
    opportunity: str
    evidence: str
    codes: tuple[str, ...]
    context: tuple[str, ...]
    #: RESTRICTED. The split of the margin movement, or the refusal to assert
    #: one. Part of the report rather than a second object beside it: it is one
    #: of the things this reader is being told about this line.
    attribution: AttributionView
    #: RESTRICTED. What the cash tied up in this line costs, or the refusal to
    #: say — on the report for the same reason the attribution is.
    working_capital: WorkingCapitalView
    #: What this quote's own record says about why it was priced as it was. The
    #: one block on this report that is **not** restricted — the desk's card
    #: carries the same type — except for the potential-leakage sentence the
    #: owner's copy leads with, which arrives from ``OwnerDiagnosis.intent`` and
    #: is on no type the desk's renderer can reach.
    intent: IntentView


def render_operations(ops: OperationsDiagnosis, *,
                      th: CommercialThresholds) -> OperationsCard:
    """The desk's card, from the desk's own type. Never sees a cost."""
    quoted = th.money(ops.quoted_unit_price) if ops.quoted_unit_price else "—"
    historical = _range(ops.historical_low, ops.historical_high, th)
    return OperationsCard(
        line_id=ops.line_id,
        renders=ops.surfaces,
        comparable=had_enough_to_compare(ops),
        headline=_ops_headline(ops.codes),
        quoted=quoted,
        historical=historical,
        evidence=strength_word(ops.strength),
        evidence_detail=_evidence_detail(ops.comparable_count,
                                         ops.recent_comparable_count),
        # The prose form, not the display form: "between ₹980 – ₹1,020" reads
        # as a typo in a sentence, and the dash belongs in the labelled field
        # above where it is doing a column's work.
        why=_ops_why(ops, _range_words(ops.historical_low, ops.historical_high,
                                       th)),
        note=_ops_note(ops.context),
        # A ``Reading`` and never a ``PricingIntent``: this function takes
        # ``OperationsDiagnosis`` and nothing else, and that type's ``intent`` is
        # declared as the half with no field a margin claim could sit in.
        intent=render_intent(ops.intent, surfaces=ops.surfaces),
    )


def _ops_headline(codes: tuple[str, ...]) -> str:
    """One sentence, in the order the codes were decided.

    ``COST_DRIVEN_MARGIN_RISK`` is worded as what it is and nothing more. The
    specification's own example templates printed "historical acquisition cost
    was ₹700, current cost is ₹900" on a salesperson's screen; this is the
    sentence that replaces them, and it carries no figure to withhold.
    """
    if COST_DRIVEN_MARGIN_RISK in codes:
        return ("Margin on this line is compressed by supply cost, not by your "
                "price. No price change needed.")
    if BELOW_HISTORICAL_RANGE in codes:
        return "Below this customer's historical pricing"
    if ABOVE_HISTORICAL_RANGE in codes:
        return "Above this customer's historical pricing"
    if INSUFFICIENT_EVIDENCE in codes:
        return "Not enough comparable history to judge this price"
    return "Consistent with this customer's historical pricing"


def _ops_why(ops: OperationsDiagnosis, historical: str) -> str:
    if COST_DRIVEN_MARGIN_RISK in ops.codes:
        return ("What this customer pays is in line with what they have paid "
                "before. The pressure on this line is on the supply side.")
    if INSUFFICIENT_EVIDENCE in ops.codes:
        return ("There are too few comparable transactions to say what this "
                "item usually sells for at this quantity.")
    if ops.comparable_count == 0:
        return "There is nothing comparable on record for this item."
    noun = ("once" if ops.comparable_count == 1
            else f"{ops.comparable_count} times")
    # "between" needs two operands and the band often has one. A customer who
    # pays the same price on every order collapses low and high to a single
    # value — the ordinary shape of a repeat account, not an edge case — and
    # the sentence came out as "between ₹218." with nothing after it.
    if ops.historical_low is None or ops.historical_high is None:
        preposition = "in"
    elif ops.historical_low == ops.historical_high:
        preposition = "at"
    else:
        preposition = "between"
    return (f"This customer has purchased this item {noun} at a comparable "
            f"quantity, {preposition} {historical}.")


def _ops_note(context: tuple[str, ...]) -> str:
    """The qualifiers a salesperson can act on, in plain words.

    Only the ones on the operations allowlist reach here, so there is nothing to
    filter — the type already did it.
    """
    notes = []
    if PRICE_RESISTANCE_OBSERVED in context:
        notes.append("This customer has declined quotes at or below the top of "
                     "this range, so the top of it may not be reachable here.")
    if POSSIBLE_EXCEPTIONAL_PRICE in context:
        notes.append("Several past transactions sat well outside the usual "
                     "range and were set aside, so read this range with care.")
    if EVIDENCE_WITHHELD in context:
        notes.append("Some past transactions were left out because it is not "
                     "clear when they became visible.")
    return " ".join(notes)


def render_owner(owner: OwnerDiagnosis, opportunity: Opportunity, *,
                 th: CommercialThresholds) -> OwnerReport:
    """The full report. Cost, margin exposure, the peer band and the evidence."""
    lines: list[str] = []
    band = owner.price.band

    if band.median is not None:
        lines.append(
            f"Quoted {th.money(owner.quoted_unit_price)} per unit against a "
            f"supported range of {_range(band.low, band.high, th)} "
            f"(median {th.money(band.median)}), from {band.sample_count} "
            f"comparable transactions knowable on {owner.as_of.isoformat()}.")
    else:
        lines.append("No comparable transaction was knowable when this quote "
                     "was written, so no range is asserted.")

    if owner.price.excluded:
        lines.append(
            f"{owner.price.excluded_low} below and "
            f"{owner.price.excluded_high} above the usual spread were set "
            f"aside as outliers "
            f"({owner.price.exclusion_rate * 100:.0f}% of the comparables)."
            + (" Past the trust limit, so confidence is capped."
               if owner.price.over_exclusion_limit else ""))

    lines.extend(_cost_lines(owner, th))
    lines.extend(_peer_lines(owner, th))

    if owner.price.resistance.observed:
        lines.append(
            f"{owner.price.resistance.lost_at_or_below_band} quote(s) to this "
            f"customer were declined at or below the top of this range. The "
            f"highest price they have accepted is "
            f"{th.money(owner.price.resistance.highest_accepted)}.")

    return OwnerReport(
        line_id=owner.line_id,
        headline=_ops_headline(owner.codes),
        lines=tuple(lines),
        opportunity=_opportunity_sentence(opportunity, th),
        evidence=_evidence_sentence(owner),
        codes=owner.codes,
        context=owner.context,
        attribution=render_attribution(owner.attribution,
                                       surfaces=owner.surfaces, th=th),
        working_capital=render_working_capital(owner.working_capital,
                                               surfaces=owner.surfaces, th=th),
        intent=render_intent(owner.intent, surfaces=owner.surfaces),
    )


def render_intent(reading: "Reading | PricingIntent", *,
                  surfaces: bool) -> IntentView:
    """What the record says, as either reader sees it. One wording, three callers.

    **Takes either projection**, for the reason ``rules.had_enough_to_compare``
    does: both carry ``codes``, both describe one record, and the two roles must
    be told the same facts in the same words. A second renderer for the owner's
    copy would be a second answer to what the record says, and the copy nobody
    reads is the one that drifts. The stored-row projection — which has a row and
    no engine object — refuses through this same function too, from
    ``INTENT_NOT_STORED``.

    **What the owner's projection adds is one sentence, and it is the only
    RESTRICTED thing here.** ``PricingIntent.exposure`` says a line below its band
    has an empty pricing-reason field on it, which is a claim about this line's
    margin rather than about the record; ``PricingIntent.codes`` is the same fact
    in the engine's vocabulary. They arrive together on one object because they
    are one fact — passing them as two arguments would let a caller supply the
    sentence without the code, and neither is ever re-derived from the other
    here. It leads the block because it is the finding and the four sentences
    under it are the support.

    The desk cannot reach that branch: ``render_operations`` takes
    ``OperationsDiagnosis`` and nothing else, and that type's ``intent`` is
    declared a ``Reading``. So this is a type error rather than a check that
    could be forgotten.

    **The refusal is a case this is written around**, as it is for the two blocks
    above. A quote drafted in the builder has no source document; a book whose
    administrator has declared nothing has no field to read. Each leaves ``lines``
    empty, and an empty block that simply did not draw would read as "no reason
    was recorded" — which is the one thing an unread record does not mean, and is
    CLAUDE.md §1's *absence of evidence is not a pass* wearing a layout. So the
    refusal goes into ``note`` in the engine's own words and the block still
    draws.
    """
    restricted = reading if isinstance(reading, PricingIntent) else None
    said = restricted.reading if restricted is not None else reading
    lines = tuple(r.sentence for r in said.reasons)
    if restricted is not None and restricted.exposure:
        lines = (restricted.exposure,) + lines
    # One gate, narrowed. ``surfaces`` is ``rules._surfaces``' answer and is
    # never widened here; the second term only makes it impossible to publish a
    # block with nothing written in it. Nothing in this reading feeds that gate:
    # a recorded intent may explain an interruption and must never cause one.
    return IntentView(
        # The producer's own flag, not ``reason == READ`` rebuilt from it. A
        # predicate re-derived downstream from published fields is a guess about
        # what the producer meant (CLAUDE.md §1), and here the producer knows
        # something the sentences do not say: a book with nothing declared is a
        # successful reading with four sentences saying exactly that.
        read=said.read,
        renders=bool(surfaces and (said.headline or said.basis)),
        headline=said.headline, lines=lines,
        # Each projection's own codes: the reading's four, or those plus the
        # claim. Read off whichever object arrived rather than rebuilt from the
        # sentence above, because a predicate re-derived downstream is a guess
        # about what the producer meant.
        codes=reading.codes,
        note=said.basis)


def render_attribution(attribution: Attribution, *, surfaces: bool,
                       th: CommercialThresholds) -> AttributionView:
    """The split, or the refusal, as an owner reads it. RESTRICTED.

    Takes an ``Attribution`` rather than an ``OwnerDiagnosis``, so the
    stored-row projection — which has a row and no diagnosis object — renders
    its own refusal through this same function instead of a copy of it.

    **The refusal is the case this is written around.** An attribution declines
    far more often than it asserts: no cost on record, evidence too thin, a
    purchase the quoter could not have seen, a residual the two effects cannot
    account for. Each of those leaves ``drivers`` empty, and an empty block that
    simply did not draw would read as "nothing to report" — which is the failure
    CLAUDE.md §1 names, in a new place. So the refusal goes into ``note`` in the
    engine's own words, naming what is missing, and ``renders`` stays true for
    it exactly as it would for a split.

    **The residual is never lost.** It is not a field here because it does not
    need to be: ``drivers`` states it in the basis on both paths — the distance
    the two effects miss the movement by when they reconcile, and the distance
    that refused them when they do not.
    """
    lines = tuple(_driver_line(d, th) for d in attribution.drivers)
    headline = _split_headline(attribution) if lines else ""
    note = attribution.basis
    # One gate, narrowed. ``surfaces`` is ``rules._surfaces``' answer and is
    # never widened here; the second term only makes it impossible to publish a
    # block with nothing written in it.
    return AttributionView(renders=bool(surfaces and (headline or note)),
                           headline=headline, drivers=lines, note=note)


def _driver_line(driver: Driver, th: CommercialThresholds) -> DriverLine:
    return DriverLine(
        code=driver.code,
        severity=driver.severity,
        # The one spelling of a grade on either card, reused rather than
        # restated: a second word for MODERATE would be a second answer.
        strength_word=strength_word(driver.strength),
        effect=(f"{_signed_pp(driver.effect_pp)} "
                f"({_signed_money(driver.effect_per_unit, th)} per unit)"),
        basis=driver.basis,
    )


def _split_headline(attribution: Attribution) -> str:
    """Both factors, named and signed, in the order they were measured.

    **Every driver appears, including one that moved nothing.** Reporting the
    larger term alone is the defect this whole computation exists to prevent: a
    cost rise of 7% beside a price cut of 3%, headlined "cost increase", is true
    and excuses the half somebody chose. A flat factor is stated as flat rather
    than dropped, because "cost level unchanged" is the sentence that tells a
    reader the price is the whole of it.
    """
    parts = ", ".join(f"{_DRIVER_LABEL.get(d.code, d.code)} "
                      f"{_signed_pp(d.effect_pp)}"
                      for d in attribution.drivers)
    reference = ("the same line at the band median price and the historical "
                 "purchase cost")
    movement = attribution.movement_pp
    if movement is None or movement == 0:
        return f"Margin on this line is level with {reference}: {parts}."
    direction = "lower" if movement < 0 else "higher"
    return (f"Margin on this line is {_pp_magnitude(movement)} {direction} than "
            f"{reference}: {parts}.")


def _signed_pp(value: Optional[Decimal]) -> str:
    """``+3.16 pp`` / ``-3.16 pp`` / ``unchanged``.

    Signed rather than magnitude-plus-a-word, because both signs appear in one
    sentence here and "3.16 pp price, 10.53 pp cost level" reads as two losses
    when one of them was a gain. ``commercial/diagnosis.py``'s ``_pp`` renders
    the magnitude and carries direction in a separate verb, which that screen's
    one-figure sentences can do and this one cannot.

    Two decimal places: ``PP_QUANTUM`` holds four, which is four more than a
    person can act on, and the unrounded figures are in ``note``.
    """
    if value is None:
        return "not measured"
    if value == 0:
        return "unchanged"
    return f"{'+' if value > 0 else '-'}{_pp_magnitude(value)}"


def _pp_magnitude(value: Decimal) -> str:
    return f"{abs(value) * 100:.2f} pp"


def _signed_money(value: Optional[Decimal], th: CommercialThresholds) -> str:
    """``+₹50`` / ``-₹50``, through the policy's own spelling of money.

    ``th.money`` puts the sign inside the amount — ``₹-50`` — which reads as a
    typo, so the magnitude goes through it and the sign is carried outside.
    Whole units, like every other amount this platform prints: the percentage
    points beside it carry the resolution.
    """
    if value is None:
        return th.money(None)
    if value == 0:
        return th.money(value)
    return f"{'+' if value > 0 else '-'}{th.money(abs(value))}"


def render_working_capital(capital: WorkingCapital, *, surfaces: bool,
                           th: CommercialThresholds) -> WorkingCapitalView:
    """What this line's cash costs, or the refusal, as an owner reads it. RESTRICTED.

    Takes a ``WorkingCapital`` rather than an ``OwnerDiagnosis``, so the
    stored-row projection — which has a row and no diagnosis object — renders its
    own refusal through this same function instead of a copy of it.

    **The refusal is the case this is written around.** The rate is owner-set
    with no default, so the commonest reading on a fresh book is ``NO_RATE``: one
    person typing one number into Settings finishes it, and the engine's own
    sentence names the field by the label that screen uses. Four more refusals
    sit behind it. Each leaves ``figures`` empty, and an empty block that simply
    did not draw would read as "nothing to report" — which is CLAUDE.md §1's
    *absence of evidence is not a pass* wearing a layout. So the refusal goes
    into ``note`` in the engine's own words and the block still draws.

    **Every figure is formatted here and nowhere else.** The front end may not
    format money or compute a number (CLAUDE.md §3), and a figure rounded in two
    places is two answers to one question.
    """
    assessed = capital.reason == ASSESSED
    figures = _capital_figures(capital, th) if assessed else ()
    headline = _capital_headline(capital, th) if assessed else ""
    note = capital.basis
    return WorkingCapitalView(
        assessed=assessed,
        # Two gates conjoined, so this can only be more silent than either.
        interrupts=bool(surfaces and capital.surfaces),
        renders=bool(surfaces and (headline or note)),
        headline=headline,
        figures=figures,
        severity=capital.severity if assessed else "",
        strength_word=strength_word(capital.strength) if assessed else "",
        note=note,
    )


def _capital_headline(capital: WorkingCapital, th: CommercialThresholds) -> str:
    """The money in one sentence.

    Deliberately the half the engine's own ``basis`` does not state. That
    sentence carries the days, where each leg's number came from, and the margin
    points; this carries the rupees. Two sentences that overlapped would be two
    roundings of one figure and would drift the first time either was edited.
    """
    if capital.charge_per_unit == _CAPITAL_ZERO:
        return ("No funding charge on this line: the money is back before the "
                "supplier is paid.")
    return (f"Funding this line's cash costs {th.money(capital.line_charge)} "
            f"({th.money(capital.charge_per_unit)} per unit), taking "
            f"{_pp_magnitude(capital.effect_pp)} off its margin.")


def _capital_figures(capital: WorkingCapital, th: CommercialThresholds,
                     ) -> tuple[tuple[str, str], ...]:
    """The reading as labelled figures, in the order the arithmetic runs.

    The two legs before the window they make, the capital before the charge
    levied on it, and the rate last — which is the order the sentence in ``note``
    walks, so a reader checking one against the other reads down rather than
    hunting.
    """
    return (
        ("Customer pays in", _days(capital.receivable_days)),
        ("Supplier credit", _days(capital.supplier_credit_days)),
        ("Money out for", _days(capital.funded_days)),
        ("Capital at risk", f"{th.money(capital.capital_at_risk)} "
                            f"({th.money(capital.capital_per_unit)} per unit)"),
        ("Funding charge", f"{th.money(capital.line_charge)} "
                           f"({th.money(capital.charge_per_unit)} per unit)"),
        ("Margin effect", _signed_pp(capital.effect_pp)),
        ("Cost of capital", _annual_pct(capital.rate)),
    )


def _days(value: Optional[int]) -> str:
    """``45 days``, ``1 day``, and a negative window said as what it means.

    ``funded_days`` is signed: negative is the supplier funding this line
    outright, which is a real and good answer rather than a missing one. Printing
    ``-15 days`` would read as an error, and printing ``0`` would hide that the
    credit more than covers the wait.
    """
    if value is None:
        return "unknown"
    if value < 0:
        return f"nothing — supplier credit runs {abs(value)} days longer"
    return f"{value} day" if value == 1 else f"{value} days"


def _annual_pct(value: Optional[Decimal]) -> str:
    """The cost of capital as a reader sees it. A ratio in, a percentage out —
    the conversion belongs here rather than in a browser, like every other."""
    if value is None:
        return "unknown"
    return f"{value * 100:.2f}% a year"


def _cost_lines(owner: OwnerDiagnosis, th: CommercialThresholds) -> list[str]:
    """RESTRICTED. Reached only from ``render_owner``."""
    cost = owner.cost
    if not cost.known:
        return ["No purchase cost was knowable when this quote was written, so "
                "the cost side of this diagnosis is withheld rather than "
                "estimated."]
    out = [f"Expected cost {th.money(cost.expected_cost)} per unit, from "
           f"{cost.observations} purchase(s) on record."]
    if KNOWN_COST_CHANGE in owner.codes:
        out.append(
            f"Cost had already moved to {th.money(cost.latest_cost)} on "
            f"{cost.latest_on.isoformat()}, recorded before this quote was "
            f"written, so the baseline reflects the new level rather than the "
            f"historical {th.money(cost.historical_cost)}.")
    if POSSIBLE_COST_DRIVEN in owner.context:
        out.append(
            "At least one purchase sat well below the normal acquisition band "
            "and was set aside. Nothing in the ledger records why, so no "
            "explanation is asserted — the rows are attached.")
    return out


def _peer_lines(owner: OwnerDiagnosis, th: CommercialThresholds) -> list[str]:
    """RESTRICTED. A peer's price is another customer's position."""
    if BELOW_PEER_BAND_STRUCTURAL not in owner.codes:
        return []
    peer = owner.peer
    scope = ("comparable customers" if peer.segment_applied
             else "every other customer on record")
    return [f"This quote is consistent with this account's own history, and "
            f"that whole history sits below what {peer.customer_count} of "
            f"{scope} pay — a median of {th.money(peer.stats.median)}. That is "
            f"an account-level pricing question, not a question about this "
            f"quote, and it is not shown to the salesperson."]


def _opportunity_sentence(opp: Opportunity, th: CommercialThresholds) -> str:
    """Potential, never missed (§I5)."""
    if not opp.exists:
        return f"No opportunity is asserted — {opp.basis}."
    sentence = (f"Historical evidence suggests a potential margin opportunity "
                f"of {th.money(opp.low)}–{th.money(opp.high)} on this line. "
                f"This is an estimate of what was plausibly achievable, not "
                f"profit forgone.")
    if opp.truncated_by_resistance:
        sentence += (" The upper end is capped at the highest price this "
                     "customer has actually accepted.")
    if not opp.cost_on_record:
        sentence += (" No purchase cost is on record, so a cost-driven cause "
                     "for this price cannot be ruled out.")
    return sentence


def _evidence_sentence(owner: OwnerDiagnosis) -> str:
    summary = owner.evidence
    parts = [f"{summary['usable']} usable, {summary['excluded']} excluded"]
    for reason, count in (summary.get("excluded_by_reason") or {}).items():
        parts.append(f"{count} {reason.lower().replace('_', ' ')}")
    if summary.get("outcome_recorded") == 0:
        parts.append("no quote outcomes recorded, so every price here is one "
                     "the customer accepted")
    if summary.get("backfill_cutover_unknown"):
        parts.append("this connection has no migration cut-over on record, so "
                     "bulk-loaded history cannot be told from live entry")
    return "; ".join(parts) + "."


def _range_words(low, high, th: CommercialThresholds) -> str:
    """The same range for the middle of a sentence."""
    if low is None or high is None:
        return "an unknown range"
    if low == high:
        return th.money(low)
    return f"{th.money(low)} and {th.money(high)}"


def _range(low, high, th: CommercialThresholds) -> str:
    if low is None or high is None:
        return "—"
    if low == high:
        return th.money(low)
    return f"{th.money(low)} – {th.money(high)}"


def _evidence_detail(total: int, recent: int) -> str:
    if total == 0:
        return "no comparable transactions"
    noun = "transaction" if total == 1 else "transactions"
    if recent == 0:
        return f"{total} comparable {noun}, none in the last 12 months"
    if recent == total:
        return f"{total} comparable {noun}, all in the last 12 months"
    return f"{total} comparable {noun}, {recent} in the last 12 months"


def dismissal_reasons() -> tuple[tuple[str, str], ...]:
    """The vocabulary a dismissal must come back in, for the front end."""
    return tuple(DISMISS_REASONS.items())


def unused_codes() -> frozenset[str]:
    """Codes the operations renderer deliberately has no sentence for.

    Named so the omission is a decision on the record rather than a gap: both
    route to the owner, and a card that mentioned either would be telling a
    salesperson something they cannot act on and should not repeat.
    """
    return frozenset({BELOW_PEER_BAND_STRUCTURAL, KNOWN_COST_CHANGE,
                      NO_COST_EVIDENCE, WITHIN_HISTORICAL_RANGE})
