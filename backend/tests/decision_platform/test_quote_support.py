"""End-to-end QUOTE_CONTEXT decision-support integration tests.

Exercises the Quote Builder ↔ Decision Platform seam over the DB-backed
orchestrator + AI layer, across the required realistic quote scenarios:
exact match, alternative product, no product match, repeat vs new customer,
missing cost, suspicious cost, previous-price context, margin deterioration,
AI unavailable, AI malformed, and the user ignoring the recommendation.
Role-gating (salesperson never receives cost/margin) is asserted throughout.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.ai.mock_provider import MockProvider
from app.authz import Principal
from app.decisions.quote_support import quote_support
from app.domain import models
from app.domain.enums import HumanAction, Role
from app.repositories import DecisionRepository
from app.seed import ensure_org_and_users

ORG = "org_sanketh"  # settings.DEFAULT_ORG_ID
AS_OF = date(2026, 7, 22)


# ── DB seeding helpers ───────────────────────────────────────────────────────
def _sale(cust, prod, d, qty, price, inv, line="l1"):
    return models.SalesTxn(
        organization_id=ORG, external_ref=f"{inv}:{line}", customer_id=cust, product_id=prod,
        date=date.fromisoformat(d), qty=Decimal(str(qty)), unit_price=Decimal(str(price)),
        line_revenue=Decimal(str(qty)) * Decimal(str(price)),
        source_ref={"system": "zoho", "record_type": "invoice", "record_id": inv, "line_id": line})


def _cost(prod, d, qty, unit_cost, bill, line="l1"):
    return models.CostRecord(
        organization_id=ORG, external_ref=f"{bill}:{line}", product_id=prod,
        date=date.fromisoformat(d), qty=Decimal(str(qty)), unit_cost=Decimal(str(unit_cost)),
        source_ref={"system": "zoho", "record_type": "bill", "record_id": bill, "line_id": line})


@pytest.fixture()
def seeded(session):
    """A read model with a repeat customer, a couple of items, and cost history."""
    from app.config import settings
    settings.DEFAULT_ORG_ID  # ensure import side effects
    global ORG
    ORG = settings.DEFAULT_ORG_ID
    ensure_org_and_users(session)

    session.add(models.Customer(customer_id="cst_pitti", organization_id=ORG,
                                external_id="cst_pitti", name="Pitti Engineering Ltd",
                                assigned_user_id="usr_sales", status="ACTIVE"))
    session.add(models.Product(product_id="prd_dnmg", organization_id=ORG,
                               external_id="prd_dnmg", name="DNMG 150608-MP insert"))
    session.add(models.Product(product_id="prd_cnmg", organization_id=ORG,
                               external_id="prd_cnmg", name="CNMG 120408-MP insert"))
    session.add(models.Product(product_id="prd_ream", organization_id=ORG,
                               external_id="prd_ream", name="8.0mm HSS-Co machine reamer"))

    rows = []
    # DNMG: repeat purchases, cost stepped up over time, price fell recent → margin deterioration
    rows.append(_cost("prd_dnmg", "2025-10-01", 100, 360, "bill-dnmg-0"))
    rows.append(_cost("prd_dnmg", "2026-01-03", 100, 372, "bill-dnmg-1"))  # +3.3%
    for i, d in enumerate(["2026-03-24", "2026-04-13", "2026-04-18"]):   # baseline price 506
        rows.append(_sale("cst_pitti", "prd_dnmg", d, 20, 506, f"inv-pitti-b{i}"))
    for i, d in enumerate(["2026-06-02", "2026-06-22", "2026-07-02"]):   # recent price 430
        rows.append(_sale("cst_pitti", "prd_dnmg", d, 20, 430, f"inv-pitti-r{i}"))
    # CNMG: purchased once (has last price) but NO cost record on file → missing cost
    rows.append(_sale("cst_pitti", "prd_cnmg", "2026-05-01", 30, 412, "inv-pitti-cnmg"))
    for r in rows:
        session.add(r)
    session.flush()
    return session


def _sales(session) -> Principal:
    return Principal(user_id="usr_sales", organization_id=ORG, role=Role.SALESPERSON,
                     name="R. Nair", email="r.nair@sanketh.in")


def _manager(session) -> Principal:
    return Principal(user_id="usr_manager", organization_id=ORG, role=Role.SALES_MANAGER,
                     name="M. Rao", email="m.rao@sanketh.in")


def _all_labels(resp) -> set[str]:
    labels = {f["label"] for f in resp["customer_facts"]}
    for it in resp["items"]:
        labels |= {f["label"] for f in it["facts"]}
    return labels


# ── scenarios ────────────────────────────────────────────────────────────────
def test_exact_match_repeat_customer_previous_price_context(seeded):
    """Exact product + repeat customer → facts include previous price + trend, AI OK."""
    resp = quote_support(seeded, _manager(seeded), customer_ref="Pitti Engineering Ltd",
                         product_refs=["DNMG 150608-MP insert"], proposed_price=440,
                         provider=MockProvider("ok"), as_of=AS_OF)
    assert resp["resolution"]["customer_resolved"] is True
    assert resp["resolution"]["products_resolved"] == ["DNMG 150608-MP insert"]
    labels = _all_labels(resp)
    assert "Last price paid" in labels                 # previous-price context
    assert "Times purchased" in labels                 # repeat customer
    assert resp["interpretation"]["status"] == "OK"
    assert resp["decision_id"] is not None


def test_alternative_product_still_resolves_and_supports(seeded):
    """Salesperson quotes an alternative item code (fuzzy) → still resolves + supports."""
    resp = quote_support(seeded, _sales(seeded), customer_ref="Pitti Engineering Ltd",
                         product_refs=["DNMG 150608-MP"],  # PIE-style code, no 'insert'
                         provider=MockProvider("ok"), as_of=AS_OF)
    assert resp["resolution"]["products_resolved"] == ["DNMG 150608-MP insert"]


def test_no_product_match(seeded):
    """Unknown product → unresolved, no item history, honest unknown; no fabrication."""
    resp = quote_support(seeded, _sales(seeded), customer_ref="Pitti Engineering Ltd",
                         product_refs=["ZZZZ 999 unknownium"],
                         provider=MockProvider("ok"), as_of=AS_OF)
    assert resp["resolution"]["products_unresolved"] == ["ZZZZ 999 unknownium"]
    assert resp["items"] == []
    assert any("not found" in u["reason"] for u in resp["unknowns"])


def test_new_customer_no_history_withholds(seeded):
    """Unmatched customer → treated as new; recommendation withheld, nothing invented."""
    resp = quote_support(seeded, _sales(seeded), customer_ref="Totally New Buyer Pvt",
                         product_refs=["DNMG 150608-MP insert"],
                         provider=MockProvider("ok"), as_of=AS_OF)
    assert resp["resolution"]["customer_resolved"] is False
    assert resp["interpretation"]["status"] == "SUPPRESSED"
    assert resp["interpretation"]["recommendation"] is None
    assert resp["decision_id"] is None


def test_missing_cost_no_margin_fact(seeded):
    """Item with sales but no cost record → margin/cost facts simply absent."""
    resp = quote_support(seeded, _manager(seeded), customer_ref="Pitti Engineering Ltd",
                         product_refs=["CNMG 120408-MP insert"],
                         provider=MockProvider("ok"), as_of=AS_OF)
    labels = _all_labels(resp)
    assert "Last price paid" in labels                 # has sales history
    assert "Current unit cost" not in labels           # but no cost on file
    assert any("cost_basis" in u["field"] for u in resp["unknowns"])


def test_suspicious_cost_flagged(seeded):
    """Cost recorded at/above the last selling price → flagged as a data-quality unknown."""
    # add a suspicious cost for CNMG that exceeds its 412 selling price
    seeded.add(_cost("prd_cnmg", "2026-05-05", 100, 500, "bill-cnmg-bad"))
    seeded.flush()
    resp = quote_support(seeded, _manager(seeded), customer_ref="Pitti Engineering Ltd",
                         product_refs=["CNMG 120408-MP insert"],
                         provider=MockProvider("ok"), as_of=AS_OF)
    assert any("cost_quality" in u["field"] for u in resp["unknowns"])


def test_margin_deterioration_price_trend_visible(seeded):
    """Falling recent price vs baseline shows a downward price trend to the human."""
    resp = quote_support(seeded, _manager(seeded), customer_ref="Pitti Engineering Ltd",
                         product_refs=["DNMG 150608-MP insert"],
                         provider=MockProvider("ok"), as_of=AS_OF)
    item = resp["items"][0]
    facts = {f["label"]: f["value"] for f in item["facts"]}
    assert facts.get("Price direction") == "down"


def test_role_gating_salesperson_never_sees_cost_or_margin(seeded):
    """A salesperson response contains no RESTRICTED cost/margin facts (absent, not masked)."""
    resp = quote_support(seeded, _sales(seeded), customer_ref="Pitti Engineering Ltd",
                         product_refs=["DNMG 150608-MP insert"],
                         provider=MockProvider("ok"), as_of=AS_OF)
    labels = _all_labels(resp)
    assert "Current unit cost" not in labels
    assert "Standard margin" not in labels
    assert "Cost change" not in labels
    # but the OPERATIONAL direction-only flag IS allowed
    assert "Cost direction" in labels
    assert resp["restricted_absent"] is True


def test_manager_sees_cost_and_margin(seeded):
    resp = quote_support(seeded, _manager(seeded), customer_ref="Pitti Engineering Ltd",
                         product_refs=["DNMG 150608-MP insert"],
                         provider=MockProvider("ok"), as_of=AS_OF)
    labels = _all_labels(resp)
    assert {"Current unit cost", "Standard margin", "Cost change"} <= labels


def test_ai_unavailable_degrades_to_facts(seeded):
    """Provider outage → FAILED status, facts still present, still actionable."""
    resp = quote_support(seeded, _manager(seeded), customer_ref="Pitti Engineering Ltd",
                         product_refs=["DNMG 150608-MP insert"],
                         provider=MockProvider("unavailable"), as_of=AS_OF)
    assert resp["interpretation"]["status"] == "FAILED"
    assert "Last price paid" in _all_labels(resp)      # facts unaffected
    assert resp["decision_id"] is not None


def test_ai_malformed_degrades(seeded):
    """Malformed model output (twice) → DEGRADED, deterministic reading shown."""
    resp = quote_support(seeded, _manager(seeded), customer_ref="Pitti Engineering Ltd",
                         product_refs=["DNMG 150608-MP insert"],
                         provider=MockProvider("malformed"), as_of=AS_OF)
    assert resp["interpretation"]["status"] == "DEGRADED"


def test_ai_hallucinated_number_rejected(seeded):
    """A fabricated figure fails grounding → DEGRADED, no invented number surfaces."""
    resp = quote_support(seeded, _manager(seeded), customer_ref="Pitti Engineering Ltd",
                         product_refs=["DNMG 150608-MP insert"],
                         provider=MockProvider("hallucinate"), as_of=AS_OF)
    assert resp["interpretation"]["status"] == "DEGRADED"
    assert "12345" not in (resp["interpretation"]["recommendation"] or "")


def test_user_ignores_recommendation_captured(seeded):
    """The salesperson's decision (reject the recommendation) feeds the Decision Store."""
    resp = quote_support(seeded, _sales(seeded), customer_ref="Pitti Engineering Ltd",
                         product_refs=["DNMG 150608-MP insert"], proposed_price=440,
                         provider=MockProvider("ok"), as_of=AS_OF)
    did = resp["decision_id"]
    assert did is not None
    repo = DecisionRepository(seeded, ORG)
    d = repo.get(did)
    # reject == DISMISS with an optional reason
    repo.record_human_action(d, HumanAction.DISMISS, actor_user_id="usr_sales",
                             reason="Customer relationship; holding my price.")
    seeded.flush()
    assert d.status == "DISMISSED"
    assert d.human_action["action"] == "DISMISS"
    assert d.override_reason == "Customer relationship; holding my price."
    # the AI recommendation is retained alongside the human decision (auditable)
    assert d.ai.get("status") == "OK"


