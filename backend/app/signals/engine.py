"""Signal Engine runner.

Runs the four proactive detectors for one organization over the current read
model, stamps provenance (detector + threshold versions), and persists immutable
Signals. Signals are write-once: a re-run emits *new* signals (the later Decision
layer dedups/supersedes). QUOTE_CONTEXT is on-demand (see ``quote_context``) and
is not run here.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..repositories import SignalRepository
from . import cost_pass_through, decline, dormancy, margin
from .aggregates import load_snapshot
from .base import Snapshot, SignalDraft
from .config import SignalThresholds, load_thresholds

_DETECTORS = (
    ("CUSTOMER_DECLINE", decline.detect),
    ("CUSTOMER_DORMANCY", dormancy.detect),
    ("MARGIN_DETERIORATION", margin.detect),
    ("COST_PASS_THROUGH", cost_pass_through.detect),
)


def compute_drafts(snapshot: Snapshot, th: SignalThresholds,
                   as_of: Optional[date] = None) -> list[SignalDraft]:
    """Pure computation: all detector drafts for a snapshot (no persistence)."""
    ref = as_of or snapshot.as_of()
    if ref is None:
        return []
    drafts: list[SignalDraft] = []
    for _name, fn in _DETECTORS:
        drafts.extend(fn(snapshot, th, ref))
    # stamp provenance
    for d in drafts:
        d.detector_version = settings.DETECTOR_VERSION
        d.threshold_config_version = th.version
    return drafts


def run_detectors(session: Session, organization_id: str,
                  thresholds: Optional[SignalThresholds] = None,
                  as_of: Optional[date] = None) -> dict:
    """Run detectors for an org and persist the resulting signals."""
    th = thresholds or load_thresholds()
    snapshot = load_snapshot(session, organization_id)
    drafts = compute_drafts(snapshot, th, as_of)

    repo = SignalRepository(session, organization_id)
    counts: dict[str, int] = {}
    ids: list[str] = []
    for d in drafts:
        model = repo.add(d.to_model(organization_id))
        session.flush()
        counts[d.signal_type] = counts.get(d.signal_type, 0) + 1
        ids.append(model.signal_id)

    return {
        "organization_id": organization_id,
        "as_of": (as_of or snapshot.as_of()).isoformat() if snapshot.as_of() else None,
        "detector_version": settings.DETECTOR_VERSION,
        "threshold_config_version": th.version,
        "signals_emitted": len(drafts),
        "by_type": counts,
        "signal_ids": ids,
    }
