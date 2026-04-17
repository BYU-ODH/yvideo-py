#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "Deploying $(git branch --show-current) at $(date)"

git fetch origin
git reset --hard "origin/$(git branch --show-current)"

docker compose build --pull
docker compose up -d

docker image prune -f

echo "Deploy complete"
