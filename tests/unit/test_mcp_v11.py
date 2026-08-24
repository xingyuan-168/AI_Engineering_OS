from __future__ import annotations

import subprocess
from pathlib import Path

from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.cli import mcp_server
from codex_ai_os.domain.config import GitPushPolicy, ProjectType


def test_mcp_repository_workflow_and_validation_error_contracts(tmp_path: Path) -> None:
    invalid = mcp_server.project_init(
        str(tmp_path / "invalid"), "PROJECT-INVALID", "Invalid", project_type="unknown"
    )
    assert invalid["ok"] is False
    assert invalid["error"]["code"] == "CONFIG_INVALID"

    root = _project(tmp_path / "mcp")
    repository = mcp_server.repository_check(str(root), target_branch="main")
    assert repository["ok"] is True
    assert repository["repository_ready"] is True

    started = mcp_server.workflow_start(
        str(root),
        "Exercise direct MCP contracts",
        workflow_name="feature-development",
        profiles=["backend"],
        target_branch="main",
    )
    assert started["ok"] is True
    run_id = str(started["run"]["id"])
    assert mcp_server.workflow_status(str(root), run_id)["run"]["id"] == run_id
    assert mcp_server.workflow_step(str(root), run_id)["next_action"] is not None
    assert mcp_server.workflow_resume(str(root), run_id)["ok"] is True

    partial_verification = mcp_server.verification_run(str(root), run_id=run_id)
    assert partial_verification["ok"] is False
    assert partial_verification["error"]["code"] == "CONFIG_INVALID"
    invalid_completion = mcp_server.task_complete(
        str(root),
        run_id,
        str(started["active_task"]["id"]),
        "invalid",
        artifact_paths_and_hashes={},
    )
    assert invalid_completion["ok"] is False
    assert invalid_completion["error"]["code"] == "CONFIG_INVALID"

    early_approval = mcp_server.approval_submit(
        str(root), run_id, "G4", True, "reviewer", "too early"
    )
    assert early_approval["ok"] is False
    assert early_approval["error"]["code"] == "APPROVAL_REQUIRED"
    missing_handoff = mcp_server.handoff_review(
        str(root),
        "HANDOFF-MISSING",
        "reviewer",
        "a" * 40,
        "accepted",
        "reviewed",
        "review.md",
        "b" * 64,
    )
    assert missing_handoff["ok"] is False
    assert missing_handoff["error"]["code"] == "RECOVERY_UNAVAILABLE"
    cleanup = mcp_server.worktree_cleanup(
        str(root), "TASK-MISSING", "main", "host", "owner", "merged"
    )
    assert cleanup["ok"] is False
    assert cleanup["error"]["code"] == "RECOVERY_UNAVAILABLE"
    docs = mcp_server.docs_check(str(root))
    assert docs["ok"] is True
    assert docs["valid"] is True


def test_mcp_memory_candidate_review_search_and_errors(tmp_path: Path) -> None:
    root = _project(tmp_path / "memory")
    content = root / "docs" / "MCP_MEMORY.md"
    source = root / "docs" / "ADR" / "0099-mcp-source.md"
    content.write_text("# Durable MCP decision\n\nUse source-linked evidence.\n", encoding="utf-8")
    source.write_text("# Source\n\nAccepted decision.\n", encoding="utf-8")

    candidate = mcp_server.memory_candidate_submit(
        str(root),
        "decision",
        "MCP source-linked decision",
        "docs/MCP_MEMORY.md",
        ["docs/ADR/0099-mcp-source.md"],
        0.95,
        tags=["mcp", "governance"],
    )
    assert candidate["ok"] is True
    memory_id = str(candidate["record"]["id"])
    activated = mcp_server.memory_review(
        str(root), memory_id, "owner", "activate", "source verified"
    )
    assert activated["record"]["status"] == "active"
    searched = mcp_server.memory_search(
        str(root),
        "MCP",
        record_types=["decision"],
        statuses=["active"],
        tags=["mcp"],
        source_ref="docs/MCP_MEMORY.md",
    )
    assert [item["id"] for item in searched["results"]] == [memory_id]

    missing = mcp_server.memory_review(
        str(root), "MEMORY-MISSING", "owner", "activate", "missing"
    )
    assert missing["ok"] is False
    assert missing["error"]["code"] == "MEMORY_INVALID"
    invalid_limit = mcp_server.memory_search(str(root), "", limit=0)
    assert invalid_limit["ok"] is False
    assert invalid_limit["error"]["code"] == "MEMORY_INVALID"


def _project(root: Path) -> Path:
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    ProjectInitializer().initialize(
        root,
        project_id=f"PROJECT-{root.name.upper()}",
        name="MCP fixture",
        project_type=ProjectType.BACKEND,
        git_push_policy=GitPushPolicy.FIXTURE_LOCAL_ONLY,
        schema_version="1.1",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "chore: initialize MCP fixture")
    return root


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
