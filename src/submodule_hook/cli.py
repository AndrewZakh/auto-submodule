"""Command-line interface.

Exit codes: 0 - ok (warnings tolerated), 1 - the config and a submodule
diverged, the commit is blocked (submodule_hook.on_error=fail), 2 - usage or
config errors (bad arguments, not a git repository, broken env.yml).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import gittools
from .config import AppConfig, ConfigError, load_config
from .gittools import GitError
from .hook import Reporter, run_hook, run_push_hook


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="submodule-hook",
        description="Pre-commit hook: keep git submodules on the commits pinned in env.yml.",
    )
    parser.add_argument(
        "--config", help="path to the YAML config (default: env.yml in the repository root)"
    )
    parser.add_argument(
        "--repo", default=".", help="path inside the target git repository (default: cwd)"
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="pre-push mode: read pushed refs from stdin and verify the submodule "
        "commits they reference exist on the submodules' remotes",
    )
    parser.add_argument("--remote", help="override submodule_hook.remote")
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="work offline: no fetch, use existing remote-tracking refs",
    )
    parser.add_argument(
        "--on-error", choices=["fail", "warn"], help="override submodule_hook.on_error"
    )
    parser.add_argument("-v", "--verbose", action="count", default=0, help="debug output")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress informational output")
    # git passes the destination remote name and URL to the pre-push hook;
    # they are not used but must be accepted
    parser.add_argument("push_remote", nargs="*", help=argparse.SUPPRESS)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    level = logging.WARNING if args.quiet else (logging.DEBUG if args.verbose else logging.INFO)
    logging.basicConfig(level=level, format="%(message)s")

    try:
        root = gittools.repo_root(Path(args.repo).resolve())
        cfg = load_config(args.config, repo_root=root)
    except (ConfigError, GitError) as exc:
        print(f"submodule-hook: {exc}", file=sys.stderr)
        return 2
    if cfg is None:
        return 0  # no env.yml / no submodules section: nothing is pinned
    _apply_overrides(cfg, args)

    reporter = Reporter(quiet=args.quiet)
    if args.push:
        return _run_push(cfg, repo=root, reporter=reporter)

    try:
        result = run_hook(cfg, repo=root, reporter=reporter)
    except ConfigError as exc:
        print(f"submodule-hook: {exc}", file=sys.stderr)
        return 2
    except GitError as exc:
        if cfg.hook.on_error == "warn":
            print(f"submodule-hook: {exc} (on_error=warn, continuing)", file=sys.stderr)
            return 0
        print(f"submodule-hook: {exc}", file=sys.stderr)
        return 1

    if result.hard_failures:
        if cfg.hook.on_error == "warn":
            print(
                "submodule-hook: on_error=warn — коммит пропущен, "
                "но конфиг и сабмодуль разошлись: " + ", ".join(result.hard_failures),
                file=sys.stderr,
            )
            return 0
        reporter.error(
            "submodule-hook: commit заблокирован — конфиг и сабмодуль разошлись: "
            f"{', '.join(result.hard_failures)} (команды починки выше)"
        )
        return 1
    return 0


def _run_push(cfg, repo: Path, reporter: Reporter) -> int:
    ref_lines = sys.stdin.read().splitlines()
    try:
        result = run_push_hook(cfg, repo=repo, ref_lines=ref_lines, reporter=reporter)
    except GitError as exc:
        if cfg.hook.on_error == "warn":
            print(f"submodule-hook: {exc} (on_error=warn, continuing)", file=sys.stderr)
            return 0
        print(f"submodule-hook: {exc}", file=sys.stderr)
        return 1
    if result.failures:
        if cfg.hook.on_error == "warn":
            print(
                "submodule-hook: on_error=warn — пуш пропущен, "
                "но есть висячие коммиты сабмодулей: " + ", ".join(result.failures),
                file=sys.stderr,
            )
            return 0
        reporter.error(
            "submodule-hook: push заблокирован — висячие коммиты сабмодулей "
            "(команды починки выше)"
        )
        return 1
    return 0


def _apply_overrides(cfg: AppConfig, args: argparse.Namespace) -> None:
    if args.remote:
        cfg.hook.remote = args.remote
    if args.no_fetch:
        cfg.hook.fetch = False
    if args.on_error:
        cfg.hook.on_error = args.on_error


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
