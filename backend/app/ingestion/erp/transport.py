"""Paced, retrying HTTP — the half of every ERP client that is not the ERP.

The discipline is the one ``zoho_client.ZohoTransport`` learned on the live
Books API, restated without Zoho in it: stay *under* the limit rather than
recover from it, back a 429 off in tens of seconds because a rate limiter is
not a transient fault, replay only what is provably safe to replay, and turn
every terminal failure into the neutral taxonomy in ``ingestion/errors`` so
the sync layer reacts to the kind of failure without knowing the system.

``ZohoTransport`` deliberately does not extend this class (yet): it predates
it, its behaviour is pinned by its own test file against the live API's
quirks, and rebasing it inside the change that introduces five new clients
would put the proven path and the new ones in one blast radius. The two
implementations of pacing are a known, named duplication; fold Zoho onto this
base as its own change.

Subclasses implement authentication as hooks: ``_auth_headers`` mints or
returns whatever each request must carry, ``_invalidate_auth`` drops cached
tokens so one 401 earns one refresh, and ``_scope_refusal`` says whether a
refusal is "this permission was not granted" rather than "bad credentials" —
the difference between telling an owner to widen a grant and telling them to
rotate a secret that is fine.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from ..errors import (IngestionError, SourceAuthError, SourceScopeError,
                      SourceThrottleError, SourceWriteUncertain)

log = logging.getLogger("pie_portal.erp")


class RestTransport:
    """One authenticated HTTP surface against one ERP instance."""

    #: Named in every error message, so a failure says which system refused.
    system = "the source system"

    #: Conservative defaults; each client overrides to its API's published
    #: limits. Pacing below the limit is worth more than recovering from it.
    requests_per_minute = 60
    timeout_seconds = 30.0
    max_retries = 5
    throttle_backoff_seconds = 10.0
    max_backoff_seconds = 90.0

    #: Methods safe to replay unconditionally. A POST that is really a read
    #: (a SuiteQL query, a login) is replayed only when the caller says so.
    _REPLAYABLE = frozenset({"GET"})

    def __init__(self, http: Any = None) -> None:
        self._http = http                     # injectable for tests
        self._last_call_at = 0.0
        #: Observable so a sync run can report what the pull actually cost.
        self.calls = 0
        # Re-check the target host is public just before we fetch it — the
        # fetch-time half of the SSRF guard, closing a base URL that was public
        # when it was stored and answers an internal address now. Only when we
        # own the socket: an injected transport (a test double) has none to
        # protect, and running a real DNS lookup for it would only make the
        # unit tests non-hermetic.
        from ..url_safety import FetchGuard
        self._fetch_guard = FetchGuard() if http is None else None

    # ── hooks ────────────────────────────────────────────────────────────────
    def _auth_headers(self) -> dict[str, str]:
        """Headers this request must carry. May mint a token via
        ``self._client()`` directly — hook calls do not recurse into
        ``request``. Raise :class:`SourceAuthError` when the grant is broken."""
        return {}

    def _invalidate_auth(self) -> None:
        """Drop any cached token so the retry after a 401 mints a fresh one."""

    def _scope_refusal(self, resp: Any) -> Optional[SourceScopeError]:
        """A refusal that means "permission not granted", or None.

        Errs towards None: mislabelling a revoked credential as a missing
        permission sends an owner to widen a grant that is already wide.
        """
        return None

    # ── transport ────────────────────────────────────────────────────────────
    def _client(self):
        if self._http is None:
            import httpx

            self._http = httpx.Client(timeout=self.timeout_seconds,
                                      follow_redirects=False)
        return self._http

    def _sleep(self, seconds: float) -> None:
        """Single seam for every wait, so tests can run the real retry logic."""
        if seconds > 0:
            time.sleep(seconds)

    def _pace(self) -> None:
        rpm = self.requests_per_minute
        if rpm <= 0 or self._last_call_at == 0.0:
            return
        self._sleep(self._last_call_at + (60.0 / rpm) - time.monotonic())

    @staticmethod
    def _retry_after(resp: Any) -> Optional[float]:
        """The API's own instruction, when it sends one, beats any guess."""
        try:
            raw = (getattr(resp, "headers", None) or {}).get("Retry-After")
            return max(0.0, float(raw)) if raw is not None else None
        except (TypeError, ValueError):
            return None

    def request(self, method: str, url: str, *, params: Any = None,
                json: Any = None, data: Any = None,
                headers: Optional[dict[str, str]] = None,
                replayable: Optional[bool] = None,
                expect_json: bool = True) -> Any:
        """One call, retried only as far as the method allows.

        ``replayable=True`` marks a POST that is really a read (a query, a
        sign-in) so it keeps the full retry budget; an unmarked non-GET is
        never replayed past a 5xx or a dropped connection, neither of which can
        be told apart from a success whose response was lost. Those two raise
        :class:`SourceWriteUncertain` rather than a plain failure, because the
        caller's next move is different: not "report it failed" but "go and
        read whether the record is there".
        """
        verb = method.upper()
        may_replay = (verb in self._REPLAYABLE) if replayable is None else replayable

        if self._fetch_guard is not None:
            from ..url_safety import UnsafeSourceUrl

            try:
                self._fetch_guard.check(url, field=f"{self.system} URL")
            except UnsafeSourceUrl as e:
                raise IngestionError(str(e)) from e

        last: Optional[str] = None
        throttled = False
        refreshed_auth = False
        attempts = max(1, self.max_retries)
        # Outside the loop and outside the try below: building the client sends
        # nothing, so a failure here must not be reported as a write whose fate
        # is unknown. Only the network call itself earns that.
        client = self._client()
        for attempt in range(attempts):
            sent = {**self._auth_headers(), **(headers or {})}
            self._pace()
            kwargs: dict[str, Any] = {"params": params, "headers": sent}
            if json is not None:
                kwargs["json"] = json
            if data is not None:
                kwargs["data"] = data
            try:
                resp = client.request(verb, url, **kwargs)
            except Exception as e:  # noqa: BLE001 — the fault is the signal
                # A fault this side of the answer is the same unknown as a 5xx:
                # the request may well have been received. Left raw it reaches
                # a write caller as a bare connection error, which reads as
                # "nothing happened".
                if may_replay:
                    raise
                raise SourceWriteUncertain(
                    f"{self.system} did not answer {verb} {url} "
                    f"({type(e).__name__}: {e}). Whether the record was written "
                    f"cannot be told from here, so it has not been sent again — "
                    f"check {self.system} before sending it again.") from e
            self._last_call_at = time.monotonic()
            self.calls += 1

            if resp.status_code in (401, 403):
                scope = self._scope_refusal(resp)
                if scope is not None:
                    raise scope
                # One refresh per request: a token can expire mid-run, but a
                # second refusal on a freshly minted grant is the grant itself.
                if not refreshed_auth:
                    refreshed_auth = True
                    self._invalidate_auth()
                    last = f"HTTP {resp.status_code}"
                    continue
                raise SourceAuthError(
                    f"{self.system} rejected the credentials at {url} "
                    f"(HTTP {resp.status_code}): {_body_hint(resp)}")
            if resp.status_code == 429:
                throttled = True
                last = "HTTP 429 (rate limited)"
                delay = self._retry_after(resp)
                if delay is None:
                    delay = min(self.max_backoff_seconds,
                                self.throttle_backoff_seconds * (2 ** attempt))
                log.warning("%s %s: rate limited, waiting %.0fs (attempt %d/%d)",
                            self.system, url, delay, attempt + 1, attempts)
                self._sleep(delay)
                continue
            if resp.status_code >= 500:
                if not may_replay:
                    raise SourceWriteUncertain(
                        f"{self.system} returned HTTP {resp.status_code} to "
                        f"{verb} {url}. Whether the record was written cannot "
                        f"be told from here, so it has not been retried — check "
                        f"{self.system} before sending it again.")
                last = f"HTTP {resp.status_code}"
                self._sleep(min(self.max_backoff_seconds, 2 ** attempt))
                continue
            if resp.status_code >= 400:
                raise IngestionError(
                    f"{self.system} refused {verb} {url} "
                    f"(HTTP {resp.status_code}): {_body_hint(resp)}")
            if not expect_json:
                return resp
            try:
                return resp.json()
            except ValueError:
                raise IngestionError(
                    f"{self.system} returned non-JSON for {url} "
                    f"(HTTP {resp.status_code}).")

        if throttled:
            raise SourceThrottleError(
                f"{self.system} rate limited the pull at {url} and did not "
                f"recover after {attempts} attempts. Everything fetched so far "
                "has been kept — run the sync again later and it will resume "
                "from where it stopped.")
        raise IngestionError(
            f"{self.system} call to {url} failed after retries ({last}).")

    def get(self, url: str, **kwargs: Any) -> Any:
        return self.request("GET", url, **kwargs)


def _body_hint(resp: Any) -> str:
    """The most useful sentence the response body offers, bounded.

    APIs put the actionable part in different pockets; this looks in the usual
    ones and falls back to raw text, clipped so a login page cannot flood a
    sync report.
    """
    try:
        body = resp.json()
        if isinstance(body, dict):
            for key in ("message", "Message", "error_description", "detail"):
                if body.get(key):
                    return str(body[key])[:300]
            error = body.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])[:300]
            if error:
                return str(error)[:300]
    except Exception:  # noqa: BLE001 — the body is only a hint
        pass
    return (getattr(resp, "text", "") or "")[:300].strip() or "no detail given"
