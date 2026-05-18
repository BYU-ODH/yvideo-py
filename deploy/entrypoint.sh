#!/bin/bash
set -euo pipefail

# Tune Gunicorn by CPU and memory budget: start WORKERS near the number of
# cores that can be dedicated to this container, and only raise THREADS if
# requests spend significant time waiting on the DB or other I/O. These
# defaults are injected by the Quadlet install script and can also be
# overridden manually at container runtime.
WORKERS="${WORKERS:-2}"
THREADS="${THREADS:-2}"

mkdir -p /app/data /app/media /app/staticfiles /app/var

uv run python manage.py migrate --noinput
uv run python manage.py collectstatic --noinput

exec uv run gunicorn yvideo.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "$WORKERS" \
    --threads "$THREADS" \
    --preload \
    --access-logfile - \
    --error-logfile -
