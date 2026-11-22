# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12.12
ARG UV_VERSION=0.5.24

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /bin/

# Install dependencies first for better layer caching
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra api --extra dev

# Copy application source and install project
COPY src ./src
COPY config ./config
COPY scripts ./scripts
COPY tests ./tests
COPY docs ./docs
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra api --extra dev

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    VIRTUAL_ENV="/app/.venv"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data /app/models/artifacts /app/backups /app/logs /app/reports /tmp \
    && chown -R appuser:appuser /app

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /app/src /app/src
COPY --from=builder --chown=appuser:appuser /app/config /app/config
COPY --from=builder --chown=appuser:appuser /app/scripts /app/scripts
COPY --from=builder --chown=appuser:appuser /app/pyproject.toml /app/uv.lock /app/README.md ./

USER appuser

EXPOSE 8000

# Container binds to 0.0.0.0 internally; host exposure is restricted to 127.0.0.1 by compose port mapping.
# --allow-remote is required by the CLI for non-loopback binds; network exposure is controlled by compose.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()" || exit 1

CMD ["sf", "serve", "--host", "0.0.0.0", "--port", "8000", "--allow-remote"]

FROM builder AS test

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    VIRTUAL_ENV="/app/.venv" \
    HOME="/tmp" \
    XDG_CACHE_HOME="/tmp/.cache" \
    UV_CACHE_DIR="/tmp/.cache/uv" \
    RUFF_CACHE_DIR="/tmp/.ruff_cache"

WORKDIR /app

CMD ["sh", "-c", "ruff check . && pytest -q"]
