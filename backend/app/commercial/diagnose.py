"""Why is the Customer × Item screen empty?

Runs the whole funnel against real synced data and names the stage that stops.
There are several honest reasons the screen shows nothing, and they look
identical from the outside:

  sales lines → cost records → metric rows → costed rows → enough history
              → signals → "items requiring attention"

Each stage can be the answer, and a screen that just renders empty makes them
indistinguishable. This prints where the drop happens and what to do about it.

    python -m app.commercial.diagnose
    python -m app.commercial.diagnose --org org_4u --customer cst_xyz

Reports structure and counts only — never a customer's prices or margins — so
it is safe to paste into a support conversation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..domain import models
from ..domain.enums import EvidenceSufficiency
from .policy import load_for_org

_DAYS_PER_MONTH = 30.44


def _count(session: Session, model, org: str) -> int:
    return session.scalar(select(func.count()).select_from(model).where(
        model.organization_id == org)) or 0


def diagnose(session: Session, org: str,
             customer_id: Optional[str] = None) -> dict:
    th = load_for_org(session, org)
    out: dict = {"organization_id": org, "customer_id": customer_id,
                 "thresholds_version": th.version, "findings": [], "verdict": ""}

    # ── stage 1: is there any source data at all ────────────────────────────
    out["customers"] = _count(session, models.Customer, org)
    out["products"] = _count(session, models.Product, org)
    out["sales_lines"] = _count(session, models.SalesTxn, org)
    out["cost_records"] = _count(session, models.CostRecord, org)

    if out["sales_lines"] == 0:
        out["verdict"] = (
            "No invoice lines have been synced for this organization. The screen "
            "has nothing to describe. Run a sync from Data & connection.")
        return out

    span = session.execute(select(func.min(models.SalesTxn.date),
                                  func.max(models.SalesTxn.date))
                           .where(models.SalesTxn.organization_id == org)).one()
    first, last = span
    out["first_sale"] = first.isoformat() if first else None
    out["last_sale"] = last.isoformat() if last else None
    months = ((last - first).days / _DAYS_PER_MONTH) if first and last else 0.0
    out["history_months"] = round(months, 1)

    # ── stage 2: cost. No bill, no margin — and that is most of the screen ──
    if out["cost_records"] == 0:
        out["findings"].append(
            "No bills have been synced, so no purchase cost is known for any "
            "item. Margin cannot be computed at all, which empties every "
            "cost-based part of the screen. Bills are pulled by the same sync "
            "as invoices — check the sync run's counters and the Zoho scopes.")

    sold = {p for (p,) in session.execute(
        select(models.SalesTxn.product_id.distinct())
        .where(models.SalesTxn.organization_id == org))}
    costed = {p for (p,) in session.execute(
        select(models.CostRecord.product_id.distinct())
        .where(models.CostRecord.organization_id == org))}
    out["products_sold"] = len(sold)
    out["products_with_cost"] = len(sold & costed)
    out["products_sold_but_never_bought"] = len(sold - costed)

    # ── stage 3: were the metrics ever computed ─────────────────────────────
    metric_q = select(models.CustomerItemMetric).where(
        models.CustomerItemMetric.organization_id == org)
    if customer_id:
        metric_q = metric_q.where(models.CustomerItemMetric.customer_id == customer_id)
    rows = list(session.scalars(metric_q))
    out["metric_rows"] = len(rows)

    if not rows:
        out["verdict"] = (
            "Invoice lines exist but no Customer x Item metric rows do — the "
            "derived table was never built for this data. Run "
            "`python -m app.commercial.backfill --org " + org + "`, or POST "
            "/api/v1/commercial/recompute. A sync normally does this at the "
            "end; if it did not, the sync log will have a "
            "'customer-item recompute failed' entry.")
        return out

    # ── stage 4: how much of it can actually carry a margin ─────────────────
    with_cost = [r for r in rows if r.cost_covered_txns]
    out["relationships"] = len(rows)
    out["relationships_with_cost"] = len(with_cost)
    out["relationships_without_cost"] = len(rows) - len(with_cost)

    levels = Counter(r.data_sufficiency for r in rows)
    out["sufficiency"] = dict(levels)

    reasons = Counter()
    for r in rows:
        for reason in (r.sufficiency_reasons or []):
            # Collapse the numbers so the shapes group: "only 2 transactions"
            # and "only 1 transaction" are one finding, not two.
            #
            # Cost is tested first on purpose: "cost known for only 0% of
            # transactions" also contains the word "transactions", and matching
            # that branch first reported a cost problem as a volume problem —
            # sending someone to sync more invoices when the gap was bills.
            key = ("cost missing on most transactions" if "cost known" in reason
                   else "too little history" if "months" in reason
                   else "too few transactions" if "transaction" in reason
                   else reason)
            reasons[key] += 1
    out["top_reasons"] = reasons.most_common(5)

    flagged = [r for r in rows if r.signals]
    out["rows_with_signals"] = len(flagged)
    out["signal_counts"] = dict(Counter(
        s for r in rows for s in (r.signals or [])))

    # ── the verdict ─────────────────────────────────────────────────────────
    insufficient = levels.get(EvidenceSufficiency.INSUFFICIENT.value, 0)

    if flagged:
        out["verdict"] = (
            f"{len(flagged)} of {len(rows)} relationships carry a signal and will "
            f"appear under 'Items requiring attention'. If the screen still looks "
            f"empty, open a customer that actually has one of them — the table is "
            f"per customer, reached from Accounts.")
    elif out["cost_records"] == 0:
        out["verdict"] = (
            "Every relationship is uncostable because no bills were synced. The "
            "screen will show revenue and quantities but no margin, and nothing "
            "will be flagged — a margin analysis with no purchase cost has "
            "nothing to say. Sync bills, then recompute.")
    elif insufficient == len(rows):
        out["verdict"] = (
            f"All {len(rows)} relationships are INSUFFICIENT, so no signal is "
            f"raised — deliberately: weak data must not produce confident output. "
            f"Most common reasons: "
            f"{', '.join(k for k, _ in reasons.most_common(3)) or 'unknown'}. "
            f"With {out['history_months']} months of history and thresholds "
            f"needing {th.min_history_months:.0f} months and "
            f"{th.min_transactions} transactions, this is expected until more "
            f"history is synced. Pull a longer window from Data & connection.")
    else:
        out["verdict"] = (
            f"{len(rows)} relationships computed and {len(rows) - insufficient} "
            f"have enough evidence, but none crossed a detection threshold — "
            f"nothing is deteriorating by more than "
            f"{th.min_margin_deterioration_pp * 100:.0f} pp or "
            f"{th.min_material_gap:,.0f} rupees. That is a real answer, "
            f"not a fault: the account screen still shows every item under "
            f"'all items'.")
    return out


def _print(d: dict) -> None:
    print(f"organization      {d['organization_id']}"
          + (f"  (customer {d['customer_id']})" if d.get("customer_id") else ""))
    print(f"thresholds        {d['thresholds_version']}")
    print()
    print("── source data ──")
    print(f"  customers       {d['customers']:>8}")
    print(f"  products        {d['products']:>8}")
    print(f"  invoice lines   {d['sales_lines']:>8}")
    print(f"  bill lines      {d['cost_records']:>8}")
    if d.get("first_sale"):
        print(f"  history         {d['first_sale']} → {d['last_sale']}  "
              f"({d['history_months']} months)")
    if "products_sold" in d:
        print(f"  items sold      {d['products_sold']:>8}")
        print(f"  …with a cost    {d['products_with_cost']:>8}")
        print(f"  …never bought   {d['products_sold_but_never_bought']:>8}"
              "   (margin impossible for these)")

    if "metric_rows" in d:
        print()
        print("── derived metrics ──")
        print(f"  relationships   {d['metric_rows']:>8}")
    if "relationships_with_cost" in d:
        print(f"  …costed         {d['relationships_with_cost']:>8}")
        print(f"  …uncostable     {d['relationships_without_cost']:>8}")
        print(f"  sufficiency     {d['sufficiency']}")
        if d.get("top_reasons"):
            print("  why not more:")
            for reason, n in d["top_reasons"]:
                print(f"    {n:>5}  {reason}")
        print(f"  with a signal   {d['rows_with_signals']:>8}"
              "   ← this is what 'items requiring attention' shows")
        if d.get("signal_counts"):
            for name, n in sorted(d["signal_counts"].items()):
                print(f"    {n:>5}  {name}")

    print()
    print("── verdict ──")
    for line in _wrap(d["verdict"]):
        print(f"  {line}")


def _wrap(text: str, width: int = 74) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", default=settings.DEFAULT_ORG_ID)
    parser.add_argument("--customer", default=None)
    args = parser.parse_args()

    from ..db import SessionLocal

    session = SessionLocal()
    try:
        _print(diagnose(session, args.org, args.customer))
    finally:
        session.close()


if __name__ == "__main__":
    main()
