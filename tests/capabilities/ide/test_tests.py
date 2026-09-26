"""TestRunner parsing — the structured pass/fail signal the repair loop reads.

Pure parser tests over real framework output shapes (pytest/jest/vitest/go/cargo)
plus the honesty contract: an unrecognized runner reads as "ran, unparsed"
(counts ``None``), never fabricated zeros; the process exit is always faithful.
A fake command tool stands in for the funnel so no real subprocess runs.
"""

from __future__ import annotations

from typing import Any

from atlas.capabilities.ide.commands import CommandRunner
from atlas.capabilities.ide.contracts import TestOutcome as Outcome
from atlas.capabilities.ide.tests import (
    TestRunner as Runner,
)
from atlas.capabilities.ide.tests import (
    _detect_framework,
    parse_test_output,
)
from atlas.infra.ids import CorrelationId
from atlas.infra.types import ToolResult

PYTEST_OUT = """\
=========================== test session starts ============================
collected 4 items

tests/test_a.py::test_ok PASSED
tests/test_a.py::test_bad FAILED
tests/test_b.py::test_err ERROR

=========================== short test summary info ========================
FAILED tests/test_a.py::test_bad - AssertionError: 1 != 2
ERROR tests/test_b.py::test_err - ValueError: boom
================== 1 failed, 2 passed, 1 skipped, 1 error in 0.42s ==========
"""

JEST_OUT = """\
Test Suites: 1 failed, 1 passed, 2 total
Tests:       1 failed, 1 skipped, 3 passed, 5 total
Snapshots:   0 total
Time:        1.2 s
"""

VITEST_OUT = """\
 Test Files  1 failed | 1 passed (2)
      Tests  1 failed | 3 passed | 1 skipped (5)
   Start at  10:00:00
   Duration  340ms
"""

GO_OUT = """\
=== RUN   TestAlpha
--- PASS: TestAlpha (0.00s)
=== RUN   TestBeta
--- FAIL: TestBeta (0.01s)
=== RUN   TestGamma
--- SKIP: TestGamma (0.00s)
FAIL
"""

CARGO_OUT = """\
running 3 tests
test tests::alpha ... ok
test tests::beta ... FAILED
test result: FAILED. 2 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out
"""


def test_detect_framework() -> None:
    assert _detect_framework(PYTEST_OUT) == "pytest"
    assert _detect_framework(JEST_OUT) == "jest"
    assert _detect_framework(VITEST_OUT) == "vitest"
    assert _detect_framework(GO_OUT) == "go"
    assert _detect_framework(CARGO_OUT) == "cargo"
    assert _detect_framework("hello world, nothing here") is None


def test_parse_pytest_counts_and_failures() -> None:
    p = parse_test_output(PYTEST_OUT, "pytest")
    assert p.passed == 2
    assert p.failed == 2  # 1 failed + 1 error folded into failed
    assert p.skipped == 1
    assert p.total == 5
    names = {f.name for f in p.failures}
    assert "test_bad" in names and "test_err" in names
    err = next(f for f in p.failures if f.name == "test_err")
    assert err.outcome == Outcome.ERROR
    assert err.file == "tests/test_b.py"
    assert err.message is not None and "boom" in err.message


def test_parse_jest_counts() -> None:
    p = parse_test_output(JEST_OUT, "jest")
    assert p.passed == 3 and p.failed == 1 and p.skipped == 1 and p.total == 5


def test_parse_vitest_counts() -> None:
    p = parse_test_output(VITEST_OUT, "vitest")
    assert p.passed == 3 and p.failed == 1 and p.skipped == 1 and p.total == 5


def test_parse_go_counts_and_failures() -> None:
    p = parse_test_output(GO_OUT, "go")
    assert p.passed == 1 and p.failed == 1 and p.skipped == 1 and p.total == 3
    assert [f.name for f in p.failures] == ["TestBeta"]


def test_parse_cargo_counts_and_failures() -> None:
    p = parse_test_output(CARGO_OUT, "cargo")
    assert p.passed == 2 and p.failed == 1 and p.skipped == 0 and p.total == 3
    assert [f.name for f in p.failures] == ["tests::beta"]


def test_unknown_framework_counts_are_none_not_zero() -> None:
    p = parse_test_output("some tool we do not know", None)
    assert p.passed is None and p.failed is None and p.total is None
    assert p.failures == []


# ── End-to-end through a fake funnel (no real subprocess) ──────────────────── #
class _FakeShell:
    name = "shell"

    def __init__(self, *, ok: bool, exit_code: int, stdout: str, denied: bool = False) -> None:
        self._ok = ok
        self._exit = exit_code
        self._stdout = stdout
        self._denied = denied

    def dry_run(self, args: dict[str, Any]) -> str:
        return "RUN"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            ok=self._ok,
            output={"exit_code": self._exit, "stdout": self._stdout, "stderr": "", "duration_ms": 7},
            error=None if self._ok else "exit 1",
        )


class _PassSafety:
    async def guard(self, req: Any, tool: Any) -> ToolResult:
        return await tool.execute(req.args)


class _DenySafety:
    async def guard(self, req: Any, tool: Any) -> ToolResult:
        from atlas.safety.engine import DeniedError

        class _D:
            reason = "blocked by policy"

        raise DeniedError(_D())  # type: ignore[arg-type]


def _runner(safety: Any, tool: Any) -> Runner:
    return Runner(CommandRunner(safety, tool))


async def test_run_reports_process_outcome_and_counts() -> None:
    runner = _runner(_PassSafety(), _FakeShell(ok=False, exit_code=1, stdout=PYTEST_OUT))
    report = await runner.run("pytest -q", cwd="/tmp", correlation_id=CorrelationId("c1"))
    assert report.framework == "pytest"
    assert report.ok is False and report.exit_code == 1
    assert report.passed == 2 and report.failed == 2
    assert len(report.failures) == 2
    assert report.output_tail  # raw tail kept for the human


async def test_run_denied_is_honest() -> None:
    runner = _runner(_DenySafety(), _FakeShell(ok=False, exit_code=1, stdout=""))
    report = await runner.run("rm -rf /", cwd="/tmp", correlation_id=CorrelationId("c2"))
    assert report.denied is True and report.error is not None
    assert report.passed is None  # nothing ran → no fabricated counts
