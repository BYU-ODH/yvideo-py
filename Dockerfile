FROM docker.io/library/python:3.13-slim-bookworm

# uv must remain in the final image: the legacy dump scheduler
# invokes `uv run scripts/dump_legacy_to_sqlite.py` via subprocess.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install Python dependencies (cached unless pyproject.toml or uv.lock change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

EXPOSE 8000

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
