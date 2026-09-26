"""DiagnosticsCollector parsing — the normalized problems feed.

Pure matcher tests over real tool output shapes (mypy/ruff/tsc/eslint) plus the
honesty contract: a linter exiting non-zero WITH findings is not an error, counts
reflect the parsed diagnostics, and unrecognized lines yield nothing (no
fabricated findings). A fake command tool stands in for the funnel.
"""

from __future__ import annotations

from typing import Any

from atlas.capabilities.ide.commands import CommandRunner
from atlas.capabilities.ide.contracts import DiagnosticSeverity
from atlas.capabilities.ide.diagnostics import DiagnosticsCollector, parse_diagnostics
from atlas.infra.ids import CorrelationId
from atlas.infra.types import ToolResult

MYPY_OUT = """\
src/atlas/app.py:12: error: Incompatible return value type  [return-value]
src/atlas/app.py:40:5: note: Consider using an explicit type
Found 1 error in 1 file (checked 3 source files)
"""

RUFF_OUT = """\
src/app.py:3:1: F401 `os` imported but unused
src/app.py:9:80: E501 Line too long (100 > 88)
Found 2 errors.
"""

TSC_OUT = """\
src/x.ts(12,5): error TS2322: Type 'string' is not assignable to type 'number'.
src/y.ts(3,1): warning TS6133: 'foo' is declared but its value is never read.
"""

ESLINT_OUT = """\
/abs/src/a.js: line 2, col 1, Error - 'foo' is defined but never used (no-unused-vars)
/abs/src/a.js: line 5, col 3, Warning - Unexpected console statement (no-console)
"""


def test_parse_mypy() -> None:
    diags = parse_diagnostics(MYPY_OUT)
    assert len(diags) == 2
    err = diags[0]
    assert err.file == "src/atlas/app.py" and err.line == 12
    assert err.severity == DiagnosticSeverity.ERROR
    assert err.code == "return-value" and err.source == "mypy"
    note = diags[1]
    assert note.line == 40 and note.col == 5 and note.severity == DiagnosticSeverity.INFO


def test_parse_ruff() -> None:
    diags = parse_diagnostics(RUFF_OUT)
    assert len(diags) == 2
    assert diags[0].code == "F401" and diags[0].col == 1 and diags[0].source == "ruff"
    assert diags[1].code == "E501"


def test_parse_tsc() -> None:
    diags = parse_diagnostics(TSC_OUT)
    assert len(diags) == 2
    assert diags[0].file == "src/x.ts" and diags[0].line == 12 and diags[0].col == 5
    assert diags[0].code == "TS2322" and diags[0].severity == DiagnosticSeverity.ERROR
    assert diags[1].severity == DiagnosticSeverity.WARNING and diags[1].source == "tsc"


def test_parse_eslint() -> None:
    diags = parse_diagnostics(ESLINT_OUT)
    assert len(diags) == 2
    assert diags[0].severity == DiagnosticSeverity.ERROR and diags[0].code == "no-unused-vars"
    assert diags[1].severity == DiagnosticSeverity.WARNING and diags[1].source == "eslint"


def test_unrecognized_lines_yield_nothing() -> None:
    assert parse_diagnostics("just some prose\nand more prose") == []


# ── End-to-end through a fake funnel ───────────────────────────────────────── #
class _FakeShell:
    name = "shell"

    def __init__(self, *, ok: bool, exit_code: int, stdout: str) -> None:
        self._ok = ok
        self._exit = exit_code
        self._stdout = stdout

    def dry_run(self, args: dict[str, Any]) -> str:
        return "RUN"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            ok=self._ok,
            output={"exit_code": self._exit, "stdout": self._stdout, "stderr": "", "duration_ms": 4},
            error=None if self._ok else "exit 1",
        )


class _PassSafety:
    async def guard(self, req: Any, tool: Any) -> ToolResult:
        return await tool.execute(req.args)


async def test_findings_with_nonzero_exit_is_not_an_error() -> None:
    # mypy exits 1 BECAUSE it found problems — that is findings, not a run failure.
    tool = _FakeShell(ok=False, exit_code=1, stdout=MYPY_OUT)
    collector = DiagnosticsCollector(CommandRunner(_PassSafety(), tool))  # type: ignore[arg-type]
    report = await collector.collect("mypy .", cwd="/tmp", correlation_id=CorrelationId("c1"))
    assert report.error is None  # non-zero-with-findings is not a runner error
    assert report.ok is False and report.exit_code == 1
    assert report.errors == 1 and len(report.diagnostics) == 2
    assert report.output_tail
