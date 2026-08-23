"""The connector-neutral failure taxonomy every source speaks.

The sync layer decides *what happens next* from the kind of failure — a
throttle stops the pull so it can resume, a missing permission degrades one
stage and names the grant to widen, anything else fails visibly — and those
decisions are the same whichever system was being read. So the kinds live
here, with no connector in them, and each connector's client raises these (or
its own subclasses of them, as ``zoho_client`` does, so callers that already
catch the Zoho names keep working).

Two directions live here, and the distinction is why their bases differ.
The classes above the divider are raised *by* a source client and read by the
sync layer. The three below it are the outcomes an adapter reports *outward*,
to the application: they answer "what happened to my write" rather than "why
did this call fail". Those three stay on :class:`RuntimeError` rather than
:class:`IngestionError` deliberately — an ``except IngestionError`` guarding a
read path must not silently start catching a write outcome.

What does **not** belong here: message text that presumes a connector.
The raiser writes the sentence; this module only fixes the taxonomy.
"""
from __future__ import annotations

from typing import Optional


class IngestionError(RuntimeError):
    """A source call failed. Carries the API's own message where there is one."""


class SourceAuthError(IngestionError):
    """Credentials were rejected — revoked, mistyped, or for the wrong host."""


class SourceThrottleError(IngestionError):
    """The rate limiter won. Distinct because the remedy is different:
    wait and resume, rather than fix a credential."""


class SourceWriteUncertain(IngestionError):
    """A write was sent and its outcome is unknown.

    Raised instead of retrying when a non-idempotent call fails in a way that
    cannot tell "the source never saw it" from "the source did it and the
    answer was lost" — a 5xx, or a connection dropped mid-flight. Replaying
    either of those is how one quote becomes two orders in a customer's inbox,
    so the caller is told the truth and can settle the question by reading the
    record back instead.

    Distinct from a plain :class:`IngestionError`, which means the source
    answered and said no: that one is safe to report as a failure, this one is
    not.
    """


class SourceScopeError(SourceAuthError):
    """The credentials are fine; this *endpoint* was not granted.

    A subclass of the auth error so nothing that already handles an auth
    failure stops working, but distinguishable because the remedy is
    completely different: re-authorise **asking for one more permission**,
    not replace a secret that is correct.

    ``scope`` is the permission to add, in the source system's own
    vocabulary; ``path`` is the endpoint that was refused. Either may be
    unknown for a system that does not name its grants.
    """

    def __init__(self, message: str, *, path: str = "",
                 scope: Optional[str] = None) -> None:
        super().__init__(message)
        self.path = path
        self.scope = scope


# ── outward: what an adapter reports happened to a write ────────────────────
# Exactly three, and there is deliberately no fourth meaning "probably fine".
# A caller that cannot tell these apart cannot tell a customer anything true.
class SourceUnavailable(RuntimeError):
    """The source could not be read.

    Every read-side adapter failure collapses to this so a single unreachable
    record cannot fail a whole intake: the line reads OFFLINE, which is what
    that state is for.
    """


class SourceWriteRefused(RuntimeError):
    """The write was not attempted, or the source answered and said no.

    Either way nothing is stored, so fixing the named problem and sending
    again is safe — and saying so is the point of this class. Carries the
    record codes responsible where there are any, so a screen can point at the
    lines rather than at the whole document.
    """

    def __init__(self, message: str, codes: Optional[list] = None) -> None:
        super().__init__(message)
        self.codes = list(codes or [])


class SourceWriteUnknown(RuntimeError):
    """The write was sent and its outcome could not be established.

    The one state that must never be reported as either success or failure.
    Carries the reference the record would have been written under, because
    looking that up is the only thing that resolves this state — and because
    sending again under the same reference is then safe.

    Distinct from :class:`SourceWriteUncertain` above, which is the transport
    saying "I cannot tell". This is the adapter's verdict *after* trying to
    settle that by reading the record back and failing to.
    """

    def __init__(self, message: str, reference: Optional[str] = None) -> None:
        super().__init__(message)
        self.reference = reference
