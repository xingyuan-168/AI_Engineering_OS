"""Deterministic ``new-project`` workflow orchestration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast

from pydantic import ValidationError

from codex_ai_os.adapters.git import (
    GitEvidenceError,
    GitEvidenceService,
    GitEvidenceVerifier,
)
from codex_ai_os.application.worktree import (
    TaskWorktreeAllocator,
    WorktreeService,
    WorktreeServiceError,
)
from codex_ai_os.domain.config import GitPushPolicy
from codex_ai_os.domain.ids import new_id
from codex_ai_os.domain.workflow import (
    AUTOMATIC_NEXT_PHASE,
    GATE_AFTER_PHASE,
    PHASE_AFTER_APPROVAL,
    PHASE_DEFINITIONS,
    ActionKind,
    Gate,
    NextAction,
    RunStatus,
    TaskCompletion,
    TaskRecord,
    TaskStatus,
    WorkflowPhase,
    WorkflowRun,
)
from codex_ai_os.infrastructure.config import load_project_config
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.workflows import (
    WorkflowConflictError,
    WorkflowNotFoundError,
    WorkflowStore,
)
from codex_ai_os.infrastructure.worktrees import WorktreeStore


class WorkflowError(RuntimeError):
    def __init__(self, code: str, message: str, exit_code: int = 30) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    run: WorkflowRun
    next_action: NextAction | None
    active_task: TaskRecord | None


class WorkflowEngine:
    def __init__(
        self,
        project_root: Path,
        *,
        git_evidence: GitEvidenceVerifier | None = None,
        worktrees: TaskWorktreeAllocator | None = None,
    ) -> None:
        self.config = load_project_config(project_root.resolve())
        database = Database(self.config.root / ".codex-os" / "state" / "state.db")
        database.migrate()
        self.store = WorkflowStore(database)
        self.worktree_store = WorktreeStore(database)
        self.git_evidence = git_evidence
        self.worktrees = worktrees or WorktreeService(self.config.root)

    def start(self, goal: str, *, workflow_name: str = "new-project") -> WorkflowResult:
        normalized_goal = goal.strip()
        if not normalized_goal:
            raise WorkflowError("CONFIG_INVALID", "goal cannot be empty", 2)
        if workflow_name != "new-project":
            raise WorkflowError(
                "CONFIG_INVALID",
                "only new-project is available in this milestone",
                2,
            )

        existing = self.store.find_active_run(
            self.config.project_id, workflow_name, normalized_goal
        )
        if existing is not None:
            return self._result(existing)

        now = _utc_now()
        run_id = new_id("RUN")
        task, action = self._task_and_action(
            run_id,
            WorkflowPhase.INTAKE,
            state_version=0,
            goal=normalized_goal,
        )
        run = WorkflowRun(
            id=run_id,
            project_id=self.config.project_id,
            workflow_name=workflow_name,
            goal=normalized_goal,
            workflow_phase=WorkflowPhase.INTAKE,
            run_status=RunStatus.RUNNING,
            state_version=0,
            risk_level=self.config.risk_level,
            checkpoint=_checkpoint(action),
            config_hash=self.store.project_config_hash(self.config.project_id),
            created_at=now,
            updated_at=now,
        )
        created = self.store.create_run(run, task)
        self._allocate_or_block(created, task, base_ref="HEAD")
        return self._result(self.store.get_run(created.id))

    def status(self, run_id: str) -> WorkflowResult:
        try:
            return self._result(self.store.get_run(run_id))
        except WorkflowNotFoundError as exc:
            raise WorkflowError("RECOVERY_UNAVAILABLE", str(exc), 30) from exc

    def complete_task(self, run_id: str, completion: TaskCompletion) -> WorkflowResult:
        run = self._get_run(run_id)
        task = self._get_task(completion.task_id)
        completion_json = completion.model_dump_json()
        if task.run_id != run.id:
            raise WorkflowError("CONFIG_INVALID", "task does not belong to workflow run", 2)
        if task.status is TaskStatus.COMPLETED:
            if task.output_ref == completion_json:
                return self._result(run)
            raise WorkflowError(
                "STATE_CONFLICT",
                "task already completed with different evidence",
                30,
            )

        action = _action_from_checkpoint(run.checkpoint)
        if (
            action is None
            or action.kind is not ActionKind.MODEL_TASK
            or action.task_id != completion.task_id
        ):
            raise WorkflowError("STATE_CONFLICT", "task is not the current next action", 30)
        if action.requires_repository_change and completion.change_kind.value != "repository":
            raise WorkflowError(
                "GIT_EVIDENCE_REQUIRED",
                "this task changes repository facts and requires pushed Git evidence",
                40,
            )
        if task.branch is not None and completion.branch != task.branch:
            raise WorkflowError(
                "GIT_EVIDENCE_INVALID",
                f"completion branch does not match task assignment: "
                f"task={task.branch}, evidence={completion.branch}",
                40,
            )
        disallowed = [
            path
            for path in completion.artifact_paths_and_hashes
            if not _artifact_path_allowed(path, action.allowed_paths)
        ]
        if disallowed:
            raise WorkflowError(
                "PATH_POLICY_VIOLATION",
                f"artifacts are outside task allowed paths: {sorted(disallowed)}",
                40,
            )

        verified_git_evidence: dict[str, Any] | None = None
        if completion.change_kind.value == "repository":
            try:
                if self.git_evidence is not None:
                    verifier = self.git_evidence
                else:
                    assignment = self.worktree_store.get_for_task(task.id)
                    verifier = GitEvidenceService(
                        Path(task.worktree) if task.worktree is not None else self.config.root,
                        require_push=(
                            self.config.git_push_policy is GitPushPolicy.REMOTE_REQUIRED
                        ),
                        base_commit=(assignment.base_commit if assignment is not None else None),
                        allowed_paths=action.allowed_paths,
                    )
                evidence = verifier.verify(completion)
            except GitEvidenceError as exc:
                raise WorkflowError("GIT_EVIDENCE_INVALID", str(exc), 40) from exc
            verified_git_evidence = {
                "branch": evidence.branch,
                "commit_sha": evidence.commit_sha,
                "remote_name": evidence.remote_name,
                "remote_url": evidence.remote_url,
                "artifact_hashes": evidence.artifact_hashes,
                "verified_at": evidence.verified_at,
                "changed_paths": list(evidence.changed_paths),
            }

        next_version = run.state_version + 1
        next_task: TaskRecord | None = None
        if gate := GATE_AFTER_PHASE.get(run.workflow_phase):
            next_action = NextAction(
                kind=ActionKind.APPROVAL,
                gate=gate,
                prompt=f"Review evidence and approve or reject {gate.value}.",
                risk_level=run.risk_level,
            )
            target_phase = run.workflow_phase
            target_status = RunStatus.NEEDS_APPROVAL
            checkpoint = _checkpoint(
                next_action,
                pending_gate=gate,
                last_completed_task=task.id,
                last_commit_sha=completion.commit_sha,
                evidence_refs=tuple(completion.artifact_paths_and_hashes),
            )
        elif next_phase := AUTOMATIC_NEXT_PHASE.get(run.workflow_phase):
            next_task, next_action = self._task_and_action(
                run.id,
                next_phase,
                state_version=next_version,
                goal=run.goal,
            )
            target_phase = next_phase
            target_status = RunStatus.RUNNING
            checkpoint = _checkpoint(next_action, last_completed_task=task.id)
        else:
            raise WorkflowError(
                "STATE_CONFLICT",
                f"phase cannot complete without a transition: {run.workflow_phase.value}",
            )

        try:
            updated = self.store.complete_task_and_transition(
                run=run,
                task=task,
                completion=completion,
                target_phase=target_phase,
                target_status=target_status,
                checkpoint=checkpoint,
                next_task=next_task,
                verified_git_evidence=verified_git_evidence,
            )
        except WorkflowConflictError as exc:
            raise WorkflowError("STATE_CONFLICT", str(exc), 30) from exc
        if next_task is not None:
            self._allocate_or_block(
                updated,
                next_task,
                base_ref=completion.commit_sha or "HEAD",
            )
        return self._result(self.store.get_run(updated.id))

    def submit_approval(
        self,
        run_id: str,
        *,
        gate: Gate,
        approved: bool,
        reviewer: str,
        reason: str,
    ) -> WorkflowResult:
        run = self._get_run(run_id)
        if run.run_status is not RunStatus.NEEDS_APPROVAL:
            raise WorkflowError("APPROVAL_REQUIRED", "workflow is not waiting for approval", 20)
        pending_gate = run.checkpoint.get("pending_gate")
        if pending_gate != gate.value:
            raise WorkflowError(
                "APPROVAL_REQUIRED",
                f"workflow is waiting for {pending_gate}, not {gate.value}",
                20,
            )
        if not reviewer.strip() or not reason.strip():
            raise WorkflowError("CONFIG_INVALID", "reviewer and reason are required", 2)

        next_version = run.state_version + 1
        next_task: TaskRecord | None = None
        if approved:
            target_phase = PHASE_AFTER_APPROVAL[gate]
            if target_phase is WorkflowPhase.COMPLETED:
                target_status = RunStatus.COMPLETED
                next_action = NextAction(kind=ActionKind.COMPLETE)
                checkpoint = _checkpoint(next_action, approved_gate=gate)
            else:
                target_status = RunStatus.RUNNING
                next_task, next_action = self._task_and_action(
                    run.id,
                    target_phase,
                    state_version=next_version,
                    goal=run.goal,
                )
                checkpoint = _checkpoint(next_action, approved_gate=gate)
        else:
            target_phase = run.workflow_phase
            target_status = RunStatus.BLOCKED
            checkpoint = {
                "next_action": None,
                "rejected_gate": gate.value,
                "rejection_reason": reason,
            }

        try:
            updated = self.store.approval_transition(
                run=run,
                gate=gate,
                approved=approved,
                reviewer=reviewer,
                reason=reason,
                target_phase=target_phase,
                target_status=target_status,
                checkpoint=checkpoint,
                next_task=next_task,
            )
        except WorkflowConflictError as exc:
            raise WorkflowError("STATE_CONFLICT", str(exc), 30) from exc
        if next_task is not None:
            base_ref = str(run.checkpoint.get("last_commit_sha") or "HEAD")
            self._allocate_or_block(updated, next_task, base_ref=base_ref)
        return self._result(self.store.get_run(updated.id))

    def resume(self, run_id: str) -> WorkflowResult:
        run = self._get_run(run_id)
        if run.run_status in {RunStatus.RUNNING, RunStatus.NEEDS_APPROVAL}:
            return self._result(run)
        if run.run_status in {RunStatus.COMPLETED, RunStatus.CANCELLED}:
            raise WorkflowError("RECOVERY_UNAVAILABLE", "terminal workflow cannot resume", 30)

        active_task = self.store.active_task(run.id)
        next_task: TaskRecord | None = None
        if active_task is not None:
            action = self._action_for_existing_task(run, active_task)
        else:
            next_task, action = self._task_and_action(
                run.id,
                run.workflow_phase,
                state_version=run.state_version + 1,
                goal=run.goal,
            )
        checkpoint = _checkpoint(action, resumed_from=run.run_status.value)
        try:
            updated = self.store.resume_transition(
                run=run,
                checkpoint=checkpoint,
                next_task=next_task,
            )
        except WorkflowConflictError as exc:
            raise WorkflowError("STATE_CONFLICT", str(exc), 30) from exc
        if next_task is not None:
            base_ref = str(run.checkpoint.get("recovery_base_ref") or "HEAD")
            self._allocate_or_block(updated, next_task, base_ref=base_ref)
        return self._result(self.store.get_run(updated.id))

    def _result(self, run: WorkflowRun) -> WorkflowResult:
        action = _action_from_checkpoint(run.checkpoint)
        active_task = self.store.active_task(run.id)
        if action is not None and active_task is not None and action.kind is ActionKind.MODEL_TASK:
            action = action.model_copy(
                update={
                    "branch": active_task.branch,
                    "worktree": active_task.worktree,
                }
            )
        return WorkflowResult(run=run, next_action=action, active_task=active_task)

    def _allocate_or_block(
        self,
        run: WorkflowRun,
        task: TaskRecord,
        *,
        base_ref: str,
    ) -> None:
        try:
            self.worktrees.allocate(run_id=run.id, task_id=task.id, base_ref=base_ref)
        except WorktreeServiceError as exc:
            try:
                self.store.block_run_for_task(
                    run=run,
                    task=task,
                    error_code=exc.code,
                    reason=str(exc),
                    recovery_base_ref=base_ref,
                )
            except WorkflowConflictError as conflict:
                raise WorkflowError("STATE_CONFLICT", str(conflict), 30) from conflict
            raise WorkflowError(exc.code, str(exc), 40) from exc

    def _get_run(self, run_id: str) -> WorkflowRun:
        try:
            return self.store.get_run(run_id)
        except WorkflowNotFoundError as exc:
            raise WorkflowError("RECOVERY_UNAVAILABLE", str(exc), 30) from exc

    def _get_task(self, task_id: str) -> TaskRecord:
        try:
            return self.store.get_task(task_id)
        except WorkflowNotFoundError as exc:
            raise WorkflowError("RECOVERY_UNAVAILABLE", str(exc), 30) from exc

    def _task_and_action(
        self,
        run_id: str,
        phase: WorkflowPhase,
        *,
        state_version: int,
        goal: str,
    ) -> tuple[TaskRecord, NextAction]:
        definition = PHASE_DEFINITIONS.get(phase)
        if definition is None:
            raise WorkflowError("STATE_CONFLICT", f"phase has no task definition: {phase.value}")
        now = _utc_now()
        task_id = new_id("TASK")
        input_hash = hashlib.sha256(
            f"{run_id}:{phase.value}:{state_version}:{goal}".encode()
        ).hexdigest()
        task = TaskRecord(
            id=task_id,
            run_id=run_id,
            agent=definition.agent,
            status=TaskStatus.PENDING,
            input_hash=input_hash,
            state_version=0,
            created_at=now,
            updated_at=now,
        )
        action = NextAction(
            kind=ActionKind.MODEL_TASK,
            task_id=task_id,
            agent=definition.agent,
            skill=definition.skill,
            prompt=definition.prompt,
            input_artifacts=_input_artifacts_for(phase),
            output_schema={
                "type": "object",
                "required": ["summary", "artifacts", "verification_results"],
                "properties": {
                    "summary": {"type": "string"},
                    "artifacts": {"type": "object"},
                    "verification_results": {"type": "array", "items": {"type": "string"}},
                },
            },
            allowed_paths=definition.allowed_paths,
            risk_level=definition.risk_level,
            requires_repository_change=True,
        )
        return task, action

    def _action_for_existing_task(self, run: WorkflowRun, task: TaskRecord) -> NextAction:
        definition = PHASE_DEFINITIONS[run.workflow_phase]
        return NextAction(
            kind=ActionKind.MODEL_TASK,
            task_id=task.id,
            agent=definition.agent,
            skill=definition.skill,
            prompt=definition.prompt,
            input_artifacts=_input_artifacts_for(run.workflow_phase),
            output_schema={"type": "object"},
            allowed_paths=definition.allowed_paths,
            risk_level=definition.risk_level,
            requires_repository_change=True,
        )


def _checkpoint(
    action: NextAction,
    *,
    pending_gate: Gate | None = None,
    approved_gate: Gate | None = None,
    last_completed_task: str | None = None,
    last_commit_sha: str | None = None,
    evidence_refs: tuple[str, ...] = (),
    resumed_from: str | None = None,
) -> dict[str, Any]:
    return {
        "next_action": action.model_dump(mode="json"),
        "pending_gate": pending_gate.value if pending_gate is not None else None,
        "approved_gate": approved_gate.value if approved_gate is not None else None,
        "last_completed_task": last_completed_task,
        "last_commit_sha": last_commit_sha,
        "evidence_refs": list(evidence_refs),
        "resumed_from": resumed_from,
    }


def _action_from_checkpoint(checkpoint: dict[str, Any]) -> NextAction | None:
    raw = checkpoint.get("next_action")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise WorkflowError("RECOVERY_UNAVAILABLE", "invalid next action checkpoint", 30)
    try:
        return NextAction.model_validate(cast(dict[str, Any], raw))
    except ValidationError as exc:
        raise WorkflowError("RECOVERY_UNAVAILABLE", f"invalid next action: {exc}", 30) from exc


def _input_artifacts_for(phase: WorkflowPhase) -> tuple[str, ...]:
    return {
        WorkflowPhase.INTAKE: ("business_goal",),
        WorkflowPhase.REQUIREMENTS: ("docs/PROJECT_MASTER.md", "docs/SCOPE.md"),
        WorkflowPhase.RESEARCH: ("docs/PRODUCT_REQUIREMENTS.md", "docs/SCOPE.md"),
        WorkflowPhase.DESIGN: ("docs/OPEN_SOURCE_RESEARCH.md", "docs/PRODUCT_REQUIREMENTS.md"),
        WorkflowPhase.IMPLEMENTATION: (
            "docs/ARCHITECTURE.md",
            "docs/API_SPEC.md",
            "docs/DATABASE.md",
        ),
        WorkflowPhase.VERIFY: ("implementation_commit", "test_plan"),
        WorkflowPhase.RELEASE: ("verification_evidence",),
        WorkflowPhase.MEMORY: ("release_candidate", "workflow_events"),
    }.get(phase, ())


def _artifact_path_allowed(raw_path: str, allowed_paths: tuple[str, ...]) -> bool:
    normalized = raw_path.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        return False
    candidate = path.as_posix()
    for raw_allowed in allowed_paths:
        allowed = raw_allowed.replace("\\", "/")
        if allowed.endswith("/"):
            if candidate.startswith(allowed) and candidate != allowed.rstrip("/"):
                return True
        elif candidate == PurePosixPath(allowed).as_posix():
            return True
    return False


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
