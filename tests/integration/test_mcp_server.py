from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import Client, StdioServerParameters, stdio_client

from codex_ai_os.adapters.git import GitEvidenceResult, GitEvidenceService
from codex_ai_os.adapters.worktree import WorktreeSpec, WorktreeState
from codex_ai_os.application.worktree import WorktreeService
from codex_ai_os.cli.mcp_server import mcp
from codex_ai_os.domain.workflow import TaskCompletion


def test_mcp_lists_the_public_runtime_tools() -> None:
    async def exercise() -> set[str]:
        async with Client(mcp, raise_exceptions=True) as client:
            result = await client.list_tools()
            return {tool.name for tool in result.tools}

    assert asyncio.run(exercise()) == {
        "project_init",
        "workflow_start",
        "workflow_status",
        "workflow_step",
        "workflow_resume",
        "approval_submit",
        "repository_check",
        "task_complete",
        "handoff_review",
        "host_operation_execute",
        "worktree_cleanup",
        "docs_check",
        "verification_run",
        "release_candidate_create",
        "memory_candidate_submit",
        "memory_review",
        "memory_search",
    }


@pytest.mark.skipif(os.name != "nt", reason="the repository plugin launcher is Windows-only")
def test_plugin_launcher_serves_the_same_tools_over_stdio() -> None:
    async def exercise() -> set[str]:
        launcher = (
            Path(__file__).parents[2]
            / "plugins"
            / "ai-engineering-os"
            / "scripts"
            / "launch_mcp.cmd"
        )
        parameters = StdioServerParameters(
            command="cmd.exe",
            args=["/d", "/s", "/c", "call", str(launcher)],
        )
        async with Client(stdio_client(parameters), raise_exceptions=True) as client:
            result = await client.list_tools()
            return {tool.name for tool in result.tools}

    names = asyncio.run(exercise())
    assert "workflow_start" in names
    assert "task_complete" in names


def test_mcp_initializes_and_starts_a_workflow(tmp_path: Path) -> None:
    async def exercise() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        async with Client(mcp, raise_exceptions=True) as client:
            initialized = await client.call_tool(
                "project_init",
                {
                    "project_root": str(tmp_path),
                    "project_id": "PROJECT-MCP",
                    "name": "MCP fixture",
                    "project_type": "backend",
                },
            )
            _use_legacy_fixture_config(tmp_path)
            _initialize_git_repository(tmp_path)
            started = await client.call_tool(
                "workflow_start",
                {"project_root": str(tmp_path), "goal": "Build an ERP API"},
            )
            checked = await client.call_tool(
                "verification_run",
                {"project_root": str(tmp_path)},
            )
            return (
                cast(dict[str, Any], initialized.structured_content),
                cast(dict[str, Any], started.structured_content),
                cast(dict[str, Any], checked.structured_content),
            )

    initialized, started, checked = asyncio.run(exercise())
    assert initialized["ok"] is True
    _assert_api_envelope(initialized)
    _assert_api_envelope(started)
    _assert_api_envelope(checked)
    assert initialized["data"]["project_id"] == "PROJECT-MCP"
    assert started["data"]["run"]["workflow_phase"] == "intake"
    assert started["next_action"]["kind"] == "model_task"
    assert started["next_action"]["branch"].startswith("agent/product-manager/")
    assert ".worktrees" in started["next_action"]["worktree"]
    assert checked["data"] == {
        "valid": True,
        "checks": [
            {"name": "sqlite_integrity", "ok": True},
            {"name": "document_governance", "ok": True},
        ],
        "deprecated": True,
    }
    assert checked["warnings"] == [
        "Supply run_id and task_id for governed verification."
    ]


