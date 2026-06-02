#!/bin/bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/common.sh"

require_non_root_user
load_deploy_env
require_deploy_user_context

root="$(repo_root)"
dest_dir="$(user_systemd_dir)"
service_name="$(deploy_service_name)"
timer_name="$(deploy_timer_name)"

mkdir -p "$dest_dir"

render_template "$root/deploy/deploy.service.in" "$dest_dir/$service_name"
render_template "$root/deploy/deploy.timer.in" "$dest_dir/$timer_name"

systemctl --user daemon-reload
systemctl --user enable --now "$timer_name"

printf 'Installed %s and %s\n' "$dest_dir/$service_name" "$dest_dir/$timer_name"
