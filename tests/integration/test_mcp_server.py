from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import Client, StdioServerParameters, stdio_client

from codex_ai_os.adapters.git import GitEvidenceResult, GitEvidenceService
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
        "task_complete",
        "docs_check",
        "verification_run",
        "release_candidate_create",
        "memory_search",
    }


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
    assert initialized["project_id"] == "PROJECT-MCP"
    assert started["run"]["workflow_phase"] == "intake"
    assert started["next_action"]["kind"] == "model_task"
    assert checked == {
        "ok": True,
        "valid": True,
        "checks": [
            {"name": "sqlite_integrity", "ok": True},
            {"name": "document_governance", "ok": True},
        ],
    }


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
    assert list((tmp_path / "dist" / "release-candidates").glob("RUN-*.json"))


def _structured(result: Any) -> dict[str, Any]:
    return cast(dict[str, Any], result.structured_content)
