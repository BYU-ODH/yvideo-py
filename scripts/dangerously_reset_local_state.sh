#!/bin/bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: bash scripts/dangerously_reset_local_state.sh [--force] [--bootstrap] [--db-dir=DIR]

Deletes development state:
- SQLite database files
- generated media under media/

Run from the project root. The database location is resolved in this order:
--db-dir, then $YVIDEO_DB_DIR (e.g. /app/data inside a deployed container),
then the project root. This matches the location settings.py uses.

Options:
  --force         Skip the confirmation prompt.
  --bootstrap     Run migrate and seed_demo_data after cleanup.
  --db-dir=DIR    Directory holding the SQLite database files.
  -h, --help      Show this help text.
EOF
}

if [[ ! -f "manage.py" ]]; then
    echo "Run this script from the project root (where manage.py lives)." >&2
    exit 1
fi

force=false
bootstrap=false
db_dir_arg=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)
            force=true
            ;;
        --bootstrap)
            bootstrap=true
            ;;
        --db-dir=*)
            db_dir_arg="${1#*=}"
            ;;
        --db-dir)
            shift
            if [[ $# -eq 0 ]]; then
                echo "--db-dir requires a directory argument." >&2
                exit 1
            fi
            db_dir_arg="$1"
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

# Resolve the database directory: --db-dir wins, then YVIDEO_DB_DIR (e.g.
# /app/data inside a deployed container), otherwise the project root. This
# matches the location settings.py uses.
db_dir="${db_dir_arg:-${YVIDEO_DB_DIR:-.}}"

if [[ "$force" != true ]]; then
    read -r -p "Delete db in '$db_dir' and generated media? [y/N] " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo "Aborted."
        exit 0
    fi
fi

rm -f \
    "$db_dir/db.sqlite3" \
    "$db_dir/db.sqlite3-journal" \
    "$db_dir/db.sqlite3-shm" \
    "$db_dir/db.sqlite3-wal" \
    "$db_dir/default"
rm -rf media/*

echo "Local state cleared."

if [[ "$bootstrap" == true ]]; then
    echo "Rebuilding local database from migrations..."
    uv run manage.py migrate
    uv run manage.py seed_demo_data
fi

echo "Next steps:"
echo "  uv run manage.py migrate"
echo "  uv run manage.py seed_demo_data"
