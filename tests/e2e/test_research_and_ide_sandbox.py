"""Phase 4 Verification: Zero-Cost Research Engine & Containerized Agentic IDE Sandbox.

Tests:
1. Bloom Filter (m=10^5, k=7) for O(k) URL deduplication
2. 128-Permutation MinHash with LSH (J >= 0.85) near-duplicate rejection
3. Eugene Myers' shortest edit script (SES) diff algorithm & patch generator
4. Docker Sandbox spec, limits, and command isolation
"""

from __future__ import annotations

import pytest

from atlas.engineering.myers import (
    DiffOpKind,
    compute_edit_operations,
    format_unified_diff,
    myers_diff,
)
from atlas.knowledge.dedup import MinHashLSH, UrlBloomFilter
from atlas.safety.sandbox import SandboxResult
from atlas.safety.sandbox_docker import DockerSandbox, SandboxSpec


def test_url_bloom_filter_deduplication() -> None:
    """Bloom filter rejects already-visited URLs in O(k) time."""
    bloom = UrlBloomFilter(m=100_000, k=7)

    url1 = "https://example.com/research/quantum-computing"
    url2 = "https://example.com/research/neuromorphic-ai"
    url3 = "https://example.com/research/topological-materials"

    assert bloom.should_visit(url1) is True
    assert bloom.should_visit(url2) is True

    # Re-visiting url1 must return False (already visited)
    assert bloom.should_visit(url1) is False
    assert url1 in bloom

    # url3 was never added
    assert url3 not in bloom
    assert bloom.should_visit(url3) is True
    assert url3 in bloom


def test_minhash_lsh_duplicate_detection() -> None:
    """128-Permutation MinHash LSH detects near-duplicates (J >= 0.85) and accepts distinct pages."""
    lsh = MinHashLSH(num_perm=128, num_bands=16, similarity_threshold=0.85)

    doc1_text = (
        "Quantum computing is a rapidly-emerging technology that harnesses the laws of quantum mechanics "
        "to solve problems too complex for classical computers. Today, IBM Quantum hardware is available "
        "to thousands of developers. Our scientists are working to advance quantum hardware and software "
        "that will enable quantum utility: the point where quantum computers can deliver solutions beyond "
        "the capabilities of classical computers. Quantum computers process information in a fundamentally "
        "different way than classical computers. Classical devices operate on binary bits, which are either 0 or 1. "
        "Quantum computers operate on qubits, which can exist in a superposition of states."
    )

    # doc2 is a slight rewording/variation (near duplicate, J >= 0.85)
    doc2_text = (
        "Quantum computing is a rapidly-emerging technology that harnesses the laws of quantum mechanics "
        "to solve problems too complex for classical computers. Today, IBM Quantum hardware is available "
        "to thousands of developers. Our scientists are working to advance quantum hardware and software "
        "that will enable quantum utility: the point where quantum computers can deliver solutions beyond "
        "the capabilities of classical computers. Quantum computers process information in a fundamentally "
        "different way than classical computers. Classical machines operate on binary bits, which are either 0 or 1. "
        "Quantum computers operate on qubits, which can exist in a superposition of states."
    )

    # doc3 is completely distinct topic
    doc3_text = (
        "Photosynthesis in plants converts water and carbon dioxide into oxygen and carbohydrates "
        "using light energy captured by chlorophyll molecules inside plant chloroplasts."
    )

    # Index doc1
    is_dup1, match1, sim1 = lsh.is_duplicate_or_add("doc1", doc1_text)
    assert not is_dup1
    assert match1 is None

    # Test doc2 against doc1
    is_dup2, match2, sim2 = lsh.is_duplicate_or_add("doc2", doc2_text)
    assert is_dup2 is True
    assert match2 == "doc1"
    assert sim2 >= 0.85

    # Test distinct doc3
    is_dup3, match3, sim3 = lsh.is_duplicate_or_add("doc3", doc3_text)
    assert not is_dup3
    assert sim3 < 0.2


def test_myers_diff_algorithm() -> None:
    """Myers algorithm computes minimal edit script and standard unified diff."""
    old_lines = ["def hello():", "    print('old line')", "    return 42"]
    new_lines = ["def hello():", "    print('new line')", "    return 42"]

    ops = myers_diff(old_lines, new_lines)
    assert len(ops) == 4
    assert ops[0].kind == DiffOpKind.KEEP
    assert ops[0].line == "def hello():"
    assert ops[1].kind == DiffOpKind.DELETE
    assert ops[1].line == "    print('old line')"
    assert ops[2].kind == DiffOpKind.INSERT
    assert ops[2].line == "    print('new line')"
    assert ops[3].kind == DiffOpKind.KEEP
    assert ops[3].line == "    return 42"

    unified = format_unified_diff(old_lines, new_lines)
    assert "-    print('old line')" in unified
    assert "+    print('new line')" in unified


def test_myers_compute_edit_operations() -> None:
    """Myers diff transforms code differences into structured IDE edit operations."""
    old_code = "line1\nline2\nline3"
    new_code = "line1\nmodified_line2\nline3\nline4"

    edit_ops = compute_edit_operations(old_code, new_code)
    assert len(edit_ops) >= 1

    # First op should replace line 2
    replace_op = next((op for op in edit_ops if op["op"] == "replace"), None)
    assert replace_op is not None
    assert replace_op["start_line"] == 2
    assert replace_op["text"] == "modified_line2"


@pytest.mark.asyncio
async def test_docker_sandbox_spec_and_runner() -> None:
    """DockerSandbox validates strict security constraints: cgroups memory, cpu, and no network."""
    spec = SandboxSpec(
        image="python:3.13-slim",
        cpus=1.0,
        memory="512m",
        pids_limit=64,
        network=False,
    )

    class FakeRunner:
        def __init__(self) -> None:
            self.last_argv: list[str] = []

        async def run(self, argv: list[str], *, timeout_s: float, stdin: bytes | None = None) -> tuple[int, str, str]:
            self.last_argv = list(argv)
            return 0, "hello sandbox\n", ""

    runner = FakeRunner()
    sandbox = DockerSandbox(spec, runner=runner)

    res = await sandbox.run(["python", "-c", "print('hello sandbox')"], mounts={"/tmp": "/tmp"})
    assert res.exit_code == 0
    assert "hello sandbox" in res.stdout_tail

    # Verify security flags in argv
    argv = runner.last_argv
    assert "--network" in argv
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv
    assert "--memory" in argv
    assert argv[argv.index("--memory") + 1] == "512m"
    assert "--pids-limit" in argv
    assert argv[argv.index("--pids-limit") + 1] == "64"