def test_mcp_host_handshake_completes_the_full_fixture_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def allow_fixture_evidence(
        _service: GitEvidenceService, completion: TaskCompletion
    ) -> GitEvidenceResult:
        return GitEvidenceResult(
            branch=completion.branch or "missing",
            commit_sha=completion.commit_sha or "missing",
            remote_name=completion.remote_name or "missing",
            remote_url="test://origin",
            artifact_hashes=dict(completion.artifact_paths_and_hashes),
            verified_at="2026-08-21T00:00:00+00:00",
        )

    monkeypatch.setattr(GitEvidenceService, "verify", allow_fixture_evidence)

    def allocate_fixture_worktree(
        _service: WorktreeService,
        *,
        run_id: str,
        task_id: str,
        base_ref: str = "HEAD",
    ) -> WorktreeState:
        spec = WorktreeSpec(
            workflow_id=run_id,
            agent="fixture-agent",
            task_id=task_id,
            branch=f"agent/fixture-agent/{task_id}",
            path=tmp_path / ".worktrees" / run_id / "fixture-agent" / task_id,
            base_commit=base_ref,
        )
        return WorktreeState(spec=spec, head_commit=base_ref, dirty=False)

    monkeypatch.setattr(WorktreeService, "allocate", allocate_fixture_worktree)

    async def exercise() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(mcp, raise_exceptions=True) as client:
            await client.call_tool(
                "project_init",
                {
                    "project_root": str(tmp_path),
                    "project_id": "PROJECT-MCP-E2E",
                    "name": "MCP end-to-end fixture",
                    "project_type": "backend",
                },
            )
            _use_legacy_fixture_config(tmp_path)
            current = _structured(
                await client.call_tool(
                    "workflow_start",
                    {"project_root": str(tmp_path), "goal": "Build an ERP API"},
                )
            )
            resumed = _structured(
                await client.call_tool(
                    "workflow_resume",
                    {"project_root": str(tmp_path), "run_id": current["run"]["id"]},
                )
            )
            assert resumed["next_action"] == current["next_action"]

            while current["run"]["run_status"] != "completed":
                action = current["next_action"]
                run_id = current["run"]["id"]
                if action["kind"] == "approval":
                    current = _structured(
                        await client.call_tool(
                            "approval_submit",
                            {
                                "project_root": str(tmp_path),
                                "run_id": run_id,
                                "gate": action["gate"],
                                "approved": True,
                                "reviewer": "fixture-reviewer",
                                "reason": "fixture evidence accepted",
                            },
                        )
                    )
                    continue

                artifact_path = action["allowed_paths"][0]
                if artifact_path.endswith("/"):
                    artifact_path += "fixture.txt"
                artifact_hash = "a" * 64
                if current["run"]["workflow_phase"] == "release":
                    candidate = _structured(
                        await client.call_tool(
                            "release_candidate_create",
                            {"project_root": str(tmp_path), "run_id": run_id},
                        )
                    )
                    assert candidate["created"] is True
                    artifact_path = candidate["path"]
                    artifact_hash = candidate["sha256"]

                current = _structured(
                    await client.call_tool(
                        "task_complete",
                        {
                            "project_root": str(tmp_path),
                            "run_id": run_id,
                            "task_id": action["task_id"],
                            "change_kind": "repository",
                            "branch": "codex/fixture",
                            "commit_sha": "a" * 40,
                            "remote_name": "origin",
                            "push_status": "pushed",
                            "artifact_paths_and_hashes": {artifact_path: artifact_hash},
                            "verification_results": ["fixture: passed"],
                        },
                    )
                )
                assert current.get("ok") is True, current

            status = _structured(
                await client.call_tool(
                    "workflow_status",
                    {"project_root": str(tmp_path), "run_id": current["run"]["id"]},
                )
            )
            memory = _structured(
                await client.call_tool(
                    "memory_search",
                    {"project_root": str(tmp_path), "query": "decision"},
                )
            )
            return status, memory

    status, memory = asyncio.run(exercise())
    assert status["run"]["workflow_phase"] == "completed"
    assert status["run"]["run_status"] == "completed"
    assert status["next_action"]["kind"] == "complete"
    assert memory["results"] == []
    assert (tmp_path / "release" / "manifest.json").is_file()


def _structured(result: Any) -> dict[str, Any]:
    payload = cast(dict[str, Any], result.structured_content)
    _assert_api_envelope(payload)
    data = cast(dict[str, Any], payload["data"])
    return {
        **data,
        "ok": payload["ok"],
        "next_action": payload["next_action"],
        "next_actions": payload["next_actions"],
    }


def _assert_api_envelope(payload: dict[str, Any]) -> None:
    assert payload["api_version"] == "1.2"
    assert str(payload["request_id"]).startswith("REQ-")
    assert str(payload["correlation_id"]).startswith("CORR-")
    next_actions = cast(list[object], payload["next_actions"])
    assert isinstance(next_actions, list)
    assert isinstance(payload["warnings"], list)
    if len(next_actions) == 1:
        assert payload["next_action"] == next_actions[0]


def _initialize_git_repository(root: Path) -> None:
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "test: initialize MCP project")


def _use_legacy_fixture_config(root: Path) -> None:
    config = root / ".codex-os" / "project.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "schema_version: '1.2'", "schema_version: '1.0'"
        ),
        encoding="utf-8",
    )


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()
