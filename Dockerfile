# ATLAS Backend Dockerfile — Zero-Cost-First
# Multi-stage build: install deps with uv, then copy to slim runtime image.

FROM python:3.13-slim AS builder

WORKDIR /build

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

# Copy dependency spec first (Docker cache layer)
COPY pyproject.toml uv.lock* ./

# Install dependencies
RUN uv sync --no-dev --frozen 2>/dev/null || uv pip install --system -r pyproject.toml

# Copy source
COPY src/ src/
COPY config/ config/

# --- Runtime stage ---
FROM python:3.13-slim AS runtime

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Copy installed packages and source from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /build/src /app/src
COPY --from=builder /build/config /app/config

# Create data directory
RUN mkdir -p /data

ENV PYTHONPATH=/app/src
ENV ATLAS_DATA_DIR=/data
ENV ATLAS_PROFILE=local_free

EXPOSE 8730

CMD ["python", "-m", "uvicorn", "atlas.interfaces.api.app:create_app", "--host", "0.0.0.0", "--port", "8730", "--factory"]
