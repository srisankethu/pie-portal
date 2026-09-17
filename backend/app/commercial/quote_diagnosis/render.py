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

from ..config import CommercialThresholds
from .opportunity import Opportunity
from .rules import (ABOVE_HISTORICAL_RANGE, BELOW_HISTORICAL_RANGE,
                    BELOW_PEER_BAND_STRUCTURAL, COST_DRIVEN_MARGIN_RISK,
                    EVIDENCE_WITHHELD, INSUFFICIENT_EVIDENCE, KNOWN_COST_CHANGE,
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
    qualification: str = QUALIFICATION
    actions: tuple[str, ...] = (REVIEW_PRICE, DISMISS)


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


def render_operations(ops: OperationsDiagnosis, *,
                      th: CommercialThresholds) -> OperationsCard:
    """The desk's card, from the desk's own type. Never sees a cost."""
    quoted = th.money(ops.quoted_unit_price) if ops.quoted_unit_price else "—"
    historical = _range(ops.historical_low, ops.historical_high, th)
    return OperationsCard(
        line_id=ops.line_id,
        renders=ops.surfaces,
        comparable=INSUFFICIENT_EVIDENCE not in ops.codes,
        headline=_ops_headline(ops.codes),
        quoted=quoted,
        historical=historical,
        evidence=_STRENGTH_WORD.get(ops.strength, "Not enough"),
        evidence_detail=_evidence_detail(ops.comparable_count,
                                         ops.recent_comparable_count),
        # The prose form, not the display form: "between ₹980 – ₹1,020" reads
        # as a typo in a sentence, and the dash belongs in the labelled field
        # above where it is doing a column's work.
        why=_ops_why(ops, _range_words(ops.historical_low, ops.historical_high,
                                       th)),
        note=_ops_note(ops.context),
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
    return (f"This customer has purchased this item {noun} at a comparable "
            f"quantity, between {historical}.")


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
    )


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
