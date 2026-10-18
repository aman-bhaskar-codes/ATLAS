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

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Sequence
from urllib.parse import urlsplit

import idna

from atlas.capabilities.browser.errors import UnsafeURLError

# Only these schemes ever reach the public web; everything else (file, data,
# javascript, about, ftp, blob, ws, chrome-extension, …) is an exfiltration or
# local-resource vector and is denied outright.
_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Hostname suffixes that resolve to internal / loopback resources by convention.
_DENIED_HOST_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home.arpa")

# Bare hostnames that are always internal.
_DENIED_HOSTS = frozenset({"localhost", "ip6-localhost", "ip6-loopback"})


# UTS-46/IDNA-2008 matches modern browsers; stdlib IDNA-2003 would resolve
# faß.test as fass.test while Chromium actually connects to xn--fa-hia.test.
def _normalize_host(host: str) -> str:
    if not host:
        return ""
    if ":" in host:
        return host.lower().rstrip(".")  # IPv6 literals do not use IDNA.
    return idna.encode(host, uts46=True).decode("ascii").lower().rstrip(".")


Resolver = Callable[[str], Awaitable[Sequence[str]]]


async def _resolve_host(host: str) -> Sequence[str]:
    records = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(record[4][0]) for record in records]


class EgressPolicy:
    """Fail-closed allow/deny gate for a single outbound navigation.

    Construct with `allowed_hosts=None` (the default) for the global SSRF guard:
    any public http(s) host is allowed, every internal / non-http target denied.
    Pass `allowed_hosts={"example.com", ...}` to additionally confine navigation
    to those registrable domains and their subdomains.
    """

    def __init__(
        self,
        allowed_hosts: frozenset[str] | None = None,
        *,
        resolver: Resolver | None = None,
        resolution_timeout_s: float = 10.0,
    ) -> None:
        self._resolver = resolver or _resolve_host
        self._resolution_timeout_s = resolution_timeout_s
        self._allowed_hosts = (
            frozenset(_normalize_host(h.strip().strip(".")) for h in allowed_hosts if h.strip())
            if allowed_hosts is not None
            else None
        )

    def check(self, url: str) -> None:
        """Raise :class:`UnsafeURLError` if `url` is not a permitted egress target."""
        if "\\" in url or any(ord(char) <= 32 or ord(char) == 127 for char in url):
            raise UnsafeURLError("egress denied: ambiguous URL characters")
        try:
            parts = urlsplit(url)
            # Validate port and authority eagerly; browsers parse them differently
            # from urllib when controls, credentials or encoded hosts are present.
            _ = parts.port
            if parts.username is not None or parts.password is not None:
                raise ValueError("credentials are not permitted")
            host = _normalize_host(parts.hostname or "")
        except (ValueError, UnicodeError) as exc:
            raise UnsafeURLError("egress denied: invalid URL authority") from exc
        scheme = parts.scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise UnsafeURLError(f"egress denied: scheme {scheme or '(none)'!r} is not http/https")

        if not host:
            raise UnsafeURLError("egress denied: URL has no host")

        if "%" in host or (":" not in host and not re.fullmatch(r"[a-z0-9.-]+", host)):
            raise UnsafeURLError("egress denied: invalid host")
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
            # WHATWG URLs accept abbreviated, integer, octal and hex IPv4.
            # Reject noncanonical numeric hosts instead of letting different URL
            # parsers reinterpret them as loopback or internal IPs.
            return bool(re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", host.rsplit(".", 1)[-1]))
        return not ip.is_global or ip.is_multicast

    async def resolve_addresses(self, url: str) -> tuple[str, ...]:
        """Return only validated numeric addresses for an actual socket connection.

        Every DNS answer must be public; one private/malformed answer denies the
        entire destination. A caller must connect these addresses directly, never
        resolve the hostname again after this validation.
        """
        self.check(url)
        host = _normalize_host(urlsplit(url).hostname or "")
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return (str(literal),)
        try:
            async with asyncio.timeout(self._resolution_timeout_s):
                answers = await self._resolver(host)
            if not answers or len(answers) > 64:
                raise ValueError("no DNS answers or excessive answer count")
            validated: list[str] = []
            for address in answers:
                ip = ipaddress.ip_address(address)
                if not ip.is_global or ip.is_multicast:
                    raise ValueError("non-public DNS answer")
                if str(ip) not in validated:
                    validated.append(str(ip))
            return tuple(validated)
        except Exception as exc:
            raise UnsafeURLError(f"egress denied: {host} resolution is not provably public") from exc

    async def check_resolved(self, url: str) -> None:
        """DNS preflight only; use resolve_addresses and pin sockets for enforcement."""
        await self.resolve_addresses(url)

    def _host_in_scope(self, host: str) -> bool:
        assert self._allowed_hosts is not None
        return any(host == allowed or host.endswith(f".{allowed}") for allowed in self._allowed_hosts)
