"""MCP security policies (Part 5 §11-§13/§20-§21/§94).

Three concrete gates applied BEFORE any transport work:

* ``StdioCommandPolicy``  — structured argv (no shell, §11/§12), arg count/size
  caps, executable allowlist, cwd validation.
* ``EnvironmentPolicy``   — allowlisted env for stdio children (§13): explicit
  configured values (credential-resolved) + a minimal safe set; ATLAS's full
  process environment is NEVER passed through.
* ``EndpointPolicy``      — SSRF guard for remote MCP URLs (§20-§21): scheme
  allowlist plus private/loopback/link-local/metadata blocking by policy mode.
  Best-effort DNS checks; hostname checks alone are NOT claimed as perfect
  SSRF prevention (§21).
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse

from atlas.tooling.mcp.errors import MCPSecurityError

#: Minimal safe inherited env for stdio children (§13).
_SAFE_ENV_KEYS: frozenset[str] = frozenset({"PATH", "HOME", "LANG", "TMPDIR", "SHELL"})

_MAX_ARGS = 32
_MAX_ARG_LEN = 4096
#: blocked in `command`: these have no legitimate use in an executable path.
#: NOTE: `&` is deliberately allowed — argv is passed WITHOUT a shell (§11), so
#: metacharacters never become execution semantics; this blocklist is defense
#: in depth for the executable token itself. (The repo path "Agentic & AI ..."
#: is the proof `&` must stay legal.)
_COMMAND_BLOCKLIST = tuple(";|`\n\x00")
_ARG_BLOCKLIST = tuple("\n\x00")


class StdioCommandPolicy:
    """§11/§12: validate structured subprocess parameters. ATLAS never builds a
    shell string and never uses shell=True — the SDK's stdio transport receives
    argv directly, and this policy validates it before that."""

    def __init__(self, *, allowed_executables: tuple[str, ...] | None = None) -> None:
        #: explicit executable allowlist (e.g. ("npx", "uvx", "python3")) — None
        #: means "any executable that passes the metacharacter/absolute checks".
        self._allowed = allowed_executables

    def validate(self, *, command: str, args: tuple[str, ...], cwd: str | None) -> None:
        if not command or any(ch in command for ch in _COMMAND_BLOCKLIST):
            raise MCPSecurityError(f"stdio command {command!r} is empty or contains shell metacharacters")
        if self._allowed is not None:
            import os.path

            base = os.path.basename(command)
            if base not in self._allowed and command not in self._allowed:
                raise MCPSecurityError(f"stdio command {command!r} is not in the allowed-executable policy")
        if len(args) > _MAX_ARGS:
            raise MCPSecurityError(f"stdio args exceed {_MAX_ARGS} entries")
        for arg in args:
            if any(ch in arg for ch in _ARG_BLOCKLIST):
                raise MCPSecurityError("stdio argument contains a newline or NUL byte")
            if len(arg) > _MAX_ARG_LEN:
                raise MCPSecurityError("stdio argument exceeds maximum length")
        if cwd and "\x00" in cwd:
            raise MCPSecurityError("stdio cwd contains a NUL byte")


class EnvironmentPolicy:
    """§13: build the child env from an explicit allowlist — never the whole
    ATLAS environment."""

    def build(self, configured: dict[str, str]) -> dict[str, str]:
        import os

        env = {key: os.environ[key] for key in _SAFE_ENV_KEYS if key in os.environ}
        for key, value in configured.items():
            if "\x00" in key or "\x00" in value:
                raise MCPSecurityError(f"env entry {key!r} contains a NUL byte")
            env[key] = value
        return env


class EndpointPolicy:
    """§20-§21: SSRF guard for remote MCP endpoints.

    Modes: remote_public (default, conservative — https only, no private
    addresses), local_loopback (loopback allowed for local servers), private
    _network (RFC1918 allowed), unrestricted.
    """

    def __init__(self, mode: str = "remote_public") -> None:
        self._mode = mode

    def validate_url(self, url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("https", "http"):
            raise MCPSecurityError(f"unsupported MCP endpoint scheme {parsed.scheme!r}")
        host = parsed.hostname or ""
        loopback_ok = self._mode in ("local_loopback", "private_network", "unrestricted")
        if not loopback_ok and (host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local")):
            raise MCPSecurityError(f"loopback endpoint {host!r} requires endpoint_policy local_loopback or wider")
        if parsed.scheme == "http" and self._mode == "remote_public" and host not in ("127.0.0.1", "localhost"):
            # cleartext http to a remote host is not acceptable in the default mode
            raise MCPSecurityError("cleartext http remote endpoints require a wider endpoint_policy")

    def validate_resolved(self, url: str) -> None:
        """§21: best-effort resolution check (DNS-rebinding reduction). Not a
        perfect SSRF defense — documented as such."""
        if self._mode == "unrestricted":
            return
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError as exc:
            raise MCPSecurityError(f"cannot resolve MCP endpoint host {host!r}: {exc}") from exc
        for info in infos:
            addr = ipaddress.ip_address(info[4][0])
            if addr.is_loopback and self._mode == "remote_public":
                raise MCPSecurityError(f"remote endpoint resolves to loopback {addr}")
            if (
                addr.is_private
                and not addr.is_loopback
                and self._mode not in ("private_network", "unrestricted")
                and not (addr.is_link_local and self._mode == "local_loopback")
            ):
                raise MCPSecurityError(f"remote endpoint resolves to private address {addr}")
            if addr.is_link_local and self._mode == "remote_public":
                raise MCPSecurityError(f"remote endpoint resolves to link-local address {addr}")
            if str(addr) in ("169.254.169.254",):  # cloud metadata service — blocked in every mode except unrestricted
                raise MCPSecurityError("metadata service endpoints are blocked")


__all__ = ["EndpointPolicy", "EnvironmentPolicy", "StdioCommandPolicy"]
