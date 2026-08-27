"""Task-group, dependency, Handoff review, and integration contracts."""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from codex_ai_os.domain.config import StrictModel
from codex_ai_os.domain.governance import (
    ReviewDecision,
    ReviewFinding,
    ReviewFindingSeverity,
    ReviewFindingStatus,
)


class TaskBlueprint(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    agent: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    skill: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    prompt: str = Field(min_length=1, max_length=2000)
    allowed_paths: tuple[str, ...]
    depends_on: tuple[str, ...] = ()

    @field_validator("allowed_paths")
    @classmethod
    def paths_are_safe_and_unique(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            path = value.replace("\\", "/").strip("/")
            if (
                not path
                or (path.startswith(".") and not path.startswith(".codex-os/memory"))
                or ".." in path.split("/")
            ):
                raise ValueError(f"unsafe task path: {value}")
            normalized.append(path + ("/" if value.endswith(("/", "\\")) else ""))
        if not normalized or len(normalized) != len(set(normalized)):
            raise ValueError("allowed_paths must be non-empty and unique")
        return tuple(normalized)


class HandoffReviewInput(StrictModel):
    handoff_id: str
    expected_handoff_version: int | None = Field(default=None, ge=0)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)
    reviewer: str = Field(min_length=1, max_length=128)
    reviewed_commit: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")
    decision: ReviewDecision
    reason: str = Field(min_length=1, max_length=2000)
    findings: tuple[ReviewFinding, ...] = ()
    risks: tuple[str, ...] = ()
    report_ref: str = Field(min_length=1, max_length=1024)
    report_hash: str = Field(pattern=r"^[0-9a-fA-F]{64}$")

    @model_validator(mode="after")
    def accepted_review_has_no_open_blocker(self) -> HandoffReviewInput:
        if self.decision is ReviewDecision.ACCEPTED and any(
            finding.status is ReviewFindingStatus.OPEN
            and finding.severity
            in {ReviewFindingSeverity.HIGH, ReviewFindingSeverity.CRITICAL}
            for finding in self.findings
        ):
            raise ValueError("accepted Handoff cannot contain open high/critical findings")
        return self


class TaskGroupView(StrictModel):
    id: str
    run_id: str
    name: str
    phase: str
    status: str
    base_commit: str
    state_version: int
    ready_task_ids: tuple[str, ...]
    blocked_task_ids: tuple[str, ...]
