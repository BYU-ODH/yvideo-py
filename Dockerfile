FROM docker.io/library/python:3.13-slim-bookworm

# uv must remain in the final image because Gunicorn and management commands
# run through it.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# LEGACY MIGRATION ONLY — delete this block when legacy_migration is
# removed. See core/legacy_migration/REMOVAL.md for the full checklist.
# openssh-client is required for legacy_migration's ssh/scp subprocess calls
# to the legacy media host.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (cached unless pyproject.toml or uv.lock change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

EXPOSE 8000

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
