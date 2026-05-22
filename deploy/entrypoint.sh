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

# Restrict the SQLite database files to the deploy user only. The bind mount
# means these chmods land on the host files too.
for db_file in /app/data/db.sqlite3 /app/data/db.sqlite3-wal /app/data/db.sqlite3-shm; do
    [ -e "$db_file" ] && chmod 600 "$db_file"
done

exec uv run gunicorn yvideo.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "$WORKERS" \
    --threads "$THREADS" \
    --preload \
    --access-logfile - \
    --error-logfile -
