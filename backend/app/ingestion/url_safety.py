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

Residual, and stated so the next audit finds a decision rather than a gap: this
validates at storage time, so a name that resolves public now and private at
fetch time (DNS rebinding) is not closed by this alone. Pinning the resolved
address through the HTTP request is the follow-up that closes it; this raises
the bar from "any URL" to "must resolve public", which stops the direct
metadata/loopback attacks that are the whole point of an SSRF here.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeSourceUrl(ValueError):
    """A source URL that the platform must not be made to fetch."""


_ALLOWED_SCHEMES = frozenset({"http", "https"})
#: Hostnames that name the local host without being IP literals.
_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


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


def require_safe_source_url(url: str, *, field: str = "URL") -> None:
    """Refuse ``url`` if it is not a fetchable public http(s) endpoint.

    Raises :class:`UnsafeSourceUrl` with a message naming ``field`` so the
    connect form can show which value was rejected. Returns ``None`` on success.
    """
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
        infos = socket.getaddrinfo(host, parsed.port or None,
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return
    for info in infos:
        sockaddr = info[4]
        try:
            addr = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if _is_blocked_ip(addr):
            raise UnsafeSourceUrl(
                f"{field} resolves to an internal address ({sockaddr[0]}) and "
                f"cannot be used as a data source.")


def require_safe_source_urls(values: dict, *, label: str = "This connector") -> None:
    """Refuse any URL-looking value in an entered ``values`` document.

    Applied to a connector's raw form values so ``base_url`` is checked wherever
    the spec happens to keep it, without this module knowing the field names.
    """
    for name, value in (values or {}).items():
        if isinstance(value, str) and _looks_like_url(value):
            require_safe_source_url(value, field=f"{label}: {name}")
