#!/bin/bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/common.sh"

require_non_root_user
load_deploy_env
require_deploy_user_context

repo_root="$(repo_root)"
cd "$repo_root"

echo "Deploying $(git rev-parse --short HEAD) at $(date)"

bash "$script_dir/install_quadlet.sh"
podman build \
    --build-arg "YVIDEO_GIT_SHA=$(git rev-parse HEAD)" \
    -t "$(build_image_tag)" -f "$repo_root/Dockerfile" "$repo_root"
systemctl --user restart "$(container_service_name)"
# International Cinema units start after the main container has migrated.
while IFS= read -r unit; do
    [ -n "$unit" ] && systemctl --user restart "$unit.service"
done < <(ic_unit_names)

podman image prune -f

echo "Deploy complete"
