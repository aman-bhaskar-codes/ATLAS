"""Tests for verification module."""

from __future__ import annotations

import pytest

from atlas.infra.cognition import (
    Evidence,
    GoalState,
)
from atlas.orchestration.verification import (
    _FAIL_MARKERS,
    _PATH_RE,
    GroundingVerifier,
    _evidence_lines,
    _extract_paths,
)


def make_goal(criteria: tuple[str, ...] = ("criteria",)) -> GoalState:
    return GoalState(objective="test objective", success_criteria=criteria)


class TestPathRegex:
    def test_matches_absolute_path(self) -> None:
        match = _PATH_RE.search("/home/user/file.txt")
        assert match is not None
        assert match.group(1) == "/home/user/file.txt"

    def test_matches_relative_path(self) -> None:
        match = _PATH_RE.search("./src/main.py")
        assert match is not None
        assert match.group(1) == "./src/main.py"

    def test_matches_src_path(self) -> None:
        match = _PATH_RE.search("src/atlas/core.py")
        assert match is not None
        assert match.group(1) == "src/atlas/core.py"

    def test_no_match_for_short_string(self) -> None:
        match = _PATH_RE.search("ab")
        assert match is None


class TestFailMarkers:
    def test_contains_failed(self) -> None:
        assert "failed" in _FAIL_MARKERS

    def test_contains_error(self) -> None:
        assert "error" in _FAIL_MARKERS


class TestEvidenceLines:
    def test_empty_evidence(self) -> None:
        result = _evidence_lines(())
        assert result == ()

    def test_formats_evidence(self) -> None:
        evidence = (Evidence(source="/file.py", summary="Found the bug", ok=True),)
        result = _evidence_lines(evidence)
        assert len(result) == 1
        assert "/file.py" in result[0]
        assert "Found the bug" in result[0]

    def test_respects_limit(self) -> None:
        evidence = tuple(Evidence(source=f"src/{i}.py", summary=f"Summary {i}", ok=True) for i in range(10))
        result = _evidence_lines(evidence, limit=3)
        assert len(result) == 3

    def test_truncates_long_summary(self) -> None:
        evidence = (Evidence(source="/file.py", summary="x" * 200, ok=True),)
        result = _evidence_lines(evidence)
        assert len(result[0]) <= 130


class TestExtractPaths:
    def test_empty_inputs(self) -> None:
        result = _extract_paths((), "", ())
        assert result == []

    def test_extracts_from_answer(self) -> None:
        result = _extract_paths((), "Created /tmp/test/file.txt", ())
        assert "/tmp/test/file.txt" in result

    def test_extracts_from_criteria(self) -> None:
        result = _extract_paths(("Check /app/main.py",), "", ())
        assert "/app/main.py" in result

    def test_extracts_from_evidence(self) -> None:
        evidence = (Evidence(source="/src/main.py", summary="ok", ok=True),)
        result = _extract_paths((), "", evidence)
        assert "/src/main.py" in result

    def test_deduplicates(self) -> None:
        result = _extract_paths(("/path/file.py",), "/path/file.py", ())
        assert result.count("/path/file.py") == 1

    def test_preserves_order(self) -> None:
        result = _extract_paths(("/first.py", "/second.py"), "", ())
        assert result.index("/first.py") < result.index("/second.py")


class TestGroundingVerifier:
    @pytest.fixture
    def verifier(self) -> GroundingVerifier:
        return GroundingVerifier(min_sources=1)

    @pytest.mark.asyncio
    async def test_fails_without_evidence(self, verifier: GroundingVerifier) -> None:
        result = await verifier.verify(
            goal=make_goal(),
            answer="some answer",
            correlation_id="test",
            evidence=(),
        )
        assert result.passed is False
        assert result.score == 0.0

    @pytest.mark.asyncio
    async def test_passes_with_evidence(self, verifier: GroundingVerifier) -> None:
        evidence = (Evidence(source="/file.py", summary="found", ok=True),)
        result = await verifier.verify(
            goal=make_goal(),
            answer="answer citing /file.py",
            correlation_id="test",
            evidence=evidence,
        )
        assert result.verifier == "none"
        assert "grounded" in result.failure_reason.lower()

    @pytest.mark.asyncio
    async def test_ignores_failed_evidence(self, verifier: GroundingVerifier) -> None:
        evidence = (Evidence(source="/file.py", summary="failed", ok=False),)
        result = await verifier.verify(
            goal=make_goal(),
            answer="answer",
            correlation_id="test",
            evidence=evidence,
        )
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_respects_min_sources(self) -> None:
        verifier = GroundingVerifier(min_sources=3)
        evidence = tuple(Evidence(source=f"/f{i}.py", summary="ok", ok=True) for i in range(2))
        result = await verifier.verify(
            goal=make_goal(),
            answer="answer",
            correlation_id="test",
            evidence=evidence,
        )
        assert result.passed is False
        assert "grounded" in result.failure_reason.lower() or "observation" in result.failure_reason.lower()
