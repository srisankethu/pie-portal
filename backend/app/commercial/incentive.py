"""Three-way negotiation: what the customer gets, what the vendor gives, what
the salesperson earns.

A distributor's deal has two negotiations happening at once. The salesperson
pushes the *price* up with the customer and the *cost* down with the vendor,
and both counterparties want something back — a discount, a rebate, a longer
credit. Whatever is given away comes out of the same pot. This module computes
that pot and how it splits, deterministically, so the trade-offs are arithmetic
rather than argument.

────────────────────────────────────────────────────────────────────────────
THE INVARIANT THIS MODULE HAD TO BE DESIGNED AROUND
────────────────────────────────────────────────────────────────────────────

Cost and margin never reach a salesperson. That is not a UI rule — it is the
first invariant in this codebase, and ``references.py`` already spells out the
exact way an incentive scheme breaks it:

    "handing a salesperson 'the floor is ₹1,798' alongside a known 12% floor
     is handing them the cost"

The same division works on an incentive. If a salesperson's payout is *k* × the
gross margin and they know *k*, then ``cost = price − payout ÷ k``. Publishing
a margin-linked incentive figure to a salesperson discloses cost exactly, and
no amount of rounding fixes it — they can solve for cost across two deals.

So the incentive a salesperson sees is **not computed on margin**. It is
computed on *price realisation against a reference this role may already see*:
what this customer last paid, or what customers of this size pay. Those are
OPERATIONAL references — the customer has already seen them — and an incentive
built on them leaks nothing that was not already on the salesperson's screen.

That turns out to be the better commercial design anyway. Rewarding margin
rewards a salesperson for a cheap purchase somebody else negotiated; rewarding
price realisation rewards the thing they actually did.

**The vendor side never shows a salesperson a cost either.** They ask for a
concession — "I need ₹40 a piece off to win this" — as a *delta*, never against
a buy price they can see. A manager sees the request against the real cost and
approves or does not. The salesperson finds out whether they got it, which is
the only part of the answer they need.

────────────────────────────────────────────────────────────────────────────
HOW THE POT SPLITS
────────────────────────────────────────────────────────────────────────────

    realisation = (agreed price − reference price) × qty      [what was won]
    vendor gain = (reference cost − agreed cost) × qty        [manager view]

    pot         = realisation + vendor gain                   [manager view]

    The salesperson's share of *realisation* is their incentive. From it they
    may fund a customer discount, which is the lever the specification asks
    for: giving something back without the company paying for it twice.

Everything is a ``Decimal``. Margin is a ratio. Nothing here is a percentage of
a percentage.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Optional

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _money(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class IncentivePolicy:
    """What the organization has decided to pay. Policy, never a default guess.

    These rates are somebody's compensation. They are owner-set, versioned with
    the rest of the commercial policy, and the values below are *placeholders
    that produce zero* — a scheme nobody configured must pay nothing rather
    than quietly pay whatever a developer typed while writing the module.
    """

    #: Share of price realisation that becomes the salesperson's incentive.
    salesperson_share: Decimal = _ZERO
    #: Share of the vendor concession that also accrues to them. Usually lower:
    #: a buying win is rarely one person's work.
    vendor_share: Decimal = _ZERO
    #: The most of their own incentive a salesperson may hand back to a
    #: customer as a discount without an approval. Above it, a manager decides.
    self_funding_cap: Decimal = _ZERO
    #: Realisation below this earns nothing. Stops a scheme paying out on
    #: rounding noise against a reference price.
    minimum_realisation: Decimal = _ZERO

    @property
    def configured(self) -> bool:
        return self.salesperson_share > _ZERO or self.vendor_share > _ZERO


@dataclass
class Negotiation:
    """One line, as the two negotiations leave it."""

    qty: Decimal
    #: What the customer has already paid for this item, or the band price.
    #: OPERATIONAL — the customer has seen it, so the salesperson may too.
    reference_price: Decimal
    agreed_price: Decimal
    #: Given back to the customer, per unit. Funded from the incentive pot
    #: below before it is ever funded from company margin.
    customer_discount: Decimal = _ZERO
    #: Asked of the vendor, per unit, as a *reduction* on what we pay today.
    #: A request, not a fact, until somebody with cost scope approves it.
    vendor_concession: Decimal = _ZERO
    #: Cost figures. RESTRICTED, and simply absent on a salesperson's path —
    #: every method below works without them.
    reference_cost: Optional[Decimal] = None

    @property
    def realisation(self) -> Decimal:
        """What the price negotiation won, net of what was given back.

        Can be negative: a price agreed below the customer's own last paid
        price is a loss of realisation and is reported as one rather than
        floored at zero, which would hide exactly the deals worth reviewing.
        """
        net_price = self.agreed_price - self.customer_discount
        return _money((net_price - self.reference_price) * self.qty)

    @property
    def vendor_gain(self) -> Decimal:
        """What the buying negotiation won, if it is granted. Manager view."""
        return _money(self.vendor_concession * self.qty)


@dataclass
class Split:
    """The pot, and who ends up with what."""

    realisation: Decimal
    vendor_gain: Decimal
    salesperson_incentive: Decimal
    customer_given: Decimal
    company_retained: Decimal
    self_funded: Decimal
    requires_approval: bool
    approval_reasons: list[str]

    def to_dict(self, *, with_cost: bool) -> dict:
        out = {
            "realisation": float(self.realisation),
            "salesperson_incentive": float(self.salesperson_incentive),
            "customer_given": float(self.customer_given),
            "self_funded": float(self.self_funded),
            "requires_approval": self.requires_approval,
            "approval_reasons": self.approval_reasons,
        }
        if with_cost:
            # Only these two involve the buy side. A salesperson's payload
            # never carries them, which is what keeps the incentive figure
            # from being divisible back into a cost.
            out["vendor_gain"] = float(self.vendor_gain)
            out["company_retained"] = float(self.company_retained)
        return out


def split(n: Negotiation, policy: IncentivePolicy) -> Split:
    """Divide what the negotiation won. Pure arithmetic on the inputs given.

    Called from both role paths with the same numbers; the *projection* differs,
    not the computation. Two code paths computing an incentive two ways is how
    a salesperson's screen and their payslip end up disagreeing.
    """
    realisation = n.realisation
    vendor_gain = n.vendor_gain
    reasons: list[str] = []

    # Below the floor the scheme pays nothing, and negative realisation pays
    # nothing either — an incentive is a share of a gain, and there is no gain.
    earning_base = realisation if realisation >= policy.minimum_realisation else _ZERO
    if earning_base < _ZERO:
        earning_base = _ZERO

    gross_incentive = _money(earning_base * policy.salesperson_share
                             + vendor_gain * policy.vendor_share)

    # The lever the specification asks for: fund the customer's discount out of
    # the salesperson's own incentive rather than out of company margin. Capped
    # by policy — a salesperson may not zero themselves out to buy a deal, and
    # they may not fund more than they have earned.
    wanted = _money(n.customer_discount * n.qty)
    fundable = _money(gross_incentive * policy.self_funding_cap)
    self_funded = min(wanted, fundable, gross_incentive)
    if self_funded < _ZERO:
        self_funded = _ZERO

    net_incentive = _money(gross_incentive - self_funded)
    # Whatever the salesperson could not fund, the company is paying for.
    company_funded = _money(wanted - self_funded)

    if realisation < _ZERO:
        reasons.append(
            "The agreed price is below what this customer has already paid for "
            "this item. That is a price decision, not an incentive one.")
    if company_funded > _ZERO:
        reasons.append(
            f"{float(company_funded):,.2f} of the discount is not covered by "
            "the salesperson's own incentive, so the company is funding it.")
    if n.vendor_concession > _ZERO:
        reasons.append(
            "A vendor concession has been asked for. It is a request until "
            "somebody who can see the buy price agrees it.")

    return Split(
        realisation=realisation,
        vendor_gain=vendor_gain,
        salesperson_incentive=net_incentive,
        customer_given=wanted,
        company_retained=_money(realisation + vendor_gain - net_incentive - company_funded),
        self_funded=self_funded,
        requires_approval=bool(reasons),
        approval_reasons=reasons,
    )


def break_even_discount(n: Negotiation, policy: IncentivePolicy) -> Optional[Decimal]:
    """The largest per-unit discount fundable entirely from the incentive.

    The number the salesperson actually wants in a negotiation: "how much can I
    give away before this starts costing the company anything?".

    **The discount pays for itself twice, and the first version missed it.**
    Giving ``d`` away reduces realisation by ``d × qty``, which reduces the
    incentive, which reduces what is fundable — so taking the undiscounted
    incentive and applying the cap overstates the answer every time. Solved as
    the fixed point instead:

        d·q = c·(s·(p − d − r)·q + v)
        d   = [c·s·q·(p − r) + c·v] ÷ [q·(1 + c·s)]

    Rounded *down* to the cent, because a break-even rounded up is not one.

    Returns None when the scheme is unconfigured or nothing was earned — an
    honest "there is nothing to give away", not zero dressed as an answer.
    """
    if not policy.configured or n.qty <= _ZERO:
        return None
    c, s_rate = policy.self_funding_cap, policy.salesperson_share
    if c <= _ZERO:
        return None

    gap = n.agreed_price - n.reference_price
    vendor = n.vendor_gain * policy.vendor_share
    denominator = n.qty * (_ONE + c * s_rate)
    if denominator <= _ZERO:
        return None
    raw = (c * s_rate * n.qty * gap + c * vendor) / denominator
    if raw <= _ZERO:
        return None

    #: Floored to the cent: a break-even that rounds up is one the company ends
    #: up part-funding, which is the thing this number exists to avoid.
    candidate = raw.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    # The minimum-realisation floor can still bite at the discounted price, and
    # that is not expressible in the closed form. Verified rather than assumed.
    probe = Negotiation(qty=n.qty, reference_price=n.reference_price,
                        agreed_price=n.agreed_price, customer_discount=candidate,
                        vendor_concession=n.vendor_concession)
    checked = split(probe, policy)
    if checked.self_funded < checked.customer_given:
        return None
    return candidate


def required_price(n: Negotiation, policy: IncentivePolicy,
                   target_incentive: Decimal) -> Optional[Decimal]:
    """The price that leaves the salesperson a given incentive.

    The other half of the negotiation: "the customer wants ₹40 off — what do I
    have to hold the price at to keep my incentive?". Solved rather than
    searched, so the answer is exact and the same every time.

    Only the *price* side is inverted. Inverting the vendor side would require
    the cost, and this function is on a salesperson's path.
    """
    if policy.salesperson_share <= _ZERO or n.qty <= _ZERO:
        return None
    # incentive = ((price − discount − reference) × qty) × share
    #   ⇒ price = reference + discount + incentive ÷ (qty × share)
    needed = (target_incentive / (n.qty * policy.salesperson_share))
    return _money(n.reference_price + n.customer_discount + needed)
