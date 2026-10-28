"""Thin wrappers around the git CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional, Sequence, Tuple


class GitError(RuntimeError):
    """Raised when a git command fails."""


def _git(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True
        )
    except FileNotFoundError as exc:  # pragma: no cover - git is virtually always there
        raise GitError("git executable not found") from exc


def git(args: Sequence[str], cwd: Path) -> str:
    """Run git, return stdout, raise GitError with stderr on failure."""
    proc = _git(args, cwd)
    if proc.returncode != 0:
        message = (proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}")
        raise GitError(f"git {' '.join(args)}: {message}")
    return proc.stdout


def repo_root(cwd: Path) -> Path:
    return Path(git(["rev-parse", "--show-toplevel"], cwd).strip())


def is_merge_in_progress(cwd: Path) -> bool:
    return _git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd).returncode == 0


def ref_exists(cwd: Path, ref: str) -> bool:
    return _git(["rev-parse", "-q", "--verify", f"{ref}^{{commit}}"], cwd).returncode == 0


def fetch(cwd: Path, remote: str, branch: str) -> None:
    """Update the remote-tracking ref of the lookup branch; raises GitError
    when offline or when the remote has no such branch."""
    git(["fetch", "--quiet", remote, branch], cwd)


def staged_text(cwd: Path, path: str) -> Optional[str]:
    """Content of ``path`` in the index, or None if the path is not tracked."""
    if not git(["ls-files", "--", path], cwd).strip():
        return None
    return git(["show", f":{path}"], cwd)


def index_gitlink(cwd: Path, path: str) -> Optional[Tuple[str, str]]:
    """(mode, sha) of the stage-0 index entry at exactly ``path``, or None."""
    out = git(["ls-files", "-s", "--", path], cwd)
    for line in out.splitlines():
        meta, _, entry_path = line.partition("\t")
        if entry_path != path:
            continue
        parts = meta.split()
        if len(parts) == 3 and parts[2] == "0":
            return parts[0], parts[1]
    return None


def tree_gitlink(cwd: Path, ref: str, path: str) -> Optional[Tuple[str, str]]:
    """(mode, sha) of the entry at ``path`` in the tree of ``ref``, or None."""
    out = git(["ls-tree", ref, "--", path], cwd)
    for line in out.splitlines():
        meta, _, entry_path = line.partition("\t")
        if entry_path != path:
            continue
        parts = meta.split()
        if len(parts) == 3:
            return parts[0], parts[2]
    return None


def submodule_checkout(cwd: Path, path: str) -> Optional[Tuple[str, str]]:
    """(status_char, sha) from `git submodule status`, or None when unavailable.

    status_char: ' ' in sync with the index, '+' checkout differs,
    '-' not initialized, 'U' in conflict.
    """
    proc = _git(["submodule", "status", "--", path], cwd)
    if proc.returncode != 0:
        return None
    lines = proc.stdout.splitlines()
    if not lines or not lines[0].strip():
        return None
    line = lines[0]
    flag = line[0]
    if flag in (" ", "+", "-", "U"):
        rest = line[1:]
    else:  # no prefix character: in sync
        flag, rest = " ", line
    parts = rest.split()
    if not parts:
        return None
    return flag, parts[0]


def rev_list_touching(
    cwd: Path, base: Optional[str], tip: str, path: str
) -> List[str]:
    """Commits in `base..tip` whose trees change `path`; with base=None the
    range is `tip --not --remotes` (commits no remote-tracking ref knows)."""
    args = ["rev-list", tip]
    if base is not None:
        args.append(f"^{base}")
    else:
        args += ["--not", "--remotes"]
    args += ["--", path]
    return [line for line in git(args, cwd).splitlines() if line]


def sub_fetch(repo: Path, folder: str) -> Optional[bool]:
    """Fetch the submodule's own remote to refresh its remote-tracking refs.

    True: fetched; False: fetch failed (offline); None: the submodule is not
    initialized or has no remotes at all — nothing to verify against.
    """
    sub = repo / folder
    if not (sub / ".git").exists():
        return None
    try:
        remotes = git(["remote"], sub).split()
    except GitError:
        return None
    if not remotes:
        return None
    remote = "origin" if "origin" in remotes else remotes[0]
    try:
        git(["fetch", "--quiet", remote], sub)
        return True
    except GitError:
        return False


def sub_remote_contains(
    repo: Path, folder: str, sha: str, fetched: Optional[bool]
) -> Optional[bool]:
    """Does any remote branch of the submodule contain `sha`?

    None means "cannot determine" (uninitialized submodule, or offline with
    no usable refs) — the caller fails open with a warning.
    """
    if fetched is None:
        return None
    sub = repo / folder
    if not (sub / ".git").exists():
        return None
    known = _git(["rev-parse", "-q", "--verify", f"{sha}^{{commit}}"], sub).returncode == 0
    if not known:
        # after a successful fetch an unknown sha is genuinely not on the remote
        return False if fetched else None
    try:
        out = git(["branch", "-r", "--contains", sha], sub)
    except GitError:
        return None
    if out.strip():
        return True
    if not fetched and not git(["for-each-ref", "refs/remotes/"], sub).strip():
        return None  # stale-free absent refs: nothing to judge by
    return False
