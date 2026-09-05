from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

PLUGIN_ROOT = Path(__file__).parents[2] / "plugins" / "ai-engineering-os"


def test_session_start_adds_context_only_for_initialized_projects(tmp_path: Path) -> None:
    script = PLUGIN_ROOT / "hooks" / "session_start.py"
    outside = _run_hook(script, {"cwd": str(tmp_path), "hook_event_name": "SessionStart"})
    assert outside.stdout == ""

    config = tmp_path / ".codex-os" / "project.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("project_id: PROJECT-HOOK\n", encoding="utf-8")
    inside = _run_hook(script, {"cwd": str(tmp_path), "hook_event_name": "SessionStart"})
    output = _json_output(inside.stdout)
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "immediate push" in output["hookSpecificOutput"]["additionalContext"]


def test_pre_tool_use_denies_force_push_and_advises_normal_changes(tmp_path: Path) -> None:
    script = PLUGIN_ROOT / "hooks" / "pre_tool_use.py"
    denied = _run_hook(
        script,
        {
            "cwd": str(tmp_path),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git push --force origin main"},
        },
    )
    denial = _json_output(denied.stdout)["hookSpecificOutput"]
    assert denial["permissionDecision"] == "deny"
    assert "Force push" in denial["permissionDecisionReason"]

    config = tmp_path / ".codex-os" / "project.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("project_id: PROJECT-HOOK\n", encoding="utf-8")
    advised = _run_hook(
        script,
        {
            "cwd": str(tmp_path),
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch"},
        },
    )
    advice = _json_output(advised.stdout)["hookSpecificOutput"]
    assert advice["hookEventName"] == "PreToolUse"
    assert "allowed paths" in advice["additionalContext"]


@pytest.mark.parametrize(
    "command",
    [
        "cmd /c rmdir /s /q C:\\unsafe",
        "cmd /c rd /q /s C:\\unsafe",
        "cmd /c del /f /s C:\\unsafe\\*",
        "cmd /c erase /s /f C:\\unsafe\\*",
        "powershell -NoProfile Remove-Item C:\\unsafe -Recurse -Force",
        "git push --delete origin obsolete",
        "git push origin :obsolete",
        "git branch -D obsolete",
        "git update-ref -d refs/heads/obsolete",
        "pip install requests",
        "python -m pip install requests",
        "npm install",
        "pnpm build",
        "yarn add react",
        "poetry install",
        "cargo build",
        "podman compose down -v",
        "docker compose down --volumes",
        "podman volume rm project-data",
        "docker volume prune -f",
        "podman system prune -a --volumes",
    ],
)
def test_pre_tool_use_denies_obvious_windows_and_git_deletion_commands(
    tmp_path: Path, command: str
) -> None:
    denied = _run_hook(
        PLUGIN_ROOT / "hooks" / "pre_tool_use.py",
        {
            "cwd": str(tmp_path),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        },
    )

    denial = _json_output(denied.stdout)["hookSpecificOutput"]
    assert denial["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "git status --short",
        "git branch --show-current",
        "powershell -NoProfile Get-ChildItem -LiteralPath .",
        "powershell -NoProfile Remove-Item -LiteralPath temp.txt",
        "podman compose down --remove-orphans",
        "docker image prune",
    ],
)
def test_pre_tool_use_does_not_block_read_only_or_narrow_commands(
    tmp_path: Path, command: str
) -> None:
    allowed = _run_hook(
        PLUGIN_ROOT / "hooks" / "pre_tool_use.py",
        {
            "cwd": str(tmp_path),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        },
    )

    assert allowed.stdout == ""


def _run_hook(script: Path, payload: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    return result


def _json_output(output: str) -> dict[str, Any]:
    value: object = json.loads(output)
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)
