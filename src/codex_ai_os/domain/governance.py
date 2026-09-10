"""Repository, evidence, review, release, and memory governance value objects."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
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
    started_at: str
    ended_at: str
    executed_at: str
    status: CheckStatus

    @model_validator(mode="after")
    def exit_code_matches_status(self) -> Self:
        if self.status is CheckStatus.PASSED and self.exit_code != 0:
            raise ValueError("passed check evidence requires exit_code=0")
        if self.status is CheckStatus.FAILED and self.exit_code == 0:
            raise ValueError("failed check evidence requires a non-zero exit code")
        try:
            started = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
            ended = datetime.fromisoformat(self.ended_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("check evidence timestamps must be ISO 8601") from exc
        if started.tzinfo is None or ended.tzinfo is None:
            raise ValueError("check evidence timestamps must include a timezone")
        if ended < started:
            raise ValueError("check evidence ended_at cannot precede started_at")
        return self


class ReviewDecision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"


class ReviewFindingSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReviewFindingStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class ReviewFinding(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    severity: ReviewFindingSeverity
    status: ReviewFindingStatus
    summary: str = Field(min_length=1, max_length=2000)


class ReviewEvidenceInput(StrictModel):
    review_type: str = Field(pattern=r"^(code|security|handoff|release|ux-prototype)$")
    reviewer: str = Field(min_length=1, max_length=128)
    reviewed_commit: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")
    decision: ReviewDecision
    findings: tuple[ReviewFinding, ...] = ()
    risks: tuple[str, ...] = ()
    report_ref: str = Field(min_length=1, max_length=1024)
    report_hash: str = Field(pattern=r"^[0-9a-fA-F]{64}$")

    @model_validator(mode="after")
    def accepted_review_has_no_open_blocker(self) -> Self:
        if self.decision is ReviewDecision.ACCEPTED and any(
            finding.status is ReviewFindingStatus.OPEN
            and finding.severity
            in {ReviewFindingSeverity.HIGH, ReviewFindingSeverity.CRITICAL}
            for finding in self.findings
        ):
            raise ValueError("accepted review cannot contain open high/critical findings")
        return self


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


def is_unsafe_repository_path(value: str) -> bool:
    """Apply Windows and POSIX lexical rules regardless of the runtime host."""
    return (
        not value
        or PurePosixPath(value) == PurePosixPath(".")
        or PurePosixPath(value).is_absolute()
        or bool(PureWindowsPath(value).drive)
        or PureWindowsPath(value).is_reserved()
        or ":" in value  # Also excludes NTFS alternate data streams.
        or "\ufffd" in value
        or any(ord(character) < 32 for character in value)
        or any(
            part == ".." or (part not in {"", "."} and part.endswith((".", " ")))
            for part in value.split("/")
        )
    )


def governed_path_allowed(path: str, allowed_paths: tuple[str, ...]) -> bool:
    """Shared project-relative allowance used by Git evidence, artifact checks,
    environment operations and the authorization kernel (ADR-0011).

    A path is allowed when it equals an allowed file entry or lies below an
    allowed entry. A directory entry (trailing ``/``) only admits paths
    strictly below it — the directory itself is not a writable artifact path.
    Absolute paths, ``..`` segments and drive-qualified paths are never
    allowed.
    """

    normalized = path.replace("\\", "/")
    posix = PurePosixPath(normalized)
    if posix.is_absolute() or ".." in posix.parts:
        return False
    if is_unsafe_repository_path(normalized):
        return False
    candidate = posix.as_posix()
    for raw_allowed in allowed_paths:
        normalized_allowed = raw_allowed.replace("\\", "/")
        if normalized_allowed.endswith("/"):
            # Directory entry: only strictly-below paths are artifacts.
            if candidate.startswith(normalized_allowed):
                return True
            continue
        allowed = PurePosixPath(normalized_allowed).as_posix()
        if candidate == allowed or candidate.startswith(f"{allowed}/"):
            return True
    return False