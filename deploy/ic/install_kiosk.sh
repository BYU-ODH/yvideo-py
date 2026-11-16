#!/bin/bash
# Install the booth's Chrome kiosk unit and render its Chrome policy.
# Run as the booth's deploy user, from its graphical session (see DEPLOY_IC.md).
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/../common.sh"

require_non_root_user
load_deploy_env
require_deploy_user_context

if [ "$IC_BOOTH" != "1" ]; then
    die "IC_BOOTH=1 is not set in .env; this checkout is not a booth"
fi

root="$(repo_root)"
unit_dir="$(user_systemd_dir)"
policy="$root/var/ic/chrome-policy.json"
mkdir -p "$unit_dir" "$(dirname "$policy")"

IC_CHROME_FLAGS="${IC_CHROME_FLAGS:-}"
render_template "$script_dir/ic-kiosk.service.in" "$unit_dir/$APP_NAME-ic-kiosk.service"
sed -i "s|@IC_CHROME_FLAGS@|$(escape_sed_replacement "$IC_CHROME_FLAGS")|g" \
    "$unit_dir/$APP_NAME-ic-kiosk.service"
render_template "$script_dir/chrome-policy.json.in" "$policy"

systemctl --user daemon-reload
systemctl --user enable "$APP_NAME-ic-kiosk.service"

cat <<MSG
Installed $unit_dir/$APP_NAME-ic-kiosk.service

Finish as an administrator (once):
  sudo install -D -m 644 $policy /etc/opt/chrome/policies/managed/ic.json

Then log out and back in (or: systemctl --user start $APP_NAME-ic-kiosk.service).
MSG
