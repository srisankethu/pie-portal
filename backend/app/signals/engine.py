"""Signal Engine runner.

Runs the four proactive detectors for one organization over the current read
model, stamps provenance (detector + threshold versions), and persists immutable
Signals. Signals are write-once: a re-run emits *new* signals (the later Decision
layer dedups/supersedes). QUOTE_CONTEXT is on-demand (see ``quote_context``) and
is not run here.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..repositories import SignalRepository
from . import cost_pass_through, decline, dormancy, margin
from .aggregates import load_snapshot
from .base import Coverage, Snapshot, SignalDraft
from .config import SignalThresholds, load_thresholds

log = logging.getLogger("pie_portal.signals.engine")

#: The four proactive detectors, as modules. Each exposes ``examine`` (drafts
#: plus the denominator and the withholds) and ``detect`` (the drafts alone,
#: delegating to it). The engine goes through ``examine``, so every run this
#: platform makes carries what it could not judge alongside what it found.
_DETECTORS = (decline, dormancy, margin, cost_pass_through)


def examine_all(snapshot: Snapshot, th: SignalThresholds,
                as_of: Optional[date] = None) -> dict[str, Coverage]:
    """Pure computation: every detector's full result for a snapshot.

    Keyed by detector name. Provenance is stamped here rather than in each
    detector for the reason it always was — a detector that had to know its own
    version string would be four places to update — and it is stamped on the way
    out of this function so no caller can obtain an unstamped draft.
    """
    ref = as_of or snapshot.as_of()
    if ref is None:
        return {}
    out: dict[str, Coverage] = {}
    for module in _DETECTORS:
        coverage = module.examine(snapshot, th, ref)
        for d in coverage.drafts:
            d.detector_version = settings.DETECTOR_VERSION
            d.threshold_config_version = th.version
        out[coverage.detector] = coverage
    return out


def compute_drafts(snapshot: Snapshot, th: SignalThresholds,
                   as_of: Optional[date] = None) -> list[SignalDraft]:
    """Pure computation: all detector drafts for a snapshot (no persistence).

    What ``examine_all`` found, with what it could not judge dropped. Callers
    that are about to *report* a count should use ``examine_all`` instead: a
    bare draft count cannot distinguish a clean book from an unexaminable one.
    """
    return [d for coverage in examine_all(snapshot, th, as_of).values()
            for d in coverage.drafts]


def thresholds_for_org(session: Session, organization_id: str) -> SignalThresholds:
    """Environment detector thresholds, with the owner-editable margin drop applied.

    Public because ``retrospective`` needs the same thresholds the queue runs on.
    Two resolutions would mean a look-back that judged the book against a
    different margin-drop threshold from the one raising the signals, and the
    difference would show as a retrospective disagreeing with the queue.

    Imported here rather than at module scope: ``commercial.quote_service`` imports
    ``signals.base``, so a module-level import of ``commercial`` from this package
    is a cycle waiting for the wrong import order.
    """
    from ..commercial.policy import load_for_org

    th = load_thresholds()
    try:
        commercial = load_for_org(session, organization_id)
    except Exception:  # noqa: BLE001 — policy is never a reason not to detect
        log.exception("could not load commercial policy for %s; using the "
                      "environment margin-drop threshold", organization_id)
        return th
    return replace(th, margin_drop_points=commercial.queue_margin_drop_pp)


def run_detectors(session: Session, organization_id: str,
                  thresholds: Optional[SignalThresholds] = None,
                  as_of: Optional[date] = None) -> dict:
    """Run detectors for an org and persist the resulting signals.

    The margin-drop threshold comes from this organization's *commercial* policy,
    which is the one an owner can edit. It used to be environment-only, so the
    "Erosion threshold" in Settings quietened the commercial screens and left the
    decision queue running on a number nobody could reach without a redeploy —
    a control that silently did half of what it said.

    An explicitly supplied ``thresholds`` is left exactly as the caller built it:
    passing one is a deliberate act, and tests and one-off scripts rely on it
    meaning what it says.
    """
    th = thresholds if thresholds is not None else thresholds_for_org(
        session, organization_id)
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
