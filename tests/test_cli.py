"""CLI entry point: exit codes and output."""

from __future__ import annotations

from submodule_hook.cli import main


def test_ok_exit_zero(repo, capsys):
    assert main(["--repo", str(repo.work)]) == 0
    assert "не изменен" in capsys.readouterr().out


def test_hard_fail_exit_one(repo, capsys):
    s3 = repo.bump_sub("s3")
    repo.set_submodule(s3)
    assert main(["--repo", str(repo.work)]) == 1
    assert "commit заблокирован" in capsys.readouterr().err


def test_on_error_warn_exit_zero(repo, capsys):
    s3 = repo.bump_sub("s3")
    repo.set_submodule(s3)
    assert main(["--repo", str(repo.work), "--on-error", "warn"]) == 0
    assert "on_error=warn" in capsys.readouterr().err


def test_no_env_yml_exit_zero(tmp_path, git, capsys):
    work = tmp_path / "plain"
    work.mkdir()
    git("init", str(work), cwd=tmp_path)
    git("symbolic-ref", "HEAD", "refs/heads/master", cwd=work)
    git("config", "user.email", "t@example.com", cwd=work)
    git("config", "user.name", "T", cwd=work)
    (work / "a.txt").write_text("x\n", encoding="utf-8")
    git("add", "-A", cwd=work)
    git("commit", "-m", "c", cwd=work)
    assert main(["--repo", str(work)]) == 0
    assert capsys.readouterr().out == ""


def test_explicit_config_missing_exit_two(repo, capsys):
    assert main(["--repo", str(repo.work), "--config", "/no/such/env.yml"]) == 2
    assert "config file not found" in capsys.readouterr().err


def test_explicit_config_other_file(repo, tmp_path, capsys):
    other = tmp_path / "other-env.yml"
    sha = repo.sub_sha()
    other.write_text(
        "submodules:\n"
        f"  - folder: libs/api-clients\n"
        f'    commit_sha: "{sha[:8]}"\n',
        encoding="utf-8",
    )
    assert main(["--repo", str(repo.work), "--config", str(other)]) == 0
    assert "не изменен" in capsys.readouterr().out


def test_broken_config_exit_two(repo, capsys):
    (repo.work / "env.yml").write_text(
        "submodules:\n  - folder: libs/api-clients\n", encoding="utf-8"
    )
    repo.git("add", "env.yml", cwd=repo.work)
    assert main(["--repo", str(repo.work)]) == 2
    assert "commit_sha" in capsys.readouterr().err


def test_config_env_var(repo, monkeypatch):
    monkeypatch.setenv("SUBMODULE_HOOK_CONFIG", str(repo.work / "env.yml"))
    assert main(["--repo", str(repo.work)]) == 0


def test_quiet_suppresses_info(repo, capsys):
    assert main(["--repo", str(repo.work), "-q"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_not_a_git_repo_exit_two(tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert main(["--repo", str(plain)]) == 2
    assert "not a git repository" in capsys.readouterr().err.lower()
