"""Test execution as a first-class ADE concept (Slice 8).

`analyze_project` surfaces a workspace's test-command CANDIDATES; the agentic
repair loop (edit → TEST → read failures → edit) needs those runs to come back as
STRUCTURED pass/fail signal, not a raw pipe it must re-parse every cycle.
`TestRunner` is that layer — and it owns NO execution path of its own: it delegates
to `CommandRunner.run`, which routes through the SAME `SafetyEngine.guard` + tool
funnel every other command uses (Constitution — no side door). All this module adds
is parsing/observation ON TOP of a governed run.

HONESTY (mirrors `CommandResult`): `ok`/`exit_code` always reflect the real
process. The counts are `None` — never a fabricated `0` — when no known framework
summary could be parsed, so an unrecognized runner reads as "ran, unparsed", not
"ran, all passed". Only failing/erroring cases are enumerated; passing cases are
counted.
"""

from __future__ import annotations

import re

from atlas.capabilities.ide.commands import CommandRunner
from atlas.capabilities.ide.contracts import (
    CommandResult,
    TestCase,
    TestOutcome,
    TestReport,
)
from atlas.infra.ids import CorrelationId

# Bound the raw tail we keep for the human (last N chars of combined output).
_TAIL_LIMIT = 6000
# Bound a single failure message so one exploded traceback can't dominate.
_MSG_LIMIT = 1200
# Cap enumerated failures so a suite failing thousands of cases stays bounded.
_MAX_FAILURES = 100

# ── Framework detection (conservative signatures over the combined output) ── #
_PYTEST_SIG = re.compile(r"={5,}\s|^\s*(?:PASSED|FAILED|ERROR)\s|test session starts", re.MULTILINE)
_JEST_SIG = re.compile(r"^\s*Tests:\s+", re.MULTILINE)
_VITEST_SIG = re.compile(r"^\s*Test Files\s+", re.MULTILINE)
_GO_SIG = re.compile(r"^(?:---\s+(?:FAIL|PASS|SKIP):|=== RUN|ok\s+\S+\s|FAIL\s+\S+\s)", re.MULTILINE)
_CARGO_SIG = re.compile(r"^test result:\s", re.MULTILINE)


def _detect_framework(text: str) -> str | None:
    """Best-effort framework label from the output shape. Order matters: cargo's
    `test result:` and jest/vitest's `Tests:` lines are unambiguous; pytest last
    as its banner is the loosest match."""
    if _CARGO_SIG.search(text):
        return "cargo"
    if _VITEST_SIG.search(text):
        return "vitest"
    if _JEST_SIG.search(text):
        return "jest"
    if _GO_SIG.search(text):
        return "go"
    if _PYTEST_SIG.search(text):
        return "pytest"
    return None


# ── Per-framework summary + failure parsers ────────────────────────────────── #
# pytest tail: "===== 1 failed, 2 passed, 1 skipped, 1 error in 0.42s ====="
_PYTEST_TOKEN = re.compile(r"(\d+)\s+(passed|failed|skipped|error|errors|xfailed|xpassed)\b")
# pytest short-summary line: "FAILED tests/test_x.py::test_y - AssertionError: ..."
_PYTEST_FAIL = re.compile(
    r"^(?P<kind>FAILED|ERROR)\s+(?P<file>[^\s:]+)(?:::(?P<name>[^\s]+))?(?:\s+-\s+(?P<msg>.*))?$",
    re.MULTILINE,
)

# jest: "Tests:       1 failed, 1 skipped, 3 passed, 5 total"
_JEST_LINE = re.compile(r"^\s*Tests:\s+(?P<body>.*)$", re.MULTILINE)
_JEST_TOKEN = re.compile(r"(\d+)\s+(passed|failed|skipped|todo|total)\b")
# vitest: "Tests  1 failed | 3 passed | 1 skipped (5)"  (NOT the "Test Files" line)
_VITEST_LINE = re.compile(r"^\s*Tests\s+(?P<body>.*)$", re.MULTILINE)
_VITEST_TOKEN = re.compile(r"(\d+)\s+(passed|failed|skipped|todo)\b")

# go: "--- FAIL: TestName (0.00s)"
_GO_CASE = re.compile(r"^\s*---\s+(FAIL|PASS|SKIP):\s+(?P<name>\S+)", re.MULTILINE)
# cargo: "test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out"
_CARGO_LINE = re.compile(
    r"test result:\s+\w+\.\s+(\d+)\s+passed;\s+(\d+)\s+failed;\s+(\d+)\s+ignored",
)
_CARGO_FAIL = re.compile(r"^\s*(?:test\s+)?(?P<name>\S+)\s+\.\.\.\s+FAILED\s*$", re.MULTILINE)


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _tail(text: str) -> str:
    return text if len(text) <= _TAIL_LIMIT else text[-_TAIL_LIMIT:]


class _Parsed:
    __slots__ = ("failed", "failures", "passed", "skipped", "total")

    def __init__(self) -> None:
        self.passed: int | None = None
        self.failed: int | None = None
        self.skipped: int | None = None
        self.total: int | None = None
        self.failures: list[TestCase] = []


