"""What a quote says about itself as a whole, once every line has been diagnosed.

The engine answers per line. A quote is what somebody actually sends, so there
is a second question — what is on this document in total — and answering it
badly is worse than not answering it at all.

── the total may not bury a line ────────────────────────────────────────────

**A line that loses money is named, whatever the total says.** ``loss_lines`` is
a required field, it is built in the same pass as the totals from the same
figures, and it is on ``to_dict`` unconditionally. There is no shape of this
object that publishes an aggregate without it, which is the point: a quote at
22% with one line at −14% is the ordinary case this whole line-level engine
exists for, and a roll-up that reported only the 22% would be the most
convincing wrong number on the screen.

``quote_service.summarize`` refuses to publish a blended margin at all, for
exactly that reason, and its docstring says so — "a single blended margin across
a mixed quote is the number people quote back at each other while the
loss-making line stays invisible". That refusal is not overturned here and this
object is not a way around it: that one serves both roles from the Quote
Builder's assessment, and this one is owner-only and carries the invisible lines
with it. The two answer different readers. If the loss lines were ever dropped
from here, that function's position would be the correct one and this object
should go.

── the arithmetic is borrowed, not restated ─────────────────────────────────

``portfolio.MarginAggregate``  Σ gross profit ÷ Σ **costed** revenue, plus the
                               share of revenue that margin actually speaks for.
                               The one accumulator, already reused by
                               ``insight/financing``; its own docstring records
                               that four modules had this rule independently and
                               one of them got it wrong on a live book.
``quote_intelligence.QuoteLineEconomics``  what one line earns at the proposed
                               price. Already RESTRICTED, already serialised.
``rules.had_enough_to_compare``  whether a line was judged at all — the field
                               that answers it, not the codes.

Revenue counts every priced line and margin is taken over the costed subset
only, which is ``economics.aggregate``'s rule as well: leaving uncosted revenue
in the denominator drags the margin toward zero and reads on screen as a pricing
problem when it is a cost-coverage one. ``revenue_coverage`` is how much of the
quote the margin speaks for, and it is published beside the margin rather than
left for a reader to wonder about.

── the split between the two readers is structural ──────────────────────────

``QuoteCoverage`` **has no money field at all** — not withheld, not masked,
absent — exactly as ``rules.OperationsDiagnosis`` has none, and
``test_quote_coverage_has_no_economics_field`` reads its field list to hold it.
Every count on it is derived from the price band, the evidence grade or the
presence of a quoted price, so a caller who walks the quoted price and watches
them move learns where a band is, which is made of prices this customer has
already seen. None of them answers a margin question, which is the line
``filterCounts.MFLOOR`` crossed: a count computed over cost is a sharper oracle
than a per-line flag, not a blunter one.

``QuoteRollup`` holds the coverage rather than restating it, the way
``intent.PricingIntent`` holds the ``Reading`` it shares with the desk, so the
two readers cannot come to be told different counts about one quote.

── two producers, one input shape ───────────────────────────────────────────

``LineFacts`` is what the roll-up reads, and ``from_diagnosis`` builds one from a
live ``OwnerDiagnosis``. The second producer is the stored row: ``GET
/quote/{quote_id}`` serves diagnoses as they were written and has no
``OwnerDiagnosis`` to hand, only columns. It has the price, the quantity, the
codes, the grade, the gate and ``cost_baseline["expected_cost"]`` — everything
except the attribution, for which there is no column, and a roll-up over stored
rows therefore names no dominant driver and says so. That is why the input is a
record rather than the diagnosis itself: a shape only one of the two callers can
build is a shape the other one copies badly.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Optional, Sequence

from ..portfolio import MarginAggregate
from ..quote_intelligence import QuoteLineEconomics
from .drivers import Attribution
from .rules import OwnerDiagnosis, had_enough_to_compare

_ZERO = Decimal("0")


# ── what the roll-up reads ───────────────────────────────────────────────────

@dataclass(frozen=True)
class LineFacts:
    """One diagnosed line, as either producer can state it.

    ``codes`` and ``strength`` are here together because
    ``rules.had_enough_to_compare`` reads both, and it is the one answer to "was
    this line judged at all" — asking it here rather than re-deriving it from a
    grade or a code is the rule that function's own docstring exists to state.
    """

    line_id: str
    product_id: str
    qty: Decimal
    #: What the customer is being asked to pay per unit. ``None`` on a line the
    #: quote carries no price for, which is counted rather than costed.
    quoted_unit_price: Optional[Decimal]
    #: RESTRICTED. ``CostBaseline.expected_cost`` — the level this quote should
    #: have been priced against, which is the same figure ``working_capital``
    #: treats as the cash that leaves the bank. ``None`` where no purchase was
    #: knowable, and never a zero: a nought cost would report a 100% margin on
    #: the platform's own ignorance.
    unit_cost: Optional[Decimal]
    codes: tuple[str, ...]
    strength: str
    #: The engine's own gate for this line — ``OwnerDiagnosis.surfaces``.
    surfaces: bool
    #: RESTRICTED. The margin split, or its refusal. A stored row has no column
    #: for it and passes ``render.NOT_STORED``, whose empty ``drivers`` is what
    #: makes the roll-up decline to name a dominant factor.
    attribution: Attribution
    #: Which policy judged this line. Carried so a roll-up over rows written
    #: either side of a policy edit can say that it is one, rather than
    #: presenting two stamps as one number.
    thresholds_version: Optional[str] = None


def from_diagnosis(owner: OwnerDiagnosis) -> LineFacts:
    """The live producer. Reads the diagnosis; computes nothing of its own."""
    return LineFacts(
        line_id=owner.line_id, product_id=owner.product_id, qty=owner.qty,
        quoted_unit_price=owner.quoted_unit_price,
        unit_cost=owner.cost.expected_cost,
        codes=owner.codes, strength=owner.strength, surfaces=owner.surfaces,
        attribution=owner.attribution,
        thresholds_version=owner.thresholds_version)


# ── the desk's half ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class QuoteCoverage:
    """What was checked on this quote and what could not be. No money anywhere.

    **Every field is a count or a word.** There is no cost field, no margin
    field, no value and no loss count — not withheld, absent. That is the
    guarantee, and a test reads this dataclass's own field list to hold it, so
    adding one is a failing test rather than a review somebody has to catch.

    A loss count is the field this type most obviously wants and most certainly
    may not have: "how many lines lose money" is a margin question with a
    yes/no answer per line, which is what ``filterCounts.MFLOOR`` was.
    """

    quote_id: str
    #: Lines diagnosed. Zero is a real answer and ``basis`` says so.
    lines: int
    #: Lines there was enough comparable history to judge —
    #: ``rules.had_enough_to_compare``, never a second reading of the codes.
    lines_compared: int
    #: And the ones there was not. Published rather than left to subtraction,
    #: because this is the number that must not read as "all clear".
    lines_not_compared: int
    #: Lines the quote carries no price for, so nothing could be compared
    #: against anything.
    lines_without_price: int
    #: Lines the engine decided were worth interrupting somebody about.
    lines_surfacing: int
    #: What was checked and what could not be, in words. Never empty: an empty
    #: panel reads as "all clear", which is the failure CLAUDE.md §1 names.
    basis: str

    def to_dict(self) -> dict:
        return {"quote_id": self.quote_id, "lines": self.lines,
                "lines_compared": self.lines_compared,
                "lines_not_compared": self.lines_not_compared,
                "lines_without_price": self.lines_without_price,
                "lines_surfacing": self.lines_surfacing,
                "basis": self.basis}


def coverage_field_names() -> frozenset[str]:
    """The coverage view's own field names, for the structural test."""
    return frozenset(f.name for f in fields(QuoteCoverage))


