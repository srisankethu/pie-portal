"""Refuse a source URL that points back inside our own network.

Every ERP connection an owner adds — a Zoho ``api_base``/``accounts_base`` on
the manual path, a connector's ``base_url`` — becomes a target the server
fetches from the moment the connection is checked or synced. That entered value
is the one piece of owner input that turns into a server-side request, so
without a guard the connect form is a server-side request forgery primitive: a
tenant owner points ``api_base`` at ``http://169.254.169.254/…`` (the cloud
metadata endpoint) or ``http://127.0.0.1:…`` (a service bound to loopback),
presses *Check*, and the fetched body comes back reflected in the check's
``detail``/error message.

This module is the one seam that refuses those targets. It is deliberately
network-light and fail-safe in the direction that matters:

- An IP literal in a private, loopback, link-local, reserved, multicast or
  unspecified range is refused outright — this is the direct-metadata and
  ``localhost`` attack, and it needs no DNS to catch.
- A hostname is resolved, and refused if *any* address it resolves to is one of
  those — this catches ``a-name-i-control.example`` deliberately pointed at
  ``169.254.169.254``.
- A hostname that cannot be resolved *here and now* is allowed through: a real
  ERP host can be temporarily unresolvable, and a connection that can never be
  stored because our DNS blinked is a worse failure than a check that fails
  later. The syntactic IP checks above still stand for that host.

The check runs in two places. At the connect seam (``require_safe_source_url``
in ``ingestion/connections``) it refuses a URL when it is saved. At egress
(``FetchGuard``, wired into ``zoho_client`` and the ERP ``RestTransport``) it
re-runs the resolution just before each request, so a host that was public when
it was stored and answers an internal address now — a repointed record, or slow
DNS rebinding — is refused before the socket opens. The fetch check is memoised
per host so a thousand-page pull adds one lookup, not a thousand.

Residual, stated so the next audit finds a decision rather than a gap: the two
checks resolve the host twice, so a resolver that flips *between* the guard's
lookup and the socket's connect (TTL-0 rebinding, sub-millisecond) is not fully
closed. Closing that last window means pinning the exact validated address
through the connection (a custom httpcore network backend), which is a change to
the proven Zoho transport this deliberately keeps out of. What is here takes the
bar from "any URL" to "must resolve public at save and at fetch", which stops
the direct metadata/loopback attack and the repoint-after-connect attack that
are the realistic SSRF here.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable, List, Optional
from urllib.parse import urlparse


class UnsafeSourceUrl(ValueError):
    """A source URL that the platform must not be made to fetch."""


_ALLOWED_SCHEMES = frozenset({"http", "https"})
#: Hostnames that name the local host without being IP literals.
_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})

#: A hostname resolver, seam-injected so a test can simulate DNS rebinding — a
#: name that answers public at storage time and internal at fetch time.
Resolver = Callable[[str, Optional[int]], List[str]]


def _default_resolver(host: str, port: Optional[int]) -> List[str]:
    return [info[4][0] for info in socket.getaddrinfo(
        host, port or None, proto=socket.IPPROTO_TCP)]


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    """A target inside our own trust boundary rather than out on the internet."""
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:                       # ::ffff:127.0.0.1 and friends
        ip = mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _looks_like_url(value: str) -> bool:
    v = (value or "").strip().lower()
    return v.startswith(("http://", "https://"))


def require_safe_source_url(url: str, *, field: str = "URL",
                            resolver: Optional[Resolver] = None) -> None:
    """Refuse ``url`` if it is not a fetchable public http(s) endpoint.

    Raises :class:`UnsafeSourceUrl` with a message naming ``field`` so the
    connect form can show which value was rejected. Returns ``None`` on success.

    ``resolver`` is the DNS seam: re-running this against the *live* resolution
    just before a request is what a caller does to close DNS rebinding, and a
    test injects one to simulate a name that flips from public to internal.
    """
    resolve = resolver or _default_resolver
    raw = (url or "").strip()
    if not raw:
        return
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeSourceUrl(
            f"{field} must be an http(s) URL, not {parsed.scheme or 'a bare'} "
            f"scheme.")
    host = (parsed.hostname or "").strip()
    if not host:
        raise UnsafeSourceUrl(f"{field} has no host.")
    if host.lower() in _LOCAL_NAMES:
        raise UnsafeSourceUrl(
            f"{field} may not point at the server itself ({host}).")

    # An IP literal is decided without touching the network.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_blocked_ip(literal):
            raise UnsafeSourceUrl(
                f"{field} may not point at an internal address ({host}).")
        return

    # A name: refuse it only if it actually resolves to something internal.
    # A name that will not resolve here is left to fail later, on its own terms.
    try:
        addresses = resolve(host, parsed.port)
    except socket.gaierror:
        return
    for raw_addr in addresses:
        try:
            addr = ipaddress.ip_address(raw_addr)
        except ValueError:
            continue
        if _is_blocked_ip(addr):
            raise UnsafeSourceUrl(
                f"{field} resolves to an internal address ({raw_addr}) and "
                f"cannot be used as a data source.")


class FetchGuard:
    """Re-validates each distinct host a transport is about to fetch from.

    Storage-time validation (``require_safe_source_url`` at the connect seam)
    proves the target was public *when it was saved*. This closes the gap the
    module docstring names: a host that was public then and answers an internal
    address now — a repointed record, or DNS rebinding — is caught here, at the
    moment before the request goes out.

    Memoized per host so a thousand-call Zoho pull does not add a thousand DNS
    lookups: each distinct base host is resolved and checked once per transport
    instance. That trades away catching a *mid-pull* flip for not putting a
    resolver in front of every page, which is the right trade for a base URL
    that does not change within a run.
    """

    def __init__(self, resolver: Optional[Resolver] = None) -> None:
        self._resolver = resolver
        self._checked: set[str] = set()

    def check(self, url: str, *, field: str = "Source URL") -> None:
        host = (urlparse((url or "").strip()).hostname or "").lower()
        if host in self._checked:
            return
        require_safe_source_url(url, field=field, resolver=self._resolver)
        self._checked.add(host)


def require_safe_source_urls(values: dict, *, label: str = "This connector") -> None:
    """Refuse any URL-looking value in an entered ``values`` document.

    Applied to a connector's raw form values so ``base_url`` is checked wherever
    the spec happens to keep it, without this module knowing the field names.
    """
    for name, value in (values or {}).items():
        if isinstance(value, str) and _looks_like_url(value):
            require_safe_source_url(value, field=f"{label}: {name}")
