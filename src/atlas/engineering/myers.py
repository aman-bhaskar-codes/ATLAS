"""Eugene Myers' Shortest Edit Script (SES) Diff Algorithm (O((N+M)D)).

Computes the optimal minimal edit script between two sequences with O((N+M)D)
time complexity, where D is the size of the edit script. Generates unified diffs
and structured IDE edit operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence


class DiffOpKind(str, Enum):
    KEEP = "keep"
    INSERT = "insert"
    DELETE = "delete"


@dataclass(frozen=True)
class DiffOp:
    kind: DiffOpKind
    old_index: int | None
    new_index: int | None
    line: str


def myers_diff(a: Sequence[str], b: Sequence[str]) -> list[DiffOp]:
    """Compute minimal edit script between sequence a and sequence b using Myers' algorithm."""
    n = len(a)
    m = len(b)
    max_d = n + m

    if max_d == 0:
        return []

    # v[k] stores the furthest-reaching x along diagonal k = x - y
    # Diagonal k ranges from -max_d to max_d.
    v: dict[int, int] = {1: 0}
    trace: list[dict[int, int]] = []

    for d in range(max_d + 1):
        v_copy = v.copy()
        trace.append(v_copy)
        for k in range(-d, d + 1, 2):
            # Down or right?
            if k == -d or (k != d and v.get(k - 1, -1) < v.get(k + 1, -1)):
                x = v.get(k + 1, 0)
            else:
                x = v.get(k - 1, 0) + 1
            y = x - k

            # Snake: follow identical elements
            while x < n and y < m and a[x] == b[y]:
                x += 1
                y += 1

            v[k] = x

            if x >= n and y >= m:
                # Target reached! Backtrack the trace to reconstruct the path
                return _backtrack(trace, a, b, d, n, m)

    return _backtrack(trace, a, b, max_d, n, m)


def _backtrack(
    trace: list[dict[int, int]],
    a: Sequence[str],
    b: Sequence[str],
    d: int,
    n: int,
    m: int,
) -> list[DiffOp]:
    """Backtrack through Myers' trace to produce a list of DiffOp items."""
    x = n
    y = m
    ops: list[DiffOp] = []

    for depth in range(d, 0, -1):
        v_prev = trace[depth]
        k = x - y

        if k == -depth or (k != depth and v_prev.get(k - 1, -1) < v_prev.get(k + 1, -1)):
            prev_k = k + 1
        else:
            prev_k = k - 1

        prev_x = v_prev[prev_k]
        prev_y = prev_x - prev_k

        # Diagonal snake steps (identical lines)
        while x > prev_x and y > prev_y:
            x -= 1
            y -= 1
            ops.append(DiffOp(kind=DiffOpKind.KEEP, old_index=x, new_index=y, line=a[x]))

        if x == prev_x:
            # Vertical step: Insertion from b
            y -= 1
            ops.append(DiffOp(kind=DiffOpKind.INSERT, old_index=None, new_index=y, line=b[y]))
        else:
            # Horizontal step: Deletion from a
            x -= 1
            ops.append(DiffOp(kind=DiffOpKind.DELETE, old_index=x, new_index=None, line=a[x]))

    # Remaining snake steps at depth 0
    while x > 0 and y > 0:
        x -= 1
        y -= 1
        ops.append(DiffOp(kind=DiffOpKind.KEEP, old_index=x, new_index=y, line=a[x]))

    ops.reverse()
    return ops


def format_unified_diff(
    a_lines: Sequence[str],
    b_lines: Sequence[str],
    *,
    from_file: str = "a",
    to_file: str = "b",
) -> str:
    """Format Myers diff ops into standard unified diff format."""
    ops = myers_diff(a_lines, b_lines)
    header = [f"--- {from_file}", f"+++ {to_file}"]
    body: list[str] = []

    for op in ops:
        if op.kind == DiffOpKind.KEEP:
            body.append(f" {op.line}")
        elif op.kind == DiffOpKind.INSERT:
            body.append(f"+{op.line}")
        elif op.kind == DiffOpKind.DELETE:
            body.append(f"-{op.line}")

    return "\n".join(header + body)


def compute_edit_operations(
    old_text: str,
    new_text: str,
) -> list[dict[str, Any]]:
    """Compute structured edit operations (insert/delete/replace) from old_text to new_text.

    Returns dicts with:
    - op: "insert" | "delete" | "replace"
    - start_line: 1-indexed
    - end_line: 1-indexed
    - text: str
    """
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    ops = myers_diff(old_lines, new_lines)

    edit_ops: list[dict[str, Any]] = []
    i = 0
    n = len(ops)

    while i < n:
        op = ops[i]
        if op.kind == DiffOpKind.KEEP:
            i += 1
            continue

        del_start = op.old_index if op.old_index is not None else 0
        del_lines: list[str] = []
        ins_lines: list[str] = []

        while i < n and ops[i].kind != DiffOpKind.KEEP:
            if ops[i].kind == DiffOpKind.DELETE:
                del_lines.append(ops[i].line)
            elif ops[i].kind == DiffOpKind.INSERT:
                ins_lines.append(ops[i].line)
            i += 1

        if del_lines and ins_lines:
            edit_ops.append(
                {
                    "op": "replace",
                    "start_line": del_start + 1,
                    "end_line": del_start + len(del_lines),
                    "text": "\n".join(ins_lines),
                }
            )
        elif del_lines:
            edit_ops.append(
                {
                    "op": "delete",
                    "start_line": del_start + 1,
                    "end_line": del_start + len(del_lines),
                    "text": "",
                }
            )
        elif ins_lines:
            edit_ops.append(
                {
                    "op": "insert",
                    "start_line": (del_start + 1) if del_start else 1,
                    "end_line": (del_start + 1) if del_start else 1,
                    "text": "\n".join(ins_lines),
                }
            )

    return edit_ops