# ── the owner's half ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LossLine:
    """A line that loses money at the price quoted. RESTRICTED.

    Its own type rather than a bare id, because a list of line ids would send a
    reader back to the per-line cards to find out how much — and the whole
    reason this exists is that a total is not where that is visible.
    """

    line_id: str
    product_id: str
    #: The line's own economics at the quoted price, from the type
    #: ``quote_intelligence`` already declares for exactly this.
    economics: QuoteLineEconomics

    def to_dict(self) -> dict:
        """RESTRICTED. Only ever serialised into an owner-facing payload."""
        return {"line_id": self.line_id, "product_id": self.product_id,
                **self.economics.to_dict()}


@dataclass(frozen=True)
class DriverTotal:
    """One factor's effect across the quote, in money. RESTRICTED.

    Money and not percentage points, and that is the whole reason this is
    summable: ``Driver.effect_per_unit`` is a difference of prices and costs
    with no division in it, so ``Σ effect_per_unit × qty`` is exact. Adding
    per-line ``effect_pp`` figures would be the mean-of-margins mistake in a
    different hat — a four-point movement on a ₹2,000 line and a four-point
    movement on a ₹2,00,000 line are not one eight-point movement.
    """

    code: str
    #: Across the quote. **Negative means this factor cost margin**, which is
    #: ``drivers``' sign convention and not a second one.
    effect: Decimal
    #: How many lines contributed. A total over two lines and a total over forty
    #: are different claims.
    lines: int

    def to_dict(self) -> dict:
        return {"code": self.code, "effect": float(round(self.effect, 2)),
                "lines": self.lines}


