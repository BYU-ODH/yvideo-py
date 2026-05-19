#!/bin/bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/common.sh"

if [ "$#" -eq 0 ]; then
    printf 'Usage: %s <manage.py args...>\n' "$0" >&2
    exit 1
fi

require_non_root_user
load_deploy_env
require_deploy_user_context

if [ -t 0 ] && [ -t 1 ]; then
    exec podman exec -it "$APP_NAME" uv run python manage.py "$@"
fi

exec podman exec -i "$APP_NAME" uv run python manage.py "$@"
