"""Local preparation CLI. Weight training is available only through IDE governance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from atlas.capabilities.ide.training.datasets import (
    DatasetError,
    export_split,
    read_workspace_file,
    split_dataset,
    validate_jsonl,
)
from atlas.capabilities.ide.training.evaluation import compare_predictions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    commands = parser.add_subparsers(dest="action", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("dataset")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("dataset")
    prepare.add_argument("--approve-local-export", action="store_true")
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("holdout")
    evaluate.add_argument("baseline")
    evaluate.add_argument("candidate")
    args = parser.parse_args()
    try:
        if args.action == "evaluate":
            gold = validate_jsonl(read_workspace_file(args.workspace, args.holdout))
            baseline = [
                json.loads(line)
                for line in read_workspace_file(args.workspace, args.baseline).splitlines()
                if line.strip()
            ]
            candidate = [
                json.loads(line)
                for line in read_workspace_file(args.workspace, args.candidate).splitlines()
                if line.strip()
            ]
            result = compare_predictions(gold, baseline, candidate).model_dump()
        else:
            examples = validate_jsonl(read_workspace_file(args.workspace, args.dataset))
            split = split_dataset(examples)
            if args.action == "prepare":
                result = export_split(args.workspace, split, approved=args.approve_local_export).model_dump()
            else:
                result = {
                    "valid": True,
                    "examples": len(examples),
                    "train": len(split.train),
                    "validation": len(split.validation),
                    "holdout": len(split.holdout),
                }
        print(json.dumps(result))
    except (DatasetError, ValueError):
        parser.exit(
            2,
            "Dataset or evaluation refused. Check schema, privacy, paths, task groups, and export approval.\n",
        )


if __name__ == "__main__":
    main()
