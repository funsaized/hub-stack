"""SSRF destination policy tests (HUB-006).

DNS-dependent cases monkeypatch url_policy.resolve_addresses so the suite
never performs live lookups and behaves identically on any machine.
"""

import asyncio
import ipaddress
import socket

import pytest

from app import url_policy
from app.research import evaluate_crawl_result
from app.url_policy import DestinationNotAllowed, vet_destination


def _fixed_resolver(mapping):
    def resolve(hostname):
        try:
            return [ipaddress.ip_address(a) for a in mapping[hostname]]
        except KeyError:
            raise socket.gaierror(socket.EAI_NONAME, "not found") from None
    return resolve


def expect_rejection(url, reason):
    with pytest.raises(DestinationNotAllowed) as exc_info:
        vet_destination(url)
    assert exc_info.value.reason == reason, exc_info.value


# --- direct IP-literal destinations (no DNS involved) ---

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://127.8.9.1/admin",
    "http://10.0.0.8/",
    "http://172.16.0.1/",
    "http://192.168.1.1/router",
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    "http://100.64.0.7/",                        # CGNAT shared space
    "http://0.0.0.0/",
    "http://224.0.0.1/",                         # multicast
    "http://[::1]/",
    "http://[fe80::1]/",                         # IPv6 link-local
    "http://[fd00:ec2::254]/",                   # IPv6 metadata (ULA)
    "http://[fc00::1]/",
    "http://[ff02::1]/",                         # IPv6 multicast
    "http://[::ffff:127.0.0.1]/",                # IPv4-mapped loopback
    "http://[::ffff:10.0.0.1]/",                 # IPv4-mapped private
])
def test_internal_ip_literals_rejected(url):
    expect_rejection(url, "destination_not_public")


def test_public_ip_literal_accepted():
    assert vet_destination("http://93.184.216.34/") == "93.184.216.34"


# --- scheme, host, and port rules ---

@pytest.mark.parametrize("url,reason", [
    ("ftp://example.com/", "scheme_not_allowed"),
    ("file:///etc/passwd", "scheme_not_allowed"),
    ("gopher://example.com/", "scheme_not_allowed"),
    ("http:///path-only", "missing_host"),
    ("http://example.com:6379/", "port_not_allowed"),
    ("http://example.com:8080/", "port_not_allowed"),
])
def test_scheme_host_port_rules(url, reason):
    expect_rejection(url, reason)


# --- DNS-resolved destinations ---

def test_hostname_resolving_to_private_address_rejected(monkeypatch):
    monkeypatch.setattr(url_policy, "resolve_addresses",
                        _fixed_resolver({"internal.example": ["10.0.0.8"]}))
    expect_rejection("https://internal.example/secrets", "destination_not_public")


def test_hostname_with_one_internal_answer_rejected(monkeypatch):
    # Mixed answers are hostile (DNS rebinding): one bad answer rejects.
    monkeypatch.setattr(url_policy, "resolve_addresses", _fixed_resolver(
        {"mixed.example": ["93.184.216.34", "127.0.0.1"]}))
    expect_rejection("https://mixed.example/", "destination_not_public")


def test_hostname_resolving_to_ipv6_private_rejected(monkeypatch):
    monkeypatch.setattr(url_policy, "resolve_addresses",
                        _fixed_resolver({"v6.example": ["fd00::1"]}))
    expect_rejection("http://v6.example/", "destination_not_public")


def test_unresolvable_hostname_rejected(monkeypatch):
    monkeypatch.setattr(url_policy, "resolve_addresses", _fixed_resolver({}))
    expect_rejection("https://nxdomain.example/", "dns_resolution_failed")


def test_empty_resolution_rejected(monkeypatch):
    monkeypatch.setattr(url_policy, "resolve_addresses", lambda hostname: [])
    expect_rejection("https://empty.example/", "dns_resolution_failed")


def test_public_hostname_accepted(monkeypatch):
    monkeypatch.setattr(url_policy, "resolve_addresses",
                        _fixed_resolver({"example.com": ["93.184.216.34"]}))
    assert vet_destination("https://example.com/page") == "example.com"


def test_encoded_hostname_fails_closed(monkeypatch):
    # Percent-encoded host bytes never reach a resolver as an address; the
    # lookup fails and the URL is rejected rather than passed through.
    monkeypatch.setattr(url_policy, "resolve_addresses", _fixed_resolver({}))
    expect_rejection("http://%31%32%37.0.0.1/", "dns_resolution_failed")


# --- post-fetch (redirect landing) validation ---

def test_redirect_landing_on_internal_address_rejected(monkeypatch):
    monkeypatch.setattr(url_policy, "resolve_addresses",
                        _fixed_resolver({"public.example": ["93.184.216.34"]}))
    result = {"url": "https://public.example/start",
              "final_url": "http://169.254.169.254/latest/meta-data/",
              "markdown": "content"}
    with pytest.raises(DestinationNotAllowed) as exc_info:
        asyncio.run(evaluate_crawl_result(result, 1000))
    assert exc_info.value.reason == "destination_not_public"


def test_redirect_landing_on_internal_hostname_rejected(monkeypatch):
    monkeypatch.setattr(url_policy, "resolve_addresses", _fixed_resolver(
        {"public.example": ["93.184.216.34"], "hub-redis": ["172.20.0.3"]}))
    result = {"url": "https://public.example/start",
              "final_url": "http://hub-redis/",
              "markdown": "content"}
    with pytest.raises(DestinationNotAllowed):
        asyncio.run(evaluate_crawl_result(result, 1000))


def test_oversized_document_rejected(monkeypatch):
    monkeypatch.setattr(url_policy, "resolve_addresses",
                        _fixed_resolver({"public.example": ["93.184.216.34"]}))
    result = {"url": "https://public.example/big",
              "final_url": "https://public.example/big",
              "markdown": "x" * 1001}
    with pytest.raises(DestinationNotAllowed) as exc_info:
        asyncio.run(evaluate_crawl_result(result, 1000))
    assert exc_info.value.reason == "response_too_large"


def test_public_landing_within_size_accepted(monkeypatch):
    monkeypatch.setattr(url_policy, "resolve_addresses",
                        _fixed_resolver({"public.example": ["93.184.216.34"]}))
    result = {"url": "https://public.example/start",
              "final_url": "https://public.example/final",
              "markdown": "x" * 500}
    assert asyncio.run(evaluate_crawl_result(result, 1000)) is None
