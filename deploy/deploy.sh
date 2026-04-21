#!/bin/bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

source "$script_dir/require_user.sh"
auto_deploy_require_repo_uid "$repo_root"

cd "$repo_root"

echo "Deploying $(git branch --show-current) at $(date)"

git fetch origin
git reset --hard "origin/$(git branch --show-current)"

docker compose build --pull
docker compose up -d

docker image prune -f

echo "Deploy complete"