@dataclass(frozen=True)
class QuoteRollup:
    """What this quote comes to, with the lines its total does not show.

    RESTRICTED except for ``coverage``, which is the half with no money on it
    and which is held here rather than duplicated so both readers count one
    quote the same way.
    """

    quote_id: str
    #: The desk's half. Published to both roles.
    coverage: QuoteCoverage

    #: Σ line revenue over every priced line, whether or not its cost is known.
    value: Decimal
    #: The part of that value whose cost is on record — the denominator the
    #: margin is actually taken over.
    costed_value: Decimal
    #: Σ gross profit over the costed lines. ``None`` — never zero — when no line
    #: on this quote has a purchase cost behind it. "We cannot say" and "we made
    #: nothing" are different answers.
    gross_profit: Optional[Decimal]
    #: ``gross_profit ÷ costed_value``, a ratio (0.24), never the mean of the
    #: per-line margins. ``None`` where nothing is costed.
    margin: Optional[float]
    #: How much of the quote's value that margin speaks for, as a ratio. A
    #: margin over a fifth of a quote is not the same claim as a margin over all
    #: of it, and a caller that shows one should be able to say which.
    revenue_coverage: Optional[float]
    #: Priced lines with no purchase cost knowable. Counted rather than costed
    #: at zero.
    lines_without_cost: int

    #: **Every line that loses money at the price quoted, whatever the total
    #: says.** Worst first. Empty means checked and none found — it is emitted
    #: on every payload, so an empty list is an answer rather than an absence.
    loss_lines: tuple[LossLine, ...]

    #: Each factor's effect across the quote, largest magnitude first.
    driver_totals: tuple[DriverTotal, ...]
    #: The factor with the largest effect across the quote, or ``None`` where no
    #: line could be attributed — which is what a roll-up over stored rows
    #: always answers, because there is no attribution column.
    dominant_driver: Optional[str]
    #: Lines whose margin movement was actually split. The denominator for the
    #: sentence above; a dominant factor drawn from one line of forty is not a
    #: statement about the quote.
    lines_attributed: int

    #: The one policy version behind these lines, or ``None`` where they do not
    #: agree — a roll-up spanning a policy edit is a fact worth seeing rather
    #: than one stamp chosen over another.
    thresholds_version: Optional[str]
    #: What these totals rest on, what is left out of them, and the loss lines
    #: first where there are any. Never empty.
    basis: str

    @property
    def loss_value(self) -> Decimal:
        """What the loss-making lines lose between them, as a positive amount."""
        return -sum((ln.economics.gross_profit or _ZERO
                     for ln in self.loss_lines), _ZERO)

    @property
    def total_hides_a_loss(self) -> bool:
        """Whether reading the total alone would miss a line that loses money.

        Reported rather than relied on: the guarantee is ``loss_lines``, which
        is published whatever this says. This is the sentence a screen leads
        with, computed once here so two screens cannot decide it differently.
        """
        return bool(self.loss_lines) and (self.margin is None
                                          or self.margin >= 0)

    def to_dict(self) -> dict:
        """RESTRICTED. Only ever serialised into an owner-facing payload."""
        def m(v: Optional[Decimal]) -> Optional[float]:
            return float(round(v, 2)) if v is not None else None
        return {
            "quote_id": self.quote_id,
            "coverage": self.coverage.to_dict(),
            "value": m(self.value),
            "costed_value": m(self.costed_value),
            "gross_profit": m(self.gross_profit),
            "margin": round(self.margin, 4) if self.margin is not None else None,
            "revenue_coverage": (round(self.revenue_coverage, 4)
                                 if self.revenue_coverage is not None else None),
            "lines_without_cost": self.lines_without_cost,
            # Unconditional, and the one key on this payload that is. A reader
            # who found it missing would read the absence as "no line loses
            # money", which is the one thing it does not mean.
            "loss_lines": [ln.to_dict() for ln in self.loss_lines],
            "loss_value": m(self.loss_value),
            "total_hides_a_loss": self.total_hides_a_loss,
            "driver_totals": [d.to_dict() for d in self.driver_totals],
            "dominant_driver": self.dominant_driver,
            "lines_attributed": self.lines_attributed,
            "thresholds_version": self.thresholds_version,
            "basis": self.basis,
        }


