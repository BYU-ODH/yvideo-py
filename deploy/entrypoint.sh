#!/bin/bash
set -euo pipefail

# Tune Gunicorn by CPU and memory budget: start WORKERS near the number of
# cores that can be dedicated to this container, and only raise THREADS if
# requests spend significant time waiting on the DB or other I/O. These
# defaults are injected by the Quadlet install script and can also be
# overridden manually at container runtime.
WORKERS="${WORKERS:-2}"
THREADS="${THREADS:-2}"

# One image, several roles (see deploy/ic/ and DEPLOY_IC.md). Only the "web"
# role migrates and collects static files; the others start after it.
#   web            the site (production) or the booth kiosk (IC_ROLE=kiosk)
#   ic-agent-web   the booth's signed sync-trigger endpoint (IC_ROLE=agent)
#   media-worker   production's International Cinema media jobs
#   ic-sync-agent  the booth's sync agent
YVIDEO_ROLE="${YVIDEO_ROLE:-web}"

mkdir -p /app/data /app/media /app/staticfiles /app/var

run_gunicorn() {
    exec uv run gunicorn yvideo.wsgi:application \
        --config /app/gunicorn.conf.py \
        --bind 0.0.0.0:8000 \
        --workers "$WORKERS" \
        --threads "$THREADS" \
        --preload \
        --access-logfile - \
        --error-logfile -
}

case "$YVIDEO_ROLE" in
    web)
        uv run python manage.py migrate --noinput
        uv run python manage.py collectstatic --noinput

        # Restrict the SQLite database files to the deploy user only. The bind mount
        # means these chmods land on the host files too.
        for db_file in /app/data/db.sqlite3 /app/data/db.sqlite3-wal /app/data/db.sqlite3-shm; do
            [ -e "$db_file" ] && chmod 600 "$db_file"
        done

        run_gunicorn
        ;;
    ic-agent-web)
        run_gunicorn
        ;;
    media-worker)
        exec uv run python manage.py process_media_jobs
        ;;
    ic-sync-agent)
        exec uv run python manage.py ic_sync_agent
        ;;
    *)
        echo "unknown YVIDEO_ROLE: $YVIDEO_ROLE" >&2
        exit 64
        ;;
esac
