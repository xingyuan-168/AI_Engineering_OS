"""The stdlib hook bridge obeys the host protocol; runtime checks have one kernel."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from codex_ai_os.application.hook_gateway import authorize_hook_payload
from codex_ai_os.core.worktree import WorktreeManager
from codex_ai_os.infrastructure.database import Database

HOOK_PATH = Path(__file__).resolve().parents[2] / "plugins/ai-engineering-os/hooks/pre_tool_use.py"
_spec = importlib.util.spec_from_file_location("pre_tool_use", HOOK_PATH)
assert _spec is not None and _spec.loader is not None
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


@pytest.fixture(autouse=True)
def isolate_protocol_from_entry_probe(monkeypatch):
    from codex_ai_os.runtime_entry import EntryError

    class Selector:
        @staticmethod
        def select_runtime(root, timeout):
            value = shutil.which("codex-os")
            if not value:
                raise EntryError("fixture runtime missing", code="AIOS_RUNTIME_UNAVAILABLE")
            return [value]

        @staticmethod
        def controlled_run(command, *, plugin_root, timeout, input_data=None):
            return subprocess.run(
                command,
                timeout=timeout,
                input=input_data,
                capture_output=True,
                text=True,
                check=False,
            )

    monkeypatch.setattr(hook, "_selector", lambda: (Selector, HOOK_PATH.parents[1]))


def payload(
    root: Path, tool: str = "apply_patch", command: str = "*** Add File: docs/x.md\n+x"
) -> dict[str, Any]:
    return {
        "cwd": str(root),
        "tool_name": tool,
        "tool_input": {"command": command},
        "hook_event_name": "PreToolUse",
    }


@pytest.mark.parametrize(
    "output",
    [
        "",
        "null",
        "[]",
        "garbage",
        '{"garbage": 1}',
        '{"hookSpecificOutput":{"permissionDecision":"ask"}}',
        '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny"}}',
        '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"wrong event"}}',
    ],
)
def test_invalid_runtime_output_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "codex-os")
    monkeypatch.setattr(
        hook.subprocess, "run", lambda *args, **kw: subprocess.CompletedProcess(args, 0, output, "")
    )
    result = hook.run_hook(payload(tmp_path))["hookSpecificOutput"]
    assert result["permissionDecision"] == "deny"
    assert "AIOS_RUNTIME_INVALID_RESPONSE" in result["permissionDecisionReason"]


@pytest.mark.parametrize("failure", ["missing", "timeout", "exit", "oserror"])
def test_runtime_failure_denies_writes_with_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None if failure == "missing" else "codex-os")

    def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert 9 < kwargs["timeout"] <= 10
        if failure == "timeout":
            raise subprocess.TimeoutExpired("codex-os", 10)
        if failure == "oserror":
            raise OSError("fixture unavailable")
        return subprocess.CompletedProcess(args, 1, "", "")

    monkeypatch.setattr(hook.subprocess, "run", run)
    result = hook.run_hook(payload(tmp_path))["hookSpecificOutput"]
    assert result["permissionDecision"] == "deny"
    assert "AIOS_RUNTIME_" in result["permissionDecisionReason"]


def test_missing_runtime_does_not_claim_read_checks_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = hook.run_hook(payload(tmp_path, "Bash", "git status"))["hookSpecificOutput"]
    assert "permissionDecision" not in result
    assert "AIOS_RUNTIME_UNAVAILABLE" in result["additionalContext"]


def test_bridge_returns_valid_runtime_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "codex-os")
    for response in (
        {},
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "rule: reason",
            }
        },
    ):
        monkeypatch.setattr(
            hook.subprocess,
            "run",
            lambda *args, response=response, **kwargs: subprocess.CompletedProcess(
                args, 0, json.dumps(response), ""
            ),
        )
        assert hook.run_hook(payload(tmp_path)) == response


@pytest.mark.parametrize(
    "command",
    [
        "git push --force origin main",
        "git push origin :refs/heads/feature",
        "git update-ref -d refs/heads/main",
        "git reset --hard",
        "git clean -fd",
        "git branch -D feature",
    ],
)
def test_shared_main_git_guards(governed_repo: Path, command: str) -> None:
    result = authorize_hook_payload(payload(governed_repo, "Bash", command))
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "git branch -d feature",
        "pip install requests",
        "npm install",
        "cargo build",
        "pytest tests",
        "ruff check src",
    ],
)
def test_native_engineering_commands_remain_native(governed_repo: Path, command: str) -> None:
    assert authorize_hook_payload(payload(governed_repo, "Bash", command)) == {}


def test_registered_worktree_and_single_writer(governed_repo: Path) -> None:
    database = Database(governed_repo / ".codex-os/state/state.db")
    database.migrate()
    manager = WorktreeManager(governed_repo, database=database)
    manager.prepare(name="hook-check")
    worktree = governed_repo / ".worktrees/hook-check"
    assert authorize_hook_payload(payload(worktree, "Bash", "git reset --hard")) == {}
    for command in ("git push --force origin main", f'git -C "{governed_repo}" reset --hard'):
        assert (
            authorize_hook_payload(payload(worktree, "Bash", command))["hookSpecificOutput"][
                "permissionDecision"
            ]
            == "deny"
        )
    for tool, command in (
        ("apply_patch", "*** Update File: docs/memory/memory.jsonl\n+x"),
        ("Bash", "echo x > docs/memory/memory.jsonl"),
    ):
        assert (
            authorize_hook_payload(payload(worktree, tool, command))["hookSpecificOutput"][
                "permissionDecision"
            ]
            == "deny"
        )
    manager.cleanup(name="hook-check")


@pytest.mark.parametrize(
    "event,tool,command,decision",
    [
        ("PreToolUse", "Bash", "git push --force origin main", "deny"),
        ("PreToolUse", "apply_patch", "*** Add File: input/asset.txt\n+x", "deny"),
        ("PreToolUse", "apply_patch", "*** Add File: docs/note.md\n+x", None),
        ("SessionStart", "", "", None),
    ],
)
def test_real_bridge_and_source_cli_protocol(
    governed_repo: Path, event: str, tool: str, command: str, decision: str | None
) -> None:
    environment = {
        **os.environ,
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
    }
    data = payload(governed_repo, tool, command)
    data["hook_event_name"] = event
    script = HOOK_PATH if event == "PreToolUse" else HOOK_PATH.with_name("session_start.py")
    result = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout).get("hookSpecificOutput", {})
    assert output.get("permissionDecision") == decision
    if event == "SessionStart":
        assert output["hookEventName"] == event
        assert "starting Git ref" in output["additionalContext"]
