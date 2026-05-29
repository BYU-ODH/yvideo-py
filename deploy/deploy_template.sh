#!/bin/bash

# >>> fetch-runner-guard:BEGIN
DEPLOY_USER=deploy-user
if [ "$(whoami)" != "$DEPLOY_USER" ] || [ "$(id -u)" -eq 0 ]; then
    printf 'fetch-runner-guard: refusing to run as %s (uid %s); required: %s, non-root\n' "$(whoami)" "$(id -u)" "$DEPLOY_USER" >&2
    exit 1
fi
# <<< fetch-runner-guard:END

set -euo pipefail

requested_branch=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --branch)
            if [ "$#" -lt 2 ]; then
                printf 'Usage: %s [--branch <branch>]\n' "$0" >&2
                exit 1
            fi
            requested_branch="$2"
            shift 2
            ;;
        *)
            printf 'Usage: %s [--branch <branch>]\n' "$0" >&2
            exit 1
            ;;
    esac
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/common.sh"

require_non_root_user
repo_root="$(repo_root)"
load_deploy_env
require_deploy_user_context

cd "$repo_root"

if [ -n "$requested_branch" ]; then
    expected_branch="$requested_branch"
    git fetch origin
    git checkout -B "$expected_branch" "origin/$expected_branch"
else
    local_branch="$(git branch --show-current)"
    if [ -z "$local_branch" ]; then
        printf 'Refusing to deploy: HEAD is detached and no --branch was given\n' >&2
        exit 1
    fi
    expected_branch="$local_branch"
    git fetch origin
fi

echo "Deploying $expected_branch at $(date)"

git reset --hard "origin/$expected_branch"

bash "$script_dir/install_quadlet.sh"
podman build -t "$(build_image_tag)" -f "$repo_root/Dockerfile" "$repo_root"
systemctl --user restart "$(container_service_name)"

podman image prune -f

echo "Deploy complete"
