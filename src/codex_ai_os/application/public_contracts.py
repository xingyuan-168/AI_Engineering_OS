"""Single registry for the public MCP/CLI/application contract surface."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PublicToolContract:
    mcp_tool: str
    cli_commands: tuple[str, ...]
    application_service: str
    writes_state: bool
    concurrency_fields: tuple[str, ...] = ()

    @property
    def cli_documentation(self) -> str:
        return "|".join(f"codex-os {command}" for command in self.cli_commands)


PUBLIC_TOOL_CONTRACTS: tuple[PublicToolContract, ...] = (
    PublicToolContract("project_init", ("init",), "ProjectInitializer", True),
    PublicToolContract("repository_check", ("repo-check",), "RepositoryGovernanceService", False),
    PublicToolContract(
        "workflow_start",
        ("run new-project", "run feature-development", "run bug-fix", "run release"),
        "WorkflowEngine",
        True,
    ),
    PublicToolContract(
        "workflow_create",
        ("workflow create",),
        "WorkflowEngine",
        True,
        ("idempotency_key",),
    ),
    PublicToolContract(
        "workflow_begin",
        ("workflow begin",),
        "WorkflowEngine",
        True,
        ("expected_state_version", "idempotency_key"),
    ),
    PublicToolContract(
        "workflow_cancel",
        ("workflow cancel",),
        "WorkflowEngine",
        True,
        ("expected_state_version", "idempotency_key"),
    ),
    PublicToolContract("workflow_status", ("status",), "WorkflowEngine", False),
    PublicToolContract("workflow_step", ("step",), "WorkflowEngine", False),
    PublicToolContract(
        "gate_preflight",
        ("gate validate",),
        "WorkflowEngine",
        False,
        ("expected_state_version",),
    ),
    PublicToolContract("workflow_resume", ("resume",), "WorkflowEngine", True),
    PublicToolContract(
        "approval_submit",
        ("approve", "reject"),
        "WorkflowEngine",
        True,
        ("expected_state_version", "idempotency_key", "evidence_bundle_hash"),
    ),
    PublicToolContract(
        "task_complete",
        ("task complete",),
        "WorkflowEngine",
        True,
        ("expected_task_version", "idempotency_key"),
    ),
    PublicToolContract(
        "task_amend_evidence",
        ("task amend-evidence",),
        "WorkflowEngine",
        True,
        ("expected_task_version", "expected_state_version", "idempotency_key"),
    ),
    PublicToolContract(
        "prototype_review_submit",
        ("prototype review",),
        "PrototypeReviewService",
        True,
        ("expected_task_version", "expected_state_version", "idempotency_key"),
    ),
    PublicToolContract(
        "handoff_review",
        ("handoff review",),
        "WorkflowEngine",
        True,
        ("expected_handoff_version", "idempotency_key"),
    ),
    PublicToolContract(
        "host_operation_execute",
        ("host-operation execute",),
        "WorkflowEngine",
        True,
        ("expected_operation_version", "idempotency_key"),
    ),
    PublicToolContract(
        "host_operation_reconcile",
        ("host-operation reconcile",),
        "HostOperationMaintenanceService",
        True,
        ("expected_operation_version", "idempotency_key"),
    ),
    PublicToolContract("worktree_cleanup", ("worktree cleanup",), "WorktreeService", True),
    PublicToolContract("docs_check", ("check-docs",), "DocumentManager", False),
    PublicToolContract(
        "environment_check",
        ("environment check",),
        "EnvironmentGovernanceService",
        False,
    ),
    PublicToolContract(
        "environment_adopt",
        ("environment adopt",),
        "EnvironmentOperationService",
        True,
        ("expected_state_version", "expected_task_version", "idempotency_key"),
    ),
    PublicToolContract(
        "environment_prepare",
        ("environment prepare",),
        "EnvironmentOperationService",
        True,
        ("expected_state_version", "expected_task_version", "idempotency_key"),
    ),
    PublicToolContract(
        "environment_verify",
        ("environment verify",),
        "EnvironmentOperationService",
        True,
        ("expected_state_version", "expected_task_version", "idempotency_key"),
    ),
    PublicToolContract("verification_run", ("verify",), "VerificationService", True),
    PublicToolContract(
        "verification_prepare",
        ("verification prepare",),
        "VerificationPrepareService",
        True,
        ("expected_state_version", "idempotency_key"),
    ),
    PublicToolContract(
        "database_migrate",
        ("database migrate",),
        "DatabaseMigrationService",
        True,
        ("expected_schema_version", "idempotency_key"),
    ),
    PublicToolContract(
        "release_candidate_create",
        ("release candidate",),
        "ReleaseCandidateService",
        True,
        ("expected_task_version",),
    ),
    PublicToolContract("memory_candidate_submit", ("memory submit",), "MemoryStore", True),
    PublicToolContract(
        "memory_review",
        ("memory review",),
        "MemoryStore",
        True,
        ("expected_version",),
    ),
    PublicToolContract("memory_search", ("memory search",), "MemoryStore", False),
)

PUBLIC_ENVELOPE_FIELDS = frozenset(
    {
        "api_version",
        "request_id",
        "correlation_id",
        "ok",
        "run_id",
        "run_status",
        "workflow_phase",
        "state_version",
        "next_actions",
        "next_action",
        "warnings",
        "data",
        "error",
    }
)


def public_tool_names() -> frozenset[str]:
    return frozenset(contract.mcp_tool for contract in PUBLIC_TOOL_CONTRACTS)
