"""Offline HTML prototype validation and independent UX confirmation."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

from codex_ai_os.application.workflow import WorkflowError
from codex_ai_os.domain.governance import (
    ReviewDecision,
    ReviewFinding,
    ReviewFindingSeverity,
    ReviewFindingStatus,
)
from codex_ai_os.domain.ids import new_id
from codex_ai_os.domain.invocation import InvocationContext
from codex_ai_os.domain.workflow import Gate, RunStatus, TaskStatus, WorkflowPhase, WorkflowRun
from codex_ai_os.infrastructure.config import load_project_config
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.documents import UNAPPROVED_PLACEHOLDER, DocumentManager
from codex_ai_os.infrastructure.workflows import WorkflowStore

REQUIRED_PROTOTYPE_STATES = frozenset(
    {
        "success",
        "empty",
        "loading",
        "validation",
        "permission",
        "failure",
        "retry",
        "cancel",
        "resume",
    }
)
EXTERNAL_REFERENCE = re.compile(r"(?i)(?:src|href)\s*=\s*['\"](?:https?:|//|/|\.\.?/)")


@dataclass(frozen=True, slots=True)
class PrototypeValidationReport:
    valid: bool
    findings: tuple[str, ...]
    states: tuple[str, ...]
    control_count: int


class _PrototypeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: set[str] = set()
        self.states: set[str] = set()
        self.controls = 0
        self.inputs: list[tuple[str | None, str | None]] = []
        self.label_targets: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        self.tags.add(normalized)
        mapping = {name.casefold(): value for name, value in attrs}
        state = mapping.get("data-state")
        if state:
            self.states.add(state.casefold())
        if normalized in {"button", "input", "select", "textarea", "a"}:
            self.controls += 1
        if normalized in {"input", "select", "textarea"}:
            self.inputs.append((mapping.get("id"), mapping.get("aria-label")))
        if normalized == "label" and mapping.get("for"):
            self.label_targets.add(str(mapping["for"]))


def validate_html_prototype(content: bytes) -> PrototypeValidationReport:
    findings: list[str] = []
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return PrototypeValidationReport(False, ("prototype is not valid UTF-8",), (), 0)
    parser = _PrototypeParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:  # HTMLParser exposes parser defects as ValueError subclasses.
        findings.append(f"prototype HTML cannot be parsed: {exc}")
    missing_tags = {"html", "head", "body"} - parser.tags
    if missing_tags:
        findings.append(f"prototype is missing HTML structure: {sorted(missing_tags)}")
    if EXTERNAL_REFERENCE.search(text):
        findings.append("prototype contains an external or non-embedded resource reference")
    missing_states = REQUIRED_PROTOTYPE_STATES - parser.states
    if missing_states:
        findings.append(f"prototype is missing interaction states: {sorted(missing_states)}")
    if parser.controls == 0:
        findings.append("prototype has no interactive controls")
    for control_id, aria_label in parser.inputs:
        if not aria_label and (not control_id or control_id not in parser.label_targets):
            findings.append("prototype contains an unlabelled form control")
            break
    if UNAPPROVED_PLACEHOLDER.search(text):
        findings.append("prototype contains an unapproved placeholder")
    return PrototypeValidationReport(
        valid=not findings,
        findings=tuple(findings),
        states=tuple(sorted(parser.states)),
        control_count=parser.controls,
    )


class PrototypeReviewService:
    def __init__(self, project_root: Path) -> None:
        self.config = load_project_config(project_root.resolve())
        self.database = Database(self.config.root / ".codex-os" / "state" / "state.db")
        self.database.migrate()
        self.store = WorkflowStore(self.database)
        self.documents = DocumentManager(self.config.root)

    def submit(
        self,
        *,
        run_id: str,
        task_id: str,
        expected_task_version: int,
        expected_state_version: int,
        idempotency_key: str,
        prototype_path: str,
        prototype_hash: str,
        reviewed_commit: str,
        decision: ReviewDecision,
        reviewer: str,
        reason: str,
        findings: tuple[ReviewFinding, ...] = (),
        invocation: InvocationContext | None = None,
    ) -> WorkflowRun:
        if not idempotency_key.strip() or not reason.strip():
            raise WorkflowError("CONFIG_INVALID", "idempotency_key and reason are required", 2)
        request = {
            "run_id": run_id,
            "task_id": task_id,
            "expected_task_version": expected_task_version,
            "expected_state_version": expected_state_version,
            "prototype_path": prototype_path,
            "prototype_hash": prototype_hash.casefold(),
            "reviewed_commit": reviewed_commit.casefold(),
            "decision": decision.value,
            "reason": reason,
            "findings": [item.model_dump(mode="json") for item in findings],
        }
        request_hash = hashlib.sha256(_json(request).encode("utf-8")).hexdigest()
        existing_hash = self.store.idempotency_request_hash(idempotency_key)
        if existing_hash is not None:
            if existing_hash == request_hash:
                return self.store.get_run(run_id)
            raise WorkflowError(
                "IDEMPOTENCY_CONFLICT",
                "idempotency key was already used for a different prototype review",
                30,
            )
        run = self.store.get_run(run_id)
        task = self.store.get_task(task_id)
        if (
            run.state_version != expected_state_version
            or task.state_version != expected_task_version
        ):
            raise WorkflowError(
                "STATE_VERSION_CONFLICT",
                "prototype review state changed before submission",
                30,
                retryable=True,
            )
        if (
            run.workflow_phase is not WorkflowPhase.PROTOTYPE
            or run.run_status is not RunStatus.NEEDS_APPROVAL
            or run.checkpoint.get("pending_gate") != Gate.G2.value
            or run.checkpoint.get("last_completed_task") != task.id
            or task.status is not TaskStatus.COMPLETED
        ):
            raise WorkflowError(
                "GATE_BLOCKED",
                "prototype review is only valid for the completed task before frontend G2",
                40,
            )
        if invocation is None:
            raise WorkflowError(
                "ROLE_NOT_AUTHORIZED",
                "prototype review requires a trusted invocation context",
                20,
            )
        trusted_reviewer = invocation.principal
        if not trusted_reviewer or trusted_reviewer.casefold() == task.agent.casefold():
            raise WorkflowError(
                "ROLE_NOT_AUTHORIZED", "prototype producer cannot confirm its own UX review", 20
            )
        if decision is ReviewDecision.ACCEPTED and any(
            finding.status is ReviewFindingStatus.OPEN
            and finding.severity in {ReviewFindingSeverity.HIGH, ReviewFindingSeverity.CRITICAL}
            for finding in findings
        ):
            raise WorkflowError(
                "GATE_BLOCKED",
                "accepted prototype review cannot contain open high/critical findings",
                40,
            )
        if task.head_commit is None or reviewed_commit.casefold() != task.head_commit.casefold():
            raise WorkflowError(
                "REVIEW_STALE", "prototype review Commit is not the current task Commit", 40
            )
        normalized = _prototype_path(prototype_path)
        worktree = Path(task.worktree or self.config.root).resolve()
        content = _committed_file(worktree, reviewed_commit, normalized)
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash.casefold() != prototype_hash.casefold():
            raise WorkflowError("EVIDENCE_STALE", "prototype artifact hash changed", 40)
        validation = validate_html_prototype(content)
        if decision is ReviewDecision.ACCEPTED and not validation.valid:
            raise WorkflowError(
                "GATE_BLOCKED",
                "HTML prototype validator failed",
                40,
                details={
                    "findings": list(validation.findings),
                    "repair_actions": [
                        {
                            "path": normalized,
                            "operation": "amend_task_evidence",
                            "task_id": task.id,
                            "expected": "valid offline HTML prototype",
                            "actual": list(validation.findings),
                            "allowed": True,
                        }
                    ],
                },
                retryable=True,
            )
        return self._persist_review(
            run=run,
            task_id=task.id,
            expected_task_version=expected_task_version,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            prototype_path=normalized,
            prototype_hash=actual_hash,
            reviewed_commit=reviewed_commit,
            decision=decision,
            reviewer=trusted_reviewer,
            reason=reason,
            findings=findings,
            validation=validation,
        )

    def _persist_review(
        self,
        *,
        run: WorkflowRun,
        task_id: str,
        expected_task_version: int,
        idempotency_key: str,
        request_hash: str,
        prototype_path: str,
        prototype_hash: str,
        reviewed_commit: str,
        decision: ReviewDecision,
        reviewer: str,
        reason: str,
        findings: tuple[ReviewFinding, ...],
        validation: PrototypeValidationReport,
    ) -> WorkflowRun:
        now = _utc_now()
        report_payload = {
            "prototype_path": prototype_path,
            "prototype_hash": prototype_hash,
            "reviewed_commit": reviewed_commit.casefold(),
            "validator": {
                "valid": validation.valid,
                "findings": list(validation.findings),
                "states": list(validation.states),
                "control_count": validation.control_count,
            },
            "review": {
                "decision": decision.value,
                "reviewer": reviewer,
                "reason": reason,
                "findings": [item.model_dump(mode="json") for item in findings],
            },
        }
        report_bytes = (_json(report_payload) + "\n").encode("utf-8")
        report_hash = hashlib.sha256(report_bytes).hexdigest()
        report_path = f".codex-os/artifacts/{run.id}/prototype-review-{request_hash}.json"
        self.documents.write_atomic(
            report_path,
            report_bytes.decode("utf-8"),
            overwrite=False,
        )
        execution_id = new_id("EXECUTION")
        review_id = new_id("REVIEWEVIDENCE")
        check_status = "passed" if validation.valid else "failed"
        exit_code = 0 if validation.valid else 1
        command_hash = hashlib.sha256(b"html-prototype-validator@1.2").hexdigest()
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                current_run = connection.execute(
                    "SELECT state_version FROM workflow_runs WHERE id = ?", (run.id,)
                ).fetchone()
                current_task = connection.execute(
                    "SELECT state_version FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                if (
                    current_run is None
                    or int(current_run["state_version"]) != run.state_version
                    or current_task is None
                    or int(current_task["state_version"]) != expected_task_version
                ):
                    raise WorkflowError(
                        "STATE_VERSION_CONFLICT", "prototype review changed concurrently", 30
                    )
                connection.execute(
                    """
                    INSERT INTO executions(
                        id, task_id, risk_level, command_hash, exit_code,
                        stdout_ref, stderr_ref, started_at, ended_at, status,
                        error_code, network_mode, mounts_json, run_id
                    ) VALUES (?, ?, 'medium', ?, ?, ?, NULL, ?, ?, ?, ?, 'disabled', '[]', ?)
                    """,
                    (
                        execution_id,
                        task_id,
                        command_hash,
                        exit_code,
                        report_path,
                        now,
                        now,
                        "completed" if validation.valid else "failed",
                        None if validation.valid else "PROTOTYPE_INVALID",
                        run.id,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO artifact_evidence(
                        id, bundle_id, run_id, task_id, path, artifact_type,
                        content_hash, source_commit, status, created_at
                    ) VALUES (?, NULL, ?, ?, ?, 'html-prototype', ?, ?, 'verified', ?)
                    """,
                    (
                        new_id("ARTEVIDENCE"),
                        run.id,
                        task_id,
                        prototype_path,
                        prototype_hash,
                        reviewed_commit.casefold(),
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO check_evidence(
                        id, bundle_id, run_id, task_id, check_name, command_hash,
                        execution_id, exit_code, report_path, report_hash,
                        source_commit, status, executed_at, started_at, ended_at
                    ) VALUES (
                        ?, NULL, ?, ?, 'html-prototype-validator', ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        new_id("CHECKEVIDENCE"),
                        run.id,
                        task_id,
                        command_hash,
                        execution_id,
                        exit_code,
                        report_path,
                        report_hash,
                        reviewed_commit.casefold(),
                        check_status,
                        now,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO review_evidence(
                        id, bundle_id, project_id, run_id, task_id, review_type,
                        reviewer, reviewed_commit, decision, findings_json, risks_json,
                        report_ref, report_hash, created_at
                    ) VALUES (?, NULL, ?, ?, ?, 'handoff', ?, ?, ?, ?, '[]', ?, ?, ?)
                    """,
                    (
                        review_id,
                        run.project_id,
                        run.id,
                        task_id,
                        reviewer,
                        reviewed_commit.casefold(),
                        decision.value,
                        _json([item.model_dump(mode="json") for item in findings]),
                        report_path,
                        report_hash,
                        now,
                    ),
                )
                task_update = connection.execute(
                    "UPDATE tasks SET review_status = ?, state_version = state_version + 1, "
                    "updated_at = ? WHERE id = ? AND state_version = ?",
                    (decision.value, now, task_id, expected_task_version),
                )
                if task_update.rowcount != 1:
                    raise WorkflowError(
                        "STATE_VERSION_CONFLICT", "prototype task changed concurrently", 30
                    )
                checkpoint = {
                    **run.checkpoint,
                    "prototype_review_id": review_id,
                    "prototype_review_commit": reviewed_commit.casefold(),
                    "prototype_review_hash": report_hash,
                }
                run_update = connection.execute(
                    "UPDATE workflow_runs SET checkpoint_json = ?, "
                    "state_version = state_version + 1, "
                    "updated_at = ? WHERE id = ? AND state_version = ?",
                    (_json(checkpoint), now, run.id, run.state_version),
                )
                if run_update.rowcount != 1:
                    raise WorkflowError(
                        "STATE_VERSION_CONFLICT", "prototype workflow changed concurrently", 30
                    )
                connection.execute(
                    """
                    INSERT INTO events(
                        event_id, project_id, run_id, task_id, event_type,
                        idempotency_key, payload_json, approval_required, created_at
                    ) VALUES (?, ?, ?, ?, 'prototype.reviewed', ?, ?, 1, ?)
                    """,
                    (
                        new_id("EVENT"),
                        run.project_id,
                        run.id,
                        task_id,
                        idempotency_key,
                        _json(
                            {
                                **report_payload,
                                "request_hash": request_hash,
                                "report_hash": report_hash,
                                "report_path": report_path,
                            }
                        ),
                        now,
                    ),
                )
                connection.commit()
            except (sqlite3.Error, WorkflowError):
                connection.rollback()
                raise
        return self.store.get_run(run.id)


def _prototype_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(path.parts) != 4
        or path.parts[:2] != ("docs", "prototypes")
        or path.name.casefold() != "index.html"
    ):
        raise WorkflowError(
            "PATH_POLICY_VIOLATION",
            "prototype entry must be docs/prototypes/<prototype-id>/index.html",
            40,
        )
    return path.as_posix()


def _committed_file(root: Path, commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{path}"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        raise WorkflowError("EVIDENCE_STALE", "prototype is not present in the Commit", 40)
    return result.stdout


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "REQUIRED_PROTOTYPE_STATES",
    "PrototypeReviewService",
    "PrototypeValidationReport",
    "validate_html_prototype",
]
