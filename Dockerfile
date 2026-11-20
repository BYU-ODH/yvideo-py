FROM docker.io/library/debian:stable-slim

# uv must remain in the final image because Gunicorn and management commands
# run through it.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# International Cinema: ffprobe/ffmpeg check and transcode projection masters.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (cached unless pyproject.toml or uv.lock change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

EXPOSE 8000

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
