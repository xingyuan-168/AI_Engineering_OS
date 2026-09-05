# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import Client
from typer.testing import CliRunner

from codex_ai_os.application.public_contracts import (
    PUBLIC_ENVELOPE_FIELDS,
    PUBLIC_TOOL_CONTRACTS,
    public_tool_names,
)
from codex_ai_os.application.responses import success_envelope
from codex_ai_os.cli import app as cli_app
from codex_ai_os.cli import mcp_server
from codex_ai_os.domain.invocation import InvocationContext, InvocationSource
from codex_ai_os.domain.operations import ReconciliationOutcome
from codex_ai_os.domain.workflow import ChangeKind, PushStatus


def test_public_registry_matches_mcp_cli_application_and_api_docs() -> None:
    async def mcp_contracts() -> tuple[set[str], dict[str, set[str]]]:
        async with Client(mcp_server.mcp, raise_exceptions=True) as client:
            tools = await client.list_tools()
        return (
            {tool.name for tool in tools.tools},
            {
                tool.name: set(
                    cast(dict[str, object], tool.input_schema.get("properties", {}))
                )
                for tool in tools.tools
            },
        )

    names, schema_properties = asyncio.run(mcp_contracts())
    assert names == public_tool_names()

    runner = CliRunner()
    for contract in PUBLIC_TOOL_CONTRACTS:
        handler = getattr(mcp_server, contract.mcp_tool)
        source = inspect.getsource(handler)
        assert contract.application_service in source
        assert set(contract.concurrency_fields) <= schema_properties[contract.mcp_tool]
        for command in contract.cli_commands:
            result = runner.invoke(cli_app.app, [*command.split(), "--help"])
            assert result.exit_code == 0, (command, result.output)
        assert contract.cli_documentation.startswith("codex-os ")

    documentation = Path("docs/API_SPEC.md").read_text(encoding="utf-8")
    for contract in PUBLIC_TOOL_CONTRACTS:
        assert f"| `{contract.mcp_tool}` |" in documentation


def test_success_envelope_exactly_contains_the_registered_contract_fields() -> None:
    assert set(success_envelope({})) == PUBLIC_ENVELOPE_FIELDS


def test_task_complete_cli_uses_shared_model_and_concurrency_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class _Engine:
        def __init__(self, root: Path) -> None:
            assert root == tmp_path

        def complete_task(
            self,
            run_id: str,
            completion: Any,
            *,
            expected_task_version: int,
            idempotency_key: str,
        ) -> object:
            captured.update(
                run_id=run_id,
                completion=completion,
                expected_task_version=expected_task_version,
                idempotency_key=idempotency_key,
            )
            return object()

    monkeypatch.setattr(cli_app, "WorkflowEngine", _Engine)
    def emit_result(result: object, *, json_output: bool) -> None:
        captured.update(result=result, json=json_output)

    monkeypatch.setattr(cli_app, "_emit_workflow", emit_result)
    evidence = json.dumps(
        {
            "artifact_paths_and_hashes": {"docs/API_SPEC.md": "a" * 64},
            "verification_results": ["legacy audit note"],
        }
    )
    result = CliRunner().invoke(
        cli_app.app,
        [
            "task",
            "complete",
            "RUN-1",
            "--task-id",
            "TASK-1",
            "--expected-task-version",
            "4",
            "--idempotency-key",
            "complete-task-1",
            "--change-kind",
            "repository",
            "--branch",
            "codex/task",
            "--commit-sha",
            "b" * 40,
            "--remote-name",
            "origin",
            "--push-status",
            "pushed",
            "--evidence-json",
            evidence,
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    completion = captured["completion"]
    assert completion.change_kind is ChangeKind.REPOSITORY
    assert completion.push_status is PushStatus.PUSHED
    assert captured["expected_task_version"] == 4
    assert captured["idempotency_key"] == "complete-task-1"

    invalid = CliRunner().invoke(
        cli_app.app,
        [
            "task",
            "complete",
            "RUN-1",
            "--task-id",
            "TASK-1",
            "--expected-task-version",
            "4",
            "--idempotency-key",
            "complete-task-1",
            "--change-kind",
            "none",
            "--evidence-json",
            "[]",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )
    assert invalid.exit_code == 2
    assert "CONFIG_INVALID" in invalid.output


def test_public_cli_failures_use_the_shared_error_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing-project"
    failures: list[tuple[str, int, bool]] = []

    def record_failure(
        code: str,
        _message: str,
        exit_code: int,
        json_output: bool,
        *,
        retryable: bool = False,
        details: dict[str, object] | None = None,
    ) -> None:
        del retryable, details
        failures.append((code, exit_code, json_output))

    monkeypatch.setattr(cli_app, "_fail", record_failure)
    cli_app.task_complete_command(
        "RUN-1",
        "TASK-1",
        0,
        "task-complete",
        ChangeKind.NONE,
        '{"artifacts":{},"checks":[]}',
        project_root=missing,
        json_output=True,
    )
    cli_app.verification_prepare_command(
        "RUN-1",
        0,
        "verification-prepare",
        "APPROVAL-1",
        "2026-09-01T00:00:00+00:00",
        project_root=missing,
        json_output=True,
    )
    cli_app.host_operation_execute_command(
        "OP-1",
        0,
        "operation-execute",
        project_root=missing,
        json_output=True,
    )
    cli_app.host_operation_reconcile_command(
        "OP-1",
        0,
        "operation-reconcile",
        ReconciliationOutcome.NOT_APPLIED,
        project_root=missing,
        json_output=True,
    )
    cli_app.database_migrate_command(
        "0006",
        project_root=missing,
        json_output=True,
    )
    cli_app.release_candidate_command(
        "RUN-1",
        project_root=missing,
        json_output=True,
    )
    cli_app.memory_submit_command(
        "release",
        "Release memory",
        "docs/RELEASE.md",
        [],
        1.0,
        project_root=missing,
        json_output=True,
    )
    cli_app.memory_review_command(
        "MEMORY-1",
        "reviewer",
        "accepted",
        "reviewed",
        project_root=missing,
        json_output=True,
    )
    cli_app.memory_search_command(
        "release",
        project_root=missing,
        json_output=True,
    )

    class _FailingWorkflowEngine:
        def __init__(self, _root: Path) -> None:
            pass

        def complete_task(self, *_args: object, **_kwargs: object) -> object:
            raise cli_app.WorkflowError("STATE_VERSION_CONFLICT", "stale task", 30)

    monkeypatch.setattr(cli_app, "WorkflowEngine", _FailingWorkflowEngine)
    cli_app.task_complete_command(
        "RUN-1",
        "TASK-1",
        0,
        "task-complete-conflict",
        ChangeKind.NONE,
        "{}",
        project_root=missing,
        json_output=True,
    )

    assert len(failures) == 10
    assert all(json_output for _code, _exit, json_output in failures)
    assert cli_app._trusted_reviewer_warnings(
        "os:fixture",
        InvocationContext(
            request_id="REQ-1",
            correlation_id="CORR-1",
            principal="os:fixture",
            source=InvocationSource.CLI,
        ),
    ) == ()
