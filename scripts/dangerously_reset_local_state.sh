#!/bin/bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: bash scripts/dangerously_reset_local_state.sh [--force] [--bootstrap] [--db-dir=DIR]

Deletes development state:
- SQLite database files
- generated media under media/

Unless --db-dir is given, the database location is read directly from Django
(settings.DATABASES['default']['NAME']) so this script can never disagree with
the app about where the database lives.

Options:
  --force         Skip the confirmation prompt.
  --bootstrap     Run migrate and seed_demo_data after cleanup.
  --db-dir=DIR    Directory holding the SQLite database files. Overrides the
                  path reported by Django.
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

# Resolve the database path. --db-dir is an explicit override; otherwise ask
# Django where the database actually lives so this script can never target a
# different location than the running app.
if [[ -n "$db_dir_arg" ]]; then
    db_path="$db_dir_arg/db.sqlite3"
else
    if ! db_path="$(uv run manage.py shell -c \
        "from django.conf import settings; print(settings.DATABASES['default']['NAME'])" \
        2>/dev/null | tail -n1)"; then
        echo "Failed to read the database path from Django. Pass --db-dir=DIR to override." >&2
        exit 1
    fi
    if [[ -z "$db_path" ]]; then
        echo "Django reported an empty database path. Pass --db-dir=DIR to override." >&2
        exit 1
    fi
fi
db_dir="$(dirname "$db_path")"

# If the resolved database doesn't exist, we're almost certainly pointed at the
# wrong place; deleting here would silently no-op and falsely report success.
if [[ ! -f "$db_path" ]]; then
    echo "WARNING: No database file found at '$db_path'." >&2
    echo "         You are likely pointed at the wrong directory; nothing would be deleted there." >&2
    if [[ "$force" == true ]]; then
        echo "Aborting instead of reporting a false success. Re-run with --db-dir=DIR if this path is wrong." >&2
        exit 1
    fi
fi

if [[ "$force" != true ]]; then
    read -r -p "Delete db at '$db_path' and generated media? [y/N] " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo "Aborted."
        exit 0
    fi
fi

rm -f \
    "$db_path" \
    "$db_path-journal" \
    "$db_path-shm" \
    "$db_path-wal" \
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
