#!/bin/sh
# Development hooks for this repository itself: the sibling auto-version-hook
# (https://github.com/AndrewZakh/auto-version-hook) bumps the version in
# env.yml, then our own submodule-hook checks run — the tools dogfood their
# own combination. Requires both commands on PATH:
#
#     python3 -m venv .venv && . .venv/bin/activate   # Python 3.11+
#     pip install -e ".[dev]" git+https://github.com/AndrewZakh/auto-version-hook.git
#     ./scripts/install-dev-hooks.sh

set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
HOOKS_DIR=$(git -C "$REPO_DIR" rev-parse --git-path hooks)
case $HOOKS_DIR in
    /*) ;;
    *) HOOKS_DIR="$REPO_DIR/$HOOKS_DIR" ;;
esac

write_hook() {
    hook=$1
    body=$2
    path="$HOOKS_DIR/$hook"
    if [ -f "$path" ]; then
        cp "$path" "$path.bak"
        echo "existing $hook hook backed up to $path.bak"
    fi
    printf '%s\n' "$body" > "$path"
    chmod +x "$path"
    echo "installed $hook: $path"
}

write_hook pre-commit '#!/bin/sh
# combined: bump the version first (it stages env.yml), then our own checks
auto-version-hook "$@" || exit $?
exec submodule-hook "$@"'

write_hook pre-push '#!/bin/sh
# our own dangling-commit check; git passes the remote name and URL as $1/$2
exec submodule-hook --push'
