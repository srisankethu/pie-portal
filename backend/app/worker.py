"""Run the queue worker as a process of its own.

    python -m app.worker

The same drain loop the API can run in-process (``messaging.worker``), given a
container of its own so that hour-long pulls and whole-organization rebuilds do
not run in a process that is also answering requests. One implementation, two
ways to start it: this module owns no loop of its own, it starts that one and
waits.

Deliberately loud about doing nothing. A worker process whose configuration
declines to start a worker is a container that stays green while the queue
fills, so this exits non-zero and says which setting refused.

Stops on SIGTERM — which is what an orchestrator sends before it replaces a
container — by asking the loop to finish its pass. A message claimed but not
finished when the process dies is not lost either way: its heartbeat goes cold
and the next worker's reaper hands it back. This just makes the common case
tidy rather than reliant on that.
"""
from __future__ import annotations

import logging
import signal
import threading

from .config import settings
from .observability import logs

log = logging.getLogger("pie_portal.worker")

_stop = threading.Event()


def _handle_signal(signum, _frame) -> None:
    log.info("worker: signal %s received; finishing the current pass", signum)
    _stop.set()


def main() -> int:
    logs.configure()

    from .db import engine
    from .messaging import start_worker, stop_worker
    from .migration_state import inspect_database

    # Said once at boot, in the words `main` uses. A worker against a database
    # whose schema is behind fails every job it claims, and the traceback names
    # a column rather than the deploy step somebody skipped.
    try:
        state = inspect_database(engine)
        if not state.healthy:
            log.error("%s", state.summary)
        else:
            log.info("database at %s", state.current)
    except Exception:  # noqa: BLE001 — a worker must still try to work
        log.exception("could not read the migration state; continuing")

    if not start_worker():
        log.error(
            "queue worker declined to start: SYNC_DISPATCH=%r and "
            "QUEUE_WORKER=%r. Set QUEUE_WORKER=1 (or SYNC_DISPATCH=queue) for "
            "a process whose whole job is draining the queue.",
            settings.SYNC_DISPATCH, settings.QUEUE_WORKER)
        return 2

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    log.info("queue worker process ready")

    _stop.wait()
    stop_worker()
    log.info("queue worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
