"""Path canonicalization + allowlist validation + mount mapping.

WHY one module: filesystem_tool and shell_tool must agree EXACTLY on what is
in-bounds and how a host path maps into the container. Divergence there is a
security bug. resolve() follows symlinks so a link out of an allowed dir is
caught. Returns the container path so the sandbox mounts only what's needed.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResolvedPath:
    host: Path
    container: str            # path inside the sandbox, e.g. /work/<name>
    mount_source: Path        # host dir to bind-mount
    mount_target: str         # container dir for the mount


class PathError(ValueError):
    """Raised when a path is outside the manifest allowlist."""


def resolve_in_allowlist(raw: str, globs: list[str], *, workdir: str = "/work") -> ResolvedPath:
    p = Path(raw).expanduser().resolve(strict=False)
    for g in globs:
        gpat = str(Path(g).expanduser())
        if fnmatch.fnmatch(str(p), gpat):
            # Mount the parent dir read/write; expose the file at /work/<name>.
            mount_source = p.parent
            container = f"{workdir}/{p.name}"
            return ResolvedPath(host=p, container=container,
                                mount_source=mount_source, mount_target=workdir)
    raise PathError(f"{p} is outside allowed paths")
