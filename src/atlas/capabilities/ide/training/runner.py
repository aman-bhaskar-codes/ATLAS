"""Optional MLX trainer entry point, invoked only through governed IDE commands."""

from __future__ import annotations

import argparse
import importlib
import os
import runpy
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--adapter-path", required=True)
    parser.add_argument("--iters", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.iters <= 500 or not Path(args.model).is_dir() or not Path(args.data).is_dir():
        parser.error("local artifacts and 1-500 iterations are required")
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1", WANDB_MODE="disabled")
    mx = importlib.import_module("mlx.core")

    mx.set_memory_limit(4 * 1024**3)
    sys.argv = [
        "mlx_lm",
        "lora",
        "--model",
        args.model,
        "--data",
        args.data,
        "--adapter-path",
        args.adapter_path,
        "--train",
        "--fine-tune-type",
        "lora",
        "--batch-size",
        "1",
        "--num-layers",
        "4",
        "--max-seq-length",
        "512",
        "--iters",
        str(args.iters),
        "--grad-checkpoint",
        "--mask-prompt",
        "--seed",
        "42",
    ]
    runpy.run_module("mlx_lm", run_name="__main__")


if __name__ == "__main__":
    main()
