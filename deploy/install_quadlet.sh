#!/bin/bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/common.sh"

reload_systemd=1

while [ "$#" -gt 0 ]; do
    case "$1" in
        --no-reload)
            reload_systemd=0
            ;;
        *)
            die "unknown option: $1"
            ;;
    esac
    shift
done

require_non_root_user
load_deploy_env
require_deploy_user_context

root="$(repo_root)"
dest_dir="$(quadlet_dir)"

if [ ! -f "$root/yvideo/secret_settings.py" ]; then
    die "missing $root/yvideo/secret_settings.py"
fi

if [ ! -d "$root/yvideo/saml_config" ]; then
    die "missing $root/yvideo/saml_config"
fi

mkdir -p \
    "$root/data" \
    "$root/media" \
    "$root/staticfiles" \
    "$root/var" \
    "$dest_dir"

render_quadlet_template "$root/deploy/quadlet.build.in" "$dest_dir/$APP_NAME.build"
render_quadlet_template "$root/deploy/quadlet.container.in" "$dest_dir/$APP_NAME.container"

if [ "$reload_systemd" -eq 1 ]; then
    systemctl --user daemon-reload
fi

printf 'Installed %s\n' "$dest_dir/$APP_NAME.build"
printf 'Installed %s\n' "$dest_dir/$APP_NAME.container"
