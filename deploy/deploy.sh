#!/bin/bash
# >>> fetch-runner-guard:BEGIN user=yvideo-py
if [ "$(whoami)" != "yvideo-py" ] || [ "$(id -u)" -eq 0 ]; then
    printf 'fetch-runner-guard: refusing to run as %s (uid %s); required: yvideo-py, non-root\n' "$(whoami)" "$(id -u)" >&2
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
repo_root="$(cd "$script_dir/.." && pwd)"

source "$script_dir/require_user.sh"
auto_deploy_require_repo_uid "$repo_root"

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

docker compose build --pull
docker compose up -d

docker image prune -f

echo "Deploy complete"
