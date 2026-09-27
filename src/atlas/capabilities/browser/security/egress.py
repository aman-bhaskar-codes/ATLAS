"""Default-deny egress policy for outbound browser navigation.

Every `NavigationEngine.goto` must clear this policy BEFORE the page provider is
touched. Unlike the reputation checker — which fails OPEN (an unreachable Safe
Browsing / VirusTotal API yields UNKNOWN → proceed) — this policy fails CLOSED:
a URL is denied unless it is provably a public http(s) endpoint.

The policy closes the SSRF / local-exfiltration class that a reputation blocklist
cannot: a page (or an injected instruction inside untrusted page text, §23) that
redirects the browser to `file://…`, `http://localhost`, the cloud metadata IP
`169.254.169.254`, or any RFC-1918 / loopback / link-local host. None of these are
legitimate targets for public web research, so denying them never costs reach.

Optionally the policy is scoped to a set of allowed hosts (`allowed_hosts`): when
set, navigation is confined to those domains and their subdomains — the literal
"default-deny egress except target domain" contract for a bounded crawl.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from atlas.capabilities.browser.errors import UnsafeURLError

# Only these schemes ever reach the public web; everything else (file, data,
# javascript, about, ftp, blob, ws, chrome-extension, …) is an exfiltration or
# local-resource vector and is denied outright.
_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Hostname suffixes that resolve to internal / loopback resources by convention.
_DENIED_HOST_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home.arpa")

# Bare hostnames that are always internal.
_DENIED_HOSTS = frozenset({"localhost", "ip6-localhost", "ip6-loopback"})


class EgressPolicy:
    """Fail-closed allow/deny gate for a single outbound navigation.

    Construct with `allowed_hosts=None` (the default) for the global SSRF guard:
    any public http(s) host is allowed, every internal / non-http target denied.
    Pass `allowed_hosts={"example.com", ...}` to additionally confine navigation
    to those registrable domains and their subdomains.
    """

    def __init__(self, allowed_hosts: frozenset[str] | None = None) -> None:
        self._allowed_hosts = (
            frozenset(h.strip().lower().lstrip(".") for h in allowed_hosts if h.strip())
            if allowed_hosts is not None
            else None
        )

    def check(self, url: str) -> None:
        """Raise :class:`UnsafeURLError` if `url` is not a permitted egress target."""
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise UnsafeURLError(f"egress denied: scheme {scheme or '(none)'!r} is not http/https")

        host = (parts.hostname or "").lower()
        if not host:
            raise UnsafeURLError("egress denied: URL has no host")

        if self._is_internal_host(host):
            raise UnsafeURLError(f"egress denied: {host} resolves to an internal/loopback address")

        if self._allowed_hosts is not None and not self._host_in_scope(host):
            raise UnsafeURLError(f"egress denied: {host} is outside the permitted domain scope")

    @staticmethod
    def _is_internal_host(host: str) -> bool:
        if host in _DENIED_HOSTS:
            return True
        if any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in _DENIED_HOST_SUFFIXES):
            return True
        # IP literals: deny anything that is not a global unicast address. This
        # covers loopback (127/8, ::1), private (10/8, 172.16/12, 192.168/16,
        # fc00::/7), link-local incl. the metadata IP 169.254.169.254, reserved,
        # multicast and unspecified addresses.
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        return not ip.is_global

    def _host_in_scope(self, host: str) -> bool:
        assert self._allowed_hosts is not None
        return any(host == allowed or host.endswith(f".{allowed}") for allowed in self._allowed_hosts)
