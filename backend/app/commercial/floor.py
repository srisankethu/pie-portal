"""F — the floor price, and the reason a salesperson may be shown one.

Two invariants in this codebase pull in opposite directions:

  I1  Cost and margin never reach a salesperson.
  I4  A salesperson must be able to compute their own incentive.

They are irreconcilable while the incentive is a function of margin, and the
old negotiation desk resolved them by changing the currency — it paid on price
realisation against what the customer last paid, which is operational data. That
worked, but it rewarded holding a price against *this customer's* history rather
than against what the line is worth, so two customers who happened to have paid
differently for the same item earned differently for the same result.

The floor price resolves the same conflict without that distortion:

    F = cost x (1 + m_floor)

``m_floor`` is owner-zone and **varies by family**, so a salesperson holding one
observed line cannot invert F to cost — and holding two lines in different
families does not help either, because the two multipliers differ and they know
neither. What they get is a number they can subtract from their own agreed price
to compute exactly what the line contributes. F is disclosable; cost is not.

**The markup convention here is deliberate and differs from the rest of this
package.** ``references.price_at_margin`` computes ``cost / (1 - m)`` because
every margin in the commercial layer is margin-on-selling-price. ``m_floor`` is
not one of those margins: it is the markup parameter the incentive mechanism was
derived and calibrated against, published in
``incentive_engine/config/parameters.yaml``, and reinterpreting it as a
margin-on-price would move every floor by several points while looking like a
tidy-up. The one parameter, read from its one home, under the convention it was
written in.

**Why an unpublished floor is not zero.** An item whose cost we have never seen
has no floor, and this module returns ``None`` for it. Defaulting to zero would
make ``P - F`` the entire selling price, turning every unmastered item into a
jackpot — the largest single exploit the mechanism has to be closed against.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import models
from . import categories, floor_families
from .config import CommercialThresholds
from .economics import CostRow, cost_basis_asof

_ZERO = Decimal("0")
_PAISE = Decimal("0.01")


@dataclass(frozen=True)
class ResolvedFloor:
    """OPERATIONS ZONE. Safe on a salesperson's screen, by type.

    There is no cost field and no ``m_floor`` field. That is the enforcement —
    a projection that forgets to strip them cannot leak what the object never
    carried. ``basis`` is written to be readable by the salesperson and says
    nothing about how far above cost the floor sits.
    """

    floor_price: Decimal
    basis: str
    as_of: date
    #: The parameter block that produced this floor. Recorded alongside the
    #: thresholds version so a past number stays explicable after a re-cut.
    config_version: str


@dataclass(frozen=True)
class FloorReconciliation:
    """OWNER ZONE. The same floor with its workings, for a manager or owner.

    A separate type rather than optional fields on ``ResolvedFloor``: the
    reconciliation view exists precisely so somebody with cost scope can check
    that the floor is sane, and the two audiences must not share a payload
    shape that one of them is trusted to prune.
    """

    floor_price: Decimal
    unit_cost: Decimal
    m_floor: Decimal
    family: Optional[str]
    #: How the family was decided — ``floor_families.BY_HSN`` and friends. An
    #: owner checking that a floor is sane needs to know whether the multiplier
    #: came from somebody's answer, the catalogue, the tariff code, or nothing
    #: at all, because "priced at default" and "priced as metrology" are
    #: different conversations about the same number.
    family_source: str
    as_of: date
    config_version: str

    @property
    def gross_margin_at_floor(self) -> float:
        """Margin-on-price at the floor, in this package's usual convention.

        Provided because every other margin an owner reads here is on selling
        price, and quoting the markup next to them invites the two to be
        compared as though they were the same number.
        """
        return float((self.floor_price - self.unit_cost) / self.floor_price)


class FloorUnavailable(Exception):
    """No floor can be resolved, with the reason a person needs to read."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@lru_cache(maxsize=8)
def parameters(on: date):
    """The parameter block in force on a date.

    Cached per date rather than globally: the engine deliberately refuses to
    apply today's rates to a historical period, and a single cached block would
    quietly defeat that.
    """
    from incentive_engine.config import ConfigError, load_config
    try:
        return load_config(as_of=on)
    except ConfigError as e:
        raise FloorUnavailable(
            f"No incentive parameter block covers {on.isoformat()}. Floors and "
            f"contribution cannot be computed for that date. ({e})") from e


