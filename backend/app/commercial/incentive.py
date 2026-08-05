"""The negotiation desk, in the one currency that gets paid: CAF.

    CAF = q x (P - F) - K - 0.5 x Toolkit + Y

Contribution above floor, less what was paid to a third party, less half of what
was spent on the customer's toolkit, plus what was won back from the vendor.
Every term is a price or a declared amount. Cost appears in none of them, which
is the only reason the same number can be shown to a salesperson at the desk and
used to pay them at the end of the month.

────────────────────────────────────────────────────────────────────────────
WHY THIS REPLACED THE REALISATION CURRENCY
────────────────────────────────────────────────────────────────────────────

The first version of this module paid on *price realisation*: the gap between
the price agreed and what this customer last paid. It was safe — the customer
has already seen their own last price — and it solved the disclosure problem.
It had two defects that only show up once the mechanism is live:

  1. **It rewarded the customer's history, not the line.** Two customers who
     happened to have paid differently for the same item earned differently for
     the same commercial result. A customer who once got a bad price became a
     permanently profitable account to sell to.

  2. **It was not the currency the month is paid in.** Contribution above floor
     is. A desk that prices a deal in one currency and a payslip that settles in
     another is a desk nobody trusts twice.

Both are fixed by measuring against a *published floor* instead of a *historical
price*. See ``commercial/floor.py`` for why F is disclosable when cost is not.

**Nothing here is a share.** The desk reports contribution in rupees. What
fraction of it becomes money is the monthly computation in ``incentive_engine``
— relationship weight, portfolio health, entity rate — and it is deliberately
not re-implemented here. One arithmetic, in one place, is the entire reason the
desk and the payslip agree.

────────────────────────────────────────────────────────────────────────────
THE THREE QUESTIONS THIS ANSWERS
────────────────────────────────────────────────────────────────────────────

    "What can I give them?"     -> ``discount_to_floor``  — exact, closed form
    "What does that cost me?"   -> ``assess(...).caf``    — recomputed as they move it
    "What do I have to hold?"   -> ``price_for_target``   — solved, not searched

The first of those needed a fixed-point solve under the old currency, because a
discount reduced the incentive which reduced what was fundable. Under CAF it is
subtraction. That is not a coincidence: linearity in rupees (I5) is a designed
property of the mechanism, and it shows up here as arithmetic a salesperson can
do on the phone.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Optional

_ZERO = Decimal("0")
_PAISE = Decimal("0.01")

#: Values of ``Customer.incentive_eligibility``. Named with the prefix because
#: ``RESTRICTED`` already means something else in this package — it is the data
#: class in ``references.py`` — and two unrelated meanings under one name is a
#: mistake waiting for the import that brings them together.
#:
#: ELIGIBILITY_PRIVATE is the only value that permits a third-party incentive.
#: Everything else, including an unclassified account, is restricted.
ELIGIBILITY_PRIVATE = "PRIVATE"
ELIGIBILITY_RESTRICTED = "RESTRICTED"


def _money(v: Decimal) -> Decimal:
    return v.quantize(_PAISE, rounding=ROUND_HALF_UP)


def may_pay_third_party(eligibility: Optional[str]) -> bool:
    """Whether a third-party incentive may be discussed for this customer.

    **Fails closed.** An account nobody has classified is treated exactly like a
    PSU, because the downside of the two mistakes is not symmetric: refusing a
    legitimate incentive costs a conversation, and paying one into a government
    supply chain is the Prevention of Corruption Act and GeM blacklisting. An
    owner classifies the account; the desk does not guess from the name.
    """
    return eligibility == ELIGIBILITY_PRIVATE


@dataclass(frozen=True)
class Deal:
    """One prospective line, as the two negotiations would leave it.

    Everything is per-unit except the three deal-level amounts, which are
    totals — that is how they are declared and how they are charged.
    """

    qty: Decimal
    #: The published floor, from ``commercial.floor``. OPERATIONAL: it is a
    #: selling price, and the margin behind it is not in this object.
    floor_price: Decimal
    agreed_price: Decimal
    #: Per unit, given back to the customer. Reduces the net realised price,
    #: which is the only place a giveaway can hide.
    customer_discount: Decimal = _ZERO
    #: K — a declared payment to somebody at the customer. Total, not per unit.
    third_party_incentive: Decimal = _ZERO
    #: Toolkit — the compliant alternative. Total, not per unit.
    toolkit_spend: Decimal = _ZERO
    #: Y — what the vendor is being asked to give back, as a total. A request
    #: until somebody with buy-side scope agrees it and a document exists.
    vendor_yield: Decimal = _ZERO

    @property
    def net_price(self) -> Decimal:
        return self.agreed_price - self.customer_discount


@dataclass(frozen=True)
class Assessment:
    """What the line contributes, with every term kept separately."""

    contribution: Decimal
    third_party: Decimal
    toolkit_charged: Decimal
    vendor_yield: Decimal
    caf: Decimal
    #: CAF x c(d) at the payment timing being assumed. CAF is banked on invoice
    #: and earned on receipt; this is what it is worth if the money arrives when
    #: the salesperson says it will.
    collected_caf: Decimal
    collection_factor: Decimal
    collection_label: str
    below_floor: bool
    warnings: list[str]

    def to_dict(self) -> dict:
        """The operations projection. There is nothing to strip.

        No cost, no margin, no ``m_floor``, no share rate — none of them is a
        field on this object, so no future serialiser can add one back by
        accident.
        """
        return {
            "contribution": float(self.contribution),
            "third_party_charged": float(self.third_party),
            "toolkit_charged": float(self.toolkit_charged),
            "vendor_yield_credited": float(self.vendor_yield),
            "caf": float(self.caf),
            "collected_caf": float(self.collected_caf),
            "collection_factor": float(self.collection_factor),
            "collection_label": self.collection_label,
            "below_floor": self.below_floor,
            "warnings": self.warnings,
        }


def _line(deal: Deal, *, as_of: date, customer_id: str, product_id: str,
          family: Optional[str], entity_id: str, salesperson_id: str):
    """A prospective invoice line for the engine's own CAF arithmetic.

    The desk prices a line that does not exist yet, so the identifiers are
    placeholders. What matters is that this is the *same* dataclass, validated
    the same way, that the monthly run will build from the real invoice — the
    desk cannot drift from the payslip because there is only one calculator.
    """
    from incentive_engine.models import InvoiceLine
    return InvoiceLine(
        entity_id=entity_id,
        invoice_id="prospective",
        invoice_date=as_of,
        customer_id=customer_id,
        customer_group_id=customer_id,
        item_id=product_id,
        item_family=family or "default",
        brand="",
        qty=deal.qty,
        unit_price_net=deal.net_price,
        floor_price=deal.floor_price,
        salesperson_id=salesperson_id,
    )


class IncentiveBlocked(ValueError):
    """This incentive must not be recorded, with the reason.

    A named type so the router can catch exactly this and map it to a 422. A
    bare ``except Exception`` around the check would turn a genuine programming
    error into a polite refusal message, which is the worst of both — the deal
    is blocked and nobody learns why.
    """


def check_third_party(amount: Decimal, *, customer_id: str,
                      eligibility: Optional[str], as_of: date,
                      entity_id: str) -> None:
    """Raise if this incentive must not be paid. I2, enforced by the engine.

    Delegates to ``ThirdPartyIncentive``, whose constructor is where the block
    lives, rather than restating the rule. A second copy of a legal hard block
    is a second place for it to be relaxed by somebody who did not know why it
    was there. The engine's message is passed through verbatim: it explains the
    statute and the consequence, and paraphrasing it here would leave two
    wordings of the same refusal to drift apart.
    """
    from incentive_engine.models import ThirdPartyIncentive, ValidationError
    try:
        ThirdPartyIncentive(
            entity_id=entity_id, deal_id="prospective", invoice_id="prospective",
            customer_id=customer_id, amount=amount, form="declared",
            declared_at=as_of,
            customer_is_restricted=not may_pay_third_party(eligibility))
    except ValidationError as e:
        raise IncentiveBlocked(str(e)) from e


def assess(deal: Deal, *, as_of: date, customer_id: str, product_id: str,
           family: Optional[str], entity_id: str, salesperson_id: str,
           expected_days_late: int = 0) -> Assessment:
    """Price the line. Pure arithmetic on the inputs given.

    Called from every role path with the same numbers; only the *projection*
    differs. Two code paths computing a contribution two ways is how a screen
    and a payslip end up disagreeing.
    """
    from incentive_engine import caf as caf_mod
    from incentive_engine import collection

    cfg = _cfg(as_of)
    line = _line(deal, as_of=as_of, customer_id=customer_id,
                 product_id=product_id, family=family, entity_id=entity_id,
                 salesperson_id=salesperson_id)
    computed = caf_mod.line_caf(
        cfg, line,
        third_party=deal.third_party_incentive,
        toolkit=deal.toolkit_spend,
        vendor_yield=deal.vendor_yield)

    factor = collection.factor(cfg, expected_days_late)
    label = _collection_label(cfg, expected_days_late)

    warnings: list[str] = []
    below = deal.net_price < deal.floor_price
    if below:
        warnings.append(
            "The net price is below the floor. This line takes contribution "
            "away rather than adding it — that is the price discipline "
            "working, not an error.")
    if computed.caf < _ZERO and not below:
        warnings.append(
            "What is being given away costs more than the line contributes, "
            "so this comes out of your month.")
    if deal.vendor_yield > _ZERO:
        warnings.append(
            "The vendor concession is credited here as though it were agreed. "
            "It is a request until the credit note or the revised PO exists.")
    if collection.triggers_clawback(cfg, expected_days_late):
        warnings.append(
            "At that payment timing this earns nothing at all, and anything "
            "paid provisionally is clawed back.")

    return Assessment(
        contribution=_money(computed.price_contribution),
        third_party=_money(computed.third_party),
        toolkit_charged=_money(computed.toolkit_charged),
        vendor_yield=_money(computed.vendor_yield),
        caf=_money(computed.caf),
        collected_caf=_money(computed.caf * factor),
        collection_factor=factor,
        collection_label=label,
        below_floor=below,
        warnings=warnings,
    )


def _net_charges(deal: Deal, as_of: date) -> Decimal:
    """K + 0.5T - Y, at the rates in force. The deal-level terms, netted.

    Both inversions below need this and both had their own copy until a
    duplicate scan found them. Two copies of the charge side is one place for a
    re-cut rate to be applied and one place for it to be missed, and the two
    answers would disagree about the same deal on the same screen.
    """
    cfg = _cfg(as_of)
    return (deal.third_party_incentive * cfg.dec("caf", "third_party_charge_rate")
            + deal.toolkit_spend * cfg.dec("caf", "toolkit_charge_rate")
            - deal.vendor_yield * cfg.dec("caf", "vendor_yield_credit"))


def discount_to_floor(deal: Deal, *, as_of: date) -> Optional[Decimal]:
    """The largest per-unit discount this line can carry and still contribute.

    The number actually wanted in a negotiation: "how much can I give away
    before this stops being worth doing?". Solving ``CAF = 0`` for the discount:

        q x (P - d - F) - K - 0.5T + Y = 0
        d = (P - F) + (Y - K - 0.5T) / q

    Rounded *down* to the paise, because a break-even rounded up is not one.
    Returns ``None`` when there is nothing to give — an honest answer, not zero
    dressed up as one.
    """
    if deal.qty <= _ZERO:
        return None
    charges = _net_charges(deal, as_of)
    raw = (deal.agreed_price - deal.floor_price) - (charges / deal.qty)
    if raw <= _ZERO:
        return None
    return raw.quantize(_PAISE, rounding=ROUND_DOWN)


def price_for_target(deal: Deal, target_caf: Decimal, *,
                     as_of: date) -> Optional[Decimal]:
    """The agreed price that leaves this line contributing exactly ``target``.

    The other half of the negotiation: "they want ₹40 a piece off — what do I
    have to hold the price at?". Solved rather than searched, so the answer is
    exact and the same every time.

        P = F + d + (target + K + 0.5T - Y) / q
    """
    if deal.qty <= _ZERO:
        return None
    charges = _net_charges(deal, as_of)
    return _money(deal.floor_price + deal.customer_discount
                  + (target_caf + charges) / deal.qty)


def _cfg(as_of: date):
    """The parameter block in force, via the one loader that caches it."""
    from .floor import parameters
    return parameters(as_of)


def _collection_label(cfg, days_late: int) -> str:
    for band in cfg.get("collection", "bands"):
        lo, hi = band["from"], band["to"]
        if lo is None and days_late <= hi:
            return str(band["label"])
        if lo is not None and days_late >= lo and (hi is None or days_late <= hi):
            return str(band["label"])
    return "unknown"
