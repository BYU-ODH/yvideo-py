#!/bin/bash
set -euo pipefail

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

deploy_script_dir() {
    cd "$(dirname "${BASH_SOURCE[0]}")" && pwd
}

repo_root() {
    cd "$(deploy_script_dir)/.." && pwd
}

quadlet_dir() {
    printf '%s\n' "${QUADLET_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/containers/systemd}"
}

user_systemd_dir() {
    printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
}

container_service_name() {
    printf '%s.service\n' "$APP_NAME"
}

deploy_service_name() {
    printf '%s-deploy.service\n' "$APP_NAME"
}

deploy_timer_name() {
    printf '%s-deploy.timer\n' "$APP_NAME"
}

build_image_tag() {
    printf 'localhost/%s:latest' "$APP_NAME"
}

require_non_root_user() {
    if [ "$(id -u)" -eq 0 ]; then
        die "refusing to run as root; use the deployment user so the rootless Quadlets land in that user's home directory"
    fi
}

validate_user_or_app_name() {
    case "$1" in
        "" | *[!a-z0-9-]*)
            die "DEPLOY_USER and APP_NAME must contain only lowercase letters, numbers, and hyphens"
            ;;
    esac
}

validate_port() {
    case "$1" in
        "" | *[!0-9]*)
            die "HOST_PORT must be an integer between 5000 and 65535"
            ;;
    esac

    if [ "$1" -lt 5000 ] || [ "$1" -gt 65535 ]; then
        die "HOST_PORT must be an integer between 5000 and 65535"
    fi
}

validate_positive_int() {
    local value="$1"
    local name="$2"

    case "$value" in
        "" | *[!0-9]* | 0)
            die "$name must be a positive integer"
            ;;
    esac
}

load_deploy_env() {
    local env_file="${1:-$(repo_root)/.env}"

    if [ ! -f "$env_file" ]; then
        die "missing $env_file; copy .env_template to .env first"
    fi

    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a

    : "${DEPLOY_USER:?DEPLOY_USER is required in $env_file}"
    : "${APP_NAME:?APP_NAME is required in $env_file}"
    : "${HOST_PORT:?HOST_PORT is required in $env_file}"

    BRANCH="${BRANCH:-}"
    WORKERS="${WORKERS:-2}"
    THREADS="${THREADS:-2}"
    # International Cinema (DEPLOY_IC.md). Production sets IC_MEDIA_WORKER=1; the
    # booth sets IC_BOOTH=1.
    IC_MEDIA_WORKER="${IC_MEDIA_WORKER:-0}"
    IC_BOOTH="${IC_BOOTH:-0}"
    IC_PROJECTION_ROOT="${IC_PROJECTION_ROOT:-$(repo_root)/ic_projection}"
    IC_PLAYBACK_HOURS="${IC_PLAYBACK_HOURS:-12-24}"

    validate_user_or_app_name "$DEPLOY_USER"
    validate_user_or_app_name "$APP_NAME"
    validate_port "$HOST_PORT"
    validate_positive_int "$WORKERS" "WORKERS"
    validate_positive_int "$THREADS" "THREADS"
    if ! [[ "$IC_PLAYBACK_HOURS" =~ ^([01]?[0-9]|2[0-4])-([01]?[0-9]|2[0-4])$ ]]; then
        die "IC_PLAYBACK_HOURS must look like 12-24 (start-end hours)"
    fi

    export DEPLOY_USER
    export APP_NAME
    export HOST_PORT
    export BRANCH
    export WORKERS
    export THREADS
    export IC_MEDIA_WORKER
    export IC_BOOTH
    export IC_PROJECTION_ROOT
    export IC_PLAYBACK_HOURS
}

# True on a booth (IC_BOOTH=1) during IC_PLAYBACK_HOURS ("start-end", local time, start
# inclusive, end exclusive), when the deploy timer must leave the kiosk alone.
in_ic_playback_hours() {
    [ "${IC_BOOTH:-0}" = "1" ] || return 1
    local start="${IC_PLAYBACK_HOURS%-*}" end="${IC_PLAYBACK_HOURS#*-}" hour
    hour=$((10#$(date +%H)))
    if [ "$start" -le "$end" ]; then
        [ "$hour" -ge "$start" ] && [ "$hour" -lt "$end" ]
    else
        # Wraps past midnight, e.g. 18-2.
        [ "$hour" -ge "$start" ] || [ "$hour" -lt "$end" ]
    fi
}

# Extra container units this checkout runs alongside $APP_NAME, one per line.
ic_unit_names() {
    if [ "${IC_MEDIA_WORKER:-0}" = "1" ]; then
        printf '%s\n' "$APP_NAME-media-worker"
    fi
    if [ "${IC_BOOTH:-0}" = "1" ]; then
        printf '%s\n' "$APP_NAME-ic-sync-agent"
    fi
}


require_deploy_user_context() {
    local current_user root

    current_user="$(id -un)"
    root="$(repo_root)"

    if [ "$current_user" != "$DEPLOY_USER" ]; then
        die "this checkout is configured for DEPLOY_USER=$DEPLOY_USER but is being managed by $current_user"
    fi

    if [ ! -O "$root" ]; then
        die "repo root $root must be owned by $current_user"
    fi
}

escape_sed_replacement() {
    printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'
}

render_template() {
    local template="$1"
    local output="$2"
    local root

    root="$(repo_root)"
    mkdir -p "$(dirname "$output")"

    # The @DEPLOY_HOME@ substitution below exists LEGACY MIGRATION ONLY, to
    # mount ~/.ssh into the container (see deploy/quadlet.container.in).
    # Delete it when legacy_migration is removed, unless another template
    # variable has started using it by then. See
    # core/legacy_migration/REMOVAL.md for the full checklist.
    sed \
        -e "s|@APP_NAME@|$(escape_sed_replacement "$APP_NAME")|g" \
        -e "s|@BUILD_IMAGE_TAG@|$(escape_sed_replacement "$(build_image_tag)")|g" \
        -e "s|@HOST_PORT@|$(escape_sed_replacement "$HOST_PORT")|g" \
        -e "s|@REPO_ROOT@|$(escape_sed_replacement "$root")|g" \
        -e "s|@DEPLOY_HOME@|$(escape_sed_replacement "$HOME")|g" \
        -e "s|@WORKERS@|$(escape_sed_replacement "$WORKERS")|g" \
        -e "s|@THREADS@|$(escape_sed_replacement "$THREADS")|g" \
        -e "s|@IC_PROJECTION_ROOT@|$(escape_sed_replacement "$IC_PROJECTION_ROOT")|g" \
        "$template" >"$output"
}