# ── the roll-up ──────────────────────────────────────────────────────────────

def roll_up(lines: Sequence[LineFacts], *, quote_id: str) -> QuoteRollup:
    """Total one quote. Pure, total and deterministic.

    One pass over the lines builds the aggregate and the loss list together, so
    there is no arrangement in which the two disagree about a line. Every
    ordering is fixed — the loss lines by their own loss and then their id, the
    driver totals by magnitude and then their code — because two runs over one
    quote must produce the same bytes and a dict iteration order is not an
    ordering.

    An empty quote is not an error and not a zero: the totals are what they are,
    and ``basis`` says that nothing was diagnosed.
    """
    agg = MarginAggregate()
    loss: list[LossLine] = []
    effects: dict[str, list[Decimal]] = {}

    compared = surfacing = without_price = without_cost = 0
    versions: set[str] = set()

    for line in lines:
        if line.thresholds_version:
            versions.add(line.thresholds_version)
        if had_enough_to_compare(line):
            compared += 1
        if line.surfaces:
            surfacing += 1
        if line.quoted_unit_price is None:
            without_price += 1

        econ = _line_economics(line)
        if econ.line_revenue is not None:
            agg.revenue += econ.line_revenue
            if econ.gross_profit is not None:
                agg.costed_revenue += econ.line_revenue
                agg.gross_profit = (agg.gross_profit or _ZERO) + econ.gross_profit
            else:
                without_cost += 1
            if econ.gross_profit is not None and econ.gross_profit < _ZERO:
                loss.append(LossLine(line_id=line.line_id,
                                     product_id=line.product_id,
                                     economics=econ))

        for driver in line.attribution.drivers:
            if driver.effect_per_unit is None:
                continue
            effects.setdefault(driver.code, []).append(
                driver.effect_per_unit * line.qty)

    loss.sort(key=lambda ln: (ln.economics.gross_profit or _ZERO, ln.line_id))
    totals = tuple(sorted(
        (DriverTotal(code=code, effect=sum(values, _ZERO), lines=len(values))
         for code, values in effects.items()),
        key=lambda d: (-abs(d.effect), d.code)))
    attributed = sum(1 for line in lines if line.attribution.drivers)

    coverage = QuoteCoverage(
        quote_id=quote_id, lines=len(lines), lines_compared=compared,
        lines_not_compared=len(lines) - compared,
        lines_without_price=without_price, lines_surfacing=surfacing,
        basis=_coverage_basis(total=len(lines), compared=compared,
                              without_price=without_price, surfacing=surfacing))

    return QuoteRollup(
        quote_id=quote_id, coverage=coverage,
        value=agg.revenue, costed_value=agg.costed_revenue,
        gross_profit=agg.gross_profit, margin=agg.margin,
        revenue_coverage=agg.revenue_coverage,
        lines_without_cost=without_cost,
        loss_lines=tuple(loss), driver_totals=totals,
        dominant_driver=(totals[0].code if totals else None),
        lines_attributed=attributed,
        # One version or none. Two stamps on one total mean the lines were
        # judged by two policies, and naming either of them would say the quote
        # was judged by a policy half of it never saw.
        thresholds_version=(next(iter(versions)) if len(versions) == 1 else None),
        basis=_basis(total=len(lines), costed=agg.costed_revenue,
                     without_cost=without_cost, losses=len(loss),
                     margin=agg.margin, totals=totals, attributed=attributed,
                     mixed_versions=len(versions) > 1))


