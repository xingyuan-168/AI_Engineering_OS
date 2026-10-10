from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def governed_repo(tmp_path: Path) -> Path:
    root = tmp_path / "项目"
    root.mkdir()
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.name", "Fixture"),
        ("config", "user.email", "fixture@example.com"),
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (root / ".codex-os").mkdir()
    (root / ".codex-os/project.yaml").write_text(
        "schema_version: '1.2'\nproject_id: PROJECT-TEST\nname: Test\nroot: .\ncode_paths: [src]\n",
        encoding="utf-8",
    )
    (root / ".gitignore").write_text(
        ".codex-os/state/\n.worktrees/\nbuild/\ndist/\n", encoding="utf-8"
    )
    for args in (("add", "."), ("commit", "-qm", "fixture baseline")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    return root
