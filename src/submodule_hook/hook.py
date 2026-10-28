"""The pre-commit check itself: three SHAs per submodule, one verdict.

CONFIG  — the commit pinned in env.yml (submodules[].commit_sha)
CURRENT — the submodule gitlink in the index, i.e. what this commit records
TARGET  — the submodule gitlink at origin/<lookup_branch>

CONFIG != CURRENT blocks the commit (the fix command is part of the message);
CONFIG != TARGET is a warning: merging this branch will switch the submodule
in the protected branch.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import gittools
from .config import AppConfig, SubmoduleSpec, parse_config
from .gittools import GitError

log = logging.getLogger("submodule_hook")

GITLINK_MODE = "160000"
ZERO_SHA = "0" * 40


class Reporter:
    """info -> stdout (suppressed by -q); warnings and errors -> stderr, always."""

    def __init__(self, quiet: bool = False, out=None, err=None):
        self.quiet = quiet
        self._out = out if out is not None else sys.stdout
        self._err = err if err is not None else sys.stderr

    def info(self, message: str) -> None:
        if not self.quiet:
            print(message, file=self._out)

    def warning(self, message: str) -> None:
        print(message, file=self._err)

    def error(self, message: str) -> None:
        print(message, file=self._err)


@dataclass
class HookResult:
    ok: bool = True
    hard_failures: List[str] = field(default_factory=list)
    skipped: bool = False


@dataclass
class _Lookup:
    remote: str
    branch: str
    ref: Optional[str]  # remote-tracking ref; None when the branch is unavailable

    @property
    def display(self) -> str:
        return f"{self.remote}/{self.branch}"


def run_hook(cfg: AppConfig, repo: Path, reporter: Reporter) -> HookResult:
    if cfg.hook.skip_merges and gittools.is_merge_in_progress(repo):
        log.debug("merge in progress, skipping (submodule_hook.skip_merges)")
        return HookResult(ok=True, skipped=True)

    cfg = _effective_config(cfg, repo, reporter)
    if not cfg.submodules:
        return HookResult()

    current = _validate_against_index(cfg, repo)
    lookups = _resolve_lookups(cfg, repo)

    hard_failures: List[str] = []
    unavailable: Dict[str, List[str]] = {}
    for spec in cfg.submodules:
        sha_current = current[spec.folder]
        lookup = lookups[spec.lookup_branch]
        target: Optional[str] = None
        target_known = lookup.ref is not None
        if target_known:
            entry = gittools.tree_gitlink(repo, lookup.ref, spec.folder)
            if entry is not None and entry[0] == GITLINK_MODE:
                target = entry[1]
        if not _report_submodule(spec, sha_current, target, target_known, lookup, reporter):
            hard_failures.append(spec.folder)
        if not target_known:
            unavailable.setdefault(lookup.display, []).append(spec.folder)
        _warn_checkout_drift(spec, sha_current, repo, reporter)

    for display, folders in unavailable.items():
        reporter.warning(
            f"submodule-hook: {display} недоступна (fetch не удался или ветки нет на remote) — "
            f"мягкая проверка пропущена для: {', '.join(folders)}"
        )

    return HookResult(ok=not hard_failures, hard_failures=hard_failures)


def _effective_config(cfg: AppConfig, repo: Path, reporter: Reporter) -> AppConfig:
    """The index copy of env.yml wins: it is what this commit will carry."""
    rel = cfg.source_rel
    if not rel or rel.startswith(".."):
        return cfg
    staged = gittools.staged_text(repo, rel)
    if staged is None:
        return cfg
    worktree: Optional[str] = None
    if cfg.source is not None and cfg.source.is_file():
        try:
            worktree = cfg.source.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - raced deletion
            worktree = None
    if worktree is None or worktree == staged:
        return cfg
    effective = parse_config(staged, f"index:{rel}")
    effective.hook = cfg.hook  # command-line overrides keep winning
    effective.source, effective.source_rel = cfg.source, cfg.source_rel
    reporter.warning(
        f"submodule-hook: {rel} изменён, но не в индексе — проверяю версию из индекса; "
        f"если правка нужна в коммите: git add {rel}"
    )
    return effective


def _validate_against_index(cfg: AppConfig, repo: Path) -> Dict[str, str]:
    """Every pinned folder must be a submodule in the index; returns its sha."""
    current: Dict[str, str] = {}
    for spec in cfg.submodules:
        if spec.lookup_branch.startswith(f"{cfg.hook.remote}/"):
            raise _config_error(
                spec,
                f"lookup_branch must not repeat the remote prefix: "
                f"{spec.lookup_branch!r} (укажи просто {spec.lookup_branch.split('/', 1)[1]!r})",
            )
        entry = gittools.index_gitlink(repo, spec.folder)
        if entry is None:
            raise _config_error(spec, "нет такой записи в индексе — folder должен указывать на сабмодуль")
        mode, sha = entry
        if mode != GITLINK_MODE:
            raise _config_error(spec, f"запись в индексе не сабмодуль (mode {mode})")
        current[spec.folder] = sha
    return current


def _resolve_lookups(cfg: AppConfig, repo: Path) -> Dict[str, _Lookup]:
    lookups: Dict[str, _Lookup] = {}
    for spec in cfg.submodules:
        if spec.lookup_branch in lookups:
            continue
        remote, branch = cfg.hook.remote, spec.lookup_branch
        if cfg.hook.fetch:
            try:
                gittools.fetch(repo, remote, branch)
            except GitError as exc:
                log.debug("fetch %s/%s failed, falling back to the local ref: %s", remote, branch, exc)
        ref = f"refs/remotes/{remote}/{branch}"
        lookups[branch] = _Lookup(
            remote, branch, ref if gittools.ref_exists(repo, ref) else None
        )
    return lookups


def _report_submodule(
    spec: SubmoduleSpec,
    sha_current: str,
    sha_target: Optional[str],
    target_known: bool,
    lookup: _Lookup,
    reporter: Reporter,
) -> bool:
    folder, pinned = spec.folder, spec.commit_sha
    hard_ok = sha_current.lower().startswith(pinned)
    target_matches = (
        target_known and sha_target is not None and sha_target.lower().startswith(pinned)
    )

    if not hard_ok:
        lines = [
            f"submodule-hook: {folder} — конфиг и ветка разошлись",
            f"    env.yml закрепляет {_display(pinned)}, в коммит поедет {_short(sha_current)}",
            "    переключи сабмодуль на коммит из конфига:",
            f"        cd {folder} && git switch --detach {pinned} && git add {folder}",
            f"    или, если переключение осознанное, обнови конфиг: commit_sha: {sha_current}",
        ]
        if target_known and sha_target is not None and sha_target.lower() == sha_current.lower():
            lines += [
                f"    подсказка: {_short(sha_current)} — текущий коммит в {lookup.display},",
                "    конфиг просто отстал; в этом случае обнови commit_sha, а сабмодуль не откатывай",
            ]
        reporter.error("\n".join(lines))
        if target_known and not target_matches:
            _warn_target_diverged(spec, sha_target, lookup, reporter)
        return False

    if not target_known:
        reporter.info(f"submodule-hook: {folder} — закреплён {_display(pinned)}")
        return True
    if sha_target is None:
        reporter.info(f"submodule-hook: {folder} — закреплён {_display(pinned)}")
        reporter.warning(
            f"submodule-hook: {folder} — после влития ветки сабмодуль появится "
            f"в {lookup.branch} (сейчас его там нет)"
        )
        return True
    if target_matches:
        reporter.info(
            f"submodule-hook: {folder} — не изменен, остаёмся на {_display(pinned)} (как в {lookup.branch})"
        )
        return True
    reporter.info(f"submodule-hook: {folder} — закреплён {_display(pinned)}")
    _warn_target_diverged(spec, sha_target, lookup, reporter)
    return True


def _warn_target_diverged(
    spec: SubmoduleSpec, sha_target: str, lookup: _Lookup, reporter: Reporter
) -> None:
    reporter.warning(
        f"submodule-hook: {spec.folder} — после влития ветки сабмодуль в {lookup.branch} "
        f"переключится: {_short(sha_target)} -> {_display(spec.commit_sha)}"
    )


def _warn_checkout_drift(
    spec: SubmoduleSpec, sha_current: str, repo: Path, reporter: Reporter
) -> None:
    status = gittools.submodule_checkout(repo, spec.folder)
    if status is None or status[0] != "+":
        return
    reporter.warning(
        f"submodule-hook: {spec.folder} — чекаут сабмодуля ({_short(status[1])}) отличается "
        f"от индекса ({_short(sha_current)}); если переключение осознанное — git add {spec.folder}"
    )


def _config_error(spec: SubmoduleSpec, message: str) -> Exception:
    from .config import ConfigError

    return ConfigError(f"env.yml: submodule '{spec.folder}': {message}")


def _short(sha: str) -> str:
    return sha[:8]


def _display(sha: str) -> str:
    """SHAs as pinned (readable); full 40-char ones are cut for display."""
    return sha if len(sha) <= 12 else sha[:12]


# -- pre-push: no dangling submodule commits --------------------------------
#
# The pre-commit check guarantees the pin and the pointer travel together,
# but it cannot know whether the submodule commit exists on the submodule's
# own remote: a developer may forget to push the submodule (or a maintainer
# force-pushes it away), and the superproject ships a gitlink pointing at
# nothing — cloning and CI break for everyone else. At push time everything
# is final, so the pre-push hook verifies that every submodule commit
# referenced by the pushed commits and by the pushed branch tips is reachable
# from a branch of the submodule's remote.


@dataclass
class PushResult:
    ok: bool = True
    failures: List[str] = field(default_factory=list)


def run_push_hook(
    cfg: AppConfig, repo: Path, ref_lines: List[str], reporter: Reporter
) -> PushResult:
    """Verify the pushed refs carry no dangling submodule commits.

    `ref_lines` are the stdin lines git passes to the pre-push hook:
    "<local_ref> <local_sha> <remote_ref> <remote_sha>".
    """
    pushes = _parse_ref_lines(ref_lines)
    if not cfg.submodules or not pushes:
        return PushResult()

    failures: List[str] = []
    for spec in cfg.submodules:
        shas = _referenced_submodule_shas(repo, pushes, spec.folder)
        if not shas:
            continue
        fetched = gittools.sub_fetch(repo, spec.folder)
        if fetched is None:
            reporter.warning(
                f"submodule-hook: {spec.folder}: сабмодуль не инициализирован — "
                f"проверка на висячие коммиты пропущена (git submodule update --init {spec.folder})"
            )
            continue
        for sha, where in shas.items():
            verdict = gittools.sub_remote_contains(repo, spec.folder, sha, fetched)
            if verdict is None:
                reporter.warning(
                    f"submodule-hook: {spec.folder}: не смогли проверить коммит "
                    f"{_short(sha)} на remote сабмодуля (offline) — проверка пропущена"
                )
            elif not verdict:
                failures.append(f"{spec.folder} @ {_short(sha)}")
                reporter.error(
                    f"submodule-hook: {spec.folder}: коммит {_short(sha)} ({where}) "
                    "отсутствует на remote сабмодуля — не запушен или удалён force-push'ом;\n"
                    f"    запушь сабмодуль: git -C {spec.folder} push\n"
                    "    или переключи указатель на существующий коммит и обнови env.yml"
                )
    return PushResult(ok=not failures, failures=failures)


def _parse_ref_lines(ref_lines: List[str]) -> List[Tuple[str, Optional[str], str]]:
    """(local_ref, base_remote_sha_or_None, local_sha); deletions are skipped."""
    pushes: List[Tuple[str, Optional[str], str]] = []
    for line in ref_lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, _remote_ref, remote_sha = parts
        if local_sha == ZERO_SHA:  # ref deletion: nothing to verify
            continue
        base = remote_sha if remote_sha != ZERO_SHA else None  # None: new ref
        pushes.append((local_ref, base, local_sha))
    return pushes


def _referenced_submodule_shas(
    repo: Path, pushes: List[Tuple[str, Optional[str], str]], folder: str
) -> Dict[str, str]:
    """Submodule gitlink shas carried by the pushed commits and branch tips,
    mapped to a human-readable place of reference."""
    shas: Dict[str, str] = {}

    def record(sha: str, where: str) -> None:
        shas.setdefault(sha, where)

    for local_ref, base, tip in pushes:
        for commit in _touching_commits(repo, base, tip, folder):
            entry = gittools.tree_gitlink(repo, commit, folder)
            if entry is not None and entry[0] == GITLINK_MODE:
                record(entry[1], f"коммит {_short(commit)}")
        entry = gittools.tree_gitlink(repo, tip, folder)
        if entry is not None and entry[0] == GITLINK_MODE:
            record(entry[1], f"tip {local_ref}")
    return shas


def _touching_commits(
    repo: Path, base: Optional[str], tip: str, folder: str
) -> List[str]:
    try:
        return gittools.rev_list_touching(repo, base, tip, folder)
    except GitError:
        # the remote sha is unknown locally (someone else moved the ref):
        # approximate with "commits no remote-tracking ref knows"
        return gittools.rev_list_touching(repo, None, tip, folder)
