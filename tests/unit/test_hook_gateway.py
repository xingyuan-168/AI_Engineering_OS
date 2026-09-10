"""Unit tests for the PreToolUse hook gateway (ADR-0011)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from codex_ai_os.application.hook_gateway import (
    authorize_hook_payload,
    parse_apply_patch_paths,
)
from codex_ai_os.cli.app import app

RUNNER = CliRunner()


def _initialized_project(tmp_path: Path) -> Path:
    config = tmp_path / ".codex-os" / "project.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "schema_version: '1.2'\nproject_id: PROJECT-HOOK\nname: hook-fixture\nroot: .\n",
        encoding="utf-8",
    )
    return tmp_path


def _payload(root: Path, tool: str, command: str) -> dict[str, Any]:
    return {
        "cwd": str(root),
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"command": command},
    }


class TestApplyPatchParsing:
    def test_parses_add_update_delete(self) -> None:
        patch = "\n".join(
            [
                "*** Begin Patch",
                "*** Add File: src/new.py",
                "+print('hi')",
                "*** Update File: src/existing.py",
                "@@",
                "*** Delete File: src/removed.py",
                "*** End Patch",
            ]
        )
        assert parse_apply_patch_paths(patch) == (
            "src/new.py",
            "src/existing.py",
            "src/removed.py",
        )

    def test_parses_rename_with_move_to(self) -> None:
        patch = "\n".join(
            [
                "*** Begin Patch",
                "*** Rename File: src/old.py",
                "*** Move to: src/renamed.py",
                "*** End Patch",
            ]
        )
        assert parse_apply_patch_paths(patch) == ("src/old.py", "src/renamed.py")

    def test_empty_patch_has_no_paths(self) -> None:
        assert parse_apply_patch_paths("*** Begin Patch\n*** End Patch") == ()


class TestAuthorizeHookPayload:
    def test_ignores_unrelated_tools(self, tmp_path: Path) -> None:
        root = _initialized_project(tmp_path)
        assert authorize_hook_payload(_payload(root, "read_file", "x")) == {}

    def test_ignores_uninitialized_projects(self, tmp_path: Path) -> None:
        assert authorize_hook_payload(_payload(tmp_path, "apply_patch", "*** Begin Patch")) == {}

    def test_apply_patch_protected_path_denies(self, tmp_path: Path) -> None:
        root = _initialized_project(tmp_path)
        patch = "\n".join(
            [
                "*** Begin Patch",
                "*** Add File: .git/hooks/evil",
                "+content",
                "*** End Patch",
            ]
        )
        output = authorize_hook_payload(_payload(root, "apply_patch", patch))
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "PATH_POLICY_VIOLATION" in decision["permissionDecisionReason"]
        assert ".git/hooks/evil" in decision["permissionDecisionReason"]

    def test_apply_patch_governance_rule_path_asks_in_maintenance_only(
        self, tmp_path: Path
    ) -> None:
        root = _initialized_project(tmp_path)
        patch = "\n".join(
            [
                "*** Begin Patch",
                "*** Update File: AGENTS.md",
                "@@",
                "*** End Patch",
            ]
        )
        output = authorize_hook_payload(_payload(root, "apply_patch", patch))
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"

    def test_apply_patch_normal_source_file_allows(self, tmp_path: Path) -> None:
        root = _initialized_project(tmp_path)
        patch = "\n".join(
            [
                "*** Begin Patch",
                "*** Update File: src/codex_ai_os/application/service.py",
                "@@",
                "*** End Patch",
            ]
        )
        assert authorize_hook_payload(_payload(root, "apply_patch", patch)) == {}

    def test_shell_redirect_into_protected_path_denies(self, tmp_path: Path) -> None:
        root = _initialized_project(tmp_path)
        command = "echo test > .codex-os/state/state.db"
        output = authorize_hook_payload(_payload(root, "Bash", command))
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert ".codex-os/state/state.db" in decision["permissionDecisionReason"]

    def test_shell_append_into_normal_path_allows(self, tmp_path: Path) -> None:
        root = _initialized_project(tmp_path)
        command = "echo note >> build/output.txt"
        assert authorize_hook_payload(_payload(root, "Bash", command)) == {}


class TestAuthorizeHookCommand:
    def test_cli_denies_and_exits_zero(self, tmp_path: Path) -> None:
        root = _initialized_project(tmp_path)
        patch = "\n".join(
            [
                "*** Begin Patch",
                "*** Add File: .codex-os/state/inject.db",
                "+x",
                "*** End Patch",
            ]
        )
        result = RUNNER.invoke(
            app,
            ["authorize-hook"],
            input=json.dumps(_payload(root, "apply_patch", patch)),
        )
        assert result.exit_code == 0
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"

    def test_cli_invalid_input_exits_nonzero(self) -> None:
        result = RUNNER.invoke(app, ["authorize-hook"], input="not-json")
        assert result.exit_code == 1
