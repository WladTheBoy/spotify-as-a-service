# ─────────────────────────────────────────────
#  Playlist-as-a-Service — Dockerfile
#  Uses uv for fast, reproducible installs
# ─────────────────────────────────────────────

# Stage 1: dependency builder
FROM python:3.12-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency specs first (better layer caching)
COPY pyproject.toml ./

# Install dependencies into /app/.venv
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

# ─────────────────────────────────────────────
# Stage 2: runtime image (smaller, no build tools)
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy virtual env from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application source
COPY app/ ./app/

# Create log directory
RUN mkdir -p logs

# Make sure venv python is on PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Use exec form so signals are handled correctly (graceful shutdown)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
