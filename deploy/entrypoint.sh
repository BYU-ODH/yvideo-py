#!/bin/bash
set -euo pipefail

mkdir -p /app/data /app/media /app/staticfiles /app/var

uv run python manage.py migrate --noinput
uv run python manage.py collectstatic --noinput

exec uv run gunicorn yvideo.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --threads 2 \
    --preload \
    --access-logfile - \
    --error-logfile -
