# Single image used by both `poller` and `rule-editor` services; compose picks
# the entrypoint via `command:`.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first so the deps layer is cached across code changes.
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-install-project 2>/dev/null || uv sync --no-install-project

# Then the package — a second sync installs it but reuses the deps layer.
COPY monzo_grafana/ ./monzo_grafana/
RUN uv sync --frozen 2>/dev/null || uv sync

CMD ["uv", "run", "monzo-poller", "schedule"]
