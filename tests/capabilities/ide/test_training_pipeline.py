"""Training preparation tests use synthetic traces; no user data or weight training."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def trace(group: str, suffix: str = "") -> dict[str, object]:
    return {
        "group_id": group,
        "privacy_class": "public",
        "messages": [
            {"role": "user", "content": f"Read the {group}{suffix} document"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "filesystem",
                        "arguments": {"operation": "read", "args": {"path": f"{group}.txt"}},
                    },
                ],
            },
            {"role": "tool", "content": "document text", "name": "filesystem", "tool_call_id": "c1"},
            {"role": "assistant", "content": f"Read {group}{suffix}"},
        ],
        "tools": [{"name": "filesystem", "description": "Read a document", "parameters": {"type": "object"}}],
    }


def jsonl(rows: list[dict[str, object]]) -> str:
    return "\n".join(json.dumps(row) for row in rows)


def test_task_group_split_is_stable_and_never_leaks_sibling_traces() -> None:
    from atlas.capabilities.ide.training.datasets import split_dataset, validate_jsonl

    rows = [trace(f"task-{i}", suffix) for i in range(10) for suffix in ("-a", "-b")]
    examples = validate_jsonl(jsonl(rows))
    first = split_dataset(examples, seed=42)
    shuffled = split_dataset(validate_jsonl(jsonl(list(reversed(rows)))), seed=42)
    assert first == shuffled
    groups = [{row.group_id for row in part} for part in (first.train, first.validation, first.holdout)]
    assert groups[0].isdisjoint(groups[1]) and groups[0].isdisjoint(groups[2]) and groups[1].isdisjoint(groups[2])
    assert [len(part) for part in (first.train, first.validation, first.holdout)] == [12, 4, 4]


@pytest.mark.parametrize(
    "bad_value", ["Bearer abcdefghijklmnopqrstuvwxyz", "person@example.com", "-----BEGIN PRIVATE KEY-----"]
)
def test_dataset_screening_rejects_secrets_and_personal_identifiers_without_echoing_them(bad_value: str) -> None:
    from atlas.capabilities.ide.training.datasets import DatasetError, validate_jsonl

    row = trace("task")
    row["messages"][0]["content"] = bad_value  # type: ignore[index]
    with pytest.raises(DatasetError) as caught:
        validate_jsonl(jsonl([row]))
    assert bad_value not in str(caught.value)


def test_dataset_rejects_orphaned_tool_result_and_cross_group_duplicate() -> None:
    from atlas.capabilities.ide.training.datasets import DatasetError, validate_jsonl

    row = trace("task")
    row["messages"][2]["tool_call_id"] = "unrequested"  # type: ignore[index]
    with pytest.raises(DatasetError, match="tool"):
        validate_jsonl(jsonl([row]))
    first = trace("same")
    second = dict(first, group_id="other")
    with pytest.raises(DatasetError, match="duplicate"):
        validate_jsonl(jsonl([first, second]))


def test_export_is_explicit_local_and_holdout_is_not_training_validation(tmp_path: Path) -> None:
    from atlas.capabilities.ide.training.datasets import DatasetError, export_split, split_dataset, validate_jsonl

    split = split_dataset(validate_jsonl(jsonl([trace(f"task-{i}") for i in range(5)])))
    with pytest.raises(DatasetError, match="approval"):
        export_split(tmp_path, split, approved=False)
    exported = export_split(tmp_path, split, approved=True)
    assert (tmp_path / exported.path / "train.jsonl").is_file()
    assert (tmp_path / exported.path / "valid.jsonl").is_file()
    assert (tmp_path / exported.path / "holdout.jsonl").is_file()
    assert not (tmp_path / exported.path / "test.jsonl").exists(), "untouched holdout must not be handed to the trainer"
    payload = json.loads((tmp_path / exported.path / "train.jsonl").read_text().splitlines()[0])
    assert payload["messages"][1]["tool_calls"][0]["function"]["name"] == "filesystem"
    assert exported.training_completed is False


def test_dataset_path_escape_and_symlink_out_are_refused(tmp_path: Path) -> None:
    from atlas.capabilities.ide.training.datasets import DatasetError, read_workspace_file

    outside = tmp_path.parent / "private-dataset.jsonl"
    outside.write_text("private data")
    (tmp_path / "link.jsonl").symlink_to(outside)
    for path in ("../private-dataset.jsonl", "link.jsonl", ".env"):
        with pytest.raises(DatasetError):
            read_workspace_file(tmp_path, path)


def test_training_source_refuses_private_key_files_and_non_regular_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import os

    from atlas.capabilities.ide.training.datasets import DatasetError, read_workspace_file

    (tmp_path / "private.key").write_text("synthetic key")
    with pytest.raises(DatasetError):
        read_workspace_file(tmp_path, "private.key")
    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "input.jsonl"
        os.mkfifo(fifo)
        # Prevent a buggy blocking open from hanging the RED run. The actual
        # filesystem type is still a real FIFO and must be refused before read.
        monkeypatch.setattr(Path, "open", lambda *args, **kwargs: io.BytesIO(b"synthetic input"))
        with pytest.raises(DatasetError):
            read_workspace_file(tmp_path, "input.jsonl")


def test_candidate_gate_requires_improvement_and_no_unsafe_calls() -> None:
    from atlas.capabilities.ide.training.datasets import validate_jsonl
    from atlas.capabilities.ide.training.evaluation import compare_predictions

    gold = validate_jsonl(jsonl([trace("case-a"), trace("case-b")]))
    expected = gold[0].messages[1].tool_calls[0].model_dump()
    baseline = [{"group_id": row.group_id, "tool_calls": [], "final_text": "incorrect"} for row in gold]
    candidate = [
        {"group_id": row.group_id, "tool_calls": [expected], "final_text": row.messages[-1].content} for row in gold
    ]
    # Different paths belong to different gold tasks.
    candidate[1]["tool_calls"] = [gold[1].messages[1].tool_calls[0].model_dump()]
    assert compare_predictions(gold, baseline, candidate).passed is True
    candidate[1]["tool_calls"] = [{"id": "danger", "name": "credential_store", "arguments": {"operation": "dump"}}]
    rejected = compare_predictions(gold, baseline, candidate)
    assert rejected.passed is False
    assert rejected.candidate_unsafe == 1


def test_candidate_gate_refuses_missing_or_repeated_cases() -> None:
    from atlas.capabilities.ide.training.datasets import DatasetError, validate_jsonl
    from atlas.capabilities.ide.training.evaluation import compare_predictions

    gold = validate_jsonl(jsonl([trace("case-a"), trace("case-b")]))
    with pytest.raises(DatasetError, match="cases"):
        compare_predictions(gold, [], [])


def test_candidate_cannot_change_an_allowed_tool_into_an_unapproved_operation() -> None:
    from atlas.capabilities.ide.training.datasets import validate_jsonl
    from atlas.capabilities.ide.training.evaluation import compare_predictions

    gold = validate_jsonl(jsonl([trace("case")]))
    baseline = [{"group_id": "case", "tool_calls": [], "final_text": "wrong"}]
    candidate = [
        {
            "group_id": "case",
            "tool_calls": [{"name": "filesystem", "arguments": {"operation": "delete"}}],
            "final_text": "wrong",
        }
    ]
    assert compare_predictions(gold, baseline, candidate).candidate_unsafe == 1


def test_training_command_uses_the_manifest_allowlisted_interpreter(tmp_path: Path) -> None:
    import yaml

    from atlas.capabilities.ide.training.service import build_training_command
    from atlas.tools.shell import ShellTool

    manifest = yaml.safe_load((Path(__file__).resolve().parents[3] / "config" / "permissions.yaml").read_text())
    tool = ShellTool(
        read_only=manifest["allowed_commands"]["read_only"],
        side_effect=manifest["allowed_commands"]["side_effect"],
        sandbox=object(),
        mounts={},
    )  # type: ignore[arg-type]
    command = build_training_command(tmp_path / "base", tmp_path / "data", tmp_path / "adapter", 10)
    allowed, reason = tool._allowed(command, cwd=str(tmp_path))
    assert allowed, reason
    assert command.startswith("python -I ")
