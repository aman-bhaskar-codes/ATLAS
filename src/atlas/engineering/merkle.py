"""Merkle Prefix Trie (Radix Tree) for Delta File Synchronization.

In-memory prefix trie with leaf hashes sha256(file_bytes) and directory
hashes sha256(sorted_child_hashes). Prunes unchanged subtrees at the root
or directory hash level and returns only modified/added nodes (O(K_changed)).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from atlas.infra.logging import get_logger

_log = get_logger("atlas.engineering.merkle")

_DEFAULT_IGNORED = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".next",
        "dist",
        "build",
        ".atlas",
        ".idea",
        ".vscode",
    }
)


@dataclass
class MerkleNode:
    name: str
    path: str  # relative path from root
    is_dir: bool
    hash: str
    size: int = 0
    children: dict[str, MerkleNode] = field(default_factory=dict)


@dataclass(frozen=True)
class MerkleDeltaItem:
    path: str
    is_dir: bool
    hash: str
    size: int
    change_type: str  # "added" | "modified" | "unchanged"


class MerklePrefixTrie:
    """Computes Merkle hashes over a filesystem root and derives delta changes."""

    def __init__(self, ignored: frozenset[str] = _DEFAULT_IGNORED) -> None:
        self.ignored = ignored

    def build_tree(self, root: Path, rel_base: str = "") -> MerkleNode:
        """Recursively build a MerkleNode tree from root."""
        if not root.exists():
            return MerkleNode(
                name=root.name or "root",
                path=rel_base,
                is_dir=True,
                hash=hashlib.sha256(b"empty").hexdigest(),
            )

        if root.is_file():
            try:
                data = root.read_bytes()
                file_hash = hashlib.sha256(data).hexdigest()
                return MerkleNode(
                    name=root.name,
                    path=rel_base or root.name,
                    is_dir=False,
                    hash=file_hash,
                    size=len(data),
                )
            except OSError as exc:
                _log.warning("merkle.read_failed", path=str(root), error=str(exc))
                return MerkleNode(
                    name=root.name,
                    path=rel_base or root.name,
                    is_dir=False,
                    hash=hashlib.sha256(b"error").hexdigest(),
                    size=0,
                )

        # Directory
        children: dict[str, MerkleNode] = {}
        child_digests: list[str] = []

        try:
            entries = sorted(root.iterdir(), key=lambda p: p.name)
        except OSError:
            entries = []

        for entry in entries:
            if entry.name in self.ignored:
                continue
            child_rel = f"{rel_base}/{entry.name}" if rel_base else entry.name
            child_node = self.build_tree(entry, child_rel)
            children[entry.name] = child_node
            child_digests.append(f"{entry.name}:{child_node.hash}")

        combined = "|".join(child_digests).encode("utf-8")
        dir_hash = hashlib.sha256(combined).hexdigest()

        return MerkleNode(
            name=root.name or "root",
            path=rel_base,
            is_dir=True,
            hash=dir_hash,
            children=children,
        )

    def compute_delta(
        self,
        current: MerkleNode,
        known_hashes: dict[str, str],
    ) -> tuple[bool, list[MerkleDeltaItem]]:
        """Compare current tree against a dictionary of {path: hash}.

        Returns (unchanged, list_of_delta_items). If root hash matches known root,
        returns (True, []) in O(1) time without inspecting child nodes.
        """
        known_root_hash = known_hashes.get("") or known_hashes.get(current.path)
        if known_root_hash and known_root_hash == current.hash:
            return True, []

        deltas: list[MerkleDeltaItem] = []
        self._diff_nodes(current, known_hashes, deltas)
        return False, deltas

    def _diff_nodes(
        self,
        node: MerkleNode,
        known_hashes: dict[str, str],
        out_deltas: list[MerkleDeltaItem],
    ) -> None:
        known_hash = known_hashes.get(node.path)

        # If this subtree has not changed, prune traversal immediately
        if known_hash is not None and known_hash == node.hash:
            return

        change_type = "added" if known_hash is None else "modified"
        out_deltas.append(
            MerkleDeltaItem(
                path=node.path,
                is_dir=node.is_dir,
                hash=node.hash,
                size=node.size,
                change_type=change_type,
            )
        )

        if node.is_dir:
            for child in node.children.values():
                self._diff_nodes(child, known_hashes, out_deltas)
