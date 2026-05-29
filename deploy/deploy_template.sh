#!/bin/bash

# >>> fetch-runner-guard:BEGIN
DEPLOY_USER=deploy-user
if [ "$(whoami)" != "$DEPLOY_USER" ] || [ "$(id -u)" -eq 0 ]; then
    printf 'fetch-runner-guard: refusing to run as %s (uid %s); required: %s, non-root\n' "$(whoami)" "$(id -u)" "$DEPLOY_USER" >&2
    exit 1
fi
# <<< fetch-runner-guard:END

set -euo pipefail

if [ "$#" -ne 1 ]; then
    printf 'Usage: %s <branch>\n' "$0" >&2
    exit 1
fi

expected_branch="$1"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/common.sh"

require_non_root_user
repo_root="$(repo_root)"
load_deploy_env
require_deploy_user_context

cd "$repo_root"

local_branch="$(git branch --show-current)"
local_branch_display="${local_branch:-DETACHED_HEAD}"

if [ "$local_branch" != "$expected_branch" ]; then
    printf 'Refusing to deploy: expected branch "%s" but local branch is "%s"\n' "$expected_branch" "$local_branch_display" >&2
    exit 1
fi

echo "Deploying $expected_branch at $(date)"

git fetch origin
git reset --hard "origin/$expected_branch"

bash "$script_dir/install_quadlet.sh"
podman build -t "$(build_image_tag)" -f "$repo_root/Dockerfile" "$repo_root"
systemctl --user restart "$(container_service_name)"

podman image prune -f

echo "Deploy complete"
