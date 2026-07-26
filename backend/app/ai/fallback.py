"""Deterministic fallback templates (spec §16).

When AI is unavailable, invalid, or ungrounded, the decision still surfaces with a
plain sentence rendered from the signal's own metrics — no generated claims, no
recommendation. Numbers here come straight from the deterministic signal, so they
are grounded by construction.
"""
from __future__ import annotations

from typing import Any


def _pct(x: Any) -> str:
    try:
        return f"{abs(float(x)) * 100:.0f}%"
    except (TypeError, ValueError):
        return "—"


def template(signal_type: str, metrics: dict[str, Any], subject_label: str) -> dict[str, str]:
    m = metrics or {}
    if signal_type == "CUSTOMER_DECLINE":
        title = f"Revenue decline: {subject_label}"
        expl = (f"Recent-period revenue is down {_pct(m.get('pct_change'))} versus the prior "
                f"comparable period.")
    elif signal_type == "CUSTOMER_DORMANCY":
        title = f"Overdue to order: {subject_label}"
        expl = (f"No order in {m.get('actual_gap_days', '—')} days; the typical interval is "
                f"about {m.get('typical_interval_days', '—')} days.")
    elif signal_type == "MARGIN_DETERIORATION":
        title = f"Margin deterioration: {subject_label}"
        expl = (f"Gross margin moved from {_pct(m.get('baseline_margin_pct'))} to "
                f"{_pct(m.get('current_margin_pct'))} between the comparison periods.")
    elif signal_type == "COST_PASS_THROUGH":
        title = f"Cost increase not passed through: {subject_label}"
        expl = (f"Purchase cost rose {_pct(m.get('cost_delta_pct'))} while the selling price "
                f"moved {_pct(m.get('price_change_pct'))}.")
    elif signal_type == "QUOTE_CONTEXT":
        title = f"Commercial context: {subject_label}"
        expl = ("The commercial facts for this customer and item are shown on the left. "
                "The reading of them is unavailable; weigh the facts and price at your discretion.")
    else:
        title = f"Signal: {subject_label}"
        expl = "A deterministic signal was detected for this subject."
    return {"concise_title": title, "explanation": expl}
