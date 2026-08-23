"""The topic registry: which callable runs a message, and nothing else.

One dict, deliberately. The alternative shape — a base class every job
subclasses — would buy nothing here: a handler takes a payload and returns
nothing, there is no state to inherit, and §7 is explicit that a single
implementation with no second one in sight does not need an interface.

Two rules the registry enforces rather than documents:

**A topic has exactly one handler.** Registering a second one for the same
topic raises rather than winning silently, because the loser would be a job
that stops running with nothing to show for it.

**The module that owns the work registers it.** ``ingestion.jobs`` registers
``sync.run``; this package never learns what a sync is. That is what keeps the
queue substitutable and keeps the ingestion knowledge in ``ingestion/`` where
§3 puts it.

A handler receives the payload dict and opens its own session if it needs one.
It must not be handed the worker's session: the worker uses that for queue
bookkeeping — the heartbeat has to be committed *while* the handler runs — and
a job that shares it would be writing inside somebody else's transaction.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

log = logging.getLogger("pie_portal.queue")

#: What a handler looks like: payload in, nothing out. A return value would be
#: a result nothing can read — the caller is a worker loop, and the message row
#: records only whether the work happened.
Handler = Callable[[Dict[str, Any]], None]

_handlers: Dict[str, Handler] = {}


class UnknownTopic(LookupError):
    """No handler is registered for this message's topic.

    Its own type because the worker treats it differently from a handler that
    raised: an unknown topic is a deployment mismatch (a message enqueued by
    newer code, claimed by older), not work that failed, and retrying it in ten
    seconds will not help.
    """


class HandlerError(RuntimeError):
    """A handler raised. Carries the original as ``__cause__``."""


def register(topic: str, fn: Handler, *, replace: bool = False) -> Handler:
    """Bind a topic to the callable that runs it. Returns the callable, so it
    can be used as a decorator factory or called outright."""
    if not replace and topic in _handlers and _handlers[topic] is not fn:
        raise ValueError(
            f"topic {topic!r} already has a handler ({_handlers[topic]!r}); "
            "two handlers for one topic means one of them silently never runs")
    _handlers[topic] = fn
    log.debug("queue: handler registered for %s", topic)
    return fn


def handler(topic: str, *, replace: bool = False) -> Callable[[Handler], Handler]:
    """Decorator form of :func:`register`."""
    def decorate(fn: Handler) -> Handler:
        return register(topic, fn, replace=replace)
    return decorate


def handler_for(topic: str) -> Handler:
    """The handler for this topic, or raise :class:`UnknownTopic`."""
    try:
        return _handlers[topic]
    except KeyError:
        raise UnknownTopic(
            f"no handler registered for topic {topic!r} — this process cannot "
            "run that message") from None


def topics() -> List[str]:
    """Every topic this process can run, for the health check to report."""
    return sorted(_handlers)


def clear() -> None:
    """Forget every registration. For tests only: a suite that registers a fake
    handler must not leak it into the next module."""
    _handlers.clear()
