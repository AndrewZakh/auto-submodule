"""Configuration: dataclasses, YAML parsing, validation.

The config lives inside the target repository's env.yml (by default) next to
the `submodules:` section it describes; an optional `submodule_hook:` section
tunes the hook itself. All other top-level sections are foreign data (e.g.
auto-version-hook's `version:`) and are ignored.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import List, Optional

import yaml

log = logging.getLogger("submodule_hook")

CONFIG_ENV_VAR = "SUBMODULE_HOOK_CONFIG"
DEFAULT_CONFIG = "env.yml"

_ENTRY_KEYS = {"folder", "commit_sha", "lookup_branch"}
_SETTING_KEYS = {"remote", "fetch", "on_error", "skip_merges"}
_VALID_ON_ERROR = {"fail", "warn"}
_SHA_RE = re.compile(r"^[0-9a-f]{4,40}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class ConfigError(ValueError):
    """Raised on an invalid or missing configuration file."""


@dataclass
class SubmoduleSpec:
    folder: str
    commit_sha: str  # normalized: lowercase hex, 4..40 chars
    lookup_branch: str = "master"


@dataclass
class HookSettings:
    remote: str = "origin"
    fetch: bool = True
    on_error: str = "fail"
    skip_merges: bool = True


@dataclass
class AppConfig:
    submodules: List[SubmoduleSpec] = field(default_factory=list)
    hook: HookSettings = field(default_factory=HookSettings)
    source: Optional[Path] = None
    source_rel: Optional[str] = None  # config path relative to the repo root


def find_config(explicit: Optional[str], repo_root: Path) -> Optional[Path]:
    """Locate env.yml: --config flag, then $SUBMODULE_HOOK_CONFIG, then the
    default name in the repository root. None means "nothing pinned"."""
    explicit_or_env = explicit or os.environ.get(CONFIG_ENV_VAR)
    if explicit_or_env:
        path = Path(explicit_or_env)
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        return path
    default = repo_root / DEFAULT_CONFIG
    return default if default.is_file() else None


def load_config(explicit: Optional[str], repo_root: Path) -> Optional[AppConfig]:
    """Read and validate the worktree copy of the config; None if there is none."""
    path = find_config(explicit, repo_root)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    cfg = parse_config(text, str(path))
    cfg.source = path
    try:
        cfg.source_rel = path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        cfg.source_rel = None  # outside the repository: no staged copy exists
    return cfg


def parse_config(text: str, where: str) -> AppConfig:
    """Parse and validate a config body (worktree file or index copy)."""
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {where}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config root must be a mapping: {where}")

    cfg = AppConfig()
    submodules_raw = raw.get("submodules")
    if submodules_raw is not None:
        if not isinstance(submodules_raw, list):
            raise ConfigError(f"{where}: submodules must be a list")
        for index, item in enumerate(submodules_raw):
            cfg.submodules.append(_parse_entry(item, f"{where}: submodules[{index}]"))
        _reject_duplicate_folders(cfg.submodules, where)

    settings_raw = raw.get("submodule_hook")
    if settings_raw is not None:
        if not isinstance(settings_raw, dict):
            raise ConfigError(f"{where}: submodule_hook must be a mapping")
        _load_settings(cfg.hook, settings_raw, where)
    _validate_settings(cfg.hook, where)

    # Everything else in env.yml is foreign data and is deliberately ignored.
    return cfg


def _parse_entry(item: object, where: str) -> SubmoduleSpec:
    if not isinstance(item, dict):
        raise ConfigError(f"{where} must be a mapping")
    unknown = set(item) - _ENTRY_KEYS
    if unknown:
        raise ConfigError(f"{where}: unknown option(s): {', '.join(sorted(unknown))}")
    if "folder" not in item:
        raise ConfigError(f"{where}: folder is required")
    if "commit_sha" not in item:
        raise ConfigError(f"{where}: commit_sha is required")
    return SubmoduleSpec(
        folder=_normalize_folder(item["folder"], where),
        commit_sha=_normalize_sha(item["commit_sha"], where),
        lookup_branch=_validate_branch(item.get("lookup_branch", "master"), where),
    )


def _reject_duplicate_folders(submodules: List[SubmoduleSpec], where: str) -> None:
    folders = [spec.folder for spec in submodules]
    duplicates = sorted({folder for folder in folders if folders.count(folder) > 1})
    if duplicates:
        raise ConfigError(f"{where}: duplicate folder(s): {', '.join(duplicates)}")


def _normalize_folder(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{where}: folder must be a string")
    folder = value.strip()
    while folder.startswith("./"):
        folder = folder[2:]
    folder = folder.rstrip("/")
    if not folder:
        raise ConfigError(f"{where}: folder must not be empty")
    if folder.startswith(("/", "\\")):
        raise ConfigError(
            f"{where}: folder must be relative to the repository root: {value!r}"
        )
    if any(part in ("", ".", "..") for part in folder.split("/")):
        raise ConfigError(f"{where}: folder must not contain '.'/'..' components: {value!r}")
    return folder


def _normalize_sha(value: object, where: str) -> str:
    # A plain int means YAML ate the quotes; coercing it back is ambiguous
    # (`0x1a2b` parses as 6699), so we demand a quoted string instead.
    if isinstance(value, bool) or not isinstance(value, str):
        raise ConfigError(
            f"{where}: commit_sha must be a quoted hex SHA string (4..40 chars), got {value!r}"
        )
    sha = value.strip().lower()
    if not _SHA_RE.fullmatch(sha):
        raise ConfigError(f"{where}: commit_sha must be a hex SHA (4..40 chars): {value!r}")
    return sha


def _validate_branch(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: lookup_branch must be a non-empty string")
    name = value.strip()
    if (
        not _BRANCH_RE.fullmatch(name)
        or ".." in name
        or name.endswith(("/", ".", ".lock"))
    ):
        raise ConfigError(f"{where}: lookup_branch is not a valid branch name: {value!r}")
    return name


def _load_settings(target: HookSettings, section: dict, where: str) -> None:
    known = {f.name for f in fields(target)}
    for key, value in section.items():
        if key not in known:
            raise ConfigError(f"{where}: unknown option 'submodule_hook.{key}'")
        setattr(target, key, value)


def _validate_settings(settings: HookSettings, where: str) -> None:
    if not isinstance(settings.remote, str) or not settings.remote:
        raise ConfigError(f"{where}: submodule_hook.remote must be a non-empty string")
    if not isinstance(settings.fetch, bool):
        raise ConfigError(f"{where}: submodule_hook.fetch must be a boolean")
    if settings.on_error not in _VALID_ON_ERROR:
        raise ConfigError(
            f"{where}: submodule_hook.on_error must be one of {sorted(_VALID_ON_ERROR)}"
        )
    if not isinstance(settings.skip_merges, bool):
        raise ConfigError(f"{where}: submodule_hook.skip_merges must be a boolean")
