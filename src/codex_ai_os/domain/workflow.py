"""Workflow phases, runtime states, gates, tasks, and host actions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from pydantic import Field, model_validator

from codex_ai_os.domain.config import RiskLevel, StrictModel
from codex_ai_os.domain.governance import ArtifactEvidenceInput, CheckEvidenceInput


class WorkflowPhase(StrEnum):
    INTAKE = "intake"
    REQUIREMENTS = "requirements"
    RESEARCH = "research"
    DESIGN = "design"
    PROTOTYPE = "prototype"
    IMPLEMENTATION = "implementation"
    VERIFY = "verify"
    RELEASE = "release"
    MEMORY = "memory"
    COMPLETED = "completed"


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    NEEDS_APPROVAL = "needs_approval"
    PAUSED = "paused"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class Gate(StrEnum):
    G0 = "G0"
    G1 = "G1"
    G2 = "G2"
    G3 = "G3"
    G4 = "G4"


class ActionKind(StrEnum):
    MODEL_TASK = "model_task"
    APPROVAL = "approval"
    HOST_OPERATION = "host_operation"
    COMPLETE = "complete"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChangeKind(StrEnum):
    NONE = "none"
    REPOSITORY = "repository"


class PushStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PUSHED = "pushed"
    LOCAL_ONLY = "local_only"
    FAILED = "failed"


class NextAction(StrictModel):
    kind: ActionKind
    operation_id: str | None = None
    task_id: str | None = None
    task_group_id: str | None = None
    gate: Gate | None = None
    agent: str | None = None
    skill: str | None = None
    prompt: str | None = None
    input_artifacts: tuple[str, ...] = ()
    output_schema: dict[str, Any] = Field(default_factory=dict)
    allowed_paths: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    branch: str | None = None
    worktree: str | None = None
    risk_level: RiskLevel = RiskLevel.MEDIUM
    requires_repository_change: bool = False
    expected_task_version: int | None = Field(default=None, ge=0)
    expected_state_version: int | None = Field(default=None, ge=0)
    expected_operation_version: int | None = Field(default=None, ge=0)
    gate_requirements: dict[str, Any] | None = None

    @model_validator(mode="after")
    def fields_match_action_kind(self) -> Self:
        if self.kind is ActionKind.MODEL_TASK:
            if not all((self.task_id, self.agent, self.skill, self.prompt)):
                raise ValueError("model_task requires task_id, agent, skill, and prompt")
            if self.gate is not None:
                raise ValueError("model_task cannot carry a gate")
        elif self.kind is ActionKind.APPROVAL:
            if self.gate is None:
                raise ValueError("approval action requires a gate")
            if self.task_id is not None:
                raise ValueError("approval action cannot carry a task_id")
        elif self.kind is ActionKind.HOST_OPERATION:
            if self.operation_id is None:
                raise ValueError("host_operation requires operation_id")
            if self.gate is not None:
                raise ValueError("host_operation cannot carry a gate")
        elif self.kind is ActionKind.COMPLETE and any((self.task_id, self.gate)):
            raise ValueError("complete action cannot carry task_id or gate")
        return self


class WorkflowRun(StrictModel):
    id: str
    project_id: str
    workflow_name: str
    goal: str
    workflow_phase: WorkflowPhase
    run_status: RunStatus
    state_version: int = Field(ge=0)
    risk_level: RiskLevel
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    config_hash: str
    created_at: str
    updated_at: str
    profiles: tuple[str, ...] = ()
    target_branch: str = "main"
    integration_branch: str | None = None
    base_commit: str | None = None
    integration_head: str | None = None
    max_parallel_agents: int = Field(default=4, ge=1, le=4)
    migration_revalidation_required: bool = False


class TaskRecord(StrictModel):
    id: str
    run_id: str
    agent: str
    status: TaskStatus
    input_hash: str
    branch: str | None = None
    worktree: str | None = None
    output_ref: str | None = None
    review_status: str | None = None
    state_version: int = Field(ge=0)
    created_at: str
    updated_at: str
    task_group_id: str | None = None
    task_key: str | None = None
    allowed_paths: tuple[str, ...] = ()
    head_commit: str | None = None
    producer: str | None = None
    skill: str | None = None
    prompt: str | None = None


class TaskCompletion(StrictModel):
    task_id: str
    change_kind: ChangeKind
    branch: str | None = None
    commit_sha: str | None = None
    remote_name: str | None = None
    push_status: PushStatus = PushStatus.NOT_REQUIRED
    artifact_paths_and_hashes: dict[str, str] = Field(default_factory=dict)
    verification_results: tuple[str, ...] = ()
    artifacts: tuple[ArtifactEvidenceInput, ...] = ()
    checks: tuple[CheckEvidenceInput, ...] = ()

    @model_validator(mode="after")
    def validate_git_evidence(self) -> Self:
        if self.change_kind is ChangeKind.NONE:
            if self.push_status is not PushStatus.NOT_REQUIRED:
                raise ValueError("read-only completion must use push_status=not_required")
            return self

        if not self.branch or not self.commit_sha:
            raise ValueError("repository completion requires branch and commit_sha")
        if self.push_status not in {PushStatus.PUSHED, PushStatus.LOCAL_ONLY}:
            raise ValueError("repository completion requires pushed or local-only Git evidence")
        if self.push_status is PushStatus.PUSHED and not self.remote_name:
            raise ValueError("pushed repository completion requires remote_name")
        if self.push_status is PushStatus.LOCAL_ONLY and self.remote_name is not None:
            raise ValueError("local-only repository completion cannot claim a remote")
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}", self.commit_sha):
            raise ValueError("commit_sha must be a 7-64 character hexadecimal Git SHA")
        if not self.artifact_paths_and_hashes:
            raise ValueError("repository completion requires at least one artifact hash")
        for path, digest in self.artifact_paths_and_hashes.items():
            normalized_parts = path.replace("\\", "/").split("/")
            if not path or path.startswith(("/", "\\")) or ".." in normalized_parts:
                raise ValueError(f"artifact path must be project-relative: {path}")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise ValueError(f"artifact hash must be SHA-256: {path}")
        for artifact in self.artifacts:
            if artifact.source_commit.casefold() != self.commit_sha.casefold():
                raise ValueError("artifact source_commit must match completion commit_sha")
            legacy_hash = self.artifact_paths_and_hashes.get(artifact.path)
            if legacy_hash is not None and legacy_hash.casefold() != artifact.sha256.casefold():
                raise ValueError("structured and compatibility artifact hashes disagree")
        for check in self.checks:
            if check.source_commit.casefold() != self.commit_sha.casefold():
                raise ValueError("check source_commit must match completion commit_sha")
        return self


@dataclass(frozen=True, slots=True)
class PhaseDefinition:
    agent: str
    skill: str
    prompt: str
    allowed_paths: tuple[str, ...]
    risk_level: RiskLevel


PHASE_DEFINITIONS: dict[WorkflowPhase, PhaseDefinition] = {
    WorkflowPhase.INTAKE: PhaseDefinition(
        "product-manager",
        "requirement-analysis",
        "Analyze the goal and prepare preliminary scope, success criteria, "
        "and risk evidence for G0.",
        ("docs/PROJECT_MASTER.md", "docs/SCOPE.md"),
        RiskLevel.MEDIUM,
    ),
    WorkflowPhase.REQUIREMENTS: PhaseDefinition(
        "product-manager",
        "requirement-analysis",
        "Produce detailed requirements, user stories, business rules, "
        "and acceptance criteria for G1.",
        (
            "docs/PRODUCT_REQUIREMENTS.md",
            "docs/USER_STORY.md",
            "docs/BUSINESS_RULES.md",
            "docs/SCOPE.md",
        ),
        RiskLevel.MEDIUM,
    ),
    WorkflowPhase.RESEARCH: PhaseDefinition(
        "architect",
        "open-source-research",
        "Research official sources, versions, licenses, reuse boundaries, and risks.",
        ("docs/OPEN_SOURCE_RESEARCH.md", "docs/TECH_STACK.md", "docs/ADR/"),
        RiskLevel.MEDIUM,
    ),
    WorkflowPhase.DESIGN: PhaseDefinition(
        "architect",
        "architecture-design",
        "Produce architecture, API, database, security, and migration design evidence for G2.",
        (
            "docs/ARCHITECTURE.md",
            "docs/API_SPEC.md",
            "docs/DATABASE.md",
            "docs/MIGRATION_SPEC.md",
            "docs/SECURITY.md",
            "docs/PRODUCT_DESIGN.md",
            "docs/INTERACTION_DESIGN.md",
            "docs/UI_DESIGN.md",
            "docs/RISK_REGISTER.md",
            "docs/AGENT_HANDOFF.md",
            "docs/ADR/",
        ),
        RiskLevel.MEDIUM,
    ),
    WorkflowPhase.PROTOTYPE: PhaseDefinition(
        "frontend-engineer",
        "html-prototype",
        "Build an offline, self-contained HTML interaction prototype covering all "
        "required UI states, then request independent UX confirmation.",
        (
            "docs/PRODUCT_DESIGN.md",
            "docs/INTERACTION_DESIGN.md",
            "docs/UI_DESIGN.md",
            "docs/prototypes/",
        ),
        RiskLevel.MEDIUM,
    ),
    WorkflowPhase.IMPLEMENTATION: PhaseDefinition(
        "backend-engineer",
        "backend-implementation",
        "Implement the approved backend slice with migrations and tests in the assigned worktree.",
        (
            "src/",
            "tests/",
            "migrations/",
            "pyproject.toml",
            "uv.lock",
            "docs/CHANGELOG.md",
        ),
        RiskLevel.HIGH,
    ),
    WorkflowPhase.VERIFY: PhaseDefinition(
        "qa",
        "testing",
        "Run tests, review, dependency checks, and security verification; record evidence for G3.",
        ("tests/", "reports/", "docs/TEST_PLAN.md"),
        RiskLevel.HIGH,
    ),
    WorkflowPhase.RELEASE: PhaseDefinition(
        "release-manager",
        "release-manager",
        "Create the release candidate, changelog, SBOM, checksums, and rollback evidence.",
        ("release/", "docs/CHANGELOG.md", "reports/"),
        RiskLevel.HIGH,
    ),
    WorkflowPhase.MEMORY: PhaseDefinition(
        "memory-manager",
        "memory-manager",
        "Record decisions, failures, release evidence, source hashes, "
        "and invalidation metadata for G4.",
        ("docs/ADR/", "docs/CHANGELOG.md", ".codex-os/memory/"),
        RiskLevel.MEDIUM,
    ),
}

GATE_AFTER_PHASE: dict[WorkflowPhase, Gate] = {
    WorkflowPhase.INTAKE: Gate.G0,
    WorkflowPhase.REQUIREMENTS: Gate.G1,
    WorkflowPhase.DESIGN: Gate.G2,
    WorkflowPhase.PROTOTYPE: Gate.G2,
    WorkflowPhase.VERIFY: Gate.G3,
    WorkflowPhase.MEMORY: Gate.G4,
}

PHASE_AFTER_APPROVAL: dict[Gate, WorkflowPhase] = {
    Gate.G0: WorkflowPhase.REQUIREMENTS,
    Gate.G1: WorkflowPhase.RESEARCH,
    Gate.G2: WorkflowPhase.IMPLEMENTATION,
    Gate.G3: WorkflowPhase.RELEASE,
    Gate.G4: WorkflowPhase.COMPLETED,
}

AUTOMATIC_NEXT_PHASE: dict[WorkflowPhase, WorkflowPhase] = {
    WorkflowPhase.RESEARCH: WorkflowPhase.DESIGN,
    WorkflowPhase.IMPLEMENTATION: WorkflowPhase.VERIFY,
    WorkflowPhase.RELEASE: WorkflowPhase.MEMORY,
}

WORKFLOW_START_PHASE: dict[str, WorkflowPhase] = {
    "new-project": WorkflowPhase.INTAKE,
    "feature-development": WorkflowPhase.REQUIREMENTS,
    "bug-fix": WorkflowPhase.IMPLEMENTATION,
    "release": WorkflowPhase.VERIFY,
}
