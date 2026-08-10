"""Deterministic aggregations that answer three questions, in order.

Every screen in the visualization layer has to say **what changed**, **why**, and
**what to do next**. That is a constraint on the *data*, not on the chart: a
figure with no comparison cannot say what changed, one with no decomposition
cannot say why, and one with no owner or size cannot say what to do. So the
shapes here are built to carry all three, and a view that cannot fill them in
says so rather than rendering a number without them.

Nothing here computes anything an existing module already computes.
``CustomerItemMetric`` is the analytical core and it already holds margin
movement, peer deviation, evidence sufficiency and the money-denominated gaps;
these functions aggregate and pivot those rows, plus the line-grain
``SalesTxn``/``CostRecord`` snapshot, into period-comparable views.

Layer rules, inherited and unchanged: this is ``commercial/``, so it is
deterministic and it never imports ``ai/``. Interpretation happens at the
``decisions/`` seam, downstream. A number that reaches a screen was computed
here or in a sibling and carries the ``thresholds_version`` that produced it.

Modules:
  ``periods``   calendar bucketing and window comparison — one definition
  ``flow``      how revenue moved between two periods, decomposed by cause
  ``cohorts``   customer state transitions and revenue-band migration
  ``radar``     ranked opportunities: impact × confidence, from persisted rows
  ``weather``   the executive roll-up, as bands rather than a single score
  ``story``     the briefing: what changed, why, what to do
  ``simulate``  scenario arithmetic over persisted facts, never a projection
                the platform cannot show its working for
  ``bonds``     how strong the tie is to each customer and each supplier, as
                five published facets rather than one opaque score — and the
                same measure at every month end, so the trajectory is watchable
  ``cycle``     the cash conversion cycle per legal entity, at every month end
                — receivable, payable and inventory positions replayed from
                dated line data, and withheld outright for a month whose
                inventory the platform never observed
  ``schemes``   what hitting a principal's target actually pays: the rebate
                slabs, what is secured, what is at stake, and — only above an
                evidence floor — where the period lands at the current rate
"""
