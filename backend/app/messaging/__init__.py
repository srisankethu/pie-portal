"""The message queue: durable background work, and the worker that drains it.

Three files, one concern each:

* ``queue`` — the broker. Enqueue, claim, complete, fail, reap, depth. Every
  state transition a message can make lives here and nowhere else.
* ``handlers`` — the topic registry. A topic string maps to one callable, and
  the module that owns the *work* registers it (``ingestion.jobs`` owns
  ``sync.run``), so the queue itself knows nothing about syncs, or Zoho, or
  what any payload means.
* ``worker`` — the loop. Claims what is due, runs it, records the outcome, and
  returns dead workers' messages to the queue.

Infrastructure, in the sense §3 gives the word: it imports neither
``commercial`` nor ``ai``, and it computes nothing. What it moves is a request
to do work, not a number.
"""
from __future__ import annotations

from .handlers import HandlerError, UnknownTopic, handler, handler_for, register
from .queue import (
    ACTIVE,
    DEAD_LETTER,
    claim,
    complete,
    depth,
    enqueue,
    fail,
    heartbeat,
    reap_stale,
)
from .worker import drain_once, start_worker, stop_worker, worker_running

__all__ = [
    "ACTIVE", "DEAD_LETTER", "HandlerError", "UnknownTopic",
    "claim", "complete", "depth", "drain_once", "enqueue", "fail", "handler",
    "handler_for", "heartbeat", "reap_stale", "register", "start_worker",
    "stop_worker", "worker_running",
]
