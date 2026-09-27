"""Tests for the default-deny browser egress policy (SSRF / local-exfil guard)."""

from __future__ import annotations

import pytest

from atlas.capabilities.browser.errors import UnsafeURLError
from atlas.capabilities.browser.security.egress import EgressPolicy


def test_public_https_url_is_allowed() -> None:
    EgressPolicy().check("https://example.com/path?q=1")
    EgressPolicy().check("http://sub.example.co.uk/article")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "data:text/html,<script>alert(1)</script>",
        "javascript:alert(1)",
        "about:blank",
        "ftp://ftp.example.com/x",
        "chrome-extension://abc/page.html",
    ],
)
def test_non_http_schemes_are_denied(url: str) -> None:
    with pytest.raises(UnsafeURLError, match="scheme"):
        EgressPolicy().check(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://localhost:8730/api/v1/agent",
        "http://127.0.0.1/",
        "https://127.0.0.1:9000/",
        "http://[::1]/",
        "http://10.0.0.5/internal",
        "http://172.16.4.9/",
        "http://192.168.1.1/router",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata SSRF
        "http://service.internal/health",
        "http://db.local/",
        "http://box.lan/",
    ],
)
def test_internal_and_loopback_hosts_are_denied(url: str) -> None:
    with pytest.raises(UnsafeURLError, match="internal|loopback"):
        EgressPolicy().check(url)


def test_url_without_host_is_denied() -> None:
    with pytest.raises(UnsafeURLError, match="no host"):
        EgressPolicy().check("https://")


def test_scope_confines_navigation_to_allowed_domains() -> None:
    policy = EgressPolicy(allowed_hosts=frozenset({"example.com"}))
    policy.check("https://example.com/a")
    policy.check("https://docs.example.com/b")  # subdomain in scope
    with pytest.raises(UnsafeURLError, match="scope"):
        policy.check("https://evil.com/c")


def test_scope_still_denies_internal_hosts() -> None:
    # An in-scope name that is nonetheless internal is denied by the SSRF guard,
    # which runs before the scope check.
    policy = EgressPolicy(allowed_hosts=frozenset({"localhost"}))
    with pytest.raises(UnsafeURLError, match="internal|loopback"):
        policy.check("http://localhost/x")
