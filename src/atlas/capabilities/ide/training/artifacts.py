"""Bounded local artifact validation. No model loading, downloads, or execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from atlas.capabilities.ide.training.datasets import DatasetError, workspace_path


def verify_safetensors(path: Path) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            length = int.from_bytes(stream.read(8), "little")
            if not 2 <= length <= 1024 * 1024 or length + 8 > size:
                raise DatasetError("invalid local safetensors artifact header")
            header = json.loads(stream.read(length))
            tensors = [value for key, value in header.items() if key != "__metadata__"]
            if not tensors:
                raise DatasetError("artifact has no tensors")
            for tensor in tensors:
                offsets = tensor["data_offsets"]
                if len(offsets) != 2 or not 0 <= offsets[0] < offsets[1] <= size - length - 8:
                    raise DatasetError("artifact tensor offsets are invalid")
            stream.seek(0)
            digest = hashlib.sha256()
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            return digest.hexdigest()
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise DatasetError("local safetensors artifact is unavailable or invalid") from exc


def verify_base(root: Path, relative: str) -> Path:
    base = workspace_path(root, relative)
    try:
        if not base.is_dir():
            raise DatasetError(
                "base artifact must be a local MLX checkpoint directory; Ollama GGUF cannot be trained by MLX"
            )
        config_path = workspace_path(root, f"{relative}/config.json")
        if config_path.stat().st_size > 1024 * 1024:
            raise DatasetError("base configuration is too large")
        config = json.loads(config_path.read_text())
        if not config.get("model_type") or config.get("auto_map"):
            raise DatasetError("base checkpoint must use a native model implementation without remote code")
        quantization = config.get("quantization", {})
        if not isinstance(quantization, dict) or quantization.get("bits", 32) > 4:
            raise DatasetError("this small-memory workflow requires a local checkpoint quantized to at most 4 bits")
        weights = sorted(base.glob("*.safetensors"))
        if not weights or sum(weight.stat().st_size for weight in weights) > 3 * 1024**3:
            raise DatasetError("local base weights are missing or exceed the 3 GiB workflow limit")
        for weight in weights:
            workspace_path(root, weight.relative_to(root).as_posix())
            verify_safetensors(weight)
        tokenizer = workspace_path(root, f"{relative}/tokenizer.json")
        if not tokenizer.is_file():
            raise DatasetError("local tokenizer.json is required; tokenizer downloads are disabled")
        return base
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        if isinstance(exc, DatasetError):
            raise
        raise DatasetError("local base checkpoint metadata is unavailable or invalid") from exc
