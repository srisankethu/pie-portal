"""Price against adoption: what PIE actually earns at each price it could ask.

The brief's warning is the design constraint here — *do not hard-code arbitrary
elasticity assumptions without identifying them*. So there are two curves, both
named, both swept, and neither presented as the truth:

**Constant elasticity** is the textbook form, ``adoption = a_ref * (p/p_ref)^e``.
Its behaviour is worth knowing precisely because it is degenerate: with ``e``
below -1 expected revenue falls monotonically with price and the model's advice
is "charge nothing"; above -1 it rises monotonically and the advice is "charge
everything". A curve with no interior optimum cannot answer "what is the
revenue-maximising price", and a model that reported one anyway would be
reporting the edge of its own grid.

**Bounded logistic** fixes that by admitting what the constant form denies: no
price wins every deal, and no price wins none. Adoption saturates at
``ceiling``, and the curve is fitted through the same ``(p_ref, a_ref)`` anchor
with the same local elasticity, so the two are directly comparable at the
reference and diverge only where the constant form stops being credible.

Every number that comes out of this module inherits ``reference_win_rate``,
which is ``NEEDS_VALIDATION``: no deal has closed. The *shape* — that the
optimum is well above the price at which a founder's nerve usually fails — is
robust across the sweep. The *level* is not, and §16's experiments exist to fix
exactly that.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from .config import MonetizationParameters, load_parameters
from .customer import money


class CurveKind(str, Enum):
    CONSTANT_ELASTICITY = "CONSTANT_ELASTICITY"
    BOUNDED_LOGISTIC = "BOUNDED_LOGISTIC"


@dataclass(frozen=True)
class AdoptionCurve:
    """Probability that a qualified prospect buys, as a function of price."""

    kind: CurveKind
    reference_price: Decimal
    reference_adoption: float
    elasticity: float
    #: The most any price could win. Only the logistic form uses it.
    ceiling: float = 0.85

    def adoption(self, price: Decimal) -> float:
        if price <= 0:
            return min(1.0, self.ceiling if self.kind is CurveKind.BOUNDED_LOGISTIC
                       else 1.0)
        ratio = float(price) / float(self.reference_price)
        if self.kind is CurveKind.CONSTANT_ELASTICITY:
            return max(0.0, min(1.0, self.reference_adoption * ratio ** self.elasticity))

        # Logistic in log price, fitted so that at p_ref the curve passes
        # through a_ref with local elasticity `elasticity`. For
        # a(p) = A / (1 + (p/p50)^k), dln a / dln p = -k * (1 - a/A), so
        # k = -e / (1 - a_ref/A). A reference adoption at or above the ceiling
        # leaves no room to fit, and the honest answer is the ceiling itself.
        ceiling = max(self.reference_adoption + 1e-9, self.ceiling)
        slack = 1.0 - self.reference_adoption / ceiling
        if slack <= 1e-9:
            return min(1.0, ceiling)
        k = -self.elasticity / slack
        if k <= 0:
            return min(1.0, ceiling)
        # p50 from a_ref = A / (1 + (p_ref/p50)^k).
        inner = ceiling / self.reference_adoption - 1.0
        if inner <= 0:
            return min(1.0, ceiling)
        p50_ratio = inner ** (1.0 / k)          # p_ref / p50
        return max(0.0, min(1.0, ceiling / (1.0 + (ratio * p50_ratio) ** k)))

    def sales_cycle_days(self, price: Decimal,
                         params: MonetizationParameters) -> float:
        """Days to close at this price. Longer prices climb a signature ladder.

        Linear in log2 of the price ratio, which is the shape a signature
        threshold actually has: each doubling moves the decision up one level,
        and levels are discrete but roughly evenly spaced in rupees-per-level.
        """
        if price <= 0 or self.reference_price <= 0:
            return params.sales_cycle_days_at_reference
        doublings = math.log2(float(price) / float(self.reference_price))
        return max(1.0, params.sales_cycle_days_at_reference
                   + params.sales_cycle_days_per_price_doubling * doublings)


@dataclass(frozen=True)
class PricePoint:
    price: Decimal
    adoption: float
    customers_won: float
    expected_revenue: Decimal
    sales_cycle_days: float

    def as_dict(self) -> dict[str, Any]:
        return {"price": str(self.price), "adoption": round(self.adoption, 6),
                "customers_won": round(self.customers_won, 3),
                "expected_revenue": str(self.expected_revenue),
                "sales_cycle_days": round(self.sales_cycle_days, 1)}


def sweep(curve: AdoptionCurve, prices: list[Decimal], prospects: int,
          params: Optional[MonetizationParameters] = None) -> list[PricePoint]:
    """Expected revenue = price x adoption x prospects, at each price."""
    params = params or load_parameters()
    out = []
    for p in prices:
        a = curve.adoption(p)
        won = a * prospects
        out.append(PricePoint(
            price=p, adoption=a, customers_won=won,
            expected_revenue=money(p * Decimal(str(a)) * Decimal(prospects)),
            sales_cycle_days=curve.sales_cycle_days(p, params)))
    return out


#: Adoption at or above this counts as saturated. Not 1.0 exactly: the clamp is
#: applied in floating point and a curve that reaches 0.9999999 has saturated
#: for every purpose this model has.
SATURATED = 0.999


def optimum(points: list[PricePoint]) -> Optional[dict[str, Any]]:
    """The revenue-maximising point, and the two ways it can fail to be one.

    ``at_grid_edge`` is not a footnote. A maximum sitting on the first or last
    price is the model saying "the answer is outside the range you gave me, or
    this curve has no interior optimum" — reporting it as a recommendation is
    how a constant-elasticity sweep gets read as advice to price at zero.

    ``at_saturation`` is the subtler one, and it was found by a test rather than
    reasoned out. A constant-elasticity curve steeper than -1 has no interior
    optimum *mathematically* — yet the sweep produced one anyway, because
    adoption is clamped at 1.0 and revenue therefore rises linearly with price
    up to the saturation point and falls after it. That peak is an artefact of
    the clamp, not a property of demand: it says "the cheapest price at which
    every prospect buys", which is true of the arithmetic and says nothing about
    what anyone would pay. A model that reported it as the revenue-maximising
    price would be recommending a number produced by its own guard rail.
    """
    if not points:
        return None
    best = max(points, key=lambda p: (p.expected_revenue, -p.price))
    index = points.index(best)
    return {**best.as_dict(),
            "at_grid_edge": index in (0, len(points) - 1),
            "at_saturation": best.adoption >= SATURATED,
            "grid_low": str(points[0].price), "grid_high": str(points[-1].price)}


def price_grid(low: Decimal, high: Decimal, steps: int = 25) -> list[Decimal]:
    """Geometrically spaced prices — the spacing a percentage curve deserves.

    Linear spacing over a 20x range puts most of the grid where the curve is
    flat and almost none of it where the optimum is.
    """
    steps = max(2, steps)
    lo, hi = float(max(Decimal("1"), low)), float(max(low + 1, high))
    factor = (hi / lo) ** (1.0 / (steps - 1))
    return [money(Decimal(str(lo * factor ** i))) for i in range(steps)]


def scenarios(prospects: int,
              params: Optional[MonetizationParameters] = None,
              *, low: Optional[Decimal] = None, high: Optional[Decimal] = None,
              steps: int = 25) -> dict[str, Any]:
    """Both curve forms x every configured elasticity, over one price grid."""
    params = params or load_parameters()
    ref = params.elasticity_reference_price
    grid = price_grid(low or money(ref / Decimal("8")),
                      high or money(ref * Decimal("8")), steps)

    runs = []
    for kind in CurveKind:
        for e in params.elasticity_scenarios:
            curve = AdoptionCurve(kind=kind, reference_price=ref,
                                  reference_adoption=params.reference_win_rate,
                                  elasticity=e)
            points = sweep(curve, grid, prospects, params)
            runs.append({
                "curve": kind.value, "elasticity": e,
                "reference_price": str(ref),
                "reference_adoption": params.reference_win_rate,
                "optimum": optimum(points),
                "points": [p.as_dict() for p in points]})

    interior = [r for r in runs
                if r["optimum"]
                and not r["optimum"]["at_grid_edge"]
                and not r["optimum"]["at_saturation"]]
    return {
        "prospects": prospects,
        "runs": runs,
        "interior_optima": [
            {"curve": r["curve"], "elasticity": r["elasticity"],
             "price": r["optimum"]["price"],
             "expected_revenue": r["optimum"]["expected_revenue"]}
            for r in interior],
        "reading": (
            "Constant-elasticity runs have no interior optimum by construction. "
            "Their maxima sit either on the grid edge or at the saturation "
            "point where adoption is clamped to 1 — both are properties of the "
            "functional form rather than price recommendations, and both are "
            "flagged. The bounded-logistic runs are the ones to read, and their "
            "optima are the range worth testing with the experiments in §16."),
        "evidence_note": ("reference_win_rate is NEEDS_VALIDATION: no deal has "
                          "closed at any price. The shape of these curves is "
                          "robust; the level of every optimum is not."),
    }
