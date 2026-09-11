"""Unit tests for the PreToolUse hook gateway (ADR-0011 / ADR-0016)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from codex_ai_os.application.hook_gateway import (
    authorize_hook_payload,
    parse_apply_patch_paths,
)
from codex_ai_os.cli.app import app
from codex_ai_os.core.gates import GateDecision, GateName

RUNNER = CliRunner()


def _initialized_project(tmp_path: Path, *, with_remote: bool = False) -> Path:
    config = tmp_path / ".codex-os" / "project.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "schema_version: '1.2'\nproject_id: PROJECT-HOOK\nname: hook-fixture\nroot: .\n"
        "code_paths:\n  - src\n",
        encoding="utf-8",
    )
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.com")
    if with_remote:
        _git(tmp_path, "remote", "add", "origin", "https://github.com/org/repo.git")
    return tmp_path


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=True,
    )


def _payload(root: Path, tool: str, command: str) -> dict[str, Any]:
    return {
        "cwd": str(root),
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"command": command},
    }


def _write_payload(root: Path, path: str) -> dict[str, Any]:
    return {
        "cwd": str(root),
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"path": path, "content": "x"},
    }


def _patch(tool: str, path: str) -> str:
    verb = "Add File" if tool == "add" else "Update File"
    return "\n".join(["*** Begin Patch", f"*** {verb}: {path}", "@@", "*** End Patch"])


def _allowed_start() -> GateDecision:
    return GateDecision(gate=GateName.CODE_START, allowed=True, findings=())


def _patch_output(root: Path, verb: str, path: str) -> dict[str, Any]:
    return authorize_hook_payload(_payload(root, "apply_patch", _patch(verb, path)))


def _blocked_start(code: str = "GITHUB_REMOTE_MISSING") -> GateDecision:
    from codex_ai_os.core.gates import GateFinding

    return GateDecision(
        gate=GateName.CODE_START,
        allowed=False,
        findings=(GateFinding(code, "blocked for the test"),),
    )


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
        output = _patch_output(root, "add", ".git/hooks/evil")
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "PATH_POLICY_VIOLATION" in decision["permissionDecisionReason"]

    def test_apply_patch_governance_rule_path_denies(self, tmp_path: Path) -> None:
        root = _initialized_project(tmp_path)
        output = _patch_output(root, "update", "AGENTS.md")
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"

    def test_input_write_denies(self, tmp_path: Path) -> None:
        root = _initialized_project(tmp_path)
        output = _patch_output(root, "add", "input/notes.txt")
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "PATH_POLICY_VIOLATION" in decision["permissionDecisionReason"]

    def test_no_github_blocks_formal_source_write(self, tmp_path: Path) -> None:
        root = _initialized_project(tmp_path)
        output = authorize_hook_payload(_payload(root, "apply_patch", _patch("add", "src/app.py")))
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "CODE_START_BLOCKED" in decision["permissionDecisionReason"]

    def test_no_github_allows_documentation_write(self, tmp_path: Path) -> None:
        root = _initialized_project(tmp_path)
        patch = _patch("add", "docs/notes.md")
        assert authorize_hook_payload(_payload(root, "apply_patch", patch)) == {}

    def test_write_tool_formal_target_follows_same_boundary(self, tmp_path: Path) -> None:
        root = _initialized_project(tmp_path)
        output = authorize_hook_payload(_write_payload(root, "src/app.py"))
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "CODE_START_BLOCKED" in decision["permissionDecisionReason"]

    def test_write_tool_documentation_target_allows(self, tmp_path: Path) -> None:
        root = _initialized_project(tmp_path)
        assert authorize_hook_payload(_write_payload(root, "docs/design/notes.md")) == {}

    def test_github_boundary_allow_lets_formal_write_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _initialized_project(tmp_path)
        import codex_ai_os.application.hook_gateway as gateway

        monkeypatch.setattr(
            gateway, "_formal_boundary", lambda root, github_hosts: _allowed_start()
        )
        patch = _patch("add", "src/app.py")
        assert authorize_hook_payload(_payload(root, "apply_patch", patch)) == {}

    def test_github_boundary_block_denies_formal_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _initialized_project(tmp_path)
        import codex_ai_os.application.hook_gateway as gateway

        monkeypatch.setattr(
            gateway,
            "_formal_boundary",
            lambda root, github_hosts: _blocked_start("GITHUB_REMOTE_UNREACHABLE"),
        )
        output = authorize_hook_payload(_payload(root, "apply_patch", _patch("add", "src/app.py")))
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "GITHUB_REMOTE_UNREACHABLE" in decision["permissionDecisionReason"]

    def test_declared_write_conflicting_user_dirty_file_asks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _initialized_project(tmp_path)
        (root / "src").mkdir()
        (root / "src" / "auth.py").write_text("x = 1\n", encoding="utf-8")
        _git(root, "add", "src/auth.py")
        _git(root, "commit", "-m", "baseline")
        (root / "src" / "auth.py").write_text("x = 2\n", encoding="utf-8")
        import codex_ai_os.application.hook_gateway as gateway

        monkeypatch.setattr(
            gateway, "_formal_boundary", lambda root, github_hosts: _allowed_start()
        )
        patch = _patch("update", "src/auth.py")
        output = authorize_hook_payload(_payload(root, "apply_patch", patch))
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "ask"
        assert "USER_DIRTY_CONFLICT" in decision["permissionDecisionReason"]

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

    def test_fake_worktree_cwd_fails_closed(self, tmp_path: Path) -> None:
        root = _initialized_project(tmp_path)
        fake = root / ".worktrees" / "fake"
        fake.mkdir(parents=True)
        git_file = fake / ".git"
        git_file.write_text(f"gitdir: {root / '.git' / 'worktrees' / 'fake'}\n", encoding="utf-8")
        worktree_git_dir = root / ".git" / "worktrees" / "fake"
        worktree_git_dir.mkdir(parents=True)
        (worktree_git_dir / "commondir").write_text("../..\n", encoding="utf-8")
        payload = _payload(fake, "apply_patch", _patch("add", "docs/notes.md"))
        output = authorize_hook_payload(payload)
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "WORKTREE_NOT_REGISTERED" in decision["permissionDecisionReason"]


class TestAuthorizeHookCommand:
    def test_cli_denies_and_exits_zero(self, tmp_path: Path) -> None:
        root = _initialized_project(tmp_path)
        result = RUNNER.invoke(
            app,
            ["authorize-hook"],
            input=json.dumps(
                _payload(root, "apply_patch", _patch("add", ".codex-os/state/inject.db"))
            ),
        )
        assert result.exit_code == 0
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"

    def test_cli_invalid_input_exits_nonzero(self) -> None:
        result = RUNNER.invoke(app, ["authorize-hook"], input="not-json")
        assert result.exit_code == 1
