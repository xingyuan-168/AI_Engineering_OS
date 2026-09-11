"""Integration tests for the PreToolUse hook script (ADR-0011 / ADR-0016)."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from codex_ai_os.core.worktree import WorktreeManager
from codex_ai_os.infrastructure.database import Database

HOOK_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "ai-engineering-os"
    / "hooks"
    / "pre_tool_use.py"
)

_spec = importlib.util.spec_from_file_location("pre_tool_use", HOOK_PATH)
assert _spec is not None and _spec.loader is not None
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)

REAL_IN_DISPOSABLE_AREA = hook._in_disposable_area


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _coordinator_with_worktree(tmp_path: Path, *, remote: bool = False) -> tuple[Path, Path]:
    """One governed coordinator project with one real registered worktree."""

    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@e.com")
    _git(root, "config", "user.name", "T")
    (root / "README.md").write_text("# t\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    if remote:
        _git(root, "remote", "add", "origin", "https://github.com/org/repo.git")
    (root / ".codex-os").mkdir()
    (root / ".codex-os" / "project.yaml").write_text(
        "schema_version: '1.0'\n"
        "project_id: PROJECT-HOOK\n"
        "name: hook-fixture\n"
        "root: .\n"
        "code_paths:\n  - src\n",
        encoding="utf-8",
    )
    database = Database(root / ".codex-os" / "state" / "state.db")
    database.migrate()
    manager = WorktreeManager(root, database=database)
    manager.prepare(name="demo")
    return root, root / ".worktrees" / "demo"


def _run_hook(tool: str, command: str, cwd: Path) -> tuple[int, str | None]:
    payload: dict[str, Any] = {
        "tool_name": tool,
        "tool_input": {"command": command},
        "cwd": str(cwd),
    }
    original_stdin, original_stdout = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(json.dumps(payload))
    sys.stdout = buffer = io.StringIO()
    try:
        code = hook.main()
    finally:
        sys.stdin, sys.stdout = original_stdin, original_stdout
    output = buffer.getvalue().strip()
    if not output:
        return code, None
    decision = json.loads(output)
    return code, decision.get("hookSpecificOutput", {}).get("permissionDecision")


def _main_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate tests from the host temp rule: pytest tmp paths live under the
    system temp directory, which would otherwise mark every fixture cwd as
    disposable and mask the worktree semantics under test."""

    monkeypatch.setattr(hook, "_temp_roots", lambda: [])
    monkeypatch.setattr(hook.shutil, "which", lambda name: None)
    monkeypatch.setattr(hook, "_authorize_via_runtime", lambda payload: "")


def test_temp_directory_is_disposable(tmp_path: Path) -> None:
    # The system temp directory (pytest tmp_path lives there) is disposable.
    assert REAL_IN_DISPOSABLE_AREA(str(tmp_path)) is True
    assert REAL_IN_DISPOSABLE_AREA(str(Path.home())) is False


def test_in_disposable_area_worktree_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _main_context(monkeypatch)
    root, worktree = _coordinator_with_worktree(tmp_path)
    # A registered, real, git-backed worktree is trusted.
    assert REAL_IN_DISPOSABLE_AREA(str(worktree)) is True
    # A fake .worktrees path without registration never gains rights.
    assert REAL_IN_DISPOSABLE_AREA(str(root / ".worktrees" / "fake")) is False


def test_registered_worktree_required_before_allowance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _main_context(monkeypatch)
    root, worktree = _coordinator_with_worktree(tmp_path)
    fake = root / ".worktrees" / "fake"
    fake.mkdir(parents=True)
    # Real registered worktree: destructive-but-local commands are allowed.
    _, decision = _run_hook("Bash", "git reset --hard HEAD~1", worktree)
    assert decision is None
    _, decision = _run_hook("Bash", "git clean -fd", worktree)
    assert decision is None
    # Fake path: fail closed.
    _, decision = _run_hook("Bash", "git reset --hard HEAD~1", fake)
    assert decision == "deny"


