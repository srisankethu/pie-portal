"""Rolling AI ops metrics (WS3) — deterministic aggregation over telemetry.

Answers the questions an operator actually has: how often is the AI degrading or
failing, how much is it costing, how well is the cache working, and what is
breaking. All arithmetic is backend-side and traceable to the logged rows.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from ..config import settings
from ..domain import models

log = logging.getLogger("pie_portal.ai.metrics")


def _rate(n: int, total: int) -> float:
    return round(n / total, 4) if total else 0.0


def health_band(degraded_rate: float, sample: int) -> tuple[str, str]:
    """Two-sided band on the DEGRADED rate.

    A gate rejecting constantly points at a prompt or model problem. A gate that
    never rejects anything is *also* a finding: either it is too permissive, or
    the prompt is so constrained that the model adds no interpretive value.
    """
    if sample < settings.AI_HEALTH_MIN_SAMPLE:
        return "INSUFFICIENT_DATA", (
            f"Fewer than {settings.AI_HEALTH_MIN_SAMPLE} calls in window; "
            "no health inference drawn.")
    if degraded_rate > settings.AI_DEGRADED_RATE_MAX:
        return "HIGH", (
            f"Degraded rate {degraded_rate:.1%} exceeds the "
            f"{settings.AI_DEGRADED_RATE_MAX:.1%} upper bound — investigate the "
            "prompt, the model, or the fact sets being sent.")
    if degraded_rate < settings.AI_DEGRADED_RATE_MIN:
        return "SUSPICIOUSLY_LOW", (
            f"Degraded rate {degraded_rate:.1%} sits below the "
            f"{settings.AI_DEGRADED_RATE_MIN:.1%} lower bound — a gate that never "
            "rejects is either too permissive or paired with a prompt too "
            "constrained to be adding interpretive value.")
    return "OK", "Degraded rate is within the expected band."


def summarize(rows: Sequence[models.AiCallLog], *, days: int) -> dict[str, Any]:
    total = len(rows)
    by_status: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    corrections: dict[str, int] = {}
    cache_hits = 0
    provider_calls = 0
    cost = 0.0
    cost_known = 0
    latencies: list[int] = []

    for r in rows:
        by_status[r.ai_status] = by_status.get(r.ai_status, 0) + 1
        if r.failure_reason:
            by_reason[r.failure_reason] = by_reason.get(r.failure_reason, 0) + 1
        for c in (r.corrections or []):
            corrections[c] = corrections.get(c, 0) + 1
        if r.cache_hit:
            cache_hits += 1
        if r.provider_called:
            provider_calls += 1
        if r.estimated_cost_usd is not None:
            cost += float(r.estimated_cost_usd)
            cost_known += 1
        if r.latency_ms is not None:
            latencies.append(r.latency_ms)

    degraded = by_status.get("DEGRADED", 0)
    degraded_rate = _rate(degraded, total)
    band, note = health_band(degraded_rate, total)
    latencies.sort()

    return {
        "window_days": days,
        "calls": total,
        "provider_calls": provider_calls,
        "by_status": by_status,
        "rates": {
            "degraded": degraded_rate,
            "failed": _rate(by_status.get("FAILED", 0), total),
            "suppressed": _rate(by_status.get("SUPPRESSED", 0), total),
            "ok": _rate(by_status.get("OK", 0), total),
            "cache_hit": _rate(cache_hits, total),
        },
        "failure_reasons": by_reason,
        "corrections": corrections,
        "cost": {
            "currency": "USD",
            "total_estimated": round(cost, 6),
            "per_decision": round(cost / total, 6) if total else 0.0,
            "per_day": round(cost / days, 6) if days else 0.0,
            "calls_with_known_usage": cost_known,
            "note": ("Estimated from logged token counts at the configured rates; "
                     "calls without reported usage are excluded from the total."),
        },
        "latency_ms": {
            "median": latencies[len(latencies) // 2] if latencies else None,
            "max": latencies[-1] if latencies else None,
        },
        "health": {"degraded_rate": degraded_rate, "band": band, "note": note},
    }


def report(repo, *, windows: tuple[int, ...] = (7, 30)) -> dict[str, Any]:
    """Build the rolling report and emit health warnings to the log."""
    now = datetime.now(timezone.utc)
    out: dict[str, Any] = {"generated_at": now.isoformat(), "windows": {}}
    for days in windows:
        rows = repo.since(now - timedelta(days=days))
        summary = summarize(rows, days=days)
        out["windows"][f"{days}d"] = summary
        band = summary["health"]["band"]
        if band in ("HIGH", "SUSPICIOUSLY_LOW"):
            log.warning("ai health %s over %dd: %s", band, days, summary["health"]["note"])
    return out
