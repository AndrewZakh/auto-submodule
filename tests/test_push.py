"""Pre-push mode: no dangling submodule commits in the pushed history."""

from __future__ import annotations

import io
import sys
from pathlib import Path

from submodule_hook.cli import main

from conftest import SUB_FOLDER


def test_push_ok_silent_when_nothing_touches_submodules(repo, capsys):
    (repo.work / "note.txt").write_text("x\n", encoding="utf-8")
    repo.commit("unrelated work")
    result = repo.run_push_hook(repo.push_lines())
    assert result.ok and not result.failures
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_push_ok_when_sub_commits_are_pushed(repo, capsys):
    s3 = repo.bump_sub("s3")
    repo.set_submodule(s3)
    repo.write_env(s3)
    repo.commit("bump submodule")
    result = repo.run_push_hook(repo.push_lines())
    assert result.ok
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_push_blocked_when_sub_commit_not_pushed(repo, capsys):
    s3 = repo.bump_sub("s3", push=False)
    repo.set_submodule(s3)
    repo.write_env(s3)
    repo.commit("bump submodule")
    result = repo.run_push_hook(repo.push_lines())
    assert not result.ok
    err = capsys.readouterr().err
    assert "отсутствует на remote сабмодуля" in err
    assert s3[:8] in err
    assert f"git -C {SUB_FOLDER} push" in err


def test_push_blocked_after_force_push_deletion(repo, capsys):
    s0 = repo.sub_sha()
    s3 = repo.bump_sub("s3")
    repo.set_submodule(s3)
    repo.write_env(s3)
    repo.commit("bump submodule")
    repo.git("push", "-q", "origin", "master", cwd=repo.work)
    # the submodule maintainer rewrites history: s3 is gone from the remote
    repo.git("push", "-qf", "origin", f"{s0}:refs/heads/master", cwd=repo.sub)
    (repo.work / "note.txt").write_text("x\n", encoding="utf-8")
    repo.commit("unrelated work")
    result = repo.run_push_hook(repo.push_lines())
    assert not result.ok  # the branch tip still points at the deleted commit
    err = capsys.readouterr().err
    assert s3[:8] in err
    assert "удалён force-push'ом" in err


def test_push_new_branch_blocked(repo, capsys):
    s3 = repo.bump_sub("s3", push=False)
    repo.set_submodule(s3)
    repo.write_env(s3)
    repo.commit("bump submodule")
    repo.git("branch", "feature/y", cwd=repo.work)
    result = repo.run_push_hook(repo.push_lines(branch="feature/y"))
    assert not result.ok
    assert "отсутствует на remote сабмодуля" in capsys.readouterr().err


def test_push_deletion_lines_skipped(repo, capsys):
    zero = "0" * 40
    local = repo.git("rev-parse", "HEAD", cwd=repo.work).stdout.strip()
    lines = [f"refs/heads/master {zero} refs/heads/master {local}"]
    result = repo.run_push_hook(lines)
    assert result.ok
    assert capsys.readouterr().err == ""


def test_push_uninitialized_submodule_fails_open(repo, git, capsys):
    s3 = repo.bump_sub("s3", push=False)
    repo.set_submodule(s3)
    repo.write_env(s3)
    repo.commit("bump submodule")
    git("submodule", "deinit", "-f", SUB_FOLDER, cwd=repo.work)
    result = repo.run_push_hook(repo.push_lines())
    assert result.ok  # cannot verify — fail open, but loudly
    err = capsys.readouterr().err
    assert "не инициализирован" in err
    assert "проверка на висячие коммиты пропущена" in err


def test_push_without_pinned_submodules_is_noop(repo, capsys):
    (repo.work / "env.yml").write_text("submodules: []\n", encoding="utf-8")
    repo.git("add", "env.yml", cwd=repo.work)
    result = repo.run_push_hook(repo.push_lines())
    assert result.ok
    assert capsys.readouterr().err == ""


def test_cli_push_reads_stdin_and_blocks(repo, monkeypatch, capsys):
    s3 = repo.bump_sub("s3", push=False)
    repo.set_submodule(s3)
    repo.write_env(s3)
    repo.commit("bump submodule")
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(repo.push_lines()) + "\n"))
    assert main(["--push", "--repo", str(repo.work)]) == 1
    assert "push заблокирован" in capsys.readouterr().err


def test_cli_push_on_error_warn_allows(repo, monkeypatch, capsys):
    s3 = repo.bump_sub("s3", push=False)
    repo.set_submodule(s3)
    repo.write_env(s3)
    repo.commit("bump submodule")
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(repo.push_lines()) + "\n"))
    assert main(["--push", "--repo", str(repo.work), "--on-error", "warn"]) == 0
    assert "on_error=warn" in capsys.readouterr().err


def test_real_git_push_blocked_then_fixed(repo, git):
    hook = git("rev-parse", "--git-path", "hooks/pre-push", cwd=repo.work).stdout.strip()
    hook_path = Path(hook) if Path(hook).is_absolute() else repo.work / hook
    hook_path.write_text(
        f"#!/bin/sh\nexec {sys.executable} -m submodule_hook --push\n",
        encoding="utf-8",
    )
    hook_path.chmod(0o755)

    s3 = repo.bump_sub("s3", push=False)
    repo.set_submodule(s3)
    repo.write_env(s3)
    repo.commit("bump submodule")

    push = git("push", "origin", "master", cwd=repo.work, check=False)
    assert push.returncode != 0
    assert "submodule-hook" in push.stderr
    assert "отсутствует на remote сабмодуля" in push.stderr

    # push the submodule, and the same superproject push goes through
    repo.git("push", "-q", "origin", "HEAD:refs/heads/master", cwd=repo.sub)
    push = git("push", "origin", "master", cwd=repo.work, check=False)
    assert push.returncode == 0
