"""scripts/install.sh: both hooks, from any cwd, with a backup."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def test_install_sh_installs_both_hooks_from_foreign_cwd(repo, tmp_path):
    subprocess.run(
        [str(SCRIPTS / "install.sh"), str(repo.work)],
        cwd=tmp_path,  # not the repository: hook paths must not depend on the cwd
        check=True,
        capture_output=True,
        text=True,
    )
    hooks = repo.work / ".git" / "hooks"
    expected = {
        "pre-commit": 'exec submodule-hook "$@"',
        "pre-push": "exec submodule-hook --push",
    }
    for hook, marker in expected.items():
        path = hooks / hook
        assert marker in path.read_text(encoding="utf-8")
        assert os.access(path, os.X_OK)


def test_install_sh_backs_up_existing_hooks(repo, tmp_path):
    existing = repo.work / ".git" / "hooks" / "pre-commit"
    existing.write_text("#!/bin/sh\necho old hook\n", encoding="utf-8")
    subprocess.run(
        [str(SCRIPTS / "install.sh"), str(repo.work)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert (repo.work / ".git" / "hooks" / "pre-commit.bak").read_text(
        encoding="utf-8"
    ) == "#!/bin/sh\necho old hook\n"
