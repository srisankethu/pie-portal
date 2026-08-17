"""The connector-neutral failure taxonomy every source speaks.

The sync layer decides *what happens next* from the kind of failure — a
throttle stops the pull so it can resume, a missing permission degrades one
stage and names the grant to widen, anything else fails visibly — and those
decisions are the same whichever system was being read. So the kinds live
here, with no connector in them, and each connector's client raises these (or
its own subclasses of them, as ``zoho_client`` does, so callers that already
catch the Zoho names keep working).

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
