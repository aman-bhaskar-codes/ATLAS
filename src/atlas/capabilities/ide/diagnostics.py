"""Diagnostics normalization — a structured problems feed (Slice 8).

A linter / type-checker is just another governed command; what the workbench (and
the agentic loop) needs is its output normalized into `Diagnostic` records it can
render and reason over, instead of re-parsing raw text every cycle.
`DiagnosticsCollector` is that layer, and — like `TestRunner` — it owns NO
execution of its own: it delegates to `CommandRunner.run` (the single
`SafetyEngine.guard` funnel), adding only line parsing on top (Constitution).

HONESTY: the counts reflect the PARSED diagnostics, not the process exit — most
linters exit non-zero precisely BECAUSE they found problems, and that is not a
runner error. `error` is reserved for a real failure to run (no tool, tool crash,
denial). An unrecognized output shape yields zero diagnostics with the raw tail
kept for the human — never fabricated findings.
"""

from __future__ import annotations

import re

from atlas.capabilities.ide.commands import CommandRunner
from atlas.capabilities.ide.contracts import (
    CommandResult,
    Diagnostic,
    DiagnosticReport,
    DiagnosticSeverity,
)
from atlas.infra.ids import CorrelationId

_TAIL_LIMIT = 6000
_MSG_LIMIT = 1000
_MAX_DIAGNOSTICS = 500

# ── Per-tool line matchers (first match wins, tried in order) ──────────────── #
# (mypy)  "pkg/mod.py:12: error: Incompatible return value  [return-value]"
#         (column optional: "pkg/mod.py:12:5: error: ...")
_MYPY = re.compile(
    r"^(?P<file>[^:\n]+?):(?P<line>\d+):(?:(?P<col>\d+):)?\s+"
    r"(?P<sev>error|warning|note):\s+(?P<msg>.*?)(?:\s+\[(?P<code>[A-Za-z0-9_-]+)\])?$"
)
# ruff / flake8:  "src/app.py:3:1: F401 `os` imported but unused"
_RUFF = re.compile(r"^(?P<file>[^:\n]+?):(?P<line>\d+):(?P<col>\d+):\s+(?P<code>[A-Z]+\d+)\s+(?P<msg>.*)$")
# tsc:  "src/x.ts(12,5): error TS2322: Type 'string' is not assignable..."
_TSC = re.compile(
    r"^(?P<file>[^(\n]+?)\((?P<line>\d+),(?P<col>\d+)\):\s+"
    r"(?P<sev>error|warning)\s+(?P<code>TS\d+):\s+(?P<msg>.*)$"
)
# eslint (compact):  "/abs/x.js: line 2, col 1, Error - 'foo' is defined... (no-unused)"
_ESLINT = re.compile(
    r"^(?P<file>.+?):\s+line\s+(?P<line>\d+),\s+col\s+(?P<col>\d+),\s+"
    r"(?P<sev>Error|Warning)\s+-\s+(?P<msg>.*?)(?:\s+\((?P<code>[^)]+)\))?$"
)


def _sev(raw: str) -> DiagnosticSeverity:
    low = raw.lower()
    if low in {"error"}:
        return DiagnosticSeverity.ERROR
    if low in {"warning", "warn"}:
        return DiagnosticSeverity.WARNING
    if low in {"note"}:
        return DiagnosticSeverity.INFO
    return DiagnosticSeverity.HINT


def _int(raw: str | None) -> int | None:
    return int(raw) if raw and raw.isdigit() else None


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _MSG_LIMIT else text[:_MSG_LIMIT] + "…"


def _match_line(line: str) -> Diagnostic | None:
    """Try each tool matcher against one output line. Returns the first normalized
    `Diagnostic`, or `None` when the line is not a recognized diagnostic."""
    m = _MYPY.match(line)
    if m:
        return Diagnostic(
            file=m.group("file"),
            line=_int(m.group("line")),
            col=_int(m.group("col")),
            severity=_sev(m.group("sev")),
            message=_clip(m.group("msg")),
            code=m.group("code"),
            source="mypy",
        )
    m = _RUFF.match(line)
    if m:
        return Diagnostic(
            file=m.group("file"),
            line=_int(m.group("line")),
            col=_int(m.group("col")),
            severity=DiagnosticSeverity.ERROR,
            message=_clip(m.group("msg")),
            code=m.group("code"),
            source="ruff",
        )
    m = _TSC.match(line)
    if m:
        return Diagnostic(
            file=m.group("file"),
            line=_int(m.group("line")),
            col=_int(m.group("col")),
            severity=_sev(m.group("sev")),
            message=_clip(m.group("msg")),
            code=m.group("code"),
            source="tsc",
        )
    m = _ESLINT.match(line)
    if m:
        return Diagnostic(
            file=m.group("file"),
            line=_int(m.group("line")),
            col=_int(m.group("col")),
            severity=_sev(m.group("sev")),
            message=_clip(m.group("msg")),
            code=m.group("code"),
            source="eslint",
        )
    return None


def parse_diagnostics(text: str) -> list[Diagnostic]:
    """Normalize a linter/type-checker's output into `Diagnostic` records, one per
    recognized line. Unrecognized lines are ignored (no fabricated findings)."""
    out: list[Diagnostic] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        diag = _match_line(line)
        if diag is not None:
            out.append(diag)
            if len(out) >= _MAX_DIAGNOSTICS:
                break
    return out


def _tail(text: str) -> str:
    return text if len(text) <= _TAIL_LIMIT else text[-_TAIL_LIMIT:]


class DiagnosticsCollector:
    """Governed diagnostics: run a lint/type-check command through `CommandRunner`
    and normalize its output into a `DiagnosticReport`. Stateless — the workspace
    root arrives as `cwd`."""

    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    async def collect(
        self,
        command: str,
        *,
        cwd: str,
        correlation_id: CorrelationId,
        timeout_s: float = 180.0,
    ) -> DiagnosticReport:
        """Run `command` in `cwd` through the funnel and parse its diagnostics.
        `error` is set only for a real failure to run (denial / no tool / tool
        crash) — never for a linter that merely exited non-zero WITH findings."""
        result: CommandResult = await self._runner.run(
            command, cwd=cwd, correlation_id=correlation_id, timeout_s=timeout_s
        )
        if result.denied:
            return DiagnosticReport(command=command, denied=True, error=result.error)
        if result.error is not None and result.exit_code is None:
            return DiagnosticReport(command=command, error=result.error)

        combined = f"{result.stdout}\n{result.stderr}" if result.stderr else result.stdout
        diagnostics = parse_diagnostics(combined)
        errors = sum(1 for d in diagnostics if d.severity == DiagnosticSeverity.ERROR)
        warnings = sum(1 for d in diagnostics if d.severity == DiagnosticSeverity.WARNING)
        return DiagnosticReport(
            command=command,
            ok=result.ok,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            diagnostics=tuple(diagnostics),
            errors=errors,
            warnings=warnings,
            output_tail=_tail(combined),
        )
