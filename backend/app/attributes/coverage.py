"""How much of one organization's catalogue carries attributes. The exit criterion.

Decision 002 makes Phase 1 succeed or fail on **published attribute coverage**,
not on accuracy — so this is the measurement that says whether the phase is
done, and it reports what IS. There is no target in this module, no band, no
verdict and no colour: a number that is lower than somebody hoped is the answer,
and the fix is more decoded product rather than a friendlier denominator
(CLAUDE.md §1, "do not weaken a rule to make output appear").

Three questions, which is what the phase gate asks:

* how many products carry **at least one** attribute;
* **how many attributes** each of those carries;
* the **fill rate per attribute_key** — the per-field census, which is the one
  that says what a retrieval layer could actually filter on.

**Read the per-key table before the headline.** A field the pack emits for
almost every row it can route — ``grade_system`` and ``material_class`` are on
essentially every catalogue record — makes "products with at least one
attribute" a measure of *decode reach* rather than of richness. That is not a
defect in the number, it is what the number means; the per-key rows are where
the difference is visible, and both are reported for that reason.

Undefined is reported as ``None``, never as zero. An organization with no
products has no fill rate — the ratio has no denominator — and 0% would read as
a measured failure of a catalogue that does not exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from ..domain import models

_PAV = models.ProductAttributeValue


@dataclass(frozen=True)
class KeyCoverage:
    """One attribute field, across the organization's products."""

    attribute_key: str
    #: Distinct products holding a live value for this key, from any source.
    products: int
    #: Live rows — higher than ``products`` where both source kinds claim the
    #: same field, which is the disagreement the table is keyed to preserve.
    values: int
    #: ``products`` over every product in the organization. ``None`` when there
    #: are none, because a ratio without a denominator is not zero.
    fill_rate: Optional[float]


@dataclass(frozen=True)
class CoverageReport:
    organization_id: str
    products_total: int
    products_with_any_attribute: int
    live_values: int
    by_key: Tuple[KeyCoverage, ...]
    #: ``(source_kind, live rows)``, so the two claims are countable apart. A
    #: run where CATALOGUE_LINK is empty and DECODED_NAME is not says the
    #: catalogue link is the thing to work on, which the totals cannot.
    by_source_kind: Tuple[Tuple[str, int], ...]

    @property
    def coverage_rate(self) -> Optional[float]:
        """Products carrying anything, over all products. ``None`` when none."""
        if not self.products_total:
            return None
        return self.products_with_any_attribute / self.products_total

    @property
    def attributes_per_decorated_product(self) -> Optional[float]:
        """Mean live values per product **that has any**.

        The denominator is the decorated products and not all of them, stated
        here because the two differ by a factor of five on the live master and a
        reader who assumes the other one will misread the number by that much.
        ``None`` when nothing is decorated.
        """
        if not self.products_with_any_attribute:
            return None
        return self.live_values / self.products_with_any_attribute


def attribute_coverage(session: Session, organization_id: str) -> CoverageReport:
    """The coverage report for one organization.

    Every query is filtered on ``organization_id``. Row-level security covers
    this table on PostgreSQL, and on SQLite these filters are the only tenant
    boundary there is — so the scoping is written here rather than relied upon,
    which is the Phase 0 report's §5 rule.
    """
    live = (_PAV.organization_id == organization_id, _PAV.superseded_at.is_(None))

    products_total = session.scalar(
        select(func.count()).select_from(models.Product).where(
            models.Product.organization_id == organization_id)) or 0

    products_with_any = session.scalar(
        select(func.count(distinct(_PAV.product_id))).where(*live)) or 0

    live_values = session.scalar(
        select(func.count()).select_from(_PAV).where(*live)) or 0

    by_key = tuple(
        KeyCoverage(
            attribute_key=key,
            products=products,
            values=values,
            fill_rate=(products / products_total) if products_total else None,
        )
        # Ordered by population and then by name: the ranking is the useful
        # read, and the name breaks ties so two runs print the same list.
        for key, products, values in session.execute(
            select(_PAV.attribute_key,
                   func.count(distinct(_PAV.product_id)),
                   func.count())
            .where(*live)
            .group_by(_PAV.attribute_key)
            .order_by(func.count(distinct(_PAV.product_id)).desc(),
                      _PAV.attribute_key)))

    by_source_kind = tuple(
        (kind, count) for kind, count in session.execute(
            select(_PAV.source_kind, func.count())
            .where(*live)
            .group_by(_PAV.source_kind)
            .order_by(_PAV.source_kind)))

    return CoverageReport(
        organization_id=organization_id,
        products_total=products_total,
        products_with_any_attribute=products_with_any,
        live_values=live_values,
        by_key=by_key,
        by_source_kind=by_source_kind,
    )
