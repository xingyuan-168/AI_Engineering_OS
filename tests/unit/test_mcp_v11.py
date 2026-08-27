from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, cast

from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.cli import mcp_server
from codex_ai_os.domain.config import GitPushPolicy, ProjectType
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.operations import HostOperationStore


def test_mcp_repository_workflow_and_validation_error_contracts(tmp_path: Path) -> None:
    invalid = mcp_server.project_init(
        str(tmp_path / "invalid"), "PROJECT-INVALID", "Invalid", project_type="unknown"
    )
    assert invalid["ok"] is False
    assert invalid["error"]["code"] == "CONFIG_INVALID"

    root = _project(tmp_path / "mcp")
    repository = mcp_server.repository_check(str(root), target_branch="main")
    assert repository["ok"] is True
    assert _data(repository)["repository_ready"] is True

    started = mcp_server.workflow_start(
        str(root),
        "Exercise direct MCP contracts",
        workflow_name="feature-development",
        profiles=["backend"],
        target_branch="main",
    )
    assert started["ok"] is True
    started_data = _data(started)
    run_id = str(cast(dict[str, Any], started_data["run"])["id"])
    status_data = _data(mcp_server.workflow_status(str(root), run_id))
    assert cast(dict[str, Any], status_data["run"])["id"] == run_id
    assert mcp_server.workflow_step(str(root), run_id)["next_action"] is not None
    assert mcp_server.workflow_resume(str(root), run_id)["ok"] is True

    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    prepare = mcp_server.verification_prepare(
        str(root),
        run_id,
        expected_state_version=int(cast(dict[str, Any], started_data["run"])["state_version"]),
        idempotency_key="prepare-cache",
        network_approval_ref="APPROVED-NETWORK",
        expires_at="2026-08-28T00:00:00+00:00",
    )
    assert prepare["ok"] is True
    assert prepare["next_action"]["kind"] == "host_operation"
    prepare_operation = cast(dict[str, Any], _data(prepare)["operation"])
    assert prepare_operation["kind"] == "verification_prepare"

    operation_store = HostOperationStore(Database(root / ".codex-os" / "state" / "state.db"))
    running_operation = operation_store.acquire(
        str(prepare_operation["operation_id"]),
        expected_version=int(prepare_operation["state_version"]),
        lease_owner="test",
    )
    unknown_operation = operation_store.mark_outcome_unknown(
        running_operation.operation_id,
        expected_version=running_operation.state_version,
        lease_owner="test",
    )
    reconciled = mcp_server.host_operation_reconcile(
        str(root),
        unknown_operation.operation_id,
        unknown_operation.state_version,
        "prepare-cache",
        "not_applied",
    )
    assert reconciled["ok"] is True
    assert reconciled["next_action"]["kind"] == "host_operation"
    assert cast(dict[str, Any], _data(reconciled)["operation"])["status"] == "pending"

    migrated = mcp_server.database_migrate(
        str(root),
        expected_schema_version="0007",
        target_schema_version="0007",
        idempotency_key="migrate-noop",
    )
    assert migrated["ok"] is True
    migration_operation = cast(dict[str, Any], _data(migrated)["operation"])
    assert migration_operation["kind"] == "database_migrate"
    assert migration_operation["status"] == "succeeded"

    partial_verification = mcp_server.verification_run(str(root), run_id=run_id)
    assert partial_verification["ok"] is False
    assert partial_verification["error"]["code"] == "CONFIG_INVALID"
    invalid_completion = mcp_server.task_complete(
        str(root),
        run_id,
        str(cast(dict[str, Any], started_data["active_task"])["id"]),
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
        expected_handoff_version=1,
        idempotency_key="missing-handoff-review",
    )
    assert missing_handoff["ok"] is False
    assert missing_handoff["error"]["code"] == "RECOVERY_UNAVAILABLE"
    missing_operation = mcp_server.host_operation_execute(
        str(root), "OP-MISSING", 0, "missing-operation"
    )
    assert missing_operation["ok"] is False
    assert missing_operation["error"]["code"] == "RECOVERY_UNAVAILABLE"
    cleanup = mcp_server.worktree_cleanup(
        str(root), "TASK-MISSING", "main", "host", "owner", "merged"
    )
    assert cleanup["ok"] is False
    assert cleanup["error"]["code"] == "RECOVERY_UNAVAILABLE"
    docs = mcp_server.docs_check(str(root))
    assert docs["ok"] is True
    assert _data(docs)["valid"] is True


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
    memory_id = str(cast(dict[str, Any], _data(candidate)["record"])["id"])
    activated = mcp_server.memory_review(
        str(root),
        memory_id,
        "owner",
        "activate",
        "source verified",
        expected_version=0,
    )
    assert cast(dict[str, Any], _data(activated)["record"])["status"] == "active"
    stale = mcp_server.memory_review(
        str(root),
        memory_id,
        "owner",
        "needs_review",
        "stale version",
        expected_version=0,
    )
    assert stale["ok"] is False
    assert stale["error"]["code"] == "MEMORY_INVALID"
    searched = mcp_server.memory_search(
        str(root),
        "MCP",
        record_types=["decision"],
        statuses=["active"],
        tags=["mcp"],
        source_ref="docs/MCP_MEMORY.md",
    )
    results = cast(list[dict[str, Any]], _data(searched)["results"])
    assert [item["id"] for item in results] == [memory_id]

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


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    assert payload["api_version"] == "1.2"
    assert str(payload["request_id"]).startswith("REQ-")
    assert str(payload["correlation_id"]).startswith("CORR-")
    return cast(dict[str, Any], payload["data"])


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
