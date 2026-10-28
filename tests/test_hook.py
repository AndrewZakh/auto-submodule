"""End-to-end hook scenarios: the decision table from the README."""

from __future__ import annotations

import pytest

from submodule_hook.config import ConfigError

from conftest import SUB_FOLDER


def test_all_equal(repo, capsys):
    result = repo.run_hook()
    assert result.ok and not result.hard_failures and not result.skipped
    out = capsys.readouterr()
    assert "не изменен, остаёмся на" in out.out
    assert "(как в master)" in out.out
    assert out.err == ""


def test_row2_target_diverged_warns(repo, capsys):
    s2 = repo.bump_sub("s2")
    repo.move_master(s2)
    repo.checkout_feature()
    result = repo.run_hook()
    assert result.ok
    captured = capsys.readouterr()
    assert "закреплён" in captured.out
    assert f"переключится: {s2[:8]} ->" in captured.err


def test_row3_hard_fail_with_fix_command(repo, capsys):
    s3 = repo.bump_sub("s3")
    repo.set_submodule(s3)
    result = repo.run_hook()
    assert not result.ok
    assert result.hard_failures == [SUB_FOLDER]
    err = capsys.readouterr().err
    assert "конфиг и ветка разошлись" in err
    assert f"cd {SUB_FOLDER} && git switch --detach" in err
    assert f"commit_sha: {s3}" in err  # full sha, copy-pasteable
    assert "подсказка" not in err  # current != target
    assert "переключится" not in err  # origin/master still matches the pin


def test_row4_hard_fail_plus_target_warning(repo, capsys):
    s2 = repo.bump_sub("s2")
    s3 = repo.bump_sub("s3")
    repo.move_master(s2)
    repo.checkout_feature()
    repo.set_submodule(s3)
    result = repo.run_hook()
    assert not result.ok
    err = capsys.readouterr().err
    assert "конфиг и ветка разошлись" in err
    assert f"переключится: {s2[:8]} ->" in err
    assert "подсказка" not in err  # current != target


def test_stale_config_hint_after_pulling_master(repo, capsys):
    s2 = repo.bump_sub("s2")
    repo.move_master(s2)
    repo.checkout_feature()
    repo.set_submodule(s2)  # as if master was merged in
    result = repo.run_hook()
    assert not result.ok
    err = capsys.readouterr().err
    assert "подсказка" in err
    assert "конфиг просто отстал" in err
    assert f"commit_sha: {s2}" in err


def test_short_pin_matches_by_prefix(repo, capsys):
    repo.write_env(repo.sub_sha()[:8])
    result = repo.run_hook()
    assert result.ok
    assert "не изменен" in capsys.readouterr().out


def test_uppercase_pin_normalized(repo):
    repo.write_env(repo.sub_sha().upper())
    assert repo.run_hook().ok


def test_offline_falls_back_to_tracking_ref(repo, git, capsys):
    s2 = repo.bump_sub("s2")
    repo.move_master(s2)
    repo.checkout_feature()
    git("remote", "set-url", "origin", "/no/such/remote", cwd=repo.work)
    result = repo.run_hook()
    assert result.ok
    assert f"переключится: {s2[:8]} ->" in capsys.readouterr().err


def test_offline_without_ref_skips_soft_check(repo, git, capsys):
    git("remote", "set-url", "origin", "/no/such/remote", cwd=repo.work)
    git("update-ref", "-d", "refs/remotes/origin/master", cwd=repo.work)
    result = repo.run_hook()
    assert result.ok and not result.hard_failures
    captured = capsys.readouterr()
    assert "закреплён" in captured.out
    assert "origin/master недоступна" in captured.err
    assert SUB_FOLDER in captured.err


def test_no_fetch_uses_existing_ref(repo, git, capsys):
    s2 = repo.bump_sub("s2")
    repo.move_master(s2)
    repo.checkout_feature()
    git("remote", "set-url", "origin", "/no/such/remote", cwd=repo.work)
    cfg = repo.load_cfg()
    cfg.hook.fetch = False
    result = repo.run_hook(cfg)
    assert result.ok
    assert "переключится" in capsys.readouterr().err


