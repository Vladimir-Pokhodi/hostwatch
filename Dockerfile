# hostwatch: the bot, the polling scheduler and (from stage 06) the login page.
# Chromium lives in a separate `browser` container, not here.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    DATA_DIR=/data

WORKDIR /app

# Dependencies first: this layer is rebuilt only when the lock file changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY hostwatch ./hostwatch
RUN uv sync --locked --no-dev

RUN useradd --system --uid 10001 --home-dir /app hostwatch \
    && mkdir /data && chown hostwatch /data
USER hostwatch
VOLUME /data

# The alert loop touches this file every minute.
HEALTHCHECK --interval=60s --timeout=5s --start-period=120s \
    CMD ["python", "-c", "import os, sys, time; sys.exit(time.time() - os.path.getmtime('/tmp/hostwatch.alive') > 300)"]

CMD ["python", "-m", "hostwatch"]
