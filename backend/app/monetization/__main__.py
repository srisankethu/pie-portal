"""``python -m app.monetization`` — the whole analysis, on a terminal.

The console screen is for exploring one customer; this is for producing the
document. It writes the same numbers the API serves, from the same functions,
because a pricing recommendation that was computed once for a deck and again
for a screen is two recommendations.

    python -m app.monetization                # the summary tables
    python -m app.monetization --json         # the whole report, machine-read
    python -m app.monetization --impact conservative
"""
from __future__ import annotations

import argparse
import json
from decimal import Decimal

from .config import load_parameters
from .customer import build_waterfall
from .report import full_report, margin_hypothesis, recommend
from .segments import ANSWERS_TO, ARCHETYPES, IMPACTS
from .unitecon import floor_price, unit_economics


def _cr(value) -> str:
    return f"{float(Decimal(str(value))) / 1e7:,.2f} Cr"


def _l(value) -> str:
    return f"{float(Decimal(str(value))) / 1e5:,.1f}L"


def main() -> int:
    ap = argparse.ArgumentParser(prog="app.monetization")
    ap.add_argument("--impact", default="base", choices=sorted(IMPACTS))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    params = load_parameters()
    if args.json:
        print(json.dumps(full_report(params, impact_key=args.impact),
                         indent=2, default=str))
        return 0

    impact = IMPACTS[args.impact]
    print(f"PIE pricing model · assumptions {params.version} · "
          f"impact set '{args.impact}'\n")

    print(f"{'segment':<12} {'GMV':>12} {'gross profit':>14} "
          f"{'incr. GP':>11} {'value':>11} {'floor':>9} {'RECOMMEND':>11} "
          f"{'ROI':>7} {'0.1% margin':>13}")
    for key, profile in ARCHETYPES.items():
        wf = build_waterfall(profile, impact, params)
        rec = recommend(wf, params)
        hyp = margin_hypothesis(wf, params)["verdict"]["readings"]
        touched = hyp["PIE_TOUCHED_GROSS_MARGIN"]["annual_fee"]
        roi = rec["evaluation"]["customer_roi"]
        flag = "" if not rec["band"]["is_empty"] else "  ← EMPTY BAND"
        print(f"{key:<12} {_cr(wf.with_pie.revenue):>12} "
              f"{_cr(wf.total_gross_margin):>14} "
              f"{_cr(wf.incremental_gross_profit):>11} "
              f"{_cr(wf.total_economic_value):>11} "
              f"{_l(floor_price(profile, params)):>9} "
              f"{_l(rec['recommended_annual_fee']):>11} "
              f"{('—' if roi is None else f'{roi:.1f}x'):>7} "
              f"{_l(touched):>13}{flag}")

    print("\nWhat each segment answers")
    for key, question in ANSWERS_TO.items():
        wf = build_waterfall(ARCHETYPES[key], impact, params)
        rec = recommend(wf, params)
        econ = unit_economics(ARCHETYPES[key],
                              Decimal(rec["evaluation"]["fee"]["annual_fee"]),
                              params, orders=wf.covered_with_pie.orders)
        print(f"  {question}")
        print(f"    {_l(rec['recommended_annual_fee'])}/yr = "
              f"{_l(rec['structure']['platform_fee'])} platform + "
              f"{rec['structure']['variable_rate_pct']} of PIE-touched GMV")
        print(f"    design partner {_l(rec['design_partner_offer']['annual_fee'])}/yr"
              f" · PIE gross margin "
              f"{'—' if econ.gross_margin is None else f'{econ.gross_margin:.0%}'}"
              f" · LTV/CAC "
              f"{'—' if econ.ltv_cac is None else f'{econ.ltv_cac:.1f}x'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
