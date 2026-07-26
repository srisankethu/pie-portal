"""QUOTE_CONTEXT deterministic assembly: facts, data-class tags, unknowns."""
from __future__ import annotations

from app.signals.config import SignalThresholds
from app.signals.quote_context import assemble

from .signal_fixtures import AS_OF, cost, sale, snap

TH = SignalThresholds()


def _fixture():
    sales, costs = [], [cost("p1", "2026-01-01", 100, 60, bill="c1"),
                        cost("p1", "2026-05-01", 100, 70, bill="c2")]
    for i, d in enumerate(["2025-09-01", "2025-10-01", "2025-11-01", "2025-12-01",
                           "2026-02-01", "2026-05-15", "2026-06-15"]):
        sales.append(sale("cust1", "p1", d, 10, 100, invoice=f"i{i}"))
    return snap(sales=sales, costs=costs, cust_names={"cust1": "Acme"},
                prod_names={"p1": "Insert"})


def test_assembles_operational_and_restricted_facts():
    ctx = assemble(_fixture(), "cust1", ["p1"], TH, AS_OF)
    assert ctx["decision_type"] == "QUOTE_CONTEXT"
    labels = {f["label"] for f in ctx["customer_facts"]}
    assert {"typical_interval_days", "days_since_last_order"} <= labels

    item = ctx["items"][0]
    ilabels = {f["label"]: f for f in item["facts"]}
    assert ilabels["last_price_paid"]["value"] == 100.0
    assert ilabels["last_price_paid"]["data_class"] == "OPERATIONAL"
    # restricted economics present and tagged RESTRICTED
    assert ilabels["current_unit_cost"]["data_class"] == "RESTRICTED"
    assert ilabels["standard_margin_pct"]["data_class"] == "RESTRICTED"
    assert ilabels["cost_delta_pct"]["data_class"] == "RESTRICTED"
    # direction-only movement flag is OPERATIONAL (safe for salesperson)
    assert ilabels["cost_movement_direction"]["data_class"] == "OPERATIONAL"
    assert ilabels["cost_movement_direction"]["value"] == "up"


def test_no_recommendation_or_severity_leaks():
    ctx = assemble(_fixture(), "cust1", ["p1"], TH, AS_OF)
    blob = str(ctx).lower()
    assert "recommend" not in blob and "severity" not in blob
    # every fact carries provenance scaffolding
    for f in ctx["items"][0]["facts"]:
        assert "source_refs" in f and "as_of" in f and "data_class" in f


def test_unknowns_are_explicit():
    ctx = assemble(_fixture(), "cust1", ["p_never"], TH, AS_OF)
    fields = {u["field"] for u in ctx["unknowns"]}
    assert any("item_history" in f for f in fields)
    assert any("cost_basis" in f for f in fields)
