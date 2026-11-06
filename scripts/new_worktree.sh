#!/bin/bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: bash scripts/new_worktree.sh BRANCH [PATH] [--base=REF] [--no-db]

Creates a git worktree for BRANCH and prepares it for local development:
- symlinks untracked local files (node_modules, secret settings, .env, etc.)
  from the main worktree
- runs uv sync --dev
- creates a fresh SQLite database with migrate and seed_demo_data

If BRANCH exists locally or on origin it is checked out; otherwise it is
created from --base (default: main).

Arguments:
  BRANCH          Branch to check out or create.
  PATH            Worktree location. Defaults to a sibling of the main
                  worktree named after BRANCH (slashes become dashes).

Options:
  --base=REF      Starting point for a new branch. Default: main.
  --no-db         Skip migrate and seed_demo_data.
  -h, --help      Show this help text.
EOF
}

branch=""
worktree_path=""
base="main"
setup_db=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base=*)
            base="${1#*=}"
            ;;
        --base)
            shift
            if [[ $# -eq 0 ]]; then
                echo "--base requires a ref argument." >&2
                exit 1
            fi
            base="$1"
            ;;
        --no-db)
            setup_db=false
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
        *)
            if [[ -z "$branch" ]]; then
                branch="$1"
            elif [[ -z "$worktree_path" ]]; then
                worktree_path="$1"
            else
                echo "Unexpected argument: $1" >&2
                usage >&2
                exit 1
            fi
            ;;
    esac
    shift
done

if [[ -z "$branch" ]]; then
    usage >&2
    exit 1
fi

# The main worktree holds the canonical copies of untracked local files, even
# when this script is run from another worktree.
main_root="$(cd "$(git rev-parse --path-format=absolute --git-common-dir)/.." && pwd)"

if [[ -z "$worktree_path" ]]; then
    worktree_path="$(dirname "$main_root")/${branch//\//-}"
fi

if [[ -e "$worktree_path" ]]; then
    echo "'$worktree_path' already exists." >&2
    exit 1
fi

git -C "$main_root" fetch --quiet origin || echo "WARNING: git fetch failed; using local refs." >&2

if git -C "$main_root" show-ref --verify --quiet "refs/heads/$branch"; then
    git -C "$main_root" worktree add "$worktree_path" "$branch"
elif git -C "$main_root" show-ref --verify --quiet "refs/remotes/origin/$branch"; then
    git -C "$main_root" worktree add --track -b "$branch" "$worktree_path" "origin/$branch"
else
    git -C "$main_root" worktree add -b "$branch" "$worktree_path" "$base"
fi

worktree_path="$(cd "$worktree_path" && pwd)"

shared_paths=(
    node_modules
    IC_player/node_modules
    .env
    yvideo/secret_settings.py
    scripts/dump_legacy_to_sqlite_settings.py
    .claude/settings.local.json
)

for rel in "${shared_paths[@]}"; do
    src="$main_root/$rel"
    dest="$worktree_path/$rel"
    if [[ ! -e "$src" ]]; then
        echo "Skipping $rel (not present in $main_root)."
        continue
    fi
    if [[ -e "$dest" || -L "$dest" ]]; then
        echo "Skipping $rel (already exists in worktree)."
        continue
    fi
    mkdir -p "$(dirname "$dest")"
    ln -s "$src" "$dest"
    echo "Linked $rel"
done

for lockfile in package-lock.json IC_player/package-lock.json; do
    if ! cmp -s "$main_root/$lockfile" "$worktree_path/$lockfile"; then
        echo "WARNING: $lockfile differs from $main_root; the shared node_modules may not match this branch." >&2
    fi
done

cd "$worktree_path"

# An activated venv from another worktree would make uv warn and ignore it.
unset VIRTUAL_ENV

uv sync --dev

if [[ "$setup_db" == true ]]; then
    uv run manage.py migrate
    uv run manage.py seed_demo_data
fi

echo
echo "Worktree ready at $worktree_path"
echo "  cd $worktree_path"
echo "  uv run manage.py runserver 8001  # pick a port not used by other worktrees"
echo "Remove with: git worktree remove $worktree_path"
