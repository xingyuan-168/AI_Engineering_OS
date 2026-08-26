"""Repository, evidence, review, release, and memory governance value objects."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from codex_ai_os.domain.config import StrictModel
from codex_ai_os.domain.versions import RUNTIME_VERSIONS


class EvidenceStatus(StrEnum):
    CANDIDATE = "candidate"
    VERIFIED = "verified"
    STALE = "stale"
    REJECTED = "rejected"


class ArtifactEvidenceInput(StrictModel):
    path: str = Field(min_length=1, max_length=1024)
    artifact_type: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    source_commit: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")
    task_id: str | None = None
    status: EvidenceStatus = EvidenceStatus.VERIFIED


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    STALE = "stale"


class CheckEvidenceInput(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    command_hash: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    execution_id: str
    exit_code: int
    report_path: str = Field(min_length=1, max_length=1024)
    report_hash: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    source_commit: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")
    executed_at: str
    status: CheckStatus

    @model_validator(mode="after")
    def exit_code_matches_status(self) -> Self:
        if self.status is CheckStatus.PASSED and self.exit_code != 0:
            raise ValueError("passed check evidence requires exit_code=0")
        if self.status is CheckStatus.FAILED and self.exit_code == 0:
            raise ValueError("failed check evidence requires a non-zero exit code")
        return self


class ReviewDecision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"


class ReviewEvidenceInput(StrictModel):
    review_type: str = Field(pattern=r"^(code|security|handoff|release)$")
    reviewer: str = Field(min_length=1, max_length=128)
    reviewed_commit: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")
    decision: ReviewDecision
    findings: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    report_ref: str = Field(min_length=1, max_length=1024)
    report_hash: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class HandoffStatus(StrEnum):
    READY = "ready"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"


class TaskGroupStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    JOINING = "joining"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class MemoryStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    NEEDS_REVIEW = "needs_review"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"
    EXPIRED = "expired"
    DELETED = "deleted"


class RepositoryFinding(StrictModel):
    code: str
    severity: str = Field(pattern=r"^(info|warning|error|critical)$")
    message: str
    path: str | None = None
    blocking: bool = True


class RepositoryCheckReport(StrictModel):
    repository_ready: bool
    mode: str
    root: str
    target_branch: str
    head_commit: str | None = None
    remote_name: str | None = None
    remote_host: str | None = None
    upstream_ref: str | None = None
    hygiene_ok: bool
    findings: tuple[RepositoryFinding, ...]
    check_hash: str
    checked_at: str


class ReleaseAuthority(StrictModel):
    authorized: bool
    scope: str = Field(pattern=r"^tag-and-github-release$")
    authorized_by: str = Field(min_length=1, max_length=128)


class G4ApprovalInput(StrictModel):
    pr_number: int = Field(gt=0)
    pr_url: str = Field(pattern=r"^https://[^\s]+/pull/\d+$")
    merge_commit: str = Field(pattern=r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
    version: str = Field(min_length=1, max_length=32)
    release_authority: ReleaseAuthority

    @model_validator(mode="after")
    def authority_must_be_explicit(self) -> Self:
        if not self.release_authority.authorized:
            raise ValueError("G4 requires explicit tag and GitHub Release authorization")
        if self.version != RUNTIME_VERSIONS.software:
            raise ValueError(f"G4 version must be {RUNTIME_VERSIONS.software}")
        return self
