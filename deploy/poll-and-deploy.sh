#!/bin/bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/common.sh"

require_non_root_user
load_deploy_env
require_deploy_user_context

repo_root="$(repo_root)"
cd "$repo_root"

branch="$BRANCH"
if [ -z "$branch" ]; then
    branch="$(git branch --show-current)"
    if [ -z "$branch" ]; then
        printf 'poll-and-deploy: BRANCH not set in .env and HEAD is detached; nothing to track\n' >&2
        exit 1
    fi
fi

git fetch --quiet origin "$branch"

if [ "$(git rev-parse HEAD)" = "$(git rev-parse "origin/$branch")" ]; then
    exit 0
fi

git reset --hard "origin/$branch"
exec "$script_dir/deploy.sh"
