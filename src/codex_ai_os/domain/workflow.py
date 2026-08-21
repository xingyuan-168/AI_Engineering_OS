"""Workflow phases, runtime states, gates, tasks, and host actions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from pydantic import Field, model_validator

from codex_ai_os.domain.config import RiskLevel, StrictModel


class WorkflowPhase(StrEnum):
    INTAKE = "intake"
    REQUIREMENTS = "requirements"
    RESEARCH = "research"
    DESIGN = "design"
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
    FAILED = "failed"


class NextAction(StrictModel):
    kind: ActionKind
    task_id: str | None = None
    gate: Gate | None = None
    agent: str | None = None
    skill: str | None = None
    prompt: str | None = None
    input_artifacts: tuple[str, ...] = ()
    output_schema: dict[str, Any] = Field(default_factory=dict)
    allowed_paths: tuple[str, ...] = ()
    risk_level: RiskLevel = RiskLevel.MEDIUM
    requires_repository_change: bool = False

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


class TaskCompletion(StrictModel):
    task_id: str
    change_kind: ChangeKind
    branch: str | None = None
    commit_sha: str | None = None
    remote_name: str | None = None
    push_status: PushStatus = PushStatus.NOT_REQUIRED
    artifact_paths_and_hashes: dict[str, str] = Field(default_factory=dict)
    verification_results: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_git_evidence(self) -> Self:
        if self.change_kind is ChangeKind.NONE:
            if self.push_status is not PushStatus.NOT_REQUIRED:
                raise ValueError("read-only completion must use push_status=not_required")
            return self

        if not self.branch or not self.commit_sha or not self.remote_name:
            raise ValueError("repository completion requires branch, commit_sha, and remote_name")
        if self.push_status is not PushStatus.PUSHED:
            raise ValueError("repository completion requires push_status=pushed")
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", self.commit_sha):
            raise ValueError("commit_sha must be a 7-40 character hexadecimal Git SHA")
        if not self.artifact_paths_and_hashes:
            raise ValueError("repository completion requires at least one artifact hash")
        for path, digest in self.artifact_paths_and_hashes.items():
            normalized_parts = path.replace("\\", "/").split("/")
            if not path or path.startswith(("/", "\\")) or ".." in normalized_parts:
                raise ValueError(f"artifact path must be project-relative: {path}")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise ValueError(f"artifact hash must be SHA-256: {path}")
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
            "docs/SECURITY.md",
            "docs/ADR/",
        ),
        RiskLevel.MEDIUM,
    ),
    WorkflowPhase.IMPLEMENTATION: PhaseDefinition(
        "backend-engineer",
        "backend-implementation",
        "Implement the approved backend slice with migrations and tests in the assigned worktree.",
        ("src/", "tests/", "migrations/", "docs/CHANGELOG.md"),
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
        ("dist/", "docs/CHANGELOG.md", "reports/"),
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
