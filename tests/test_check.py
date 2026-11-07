"""Validator mode (--check): server-side pin verification for CI."""

from __future__ import annotations

from submodule_hook.cli import main


def test_check_passes_when_in_sync(repo, capsys):
    assert main(["--check", "--repo", str(repo.work)]) == 0
    assert "pin check passed" in capsys.readouterr().out


def test_check_tolerates_warnings(repo, capsys):
    s2 = repo.bump_sub("s2")
    repo.move_master(s2)
    repo.checkout_feature()
    assert main(["--check", "--repo", str(repo.work)]) == 0
    captured = capsys.readouterr()
    assert "will switch" in captured.err  # a warning, not a failure


def test_check_fails_on_divergence(repo, capsys):
    s3 = repo.bump_sub("s3")
    repo.set_submodule(s3)
    assert main(["--check", "--repo", str(repo.work)]) == 1
    assert "would block" in capsys.readouterr().err


def test_check_ignores_on_error_warn(repo):
    s3 = repo.bump_sub("s3")
    repo.set_submodule(s3)
    assert main(["--check", "--repo", str(repo.work), "--on-error", "warn"]) == 1


def test_check_config_error_exit_two(repo, capsys):
    (repo.work / "env.yml").write_text("submodules: 42\n", encoding="utf-8")
    repo.git("add", "env.yml", cwd=repo.work)
    assert main(["--check", "--repo", str(repo.work)]) == 2
    assert "submodules" in capsys.readouterr().err
