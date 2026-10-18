"""Checks for operator-configured outbound URLs before Cryo posts to them."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

SLACK_WEBHOOK_HOSTS = frozenset({"hooks.slack.com", "hooks.slack-gov.com"})
_BLOCKED_HOSTS = frozenset({"localhost", "metadata.google.internal", "metadata.internal"})

Resolver = Callable[[str], tuple[str, ...]]


class OutboundURLError(ValueError):
    pass


def validated_https_url(
    url: str,
    *,
    allowed_hosts: frozenset[str] | None = None,
    resolve: Resolver | None = None,
) -> str:
    parsed = urlsplit(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise OutboundURLError("outbound URL must be https and must not embed credentials")
    if host in _BLOCKED_HOSTS:
        raise OutboundURLError("outbound URL host is not allowed")
    if allowed_hosts is not None and host not in allowed_hosts:
        raise OutboundURLError("outbound URL host is not allowed")
    _reject_target(host, resolve or _resolve)
    port = "" if parsed.port in (None, 443) else f":{parsed.port}"
    return urlunsplit(("https", f"{host}{port}", parsed.path, parsed.query, ""))


def _reject_target(host: str, resolve: Resolver) -> None:
    addresses: tuple[str, ...]
    try:
        addresses = (str(ipaddress.ip_address(host)),)
    except ValueError:
        addresses = resolve(host)
    if not addresses:
        raise OutboundURLError("outbound URL host did not resolve")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise OutboundURLError("outbound URL must not target a private or local address")


def _resolve(host: str) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise OutboundURLError("outbound URL host did not resolve") from exc
    return tuple(str(item[4][0]) for item in infos)