def test_merge_commit_skipped(repo, git, capsys):
    git("checkout", "-q", "-b", "side", "master", cwd=repo.work)
    (repo.work / "side.txt").write_text("x\n", encoding="utf-8")
    repo.commit("side work")
    git("checkout", "-q", "master", cwd=repo.work)
    git("merge", "--no-ff", "--no-commit", "side", cwd=repo.work)
    result = repo.run_hook()
    assert result.ok and result.skipped
    assert capsys.readouterr().out == ""


def test_merge_not_skipped_when_disabled(repo, git):
    git("checkout", "-q", "-b", "side", "master", cwd=repo.work)
    (repo.work / "side.txt").write_text("x\n", encoding="utf-8")
    repo.commit("side work")
    git("checkout", "-q", "master", cwd=repo.work)
    git("merge", "--no-ff", "--no-commit", "side", cwd=repo.work)
    cfg = repo.load_cfg()
    cfg.hook.skip_merges = False
    result = repo.run_hook(cfg)
    assert not result.skipped
    assert result.ok  # pin, index and origin/master are all in sync


def test_index_copy_of_env_wins(repo, capsys):
    # the worktree pins a wrong sha without staging it
    (repo.work / "env.yml").write_text(
        "submodules:\n"
        f"  - folder: {SUB_FOLDER}\n"
        '    commit_sha: "deadbeef"\n',
        encoding="utf-8",
    )
    result = repo.run_hook()
    assert result.ok  # the staged copy still pins the right sha
    assert "изменён, но не в индексе" in capsys.readouterr().err


def test_broken_staged_config_is_config_error(repo, git):
    (repo.work / "env.yml").write_text("submodules: [\n", encoding="utf-8")
    git("add", "env.yml", cwd=repo.work)
    repo.write_env(repo.sub_sha(), add=False)  # worktree fixed, not staged
    with pytest.raises(ConfigError, match="invalid YAML"):
        repo.run_hook()


def test_submodule_checkout_drift_warns(repo, capsys):
    s2 = repo.bump_sub("s2")
    repo.set_submodule(s2, add=False)  # switched but not staged
    result = repo.run_hook()
    assert result.ok
    err = capsys.readouterr().err
    assert "чекаут сабмодуля" in err
    assert f"git add {SUB_FOLDER}" in err


def test_uninitialized_submodule_still_checked(repo, git, capsys):
    git("submodule", "deinit", "-f", SUB_FOLDER, cwd=repo.work)
    result = repo.run_hook()
    assert result.ok
    assert "не изменен" in capsys.readouterr().out


def test_folder_not_a_submodule_is_config_error(repo):
    repo.write_env(repo.sub_sha(), folder="README.md")
    with pytest.raises(ConfigError, match="не сабмодуль"):
        repo.run_hook()


def test_unknown_folder_is_config_error(repo):
    repo.write_env(repo.sub_sha(), folder="no/such/path")
    with pytest.raises(ConfigError, match="нет такой записи"):
        repo.run_hook()


def test_lookup_branch_with_remote_prefix_is_config_error(repo):
    repo.write_env(repo.sub_sha(), branch="origin/master")
    with pytest.raises(ConfigError, match="remote prefix"):
        repo.run_hook()


def test_target_absent_in_lookup_branch(repo, git, capsys):
    # branch `old` predates the submodule: after the merge it will appear there
    git("push", "-q", "origin", "master~1:refs/heads/old", cwd=repo.work)
    repo.write_env(repo.sub_sha(), branch="old")
    result = repo.run_hook()
    assert result.ok
    assert "появится в old" in capsys.readouterr().err


def test_empty_submodules_section_is_noop(repo, capsys):
    (repo.work / "env.yml").write_text("submodules: []\n", encoding="utf-8")
    repo.git("add", "env.yml", cwd=repo.work)
    result = repo.run_hook()
    assert result.ok and not result.skipped
    assert capsys.readouterr().out == ""


def test_two_submodules_reported_together(repo_two, capsys):
    s_new = repo_two.bump_sub("n1")
    repo_two.set_submodule(s_new)
    result = repo_two.run_hook()
    assert not result.ok
    assert result.hard_failures == [SUB_FOLDER]
    captured = capsys.readouterr()
    assert "libs/other" in captured.out  # the healthy one is reported too
    assert "не изменен" in captured.out
