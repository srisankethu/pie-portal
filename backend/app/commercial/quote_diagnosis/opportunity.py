"""What the evidence suggests was available on this line. Potential, never missed.

**The engine never says "you lost ₹X".** It says historical evidence suggests a
potential opportunity of ₹X–₹Y. That is not a euphemism: the difference is
whether the number is a measurement or a counterfactual. Nobody knows what this
customer would have paid — the band says what comparable customers and this
customer's own past did pay, which is evidence about a price that was plausibly
achievable and not proof of profit forgone. A tool that reports the second loses
its reader the first time a salesperson can explain why one of those lines was
quoted the way it was.

**Purely price-derived, and therefore honest without a cost baseline.** The range
is ``(band low − quoted)`` to ``(band median − quoted)``, times quantity. No
purchase cost enters it at any point, which is why it can still be computed on
the half of a catalogue that has no usable cost on record — and why it must be
qualified there rather than withheld: a line whose cost has quietly risen is not
a pricing opportunity at all, and without the cost side the engine cannot tell.
``cost_on_record`` carries that distinction to the renderer.

**Owner-only.** ``OperationsDiagnosis`` has no field for any of this, by
construction. A salesperson gets the direction and the band, which is what they
act on; "this account is worth ₹40,000 more" is a management figure.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from ..config import CommercialThresholds
from .baselines import PriceBaseline
from .rules import BELOW_HISTORICAL_RANGE, OwnerDiagnosis

_ZERO = Decimal("0")


@dataclass(frozen=True)
class Opportunity:
    """A range, a basis, and every reason it might be wrong. RESTRICTED.

    ``low`` and ``high`` are line totals. They are ``None`` together — there is
    no half-computed opportunity, because a single number here would be read as
    a figure rather than as the end of a range somebody truncated.
    """

    low: Optional[Decimal]
    high: Optional[Decimal]
    per_unit_low: Optional[Decimal]
    per_unit_high: Optional[Decimal]
    #: The price the upper end was computed against, after any truncation.
    target_price: Optional[Decimal]
    #: This customer has declined quotes at or below the top of the band, so the
    #: upper end was capped at the highest price they have actually accepted.
    truncated_by_resistance: bool
    #: False when no purchase cost was knowable. The range is still sound as a
    #: *price* observation; what cannot be ruled out is that the line is
    #: cost-driven, in which case there is no opportunity here at all.
    cost_on_record: bool
    #: One sentence saying what the range rests on, for the audit trail.
    basis: str

    @property
    def exists(self) -> bool:
        return self.low is not None and self.high is not None

    def to_dict(self) -> dict:
        def m(v: Optional[Decimal]) -> Optional[float]:
            return float(round(v, 2)) if v is not None else None
        return {"low": m(self.low), "high": m(self.high),
                "per_unit_low": m(self.per_unit_low),
                "per_unit_high": m(self.per_unit_high),
                "target_price": m(self.target_price),
                "truncated_by_resistance": self.truncated_by_resistance,
                "cost_on_record": self.cost_on_record,
                "basis": self.basis}


def _nothing(basis: str) -> Opportunity:
    """No range, and the reason there is none.

    ``basis`` is a *reason*, and the renderer reads it as one: it writes "No
    opportunity is asserted — {basis}." This used to be a single module
    constant whose basis was the conclusion itself, so a manager's card read
    "No opportunity is asserted — no opportunity is asserted." Nobody saw it
    for as long as nothing drew the owner projection. Two call sites decline
    for two different reasons and each one states its own.
    """
    return Opportunity(
        low=None, high=None, per_unit_low=None, per_unit_high=None,
        target_price=None, truncated_by_resistance=False, cost_on_record=False,
        basis=basis)


def compute(owner: OwnerDiagnosis, *, th: CommercialThresholds) -> Opportunity:
    """The potential range on one line, or nothing asserted.

    Nothing is asserted unless the line is actually below its supported band.
    A line inside the band has no opportunity to report, and one *above* it has
    a risk rather than an opportunity — the engine does not know whether a high
    price wins the order, so it does not put a number on either reading.

    A cost-driven line likewise gets nothing. §10 is explicit that cost-driven
    erosion is not pricing leakage, and attaching a money figure to it under the
    word "opportunity" would tell somebody to renegotiate a price that was never
    the problem.
    """
    if BELOW_HISTORICAL_RANGE not in owner.codes:
        return _nothing("this line is not below the range its history "
                        "supports, so there is nothing to reclaim on it")
    price = owner.quoted_unit_price
    band = owner.price.band
    if price is None or band.low is None or band.median is None:
        return _nothing("no usable price band was knowable when this quote "
                        "was written")

    target, truncated = _target(owner.price, band.median)
    if target <= price:
        # The truncation removed the whole range: this customer has never
        # accepted a price above what they are being quoted. Saying nothing is
        # the correct output, and it is the case §8 exists to produce.
        return Opportunity(
            low=None, high=None, per_unit_low=None, per_unit_high=None,
            target_price=target, truncated_by_resistance=truncated,
            cost_on_record=owner.cost.known,
            basis=("this customer has not accepted a price above the one "
                   "quoted, so no opportunity is asserted"))

    per_unit_low = band.low - price
    per_unit_high = target - price
    qty = owner.qty

    return Opportunity(
        low=per_unit_low * qty,
        high=per_unit_high * qty,
        per_unit_low=per_unit_low,
        per_unit_high=per_unit_high,
        target_price=target,
        truncated_by_resistance=truncated,
        cost_on_record=owner.cost.known,
        basis=_basis(owner, band.sample_count, truncated, th),
    )


def _target(baseline: PriceBaseline, median: Decimal) -> tuple[Decimal, bool]:
    """The upper price the range reaches for, after §8's truncation.

    **Currently a guard rather than an active adjustment, and it is worth saying
    so.** The band is built only from prices this customer accepted, so its
    median can never exceed the highest of them and the cap never binds today.
    It is here because the day a band widens — a segment tier, a peer-informed
    upper bound, anything that lets the target come from outside this account's
    own accepted history — the cap is what stops the engine recommending a price
    this customer has already refused. A guard added afterwards is a guard added
    after the first wrong recommendation.
    """
    ceiling = baseline.resistance.highest_accepted
    if baseline.resistance.observed and ceiling is not None and ceiling < median:
        return ceiling, True
    return median, False


def _basis(owner: OwnerDiagnosis, samples: int, truncated: bool,
           th: CommercialThresholds) -> str:
    parts = [f"the bottom and middle of {samples} comparable "
             f"{'transaction' if samples == 1 else 'transactions'} "
             f"knowable on {owner.as_of.isoformat()}"]
    if truncated:
        parts.append("capped at the highest price this customer has accepted")
    if not owner.cost.known:
        parts.append("no purchase cost on record, so a cost-driven cause "
                     "cannot be ruled out")
    if owner.price.over_exclusion_limit:
        parts.append("more than a quarter of the comparables were trimmed "
                     "as outliers")
    return "; ".join(parts)
