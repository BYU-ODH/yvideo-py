#!/bin/bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: bash scripts/dangerously_reset_local_state.sh [--force] [--bootstrap]

Deletes local development state for the pre-pilot workflow:
- SQLite database files
- generated media under media/
- local core migration files created with makemigrations

Options:
  --force      Skip the confirmation prompt.
  --bootstrap  Run migrate and seed_demo_data after cleanup.
  -h, --help   Show this help text.
EOF
}

if [[ ! -f "manage.py" ]]; then
    echo "Run this script from the project root (where manage.py lives)." >&2
    exit 1
fi

force=false
bootstrap=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)
            force=true
            ;;
        --bootstrap)
            bootstrap=true
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
    shift
done

if [[ "$force" != true ]]; then
    read -r -p "Delete local db, generated media, and local core migrations? [y/N] " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo "Aborted."
        exit 0
    fi
fi

rm -f db.sqlite3 db.sqlite3-journal db.sqlite3-shm db.sqlite3-wal default
rm -rf media/*

if [[ -d "core/migrations" ]]; then
    find core/migrations -maxdepth 1 -type f -name '[0-9]*_*.py' -delete
    rm -rf core/migrations/__pycache__
fi

echo "Local state cleared."

if [[ "$bootstrap" == true ]]; then
    echo "Rebuilding local database from current models..."
    uv run manage.py migrate
    uv run manage.py seed_demo_data
fi

echo "Next steps:"
echo "  uv run manage.py migrate"
echo "  uv run manage.py seed_demo_data"
