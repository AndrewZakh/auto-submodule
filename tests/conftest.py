"""Shared fixtures: a `git` runner and a superproject with a real submodule."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List

import pytest

SUB_FOLDER = "libs/api-clients"


@pytest.fixture()
def git():
    def _git(*args, cwd, check=True):
        return subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=check
        )

    return _git


class Repo:
    """Superproject `work` whose submodule SUB_FOLDER comes from a bare source.

    Layout in tmp_path: origin.git (superproject origin), sub_origin.git
    (submodule source), work (the checked-out superproject).
    """

    def __init__(self, git, tmp_path: Path):
        self.git = git
        self.tmp = tmp_path
        self.origin = tmp_path / "origin.git"
        self.sub_origin = tmp_path / "sub_origin.git"
        self.work = tmp_path / "work"

    # -- setup ----------------------------------------------------------
    def init_bare(self, path: Path) -> None:
        self.git("init", "--bare", str(path), cwd=self.tmp)
        self.git("symbolic-ref", "HEAD", "refs/heads/master", cwd=path)

    def init_work(self, path: Path) -> None:
        path.mkdir(parents=True)
        self.git("init", str(path), cwd=self.tmp)
        self.git("symbolic-ref", "HEAD", "refs/heads/master", cwd=path)
        self.set_identity(path)
        # git >= 2.38 disallows the file transport for submodule clones; the
        # test remotes are plain local paths
        self.git("config", "protocol.file.allow", "always", cwd=path)

    def set_identity(self, path: Path) -> None:
        self.git("config", "user.email", "test@example.com", cwd=path)
        self.git("config", "user.name", "Test User", cwd=path)

    def submodule(self, *args, cwd: Path = None) -> None:
        """`git submodule` with the file transport allowed (local test remotes)."""
        self.git("-c", "protocol.file.allow=always", "submodule", *args, cwd=cwd or self.work)

    def seed_bare(self, bare: Path, content: str) -> str:
        """Initial commit in a scratch clone, pushed to `bare`; returns its sha."""
        seed = bare.parent / (bare.stem + "_seed")
        self.init_work(seed)
        (seed / "lib.txt").write_text(content, encoding="utf-8")
        self.git("remote", "add", "origin", str(bare), cwd=seed)
        self.commit(f"seed: {content}", cwd=seed)
        self.git("push", "-q", "origin", "master", cwd=seed)
        return self.git("rev-parse", "HEAD", cwd=seed).stdout.strip()

    # -- paths ----------------------------------------------------------
    @property
    def sub(self) -> Path:
        return self.work / SUB_FOLDER

    # -- submodule source -----------------------------------------------
    def sub_sha(self, ref: str = "HEAD") -> str:
        return self.git("rev-parse", ref, cwd=self.sub).stdout.strip()

    def bump_sub(self, content: str, push: bool = True) -> str:
        """New commit on top of the submodule checkout; push=False keeps it
        local-only (the dangling-commit scenario)."""
        (self.sub / "lib.txt").write_text(content, encoding="utf-8")
        self.git("add", "-A", cwd=self.sub)
        self.git("commit", "-m", f"sub: {content}", cwd=self.sub)
        if push:
            self.git("push", "-q", "origin", "HEAD:refs/heads/master", cwd=self.sub)
        return self.sub_sha()

    # -- superproject ----------------------------------------------------
    def commit(self, message: str, cwd: Path = None) -> None:
        cwd = cwd or self.work
        self.git("add", "-A", cwd=cwd)
        self.git("commit", "-m", message, cwd=cwd)

    def set_submodule(self, sha: str, add: bool = True) -> None:
        """Point the superproject's submodule at `sha` (fetch + detach)."""
        self.git("fetch", "-q", "origin", cwd=self.sub)
        self.git("checkout", "-q", "--detach", sha, cwd=self.sub)
        if add:
            self.git("add", SUB_FOLDER, cwd=self.work)

    def sync_submodule(self) -> None:
        """Check the submodule out at the gitlink recorded in the index."""
        self.submodule("update", "--init", "--quiet")

    def write_env(
        self, sha: str, branch: str = "master", add: bool = True, folder: str = SUB_FOLDER
    ) -> None:
        text = (
            "submodules:\n"
            f"  - folder: {folder}\n"
            f'    commit_sha: "{sha}"\n'
            f"    lookup_branch: {branch}\n"
        )
        (self.work / "env.yml").write_text(text, encoding="utf-8")
        if add:
            self.git("add", "env.yml", cwd=self.work)

    def move_master(self, sha: str) -> None:
        """Consciously advance master: submodule + env.yml in one commit, pushed."""
        self.set_submodule(sha)
        self.write_env(sha)
        self.commit("bump submodule")
        self.git("push", "-q", "origin", "master", cwd=self.work)

    def checkout_feature(self, base: str = "master~1") -> None:
        """Feature branch one commit behind master (submodule + pin as before).

        Call after move_master(): master~1 is then the commit that introduced
        the submodule with its current pin.
        """
        self.git("checkout", "-q", "-b", "feature/x", base, cwd=self.work)
        self.sync_submodule()

    # -- running the hook -------------------------------------------------
    def load_cfg(self):
        from submodule_hook.config import load_config

        cfg = load_config(None, repo_root=self.work)
        assert cfg is not None
        return cfg

    def run_hook(self, cfg=None):
        from submodule_hook.hook import Reporter, run_hook

        return run_hook(cfg or self.load_cfg(), repo=self.work, reporter=Reporter())

    def push_lines(self, branch: str = "master", local: str = "HEAD") -> List[str]:
        """A pre-push stdin line: pushing `local` to origin/<branch>."""
        zero = "0" * 40
        local_sha = self.git("rev-parse", local, cwd=self.work).stdout.strip()
        proc = self.git(
            "rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}",
            cwd=self.work, check=False,
        )
        remote_sha = proc.stdout.strip() if proc.returncode == 0 else zero
        return [f"refs/heads/{branch} {local_sha} refs/heads/{branch} {remote_sha}"]

    def run_push_hook(self, lines, cfg=None):
        from submodule_hook.hook import Reporter, run_push_hook

        return run_push_hook(
            cfg or self.load_cfg(), repo=self.work, ref_lines=lines, reporter=Reporter()
        )


