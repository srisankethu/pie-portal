"""Recomputing the derived metrics as a background job.

``recompute`` rebuilds every metric row for an organization and runs the
detectors over the result — O(customers × items), with a detector pass per
pair. Asked for through ``POST /commercial/recompute`` it runs inside the
request, which is the same shape the sync had before it was moved to the
background: a browser on a spinner with no way to tell whether the work had
started, stalled or died, and a gateway that gives up before the work does.

So the same seam, in the same shape ``ingestion/jobs`` established: this module
owns the topic and the handler, because it owns the *work* — the queue knows
nothing about metrics, detectors or thresholds. The caller decides which way it
goes; the default stays synchronous, because that is what the endpoint has
always done and what a caller with a small organization wants.

Deliberately not the whole of ``commercial``. Nothing here computes: it opens a
session, calls the computation that already exists, and records what came back.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..domain import models

log = logging.getLogger("pie_portal.commercial")

#: The queue topic this module owns.
RECOMPUTE_TOPIC = "commercial.recompute"


def _run_queued_recompute(payload: dict) -> None:
    """The worker's side: rebuild this organization's metrics on its own session.

    Its own session for the reason ``ingestion.jobs.run_job`` gives — the
    request that queued this is long finished and its session with it — and
    committed here, because a rebuild nobody committed is a rebuild that did
    not happen.
    """
    from ..db import SessionLocal
    from .compute import recompute

    organization_id = payload["organization_id"]
    customer_id = payload.get("customer_id")
    session = SessionLocal()
    try:
        report = recompute(
            session, organization_id,
            customer_ids={customer_id} if customer_id else None,
            emit_signals=bool(payload.get("emit_signals", True)))
        session.commit()
        log.info("queued recompute finished for %s: %s", organization_id,
                 report.to_dict())
    except Exception:
        session.rollback()
        # Re-raised so the queue records the failure and retries it. Swallowing
        # it here would leave a DONE message over work that did not happen.
        log.exception("queued recompute failed for %s", organization_id)
        raise
    finally:
        session.close()


def enqueue_recompute(session: Session, organization_id: str, *,
                      customer_id: Optional[str] = None,
                      emit_signals: bool = True) -> models.QueuedMessage:
    """Ask for a rebuild in the background. Does not commit — the caller does.

    Deduplicated on the organization and the scope, so a second click while the
    first rebuild is still queued is the same rebuild rather than two passes
    upserting the same rows.
    """
    from ..messaging import enqueue

    return enqueue(
        session, RECOMPUTE_TOPIC,
        {"organization_id": organization_id, "customer_id": customer_id,
         "emit_signals": bool(emit_signals)},
        organization_id=organization_id,
        dedupe_key=f"{RECOMPUTE_TOPIC}:{organization_id}:{customer_id or '*'}")


def register_topics() -> None:
    """Bind ``commercial.recompute`` to the handler above.

    Called at import and again by ``messaging.worker.load_handlers`` — see the
    note there: importing an already-imported module runs nothing, so a worker
    in a process that never populated the registry would claim the message and
    fail it as an unknown topic.
    """
    from ..messaging.handlers import register

    register(RECOMPUTE_TOPIC, _run_queued_recompute, replace=True)


register_topics()


__all__: list[Any] = ["RECOMPUTE_TOPIC", "enqueue_recompute", "register_topics"]
