# CI-only image with the test toolchain preinstalled. tests.yaml rebuilds it
# whenever this file or the Python dependency files change.
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

# Lets the workflow's GITHUB_TOKEN pull and push this package.
LABEL org.opencontainers.image.source=https://github.com/BYU-ODH/yvideo-py

COPY --from=docker.io/library/node:24-trixie-slim /usr/local/bin/node /usr/local/bin/
COPY --from=docker.io/library/node:24-trixie-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

# The checkout is owned by the runner user, but container jobs run as root.
RUN git config --system --add safe.directory '*'

# Outside the checkout so the job's `uv run` finds dependencies already installed.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /tmp/deps
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project --no-cache \
    && /opt/venv/bin/playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
