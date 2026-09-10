from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

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


def _worktree_only_disposable(value: str) -> bool:
    posix = Path(value).resolve().as_posix().casefold()
    return "/.worktrees/" in posix or posix.endswith("/.worktrees")


@pytest.fixture(autouse=True)
def _ignore_system_temp(monkeypatch: pytest.MonkeyPatch) -> None:
    # pytest runs under the system temp directory, which the hook itself
    # treats as disposable. Scope the hook under test to worktrees only;
    # the real temp/worktree detection is covered directly below.
    monkeypatch.setattr(hook, "_in_disposable_area", _worktree_only_disposable)


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


def test_in_disposable_area_semantics(tmp_path: Path) -> None:
    assert REAL_IN_DISPOSABLE_AREA(str(tmp_path / ".worktrees" / "demo")) is True
    # pytest tmp_path lives under the system temp directory: disposable by rule.
    assert REAL_IN_DISPOSABLE_AREA(str(tmp_path)) is True
    assert REAL_IN_DISPOSABLE_AREA(str(Path.home())) is False


def test_force_push_is_denied(tmp_path: Path) -> None:
    code, decision = _run_hook("Bash", "git push --force origin main", tmp_path)
    assert decision == "deny"
    assert code == 0


def test_remote_ref_deletion_and_update_ref_denied(tmp_path: Path) -> None:
    _, decision = _run_hook("Bash", "git push origin :refs/heads/feature", tmp_path)
    assert decision == "deny"
    _, decision = _run_hook("Bash", "git update-ref -d refs/heads/main", tmp_path)
    assert decision == "deny"


def test_destructive_git_denied_in_main_allowed_in_worktree(tmp_path: Path) -> None:
    _, decision = _run_hook("Bash", "git reset --hard HEAD~1", tmp_path)
    assert decision == "deny"
    worktree = tmp_path / ".worktrees" / "demo"
    worktree.mkdir(parents=True)
    _, decision = _run_hook("Bash", "git reset --hard HEAD~1", worktree)
    assert decision is None
    _, decision = _run_hook("Bash", "git clean -fd", worktree)
    assert decision is None
    _, decision = _run_hook("Bash", "git branch -D demo", worktree)
    assert decision is None


def test_engineering_commands_are_never_blocked(tmp_path: Path) -> None:
    for command in (
        "pip install requests",
        "npm install",
        "yarn add react",
        "cargo build",
        "sed -i s/a/b/ file.txt",
    ):
        _, decision = _run_hook("Bash", command, tmp_path)
        assert decision is None, command


def test_recursive_deletion_denied_in_main(tmp_path: Path) -> None:
    _, decision = _run_hook("Bash", "cmd /c rd /s /q C:\\unsafe", tmp_path)
    assert decision == "deny"
    _, decision = _run_hook(
        "Bash", "powershell -NoProfile Remove-Item C:\\x -Recurse -Force", tmp_path
    )
    assert decision == "deny"


def test_apply_patch_into_input_is_denied(tmp_path: Path) -> None:
    patch = "*** Add File: input/notes.txt\n+data"
    _, decision = _run_hook("apply_patch", patch, tmp_path)
    assert decision == "deny"


def test_memory_single_writer_rule(tmp_path: Path) -> None:
    worktree = tmp_path / ".worktrees" / "demo"
    worktree.mkdir(parents=True)
    patch = "*** Update File: docs/memory/memory.jsonl\n+memory line"
    _, decision = _run_hook("apply_patch", patch, worktree)
    assert decision == "deny"
    _, decision = _run_hook("apply_patch", patch, tmp_path)
    assert decision is None
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
    _, decision = _run_hook("Bash", "echo x > docs/memory/memory.jsonl", tmp_path)
    assert decision is None
