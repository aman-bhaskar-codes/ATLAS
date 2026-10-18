"""Pure command parsing shared by safety classification and shell execution."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from pathlib import Path

SHELL_OPERATORS = frozenset({"|", "||", "&&", ";", "`", "$(", ">>", ">", "<"})
_EXECUTABLE_CODE = frozenset({"python", "python3", "node", "ruby", "perl", "sh", "bash", "zsh"})
_NETWORK_COMMANDS = frozenset({"curl", "wget"})
_FIND_WRITES = frozenset({"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"})


def parse_command(command: str) -> list[str]:
    argv = shlex.split(command)
    if not argv:
        raise ValueError("empty command")
    for operator in SHELL_OPERATORS:
        if operator in command:
            raise ValueError(f"shell operator {operator!r} is not permitted")
    return argv


def matches_command_prefix(argv: list[str], entries: list[str]) -> bool:
    for entry in entries:
        tokens = shlex.split(entry)
        if tokens and argv[: len(tokens)] == tokens:
            return True
    return False


def is_read_only_command(argv: list[str], entries: list[str]) -> bool:
    if not matches_command_prefix(argv, entries):
        return False
    if argv[0] in _EXECUTABLE_CODE | _NETWORK_COMMANDS:
        return False
    if argv[0] == "find" and any(arg in _FIND_WRITES for arg in argv[1:]):
        return False
    if argv[0] == "git":
        # Git read verbs may explicitly write output or invoke configured code.
        if any(arg.split("=", 1)[0] in {"--output", "--ext-diff", "--textconv"} for arg in argv[2:]):
            return False
    return True


def remap_mount_path(value: str, mounts: Mapping[str, str]) -> str:
    """Map the most specific container target, identically for guards and execution."""
    for host, target in sorted(mounts.items(), key=lambda item: len(item[1]), reverse=True):
        target = target.rstrip("/")
        if value == target or value.startswith(target + "/"):
            return host.rstrip("/") + value[len(target) :]
    return value


def command_paths(argv: list[str], *, cwd: str | None = None, mounts: Mapping[str, str] | None = None) -> list[str]:
    """Include flag values as well as positional paths for credential checks."""
    paths = []
    base = Path(remap_mount_path(cwd, mounts or {})).expanduser() if cwd else None
    for argument in argv[1:]:
        value = remap_mount_path(argument.split("=", 1)[-1] if argument.startswith("-") else argument, mounts or {})
        path = Path(value).expanduser()
        if base is not None and not path.is_absolute():
            path = base / path
        paths.append(str(path) if value else "")
    return paths
