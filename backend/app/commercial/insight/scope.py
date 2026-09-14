"""Narrowing a persisted-row read to a set somebody drew.

Every screen scoped to a group reads fewer rows and recomputes its figures
inside them — never the whole book's figures with rows hidden underneath. Where
the rows are sales or cost lines that is ``load_snapshot``'s bounds; where they
are the analytical core, ``CustomerItemMetric``, it is this.

One function rather than the same ``.where(... .in_(ids))`` pair written at each
call site. Two builders read that table directly and more will, and the pair
carries a rule that is easy to get subtly wrong in the fourth copy:

**``None`` is "no group", and ``[]`` is "a group nobody is in".** They are
different answers and this is where the difference is kept. ``in_([])`` matches
nothing, so an empty group produces an empty screen — which is the correct
answer to a question about an empty set, and the one ``group_scope.py``'s second
rule exists to protect. A filter that skipped an empty list would answer a
question about a set nobody is in with the whole book's figures.

No dependency on ``groups/`` and none on a request: this takes id lists, so it
stays a fact about a query rather than about who asked.
"""
from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy import Select, select

from ...domain import models


def metrics_for(org: str, *,
                customers: Optional[Iterable[str]] = None,
                products: Optional[Iterable[str]] = None,
                ) -> Select:
    """``CustomerItemMetric`` rows for one organisation, inside a group or not.

    Both bounds may apply at once: a relationship is a customer *and* an item,
    so "the aerospace book's position on the drills line" is one query rather
    than two reads intersected in Python.
    """
    q = select(models.CustomerItemMetric).where(
        models.CustomerItemMetric.organization_id == org)
    if customers is not None:
        q = q.where(models.CustomerItemMetric.customer_id.in_(list(customers)))
    if products is not None:
        q = q.where(models.CustomerItemMetric.product_id.in_(list(products)))
    return q
