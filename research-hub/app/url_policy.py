"""SSRF destination policy for crawler URLs (HUB-006).

Every URL handed to the crawler is vetted here twice: before the fetch is
requested (rejecting internal destinations directly, by encoding, or via DNS)
and after the fetch, against the landing URL Crawl4AI reports (rejecting
redirect-based escapes). The fetch itself runs inside the Crawl4AI container,
so a DNS answer can still change between our check and its fetch; that
residual is bounded by the landing-URL recheck and by network-layer isolation
in docker-compose.yml.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {80, 443}


class DestinationNotAllowed(Exception):
    """A crawl destination violates the SSRF policy."""

    def __init__(self, destination: str, reason: str):
        self.destination = destination
        self.reason = reason
        super().__init__(f"{reason}: {destination}")


def _effective_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    # Python 3.12's is_global does not unwrap IPv4-mapped IPv6 addresses, so
    # ::ffff:10.0.0.1 must be judged as 10.0.0.1.
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped if mapped is not None else address


def address_allowed(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Only globally routable unicast addresses may be crawled.

    is_global excludes loopback, RFC1918/ULA private ranges, link-local (which
    covers 169.254.169.254 metadata), shared CGNAT space, reserved, and
    unspecified addresses; multicast is excluded explicitly for clarity.
    """
    effective = _effective_address(address)
    return effective.is_global and not effective.is_multicast


def resolve_addresses(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve a hostname to every A/AAAA answer. Blocking; raises gaierror."""
    infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def vet_destination(url: str) -> str:
    """Validate scheme, port, and every resolved address of a crawl URL.

    Returns the normalized destination ("host" or "host -> address") for
    logging. Raises DestinationNotAllowed on any violation; DNS failures
    reject (fail closed). Blocking on DNS — use vet_destination_async from
    async code.
    """
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise DestinationNotAllowed(url, "scheme_not_allowed")
    hostname = (parsed.hostname or "").strip("[]").lower()
    if not hostname:
        raise DestinationNotAllowed(url, "missing_host")
    try:
        port = parsed.port
    except ValueError:
        raise DestinationNotAllowed(url, "invalid_port") from None
    if port is not None and port not in ALLOWED_PORTS:
        raise DestinationNotAllowed(f"{hostname}:{port}", "port_not_allowed")
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            addresses = resolve_addresses(hostname)
        except socket.gaierror:
            raise DestinationNotAllowed(hostname, "dns_resolution_failed") from None
    if not addresses:
        raise DestinationNotAllowed(hostname, "dns_resolution_failed")
    # Reject when any answer is internal: a resolver returning mixed answers
    # is treated as hostile rather than retried for a clean one.
    for address in addresses:
        if not address_allowed(address):
            raise DestinationNotAllowed(
                f"{hostname} -> {address}", "destination_not_public"
            )
    return hostname


async def vet_destination_async(url: str) -> str:
    return await asyncio.to_thread(vet_destination, url)
