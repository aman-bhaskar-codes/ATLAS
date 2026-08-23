# ATLAS Backend Dockerfile — Zero-Cost-First
# Multi-stage build: resolve dependencies with uv into a venv, then copy that venv
# into a slim runtime image.
#
# WHY the previous version could not work
# --------------------------------------
#   RUN uv sync --no-dev --frozen 2>/dev/null || uv pip install --system -r pyproject.toml
#
# Three separate failures in one line:
#  1. `uv sync` writes /build/.venv, and the runtime stage copied
#     /usr/local/lib/python3.13/site-packages — a directory `uv sync` never
#     touches. So on the happy path the image shipped with NO dependencies.
#  2. `2>/dev/null` hid the reason it fell through, so in practice every image was
#     built from the unpinned `uv pip install -r pyproject.toml` fallback. A lock
#     file that exists but is never honoured is worse than no lock file: the build
#     looks reproducible and is not.
#  3. Neither branch installed the ATLAS project itself, only its dependencies.
#     `importlib.metadata.version("atlas")` — which the API lifespan calls to fill
#     `/runtime/status` — therefore raised PackageNotFoundError and took startup
#     down with it.
#
# So: no fallback (a lock mismatch must fail the build loudly), the project itself
# installed so its distribution metadata exists, and the venv actually copied.

FROM python:3.13-slim AS builder

WORKDIR /build

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

# Dependency spec first, as its own layer: this is the expensive step and it only
# needs to re-run when the lock changes. uv.lock is NOT globbed — with --frozen a
# missing lock file must be a build failure, not a silent resolve.
COPY pyproject.toml uv.lock ./

# Dependencies only. --frozen fails the build if uv.lock disagrees with
# pyproject.toml, which is the entire point of committing a lock file.
RUN uv sync --frozen --no-dev --no-install-project

# Copy source. README.md is required because pyproject declares
# `readme = "README.md"`, and hatchling reads it while building the wheel.
COPY README.md ./
COPY src/ src/
COPY config/ config/

# Now install the project itself into the same venv. --no-editable writes a real
# copy plus .dist-info into site-packages, so the metadata survives the stage copy
# and does not depend on a .pth file pointing at a /build path that will not exist
# in the runtime image.
RUN uv sync --frozen --no-dev --no-editable

# --- Runtime stage ---
FROM python:3.13-slim AS runtime

WORKDIR /app

# curl is here for the HEALTHCHECK below. It was installed "for healthcheck"
# before, with no HEALTHCHECK in the file.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged runtime user. The container previously ran as root, so any code
# execution inside it — including a sandbox escape from the shell tool — held root
# on the container filesystem.
RUN useradd --create-home --uid 10001 atlas

# The whole virtualenv, dependencies and the atlas distribution together.
COPY --from=builder --chown=atlas:atlas /build/.venv /app/.venv
COPY --from=builder --chown=atlas:atlas /build/config /app/config

# /data holds the SQLite database, ChromaDB and backups; the runtime user must own
# it or the first write fails. Declared as a volume so an operator who forgets to
# mount one does not silently store state in the container layer.
RUN mkdir -p /data && chown atlas:atlas /data
VOLUME ["/data"]

# PATH before anything else so `python` resolves to the venv interpreter.
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV ATLAS_DATA_DIR=/data
ENV ATLAS_PROFILE=local_free

USER atlas

EXPOSE 8730

# /api/v1/live is deliberately NOT behind require_principal (see app.py) so this
# works whether or not ATLAS_API_KEYS is set. start-period covers the Atlas build:
# database migrations, ChromaDB and provider probes all run before the first
# request is served.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8730/api/v1/live || exit 1

# --host 0.0.0.0 is required, not a mistake: a container that binds 127.0.0.1 is
# unreachable from outside its network namespace, so published ports do nothing.
# Reaching it is still gated by what the operator publishes and by ATLAS_API_KEYS.
CMD ["python", "-m", "uvicorn", "atlas.interfaces.api.app:create_app", "--host", "0.0.0.0", "--port", "8730", "--factory"]
