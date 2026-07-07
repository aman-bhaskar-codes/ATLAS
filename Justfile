# Run with `just <target>`. Install just: `brew install just`.

set shell := ["bash", "-cu"]

# create venv + install all deps (incl. macos extras) + install hooks
setup:
    uv sync --all-extras
    uv run pre-commit install

lint:
    uv run ruff check .
    uv run ruff format --check .

typecheck:
    uv run mypy

imports:
    uv run lint-imports --config importlinter.ini

test:
    uv run pytest

# full gate — what CI runs
check: lint typecheck imports test
    uv run atlas doctor --verify-manifest

doctor:
    uv run atlas doctor

# C1/C2 smoke (macOS)
see:
    uv run atlas see

fmt:
    uv run ruff format .
    uv run ruff check --fix .