def _parse_pytest(text: str) -> _Parsed:
    p = _Parsed()
    counts: dict[str, int] = {}
    for m in _PYTEST_TOKEN.finditer(text):
        key = "error" if m.group(2) in {"error", "errors"} else m.group(2)
        counts[key] = counts.get(key, 0) + int(m.group(1))
    if counts:
        p.passed = counts.get("passed", 0)
        p.failed = counts.get("failed", 0) + counts.get("error", 0)
        p.skipped = counts.get("skipped", 0)
        p.total = p.passed + p.failed + p.skipped
    for m in _PYTEST_FAIL.finditer(text):
        if len(p.failures) >= _MAX_FAILURES:
            break
        name = m.group("name") or m.group("file")
        p.failures.append(
            TestCase(
                name=name,
                outcome=TestOutcome.ERROR if m.group("kind") == "ERROR" else TestOutcome.FAILED,
                file=m.group("file") or None,
                message=_clip(m.group("msg"), _MSG_LIMIT) if m.group("msg") else None,
            )
        )
    return p


def _parse_tokens(text: str, line_re: re.Pattern[str], token_re: re.Pattern[str]) -> _Parsed:
    """Shared jest/vitest counting: isolate the single summary line (so the
    ``Test Suites`` / ``Test Files`` line above it is NOT double-counted), then sum
    its labelled integers. `total` is trusted from the runner when present."""
    p = _Parsed()
    line = line_re.search(text)
    if line is None:
        return p
    body = line.group("body")
    counts: dict[str, int] = {}
    for m in token_re.finditer(body):
        label = m.group(2)
        counts[label] = counts.get(label, 0) + int(m.group(1))
    if not counts:
        return p
    p.passed = counts.get("passed", 0)
    p.failed = counts.get("failed", 0)
    p.skipped = counts.get("skipped", 0) + counts.get("todo", 0)
    p.total = counts.get("total") or (p.passed + p.failed + p.skipped)
    return p


def _parse_go(text: str) -> _Parsed:
    p = _Parsed()
    passed = failed = skipped = 0
    seen = False
    for m in _GO_CASE.finditer(text):
        seen = True
        kind, name = m.group(1), m.group("name")
        if kind == "FAIL":
            failed += 1
            if len(p.failures) < _MAX_FAILURES:
                p.failures.append(TestCase(name=name, outcome=TestOutcome.FAILED))
        elif kind == "PASS":
            passed += 1
        else:
            skipped += 1
    if seen:
        p.passed, p.failed, p.skipped = passed, failed, skipped
        p.total = passed + failed + skipped
    return p


def _parse_cargo(text: str) -> _Parsed:
    p = _Parsed()
    m = _CARGO_LINE.search(text)
    if m:
        p.passed = int(m.group(1))
        p.failed = int(m.group(2))
        p.skipped = int(m.group(3))
        p.total = p.passed + p.failed + p.skipped
    for fm in _CARGO_FAIL.finditer(text):
        if len(p.failures) >= _MAX_FAILURES:
            break
        p.failures.append(TestCase(name=fm.group("name"), outcome=TestOutcome.FAILED))
    return p


def parse_test_output(text: str, framework: str | None) -> _Parsed:
    """Route to the framework parser. An unknown framework yields an empty
    `_Parsed` (all-`None` counts) — honest "unparsed", not a fabricated zero."""
    if framework == "pytest":
        return _parse_pytest(text)
    if framework in {"jest"}:
        return _parse_tokens(text, _JEST_LINE, _JEST_TOKEN)
    if framework == "vitest":
        return _parse_tokens(text, _VITEST_LINE, _VITEST_TOKEN)
    if framework == "go":
        return _parse_go(text)
    if framework == "cargo":
        return _parse_cargo(text)
    return _Parsed()


class TestRunner:
    """Governed test execution: run a command through `CommandRunner`, then parse
    its output into a structured `TestReport`. Stateless — one instance serves
    every workspace, the workspace root arrives as `cwd`."""

    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    async def run(
        self,
        command: str,
        *,
        cwd: str,
        correlation_id: CorrelationId,
        timeout_s: float = 300.0,
    ) -> TestReport:
        """Run `command` in `cwd` through the funnel and parse the result. Never
        raises for expected outcomes: denial → `denied`, tool/runner failure →
        `error`, otherwise the process outcome plus parsed counts (or honest
        `None` counts when the framework is unrecognized)."""
        result: CommandResult = await self._runner.run(
            command, cwd=cwd, correlation_id=correlation_id, timeout_s=timeout_s
        )
        if result.denied:
            return TestReport(command=command, denied=True, error=result.error)
        # No tool wired, or the tool itself errored before/without a real exit.
        if result.error is not None and result.exit_code is None:
            return TestReport(command=command, error=result.error)

        combined = f"{result.stdout}\n{result.stderr}" if result.stderr else result.stdout
        framework = _detect_framework(combined)
        parsed = parse_test_output(combined, framework)
        return TestReport(
            command=command,
            framework=framework,
            ok=result.ok,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            passed=parsed.passed,
            failed=parsed.failed,
            skipped=parsed.skipped,
            total=parsed.total,
            failures=tuple(parsed.failures),
            error=result.error if not result.ok else None,
            output_tail=_tail(combined),
        )
