#!/bin/sh
# Development hooks for this repository itself, via the pre-commit framework:
# auto-version-hook (env.yml bump + pyproject mirror) and our own submodule
# checks. Requires pre-commit, auto-version-hook and submodule-hook on PATH:
#
#     python3 -m venv .venv && . .venv/bin/activate   # Python 3.11+
#     pip install -e ".[dev]" \
#         git+https://github.com/AndrewZakh/auto-version-hook.git \
#         git+https://github.com/AndrewZakh/jearless.git
#     ./scripts/install-dev-hooks.sh

set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

command -v pre-commit >/dev/null 2>&1 || {
    echo "pre-commit not found on PATH: pip install -e \".[dev]\"" >&2
    exit 1
}

cd "$REPO_DIR"
# --overwrite: this repo's hooks are reproducibly installed by this script
pre-commit install --overwrite --hook-type pre-commit --hook-type pre-push
