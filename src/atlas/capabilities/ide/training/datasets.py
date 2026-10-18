"""Validated tool-call datasets and disjoint task-group splits; no network I/O."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from atlas.infra.types import PrivacyClass, ToolCallSpec
from atlas.intelligence.contracts import Message, Role

MAX_DATASET_BYTES = 2 * 1024 * 1024
MAX_EXAMPLES = 2000


class DatasetError(ValueError):
    """A dataset/path/approval cannot be accepted; messages never quote private input."""


class TrainingExample(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}
    group_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")
    case_id: str | None = None
    privacy_class: PrivacyClass = PrivacyClass.PRIVATE
    messages: tuple[Message, ...]
    tools: tuple[ToolCallSpec, ...] = ()

    @property
    def identity(self) -> str:
        return self.case_id or hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:32]


class DatasetSplit(BaseModel):
    model_config = {"frozen": True}
    train: tuple[TrainingExample, ...]
    validation: tuple[TrainingExample, ...]
    holdout: tuple[TrainingExample, ...]
    seed: int


class DatasetExport(BaseModel):
    export_id: str
    path: str
    train_count: int
    validation_count: int
    holdout_count: int
    digest: str
    training_completed: bool = False


_SECRET_KEYS = frozenset(
    {"password", "api_key", "access_token", "refresh_token", "client_secret", "authorization", "private_key"}
)
_SENSITIVE = re.compile(
    r"-----BEGIN (?:\w+ )?PRIVATE KEY-----|\bBearer\s+\S+|\bAKIA[A-Z0-9]{16}\b|"
    r"\b(?:sk-|ghp_|github_pat_)[a-zA-Z0-9_-]{12,}|"
    r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|(?:\+\d{1,3}[ -])(?:\d[ -]?){9,12}|"
    r"(?:^|[/\\])(?:\.ssh|\.aws|\.env)(?:[/\\.\s]|$)",
    re.IGNORECASE,
)


def contains_sensitive(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            (str(key).lower() in _SECRET_KEYS and bool(item)) or contains_sensitive(item) for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_sensitive(item) for item in value)
    return isinstance(value, str) and bool(_SENSITIVE.search(value))


def validate_jsonl(text: str) -> tuple[TrainingExample, ...]:
    if len(text.encode("utf-8")) > MAX_DATASET_BYTES:
        raise DatasetError("dataset exceeds the 2 MiB limit")
    rows: list[TrainingExample] = []
    seen: dict[str, str] = {}
    case_ids: set[str] = set()
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if len(rows) >= MAX_EXAMPLES:
            raise DatasetError("dataset exceeds the 2000 example limit")
        try:
            raw = json.loads(line)
            if contains_sensitive(raw):
                raise DatasetError(f"line {line_no} contains sensitive data; remove it before export")
            row = TrainingExample.model_validate(raw)
        except (ValidationError, json.JSONDecodeError, TypeError) as exc:
            raise DatasetError(f"line {line_no} is not a valid training trace") from exc
        if row.privacy_class in {PrivacyClass.SECRET, PrivacyClass.SENSITIVE}:
            raise DatasetError(f"line {line_no} privacy classification is not eligible for training")
        pending: dict[str, str] = {}
        advertised = {tool.name for tool in row.tools}
        saw_user = False
        saw_call = False
        for index, message in enumerate(row.messages):
            if message.images or message.reasoning_details:
                raise DatasetError(f"line {line_no} includes unsupported image/reasoning training data")
            if message.role is Role.SYSTEM and index != 0:
                raise DatasetError(f"line {line_no} has a misplaced system turn")
            if message.role is Role.USER:
                saw_user = True
            if pending and message.role is not Role.TOOL:
                raise DatasetError(f"line {line_no} has unanswered tool calls")
            if message.tool_calls:
                if message.role is not Role.ASSISTANT or not saw_user:
                    raise DatasetError(f"line {line_no} has misplaced tool calls")
                for call in message.tool_calls:
                    if not call.id or call.id in pending or call.name not in advertised or call.thought_signature:
                        raise DatasetError(f"line {line_no} has an invalid or unadvertised tool call")
                    pending[call.id] = call.name
                    saw_call = True
            if message.role is Role.TOOL:
                if pending.pop(message.tool_call_id or "", None) != message.name:
                    raise DatasetError(f"line {line_no} has an orphaned or mismatched tool result")
        if not saw_user or not saw_call or pending or not row.messages or row.messages[-1].role is not Role.ASSISTANT:
            raise DatasetError(f"line {line_no} is not a complete tool-call trace")
        content_hash = hashlib.sha256(
            json.dumps(
                {
                    "messages": [message.model_dump(mode="json") for message in row.messages],
                    "tools": [tool.model_dump(mode="json") for tool in row.tools],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if content_hash in seen and seen[content_hash] != row.group_id:
            raise DatasetError(f"line {line_no} duplicates a trace in another task group")
        if row.identity in case_ids:
            raise DatasetError(f"line {line_no} duplicates a case")
        seen[content_hash] = row.group_id
        case_ids.add(row.identity)
        rows.append(row)
    if not rows:
        raise DatasetError("dataset is empty")
    return tuple(rows)


def split_dataset(examples: Sequence[TrainingExample], *, seed: int = 42) -> DatasetSplit:
    groups = sorted(
        {row.group_id for row in examples}, key=lambda group: hashlib.sha256(f"{seed}:{group}".encode()).hexdigest()
    )
    if len(groups) < 5:
        raise DatasetError(
            "at least five independent task groups are required for train, validation, and holdout splits"
        )
    reserved = max(1, len(groups) // 5)
    holdout_groups = set(groups[:reserved])
    validation_groups = set(groups[reserved : 2 * reserved])
    ordered = sorted(examples, key=lambda row: (row.group_id, row.identity))
    return DatasetSplit(
        train=tuple(row for row in ordered if row.group_id not in holdout_groups | validation_groups),
        validation=tuple(row for row in ordered if row.group_id in validation_groups),
        holdout=tuple(row for row in ordered if row.group_id in holdout_groups),
        seed=seed,
    )


def workspace_path(root: Path, relative: str) -> Path:
    rel = Path(relative)
    if not relative or rel.is_absolute() or ".." in rel.parts:
        raise DatasetError("path must be relative to the selected workspace")
    if any(part in {".ssh", ".aws", ".git", ".codex", ".agents"} or part.startswith(".env") for part in rel.parts):
        raise DatasetError("credential and internal paths are not eligible for training")
    if rel.suffix.lower() in {".key", ".pem", ".p12", ".keychain"}:
        raise DatasetError("credential files are not eligible for training")
    root = root.resolve(strict=True)
    current = root
    for part in rel.parts:
        current /= part
        if current.is_symlink():
            raise DatasetError("training paths must not contain symlinks")
    if not current.resolve(strict=False).is_relative_to(root):
        raise DatasetError("training path escapes the selected workspace")
    return current


def read_workspace_file(root: Path, relative: str) -> str:
    path = workspace_path(root, relative)
    try:
        if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
            raise DatasetError("training input must be a regular file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise DatasetError("training input must be a regular file")
            data = stream.read(MAX_DATASET_BYTES + 1)
        if len(data) > MAX_DATASET_BYTES:
            raise DatasetError("file exceeds the 2 MiB limit")
        return data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DatasetError("training file is unavailable or not UTF-8") from exc


def trainer_row(row: TrainingExample) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    for message in row.messages:
        turn: dict[str, Any] = {"role": message.role.value, "content": message.content}
        if message.tool_calls:
            turn["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.arguments,
                    },
                }
                for call in message.tool_calls
            ]
        if message.role is Role.TOOL:
            turn.update(tool_call_id=message.tool_call_id, name=message.name)
        messages.append(turn)
    return {
        "messages": messages,
        "tools": [{"type": "function", "function": tool.model_dump(mode="json")} for tool in row.tools],
    }


def export_split(root: Path, split: DatasetSplit, *, approved: bool) -> DatasetExport:
    if not approved:
        raise DatasetError("explicit local dataset export approval is required")
    export_id = uuid4().hex
    relative = f".atlas-training/exports/{export_id}"
    destination = workspace_path(root, relative)
    ensure_private_training_dir(root)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    payloads = {
        "train.jsonl": "\n".join(json.dumps(trainer_row(row)) for row in split.train) + "\n",
        "valid.jsonl": "\n".join(json.dumps(trainer_row(row)) for row in split.validation) + "\n",
        "holdout.jsonl": "\n".join(
            row.model_copy(update={"case_id": row.identity}).model_dump_json() for row in split.holdout
        )
        + "\n",
    }
    digest = hashlib.sha256(json.dumps(payloads, sort_keys=True).encode()).hexdigest()
    result = DatasetExport(
        export_id=export_id,
        path=relative,
        digest=digest,
        train_count=len(split.train),
        validation_count=len(split.validation),
        holdout_count=len(split.holdout),
    )
    for name, content in payloads.items():
        write_private(destination / name, content)
    manifest = result.model_dump(mode="json") | {
        "seed": split.seed,
        "approved_local_export": True,
        "groups": {
            name: sorted({row.group_id for row in part})
            for name, part in (
                ("train", split.train),
                ("validation", split.validation),
                ("holdout", split.holdout),
            )
        },
        "file_hashes": {name: hashlib.sha256(content.encode()).hexdigest() for name, content in payloads.items()},
    }
    write_private(destination / "manifest.json", json.dumps(manifest, indent=2))
    return result


def write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)


def ensure_private_training_dir(root: Path) -> Path:
    directory = workspace_path(root, ".atlas-training")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    ignored = directory / ".gitignore"
    if not ignored.exists():
        write_private(ignored, "*\n!.gitignore\n")
    return directory
