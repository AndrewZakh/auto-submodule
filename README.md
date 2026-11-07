# submodule-hook

[![tests](https://github.com/AndrewZakh/auto-submodule/actions/workflows/tests.yml/badge.svg)](https://github.com/AndrewZakh/auto-submodule/actions/workflows/tests.yml)
[![version-check](https://github.com/AndrewZakh/auto-submodule/actions/workflows/version-check.yml/badge.svg)](https://github.com/AndrewZakh/auto-submodule/actions/workflows/version-check.yml)

Git hooks in Python (pre-commit + pre-push): they keep submodules on the commits
pinned in `env.yml`, never let the pin and the pointer drift apart, and keep
dangling submodule commits out of the remote. The ideological successor to
[auto-version-hook](https://github.com/AndrewZakh/auto-version-hook): the same
stack (Python + PyYAML, nothing else), the same exit codes, the same
`scripts/install.sh`.

Why: a submodule "drifts" when a developer switches it (or pulls master) while
the `env.yml` pin stays old — or the other way around, the config is updated but
the submodule is not. The invariant the hook enforces: **the commit pinned in
`env.yml` and the submodule commit recorded in the branch always match;
switching a submodule is always two changes in one commit (submodule + config)**.

## How it works

Three SHAs per submodule:

```
env.yml → submodules[].commit_sha ... CONFIG    what the config pins
git index → <folder> ............... CURRENT    what this commit will carry
origin/<lookup_branch> → <folder> .. TARGET     what the protected branch has now
```

On every commit:

1. merge commits are skipped (`skip_merges`, true by default);
2. no `submodules:` section in `env.yml` — the hook quietly exits (code 0);
3. lookup branches are updated (`git fetch <remote> <lookup_branch>`, once per
   unique branch), `TARGET` is read via `git ls-tree`;
4. `CURRENT` is read from the superproject index — exactly what this commit will pin;
5. hard check: `CONFIG` against `CURRENT` — a mismatch blocks the commit;
6. soft check: `CONFIG` against `TARGET` — a mismatch warns.

Decision table:

| `CONFIG` vs `CURRENT` | `CONFIG` vs `TARGET` | Verdict |
|---|---|---|
| match | match | ✅ "submodule unchanged, staying on the same commit" |
| match | differ | ⚠️ "after this branch merges, the submodule in `<lookup_branch>` will switch: `TARGET` → `CONFIG`" |
| differ | match | ❌ block: switch the submodule (ready-made command in the message) or update the config |
| differ | differ | ❌ block + the warning above; if `CURRENT == TARGET` — a hint: "the config is stale: update `commit_sha`, do not roll the submodule back" |

SHAs are compared by prefix: a short `12351adf` in the config equals the full
`12351adf…` in the branch. Hex SHAs from 4 to 40 characters are valid; tags and
branch names are not accepted.

The second half of the tool is the pre-push hook against dangling submodule
commits — see "Dangling commits (pre-push)".

## Hook messages

Error — the config and the branch diverged (the commit is blocked, exit code 1):

```
$ git commit -m "feature work"
submodule-hook: libs/api-clients — config and branch diverged
    env.yml pins 12351adf, the commit will carry 9a4c02e4
    switch the submodule to the pinned commit:
        cd libs/api-clients && git switch --detach 12351adf && git add libs/api-clients
    or, if the switch is intentional, update the config: commit_sha: 9a4c02e4…
    hint: 9a4c02e4 is the current commit in origin/master — the config is simply
    stale; in that case update commit_sha and do not roll the submodule back
```

The origin/master hint appears only when `CURRENT == TARGET` — the branch
carries exactly what the protected branch already has, and following the switch
command above would roll the submodule back by mistake.

Warning — merging this branch will switch the submodule in the protected branch
(the commit passes):

```
$ git commit -m "bump api-clients"
submodule-hook: libs/api-clients — pinned 12351adf
submodule-hook: libs/api-clients — after this branch merges, the submodule in master
    will switch: 9a4c02e4 -> 12351adf
```

All good:

```
$ git commit -m "feature work"
submodule-hook: libs/api-clients — unchanged, staying on 12351adf (as in master)
```

## Dangling commits (pre-push)

Pre-commit guarantees the pin and the pointer travel together, but it cannot
know whether the submodule commit exists on the submodule's own remote: a
developer may forget to push the submodule (or the submodule maintainer
force-pushes it away) — and the superproject ships a gitlink pointing at
nothing; cloning and CI break for everyone else.

That is why `install.sh` installs a second hook — pre-push
(`submodule-hook --push`). Before a push it verifies, for every submodule
pinned in `env.yml`, that all submodule commits referenced by the pushed
commits and by the pushed branch tips are reachable from a branch on the
submodule's remote:

```
git -C <folder> fetch && git -C <folder> branch -r --contains <sha>
```

The push is blocked with a fix command:

```
$ git push
submodule-hook: libs/api-clients: commit e38cb215 (commit c3a1b2c3) is missing from
    the submodule's remote — not pushed or deleted by a force-push;
    push the submodule: git -C libs/api-clients push
    or switch the pointer to an existing commit and update env.yml
submodule-hook: push blocked — dangling submodule commits (fix commands above)
```

Notes:

- only submodules pinned in the working copy's `env.yml` are checked; gitlinks
  of unpinned submodules are invisible to the hook — pin them in the config;
- submodule not initialized — the check is skipped with a warning (fail-open,
  the push is not blocked);
- offline and the commit is unknown even locally — skipped with a warning;
- `on_error: warn` turns the block into a warning;
- a successful push is silent: pre-push prints only warnings and errors;
- the built-in alternative is `git config push.recurseSubmodules check`: the
  same idea, but configured separately in every clone and unaware of env.yml pins.

## Installation

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install .          # provides the submodule-hook command
./scripts/install.sh   # installs .git/hooks/pre-commit and .git/hooks/pre-push
```

`scripts/install.sh` takes a repository path as an argument and backs up
existing hooks.

Manual variant — the file `.git/hooks/pre-commit`:

```sh
#!/bin/sh
exec submodule-hook "$@"
```

and `.git/hooks/pre-push` (git passes the remote name and URL as arguments —
the hook ignores them; the pushed refs arrive on stdin):

```sh
#!/bin/sh
exec submodule-hook --push
```

Variant for the [pre-commit framework](https://pre-commit.com) (local hooks,
no environment isolation):

```yaml
repos:
  - repo: local
    hooks:
      - id: submodule-hook
        name: submodule pin check
        language: system
        entry: submodule-hook
        always_run: true
        pass_filenames: false
      - id: submodule-push
        name: submodule dangling check
        language: system
        entry: submodule-hook --push
        stages: [pre-push]
        always_run: true
        pass_filenames: false
```

## Configuration

Everything lives in `env.yml` itself, next to the `submodules:` section — the
hook has no separate config file. A full example — [env.example.yml](env.example.yml):

```yaml
submodules:
  - folder: path/to/submodule_1   # submodule path from the repo root, as in .gitmodules
    commit_sha: "12351adf"        # the commit the submodule must point to
    lookup_branch: master         # the protected branch where merges land

submodule_hook:                   # optional hook tuning (defaults shown)
  remote: origin
  fetch: true
  on_error: fail
  skip_merges: true
```

Submodule fields:

- `folder` — path from the repository root; validated to be a stage-0 index
  entry with mode 160000, otherwise a config error; duplicate paths are rejected;
- `commit_sha` — a hex SHA (4–40 characters, short ones are fine);
- `lookup_branch` — branch name on the remote without the `origin/` prefix;
  defaults to `master`.

`submodule_hook` fields:

- `remote` (origin) — where to fetch lookup branches from;
- `fetch` (true) — update lookup branches on every run; `--no-fetch` works
  offline using existing remote-tracking refs;
- `on_error` (fail) — `warn` lets the commit through with a warning
  (transition period);
- `skip_merges` (true) — do not act on merge commits.

The config is validated on every run. The file path can be overridden with
`--config` or the `SUBMODULE_HOOK_CONFIG` environment variable. Other
arguments: `--remote`, `--no-fetch`, `--on-error {fail,warn}`, the utility
`--repo`, `-v`, `-q`. There is no dry-run: the hook never writes anything —
it only reads and delivers a verdict.

Exit codes: `0` — fine (warnings allowed); `1` — the config and a submodule
diverged, the commit is blocked (not with `on_error: warn`); `2` — config
problems.

## Behavior the team should know

- **The index is the source of truth.** The hook compares both the pinned
  commit and the submodule state against the index — that is, what will
  actually land in the commit. If the working copy diverged from the index
  (`env.yml` edited or the submodule checkout switched without `git add`) —
  a warning with the ready-made command.
- **The hook changes nothing.** It only reads and blocks. Repairs are always
  manual — the command is part of the error message.
- **Pulling master into a feature branch.** The merge commit is skipped
  (`skip_merges`); if the submodule drifted away from `CONFIG` afterwards,
  it surfaces on the first regular commit — with the hint "update
  `commit_sha`", not with a submodule rollback.
- **Offline.** Fetch failed — `TARGET` is taken from the local remote-tracking
  ref; if there is none either, the soft check is skipped with a warning. The
  hard check is local and always works.
- **Uninitialized submodule** (`git submodule status` with a minus) — the
  checks still work: the superproject index has the link; the checkout is not
  compared.
- **`git commit --no-verify`** bypasses pre-commit locally, and
  `git push --no-verify` bypasses pre-push; a server-side CI validator is
  task `submodule/1` in [tasks.yml](tasks.yml).

## This project runs on its own tools

The repository eats its own dog food. The version lives in [env.yml](env.yml)
and is bumped by [auto-version-hook](https://github.com/AndrewZakh/auto-version-hook)
(config — [version-hook.yml](version-hook.yml), trunk-based: `feature/*` raise
the minor, `patch/*` the patch, `tech|chore|docs|ci` never bump); the package
version in `pyproject.toml` is mirrored automatically (`version_files`). Both
tools live in one [.pre-commit-config.yaml](.pre-commit-config.yaml): first
auto-version-hook updates and stages `env.yml`/`pyproject.toml`, then the
submodule checks quietly pass (this `env.yml` has no `submodules:` section —
the project has no submodules of its own, and the hook correctly does nothing).
The server half of version validation is CI —
[version-check.yml](.github/workflows/version-check.yml); release tags are
placed by [release-tag.yml](.github/workflows/release-tag.yml).

Project tasks are tracked in [tasks.yml](tasks.yml) via
[jearless](https://github.com/AndrewZakh/jearless) — a git-native tracker:
status is never stored, it is derived from branches and merge commits:

```sh
jear sprint                       # available / in_progress / done
jear add submodule "summary"      # a new task (the registry language is English)
jear take submodule 1             # taking a task = pushing branch feature/submodule/1-…
```

Development hooks are installed by `scripts/install-dev-hooks.sh`
(pre-commit framework).

## Development

```sh
python3 -m venv .venv && . .venv/bin/activate   # Python 3.11+ (needed by
                                                # auto-version-hook; the package
                                                # itself supports 3.9+)
pip install -e ".[dev]" \
    git+https://github.com/AndrewZakh/auto-version-hook.git \
    git+https://github.com/AndrewZakh/jearless.git
pytest
./scripts/install-dev-hooks.sh   # auto-version + own checks in one pre-commit
```

Tests build throwaway git repositories with real submodules (a bare source
repo + a superproject) and run the hook end to end: all four rows of the
decision table, offline, merge commits, short SHAs, dangling pushes —
including a real `git push` through an installed hook.

To road-test the tool on live scenarios (onboarding from a fresh clone, a
conscious bump, a dangling push, a stale config, a force-push in the
submodule) — in a disposable sandbox with two "developers":

```sh
. .venv/bin/activate
./scripts/demo.sh
```