def _line_economics(line: LineFacts) -> QuoteLineEconomics:
    """One line at the price quoted. RESTRICTED.

    The same four figures ``quote_intelligence._economics`` derives, into the
    same type, and it is restated here rather than called for one reason: that
    function is private to its module and takes a ``CostRow``-shaped basis to
    read a ``cost_source_ref`` off. A diagnosis holds a ``CostBaseline`` — a
    trimmed level over many purchases, citing its rows by id — and has no single
    row to hand it. Manufacturing one to satisfy the signature would be a worse
    lie than four lines of arithmetic, which is the trade-off
    ``portfolio.MarginAggregate`` records making against ``economics.aggregate``
    for the same reason. Both sites name the other; if the rule ever changes,
    the docstrings lead to each other.

    Missing cost stays missing. A zero or negative cost never reaches here —
    ``baselines.cost_baseline`` withholds one rather than reporting a 100%
    margin on a placeholder.
    """
    price = line.quoted_unit_price
    revenue = (price * line.qty) if price is not None else None
    cogs = (line.unit_cost * line.qty) if line.unit_cost is not None else None
    gross_profit = (revenue - cogs) if (revenue is not None
                                        and cogs is not None) else None
    margin = (float(gross_profit / revenue)
              if gross_profit is not None and revenue is not None
              and revenue > _ZERO else None)
    return QuoteLineEconomics(
        unit_cost=line.unit_cost, quoted_unit_price=price, qty=line.qty,
        line_revenue=revenue, cogs=cogs, gross_profit=gross_profit,
        margin=margin)


def _coverage_basis(*, total: int, compared: int, without_price: int,
                    surfacing: int) -> str:
    """What was checked and what could not be. Three sentences at most.

    The absence half is written first on purpose where it is the larger one: a
    reader who is told "nothing stood out" before being told that half the lines
    had nothing to compare against has been told the wrong thing first.
    """
    if total == 0:
        return ("No line on this quote was diagnosed, so nothing was checked "
                "and nothing is claimed about it.")
    noun = "line" if total == 1 else "lines"
    parts = [f"{compared} of {total} {noun} had enough comparable history to "
             f"judge the price against."]
    gap = total - compared
    if gap:
        parts.append(
            f"{gap} did not, so nothing is claimed about {'it' if gap == 1 else 'them'}"
            + (f" — {without_price} of those carries no quoted price at all."
               if without_price else " — that is an absence of evidence, not a pass."))
    parts.append(f"{surfacing} raised something worth reading."
                 if surfacing else
                 "Nothing on the lines that could be judged was unusual enough "
                 "to interrupt anybody.")
    return " ".join(parts)


def _basis(*, total: int, costed: Decimal, without_cost: int, losses: int,
           margin: Optional[float], totals: Sequence[DriverTotal],
           attributed: int, mixed_versions: bool) -> str:
    """What the totals rest on, with the loss lines named first.

    No money figure appears in this sentence. The amounts are on the object and
    are spelled by the renderer in the tenant's currency; a number formatted
    here as well would be a second rounding of one value, and the two would
    disagree on a screen somebody is reading both halves of.
    """
    if total == 0:
        return ("No line on this quote was diagnosed, so there is nothing to "
                "total. This is not a quote with nothing wrong with it.")
    parts: list[str] = []
    if losses:
        noun = "line loses" if losses == 1 else "lines lose"
        parts.append(
            f"{losses} {noun} money at the price quoted and "
            f"{'is' if losses == 1 else 'are'} listed in full: the quote's own "
            f"total does not show {'it' if losses == 1 else 'them'}.")
    if margin is None:
        parts.append("No line on this quote has a purchase cost on record, so "
                     "no margin is asserted — not a margin of nothing.")
    else:
        parts.append("Margin is total gross profit divided by the revenue whose "
                     "cost is known, never the mean of the per-line margins.")
        if without_cost:
            noun = "line" if without_cost == 1 else "lines"
            parts.append(f"{without_cost} priced {noun} carr"
                         f"{'ies' if without_cost == 1 else 'y'} no purchase "
                         f"cost and {'is' if without_cost == 1 else 'are'} "
                         f"counted in the value and left out of the margin.")
    if totals:
        noun = "line" if attributed == 1 else "lines"
        parts.append(f"The largest factor across the {attributed} attributed "
                     f"{noun} is {totals[0].code}.")
    else:
        parts.append("No line's margin movement could be split, so no dominant "
                     "factor is named.")
    if mixed_versions:
        parts.append("These lines were judged by more than one version of the "
                     "commercial policy, so no single version is stamped on "
                     "the total.")
    return " ".join(parts)
