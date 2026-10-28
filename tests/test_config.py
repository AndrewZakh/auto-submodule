"""Config parsing and validation (no git involved)."""

from __future__ import annotations

import pytest

from submodule_hook.config import (
    ConfigError,
    HookSettings,
    SubmoduleSpec,
    find_config,
    load_config,
    parse_config,
)

VALID = """\
submodules:
  - folder: libs/api-clients
    commit_sha: "12351adf"
    lookup_branch: main
"""


def parse(text: str):
    return parse_config(text, "env.yml")


def test_empty_config_has_no_submodules():
    cfg = parse("")
    assert cfg.submodules == []
    assert cfg.hook == HookSettings()


def test_foreign_sections_are_ignored():
    cfg = parse("version: v1.2.3\nsomething_else: {a: 1}\n" + VALID)
    assert len(cfg.submodules) == 1


def test_valid_entry():
    cfg = parse(VALID)
    assert cfg.submodules == [SubmoduleSpec("libs/api-clients", "12351adf", "main")]


def test_lookup_branch_defaults_to_master():
    cfg = parse('submodules:\n  - folder: a\n    commit_sha: "abcd1234"\n')
    assert cfg.submodules[0].lookup_branch == "master"


def test_empty_submodules_list_is_ok():
    assert parse("submodules: []\n").submodules == []


def test_root_must_be_mapping():
    with pytest.raises(ConfigError, match="mapping"):
        parse("- a\n- b\n")


def test_submodules_must_be_a_list():
    with pytest.raises(ConfigError, match="list"):
        parse("submodules: 42\n")


def test_entry_must_be_mapping():
    with pytest.raises(ConfigError, match="mapping"):
        parse("submodules:\n  - 42\n")


def test_unknown_entry_key_rejected():
    with pytest.raises(ConfigError, match="unknown option"):
        parse('submodules:\n  - folder: a\n    commit_sha: "abcd"\n    branch: master\n')


def test_missing_folder_rejected():
    with pytest.raises(ConfigError, match="folder is required"):
        parse('submodules:\n  - commit_sha: "abcd"\n')


def test_missing_commit_sha_rejected():
    with pytest.raises(ConfigError, match="commit_sha is required"):
        parse("submodules:\n  - folder: a\n")


def test_commit_sha_must_be_a_quoted_string():
    with pytest.raises(ConfigError, match="quoted hex SHA"):
        parse("submodules:\n  - folder: a\n    commit_sha: 1234\n")


@pytest.mark.parametrize("bad", ["abc", "123", "a" * 41, "xyz1234", ""])
def test_commit_sha_must_be_hex_4_to_40(bad):
    with pytest.raises(ConfigError, match="hex SHA"):
        parse(f'submodules:\n  - folder: a\n    commit_sha: "{bad}"\n')


def test_commit_sha_uppercase_is_lowered():
    cfg = parse('submodules:\n  - folder: a\n    commit_sha: "ABCD1234"\n')
    assert cfg.submodules[0].commit_sha == "abcd1234"


def test_folder_trailing_slash_and_dot_prefix_normalized():
    cfg = parse('submodules:\n  - folder: "./libs/a/"\n    commit_sha: "abcd"\n')
    assert cfg.submodules[0].folder == "libs/a"


@pytest.mark.parametrize("bad", ["..", "a/../b", "/abs/path", "a//b"])
def test_bad_folder_paths_rejected(bad):
    with pytest.raises(ConfigError, match="folder"):
        parse(f'submodules:\n  - folder: "{bad}"\n    commit_sha: "abcd"\n')


def test_duplicate_folders_rejected():
    text = (
        "submodules:\n"
        '  - folder: a\n    commit_sha: "abcd"\n'
        '  - folder: a\n    commit_sha: "ef12"\n'
    )
    with pytest.raises(ConfigError, match="duplicate"):
        parse(text)


@pytest.mark.parametrize("bad", ["a b", "-x", "feature/", "x..y", "a.lock", ".hidden"])
def test_bad_branch_names_rejected(bad):
    with pytest.raises(ConfigError, match="branch name"):
        parse(f'submodules:\n  - folder: a\n    commit_sha: "abcd"\n    lookup_branch: "{bad}"\n')


@pytest.mark.parametrize("good", ["master", "main", "release/v2.0", "release-1.2"])
def test_good_branch_names_accepted(good):
    cfg = parse(f'submodules:\n  - folder: a\n    commit_sha: "abcd"\n    lookup_branch: "{good}"\n')
    assert cfg.submodules[0].lookup_branch == good


def test_settings_unknown_key_rejected():
    with pytest.raises(ConfigError, match="unknown option"):
        parse("submodule_hook:\n  nope: 1\n")


def test_settings_on_error_validated():
    with pytest.raises(ConfigError, match="on_error"):
        parse("submodule_hook:\n  on_error: ignore\n")


def test_settings_fetch_must_be_bool():
    with pytest.raises(ConfigError, match="fetch must be a boolean"):
        parse("submodule_hook:\n  fetch: sure\n")


def test_settings_loaded():
    cfg = parse(
        "submodule_hook:\n"
        "  remote: upstream\n"
        "  fetch: false\n"
        "  on_error: warn\n"
        "  skip_merges: false\n"
    )
    assert cfg.hook == HookSettings("upstream", False, "warn", False)


# -- find_config / load_config -------------------------------------------


def test_find_config_explicit_missing(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        find_config("/no/such/env.yml", repo_root=tmp_path)


def test_find_config_default_missing(tmp_path):
    assert find_config(None, repo_root=tmp_path) is None


def test_find_config_default_present(tmp_path):
    (tmp_path / "env.yml").write_text("{}", encoding="utf-8")
    assert find_config(None, repo_root=tmp_path) == tmp_path / "env.yml"


def test_find_config_env_var(tmp_path, monkeypatch):
    other = tmp_path / "other.yml"
    other.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SUBMODULE_HOOK_CONFIG", str(other))
    assert find_config(None, repo_root=tmp_path) == other


def test_load_config_sets_source_and_rel(tmp_path):
    (tmp_path / "env.yml").write_text(VALID, encoding="utf-8")
    cfg = load_config(None, repo_root=tmp_path)
    assert cfg is not None
    assert cfg.source == tmp_path / "env.yml"
    assert cfg.source_rel == "env.yml"
    assert cfg.submodules[0].commit_sha == "12351adf"


def test_load_config_outside_repo_has_no_rel(tmp_path):
    outside = tmp_path / "outside.yml"
    outside.write_text(VALID, encoding="utf-8")
    inner = tmp_path / "repo"
    inner.mkdir()
    cfg = load_config(str(outside), repo_root=inner)
    assert cfg is not None
    assert cfg.source_rel is None


def test_load_config_broken_yaml(tmp_path):
    (tmp_path / "env.yml").write_text("submodules: [\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(None, repo_root=tmp_path)