@pytest.fixture()
def repo(tmp_path: Path, git):
    """master: README (c1), then submodule + env.yml pinning it (c2), pushed."""
    r = Repo(git, tmp_path)
    r.init_bare(r.sub_origin)
    r.init_bare(r.origin)
    r.seed_bare(r.sub_origin, "s0\n")
    r.init_work(r.work)
    (r.work / "README.md").write_text("base\n", encoding="utf-8")
    r.commit("c1")
    r.submodule("add", str(r.sub_origin), SUB_FOLDER)
    r.set_identity(r.sub)
    r.write_env(r.sub_sha())
    r.commit("c2: submodule + env.yml")
    r.git("remote", "add", "origin", str(r.origin), cwd=r.work)
    r.git("push", "-qu", "origin", "master", cwd=r.work)
    return r


@pytest.fixture()
def repo_two(repo):
    """`repo` plus a second submodule libs/other, both pinned in env.yml."""
    sub2_origin = repo.tmp / "sub2_origin.git"
    repo.init_bare(sub2_origin)
    other_sha = repo.seed_bare(sub2_origin, "o0\n")
    repo.submodule("add", str(sub2_origin), "libs/other")
    repo.set_identity(repo.work / "libs/other")
    text = (
        "submodules:\n"
        f"  - folder: {SUB_FOLDER}\n"
        f'    commit_sha: "{repo.sub_sha()}"\n'
        "    lookup_branch: master\n"
        f"  - folder: libs/other\n"
        f'    commit_sha: "{other_sha}"\n'
        "    lookup_branch: master\n"
    )
    (repo.work / "env.yml").write_text(text, encoding="utf-8")
    repo.commit("c3: second submodule")
    repo.git("push", "-q", "origin", "master", cwd=repo.work)
    return repo