def test_force_push_is_denied(tmp_path: Path) -> None:
    code, decision = _run_hook("Bash", "git push --force origin main", tmp_path)
    assert decision == "deny"
    assert code == 0


def test_remote_ref_deletion_and_update_ref_denied(tmp_path: Path) -> None:
    _, decision = _run_hook("Bash", "git push origin :refs/heads/feature", tmp_path)
    assert decision == "deny"
    _, decision = _run_hook("Bash", "git update-ref -d refs/heads/main", tmp_path)
    assert decision == "deny"


def test_destructive_git_denied_in_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _main_context(monkeypatch)
    root, _ = _coordinator_with_worktree(tmp_path)
    _, decision = _run_hook("Bash", "git reset --hard HEAD~1", root)
    assert decision == "deny"


def test_engineering_commands_are_never_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Offline mode: the runtime CLI is treated as unreachable so the test
    # exercises the hook's own rules instead of spawning the real gateway.
    monkeypatch.setattr(hook.shutil, "which", lambda name: None)
    monkeypatch.setattr(hook, "_authorize_via_runtime", lambda payload: "")
    root, _ = _coordinator_with_worktree(tmp_path)
    for command in (
        "pip install requests",
        "npm install",
        "pnpm add vite",
        "yarn add react",
        "cargo build",
        "pytest tests/unit/test_gates.py",
        "ruff check src",
        "sed -i s/a/b/ file.txt",
    ):
        _, decision = _run_hook("Bash", command, root)
        assert decision is None, command


def test_offline_no_github_blocks_formal_source_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _main_context(monkeypatch)
    root, _ = _coordinator_with_worktree(tmp_path, remote=False)
    code, decision = _run_hook("apply_patch", "*** Add File: src/app.py\n+x", root)
    assert code == 0
    assert decision == "deny"
    # Documentation writes stay allowed without GitHub.
    _, decision = _run_hook("apply_patch", "*** Add File: docs/notes.md\n+x", root)
    assert decision is None


def test_offline_github_remote_allows_formal_source_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _main_context(monkeypatch)
    root, _ = _coordinator_with_worktree(tmp_path, remote=True)
    _, decision = _run_hook("apply_patch", "*** Add File: src/app.py\n+x", root)
    assert decision is None


def test_recursive_deletion_denied_in_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _main_context(monkeypatch)
    root, _ = _coordinator_with_worktree(tmp_path)
    _, decision = _run_hook("Bash", "cmd /c rd /s /q D:/unsafe", root)
    assert decision == "deny"
    _, decision = _run_hook(
        "Bash", "powershell -NoProfile Remove-Item D:/x -Recurse -Force", root
    )
    assert decision == "deny"


def test_apply_patch_into_input_is_denied(tmp_path: Path) -> None:
    patch = "*** Add File: input/notes.txt\n+data"
    _, decision = _run_hook("apply_patch", patch, tmp_path)
    assert decision == "deny"


def test_memory_single_writer_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _main_context(monkeypatch)
    root, worktree = _coordinator_with_worktree(tmp_path)
    patch = "*** Update File: docs/memory/memory.jsonl\n+memory line"
    _, decision = _run_hook("apply_patch", patch, worktree)
    assert decision == "deny"
    _, decision = _run_hook(
        "Bash", "codex-os memory record --title t --summary s --source docs/a.md", worktree
    )
    assert decision == "deny"
    _, decision = _run_hook(
        "Bash",
        "codex-os memory record --candidate --title t --summary s --source docs/a.md",
        worktree,
    )
    assert decision is None
    _, decision = _run_hook("Bash", "echo x > docs/memory/memory.jsonl", worktree)
    assert decision == "deny"
    # The main session (coordinator root) may write the JSONL directly.
    _, decision = _run_hook("Bash", "echo x > docs/memory/memory.jsonl", root)
    assert decision is None
