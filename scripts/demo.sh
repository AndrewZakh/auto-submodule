#!/bin/sh
# A disposable sandbox that road-tests submodule-hook end to end:
# onboarding from a fresh clone, a clean commit, a conscious bump,
# a dangling push, a stale config, and a submodule force-push.
# Nothing outside the sandbox directory is touched.
#
# Usage: ./scripts/demo.sh [dir]     (default: a fresh temp directory)
# Requires the `submodule-hook` command on PATH (pip install .).

set -eu

command -v submodule-hook >/dev/null 2>&1 || {
    echo "submodule-hook не найден в PATH:" >&2
    echo "    python3 -m venv .venv && . .venv/bin/activate && pip install ." >&2
    exit 1
}

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${1:-$(mktemp -d /tmp/submodule-hook-demo.XXXXXX)}
echo "песочница: $ROOT"

title() { printf '\n\n=== %s ===\n' "$1"; }
run()   { printf '\n$ %s\n' "$*"; "$@"; }
fail()  { printf '\n$ %s    <- должен заблокировать\n' "$*"; if "$@"; then
              echo "ОШИБКА: должно было заблокироваться" >&2; exit 1; fi; }
pin()   { printf 'submodules:\n  - folder: libs/api-clients\n    commit_sha: "%s"\n    lookup_branch: master\n' \
              "$1" > "$2/env.yml"; }

# The sandbox remotes are local paths: git >= 2.38 blocks the file transport for
# submodule clones by default (real ssh/https remotes are unaffected), and the
# demo commits need an identity.
git() { command git \
    -c protocol.file.allow=always \
    -c user.email=demo@example.com \
    -c user.name=Demo \
    "$@"; }

# --- 0. infrastructure ------------------------------------------------------

title "0. песочница: bare-источники и первичное состояние проекта"

git init --bare -q "$ROOT/sub_origin.git"
git -C "$ROOT/sub_origin.git" symbolic-ref HEAD refs/heads/master
git init --bare -q "$ROOT/origin.git"
git -C "$ROOT/origin.git" symbolic-ref HEAD refs/heads/master

SEED="$ROOT/seed"
git init -q "$SEED"
git -C "$SEED" symbolic-ref HEAD refs/heads/master
echo "lib: v0" > "$SEED/lib.txt"
git -C "$SEED" add -A
git -C "$SEED" commit -qm "lib: v0"
git -C "$SEED" remote add origin "$ROOT/sub_origin.git"
git -C "$SEED" push -q origin master
S0=$(git -C "$SEED" rev-parse HEAD)

BOOT="$ROOT/boot"
git init -q "$BOOT"
git -C "$BOOT" symbolic-ref HEAD refs/heads/master
echo base > "$BOOT/README.md"
git -C "$BOOT" add -A
git -C "$BOOT" commit -qm "c1"
git -C "$BOOT" submodule add "$ROOT/sub_origin.git" libs/api-clients
pin "$S0" "$BOOT"
git -C "$BOOT" add -A
git -C "$BOOT" commit -qm "c2: submodule + env.yml"
git -C "$BOOT" remote add origin "$ROOT/origin.git"
git -C "$BOOT" push -q origin master
rm -rf "$SEED" "$BOOT"

# --- dev A -------------------------------------------------------------------

title "1. dev A: свежий клон, инициализация сабмодуля, установка хуков"

A="$ROOT/a"
run git clone -q "$ROOT/origin.git" "$A"
run git -C "$A" submodule update --init
run "$SCRIPT_DIR/install.sh" "$A"
SUB="$A/libs/api-clients"

title "2. обычный коммит: пин, указатель и master синхронны"

echo "заметка" >> "$A/README.md"
run git -C "$A" add README.md
run git -C "$A" commit -m "A: обычная работа"

title "3. осознанный бамп: коммит сабмодуля -> пуш сабмодуля -> конфиг+указатель одним коммитом"

echo "lib: v1" > "$SUB/lib.txt"
run git -C "$SUB" add -A
run git -C "$SUB" commit -qm "lib: v1"
run git -C "$SUB" push -q origin HEAD:refs/heads/master
S1=$(git -C "$SUB" rev-parse HEAD)
run git -C "$A" add libs/api-clients
pin "$S1" "$A"
run git -C "$A" add env.yml
run git -C "$A" commit -m "A: осознанный бамп сабмодуля"
run git -C "$A" push origin master

title "4. бамп без пуша сабмодуля: pre-commit пропустит, pre-push заблокирует"

echo "lib: v2" > "$SUB/lib.txt"
git -C "$SUB" add -A
git -C "$SUB" commit -qm "lib: v2 (локально)"
S2=$(git -C "$SUB" rev-parse HEAD)
git -C "$A" add libs/api-clients
pin "$S2" "$A"
git -C "$A" add env.yml
run git -C "$A" commit -m "A: бамп, сабмодуль ещё не запушен"
fail git -C "$A" push origin master
printf '\n-- чиним: пушим сабмодуль, затем суперпроект\n'
run git -C "$SUB" push -q origin HEAD:refs/heads/master
run git -C "$A" push origin master

# --- dev B -------------------------------------------------------------------

title "5. dev B: свежий клон, ветка от прошлого состояния, подтянул сабмодуль — конфиг отстал"

B="$ROOT/b"
run git clone -q "$ROOT/origin.git" "$B"
run git -C "$B" submodule update --init
run "$SCRIPT_DIR/install.sh" "$B"
BSUB="$B/libs/api-clients"
run git -C "$B" checkout -qb feature/cool origin/master~1
run git -C "$B" submodule update -q
run git -C "$BSUB" fetch -q origin
run git -C "$BSUB" checkout -q --detach "$S2"
run git -C "$B" add libs/api-clients
echo "работа" >> "$B/README.md"
git -C "$B" add README.md
fail git -C "$B" commit -m "B: работа с новым сабмодулем, конфиг не обновил"
printf '\n-- чиним: обновляем конфиг под фактический коммит\n'
pin "$S2" "$B"
git -C "$B" add env.yml
run git -C "$B" commit -m "B: конфиг подтянут под сабмодуль"
run git -C "$B" push -q origin feature/cool

# --- history rewrite in the submodule ----------------------------------------

title "6. мейнтейнер сабмодуля переписал историю: v2 исчез из remote"

run git -C "$SUB" push -qf origin "$S1:refs/heads/master"
echo "ещё работа" >> "$A/README.md"
git -C "$A" add README.md
run git -C "$A" commit -m "A: обычная работа после force-push в сабмодуле"
fail git -C "$A" push origin master
printf '\n-- чиним: откатываем сабмодуль и конфиг на существующий коммит\n'
run git -C "$SUB" checkout -q --detach "$S1"
run git -C "$A" add libs/api-clients
pin "$S1" "$A"
git -C "$A" add env.yml
run git -C "$A" commit -m "A: откат сабмодуля на существующий коммит"
run git -C "$A" push origin master

# -----------------------------------------------------------------------------

title "все сценарии отработали"
echo "песочница осталась здесь: $ROOT"
