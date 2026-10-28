#!/bin/sh
# Install submodule-hook as git pre-commit and pre-push hooks.
#
# Usage: ./scripts/install.sh [repo-path]
# (default: the repository containing the current directory)
#
# pre-commit keeps submodules on the commits pinned in env.yml;
# pre-push blocks pushes that reference submodule commits missing
# from the submodules' remotes.
#
# Requires the `submodule-hook` command to be on PATH (pip install .).

set -eu

REPO_DIR=${1:-$(git rev-parse --show-toplevel)}
REPO_DIR=$(cd "$REPO_DIR" && pwd)

install_hook() {
    hook=$1
    body=$2
    # --git-path may answer relative to the repository, not to the cwd
    hook_path=$(git -C "$REPO_DIR" rev-parse --git-path "hooks/$hook")
    case $hook_path in
        /*) ;;
        *) hook_path="$REPO_DIR/$hook_path" ;;
    esac
    if [ -f "$hook_path" ]; then
        cp "$hook_path" "$hook_path.bak"
        echo "existing $hook hook backed up to $hook_path.bak"
    fi
    printf '%s\n' "$body" > "$hook_path"
    chmod +x "$hook_path"
    echo "installed $hook hook: $hook_path"
}

install_hook pre-commit '#!/bin/sh
# installed by submodule-hook
exec submodule-hook "$@"'

install_hook pre-push '#!/bin/sh
# installed by submodule-hook; git passes the remote name and URL as $1/$2,
# the pushed refs arrive on stdin
exec submodule-hook --push'