def test_repeat_request_refreshes_same_decision(seeded):
    """Re-requesting support for the same (customer, item) updates one decision row."""
    p = _manager(seeded)
    r1 = quote_support(seeded, p, customer_ref="Pitti Engineering Ltd",
                       product_refs=["DNMG 150608-MP insert"], provider=MockProvider("ok"),
                       as_of=AS_OF)
    r2 = quote_support(seeded, p, customer_ref="Pitti Engineering Ltd",
                       product_refs=["DNMG 150608-MP insert"], provider=MockProvider("ok"),
                       as_of=AS_OF)
    assert r1["decision_id"] == r2["decision_id"]


def test_repeat_request_skips_second_ai_call_and_signal(seeded):
    """Cost control: identical context reuses the stored interpretation — no second
    model call and no duplicate signal row (unbounded AI spend + audit growth)."""
    p = _manager(seeded)
    prov = MockProvider("ok")
    quote_support(seeded, p, customer_ref="Pitti Engineering Ltd",
                  product_refs=["DNMG 150608-MP insert"], provider=prov, as_of=AS_OF)
    calls_after_first = prov.calls
    sigs_after_first = seeded.query(models.Signal).filter(
        models.Signal.signal_type == "QUOTE_CONTEXT").count()

    r2 = quote_support(seeded, p, customer_ref="Pitti Engineering Ltd",
                       product_refs=["DNMG 150608-MP insert"], provider=prov, as_of=AS_OF)
    assert prov.calls == calls_after_first            # no second inference
    assert r2["interpretation"]["status"] == "OK"     # served from cache
    sigs_after_second = seeded.query(models.Signal).filter(
        models.Signal.signal_type == "QUOTE_CONTEXT").count()
    assert sigs_after_second == sigs_after_first       # no duplicate signal


def test_quote_context_excluded_from_proactive_list(seeded):
    """On-demand QUOTE_CONTEXT decisions do not clutter the proactive queue."""
    from app.routers.decisions import list_decisions
    quote_support(seeded, _sales(seeded), customer_ref="Pitti Engineering Ltd",
                  product_refs=["DNMG 150608-MP insert"], provider=MockProvider("ok"),
                  as_of=AS_OF)
    rows = list_decisions(type=None, status_filter=None, principal=_sales(seeded), session=seeded)
    assert all(r.decision_type != "QUOTE_CONTEXT" for r in rows)
    # but reachable when explicitly filtered by type
    only = list_decisions(type="QUOTE_CONTEXT", status_filter=None,
                          principal=_sales(seeded), session=seeded)
    assert len(only) == 1