def _unit_cost(session: Session, org: str, product_id: str,
               as_of: date) -> Optional[Decimal]:
    """What this item cost us, as of a date.

    Delegates the "which cost record applies" rule to ``economics`` rather than
    writing a second one. Two answers to that question is two screens quoting
    different floors for the same line on the same day.
    """
    rows = [
        CostRow(product_id=c.product_id, date=c.date, qty=Decimal(c.qty),
                unit_cost=Decimal(c.unit_cost), source_ref=c.source_ref or {},
                external_ref=c.external_ref)
        for c in session.scalars(
            select(models.CostRecord).where(
                models.CostRecord.organization_id == org,
                models.CostRecord.product_id == product_id,
                models.CostRecord.date <= as_of))
    ]
    rows.sort(key=lambda r: r.date)          # cost_basis_asof requires ascending
    basis = cost_basis_asof(rows, as_of)
    if basis is None:
        return None
    cost = Decimal(basis.unit_cost)
    # Mirrors ``line_economics``: a zero or negative cost is a placeholder from
    # an incomplete bill, not a purchase price. A floor built on it would sit at
    # zero and pay full price as contribution.
    return cost if cost > _ZERO else None


def _m_floor(family: Optional[str], on: date) -> Decimal:
    from incentive_engine.floor import m_floor_for_family
    return m_floor_for_family(parameters(on), family or "default")


def _resolve_family(session: Session, org: str, product_id: str,
                    th: Optional[CommercialThresholds]
                    ) -> floor_families.FamilyResolution:
    """Which floor family this item prices in, from the item itself.

    Every caller used to pass ``family=None`` and every line in the catalogue
    priced at the default multiplier — see ``floor_families`` for why that was
    both a wrong floor and a weakened disclosure defence.

    An item the platform has no product row for resolves nothing rather than
    raising: it still has a cost record and therefore still has a floor, and
    refusing to price it because the master is incomplete would take a working
    number away over a data-quality problem the salesperson cannot fix.
    """
    from .policy import load_for_org

    product = session.scalars(
        select(models.Product).where(
            models.Product.organization_id == org,
            models.Product.product_id == product_id)).first()
    if product is None:
        return floor_families.FamilyResolution(None, floor_families.BY_NOTHING)
    th = th if th is not None else load_for_org(session, org)
    # The per-item line, which is all this needs: only the vendor pass in
    # ``categories.resolve_all`` requires the whole catalogue, and the three
    # lines that determine a family are all decided from the item itself.
    line = categories.resolve(product, th).category
    return floor_families.resolve(product, th, line=line)


def resolve(session: Session, org: str, product_id: str, *,
            family: Optional[str], as_of: date,
            th: Optional[CommercialThresholds] = None) -> ResolvedFloor:
    """The floor a salesperson may be shown. Raises rather than guessing.

    ``FloorUnavailable`` carries the reason, because "no floor" on a
    negotiation screen with no explanation reads as a bug and gets worked
    around; with the reason it gets fixed.

    ``family`` is an override, not the source of truth. ``None`` — which is what
    every caller passes — means "work it out from the item", and an explicit
    value means the caller knows something this module does not.

    **The family it resolved is deliberately not returned.** ``ResolvedFloor``
    is the operations type and has no field for it: telling a salesperson that
    two items share a family tells them the two share a multiplier, and one
    leaked cost would then invert both. That is exactly the inference the
    family variation exists to block, so the family reaches ``reconcile`` and
    stops there.
    """
    cfg = parameters(as_of)
    cost = _unit_cost(session, org, product_id, as_of)
    if cost is None:
        raise FloorUnavailable(
            "We have no purchase record for this item on or before this date, "
            "so it has no floor and no contribution can be computed. Raise it "
            "with whoever masters the item — pricing it without a floor is a "
            "guess, and the desk will not pretend otherwise.")

    key = (family if family is not None
           else _resolve_family(session, org, product_id, th).key)
    m = _m_floor(key, as_of)
    floor = (cost * (Decimal("1") + m)).quantize(_PAISE)
    return ResolvedFloor(
        floor_price=floor,
        basis=("the published floor for this item — the price below which the "
               "line stops contributing"),
        as_of=as_of,
        config_version=cfg.version)


def reconcile(session: Session, org: str, product_id: str, *,
              family: Optional[str], as_of: date,
              th: Optional[CommercialThresholds] = None) -> FloorReconciliation:
    """The same floor with cost and ``m_floor`` shown. Manager and owner only.

    Resolves the family the same way ``resolve`` does, and by the same call, so
    the two cannot report different floors for the same item on the same day —
    which is the failure a reconciliation view exists to catch rather than
    commit.
    """
    cfg = parameters(as_of)
    cost = _unit_cost(session, org, product_id, as_of)
    if cost is None:
        raise FloorUnavailable(
            "No purchase record for this item on or before this date.")
    resolved = (floor_families.FamilyResolution(family, floor_families.BY_OVERRIDE)
                if family is not None
                else _resolve_family(session, org, product_id, th))
    m = _m_floor(resolved.key, as_of)
    return FloorReconciliation(
        floor_price=(cost * (Decimal("1") + m)).quantize(_PAISE),
        unit_cost=cost, m_floor=m, family=resolved.family,
        family_source=resolved.source, as_of=as_of,
        config_version=cfg.version)
