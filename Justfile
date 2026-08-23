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

serve:
    uv run uvicorn atlas.interfaces.api.app:create_app --factory --host 127.0.0.1 --port 8730 --reload

# backend tests + browsable coverage report (htmlcov/index.html)
cov:
    uv run pytest --cov=atlas --cov-report=term-missing --cov-report=xml --cov-report=html

# ── frontend ────────────────────────────────────────────────────────────────

web-lint:
    cd frontend && npm run lint

# pure-logic unit tests (vitest, no jsdom): typed API errors + retry predicate
web-test:
    cd frontend && npm run test:unit

# also runs tsc --noEmit across every route
web-build:
    cd frontend && npm run build

# one-time: fetch the Chromium build Playwright drives
e2e-install:
    cd frontend && npm run test:e2e:install

# Browser E2E. Boots a CLEAN backend on :8730 (fresh .e2e-data) and a production
# frontend on :3000 — the only origin the API's CORS policy allows.
e2e:
    cd frontend && npm run test:e2e

e2e-report:
    cd frontend && npm run test:e2e:report

# the whole pipeline, exactly what CI enforces (backend gates + frontend + E2E)
check-all: check web-lint web-test web-build e2e
