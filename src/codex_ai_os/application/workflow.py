"""Deterministic ``new-project`` workflow orchestration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError

from codex_ai_os.adapters.git import (
    GitEvidenceError,
    GitEvidenceService,
    GitEvidenceVerifier,
)
from codex_ai_os.application.coordination import CoordinationService, IntegrationResult
from codex_ai_os.application.g4 import (
    G4Publisher,
    GitHubReleaseGovernanceService,
    ReleaseGovernanceError,
)
from codex_ai_os.application.repository import (
    RepositoryGovernanceError,
    RepositoryGovernanceService,
)
from codex_ai_os.application.routing import ProfileRouter
from codex_ai_os.application.verification_cache import (
    VerificationCachePrepareError,
    VerificationCachePreparer,
)
from codex_ai_os.application.worktree import (
    TaskWorktreeAllocator,
    WorktreeService,
    WorktreeServiceError,
)
from codex_ai_os.domain.config import GitPushPolicy, RiskLevel
from codex_ai_os.domain.coordination import (
    HandoffReviewInput,
    TaskBlueprint,
    TaskGroupView,
)
from codex_ai_os.domain.governance import G4ApprovalInput
from codex_ai_os.domain.ids import new_id
from codex_ai_os.domain.invocation import InvocationContext
from codex_ai_os.domain.operations import (
    HostOperation,
    HostOperationKind,
    HostOperationStatus,
)
from codex_ai_os.domain.versions import RUNTIME_VERSIONS
from codex_ai_os.domain.workflow import (
    AUTOMATIC_NEXT_PHASE,
    GATE_AFTER_PHASE,
    PHASE_AFTER_APPROVAL,
    PHASE_DEFINITIONS,
    WORKFLOW_START_PHASE,
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
from codex_ai_os.infrastructure.coordination import CoordinationError, CoordinationStore
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.evidence import EvidenceError, EvidenceStore
from codex_ai_os.infrastructure.operations import HostOperationError, HostOperationStore
from codex_ai_os.infrastructure.workflows import (
    WorkflowConflictError,
    WorkflowNotFoundError,
    WorkflowStore,
)
from codex_ai_os.infrastructure.worktrees import WorktreeStore

if TYPE_CHECKING:
    from codex_ai_os.application.execution import ExecutionService


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
    next_actions: tuple[NextAction, ...] = ()
    integration_result: IntegrationResult | None = None


class WorkflowEngine:
    def __init__(
        self,
        project_root: Path,
        *,
        git_evidence: GitEvidenceVerifier | None = None,
        worktrees: TaskWorktreeAllocator | None = None,
        g4_publisher: G4Publisher | None = None,
        verification_cache_preparer: VerificationCachePreparer | None = None,
        release_execution_service: ExecutionService | None = None,
        readonly: bool = False,
    ) -> None:
        self.config = load_project_config(project_root.resolve())
        database = Database(self.config.root / ".codex-os" / "state" / "state.db")
        if readonly:
            current = database.current_version()
            if current != RUNTIME_VERSIONS.sqlite_schema:
                raise WorkflowError(
                    "MIGRATION_REQUIRED",
                    f"read-only workflow access requires SQLite "
                    f"{RUNTIME_VERSIONS.sqlite_schema}, found {current}",
                    40,
                )
        else:
            database.migrate()
        self.store = WorkflowStore(database)
        self.evidence_store = EvidenceStore(database, self.config.root)
        self.coordination_store = CoordinationStore(database)
        self.worktree_store = WorktreeStore(database)
        self.operations = HostOperationStore(database)
        self.git_evidence = git_evidence
        self.worktrees = worktrees or (
            None if readonly else WorktreeService(self.config.root)
        )
        self.g4_publisher = g4_publisher or (
            None if readonly else GitHubReleaseGovernanceService(self.config.root)
        )
        self.verification_cache_preparer = verification_cache_preparer or (
            None if readonly else VerificationCachePreparer(self.config.root)
        )
        self.release_execution_service = release_execution_service

    def start(
        self,
        goal: str,
        *,
        workflow_name: str = "new-project",
        profiles: tuple[str, ...] = (),
        target_branch: str | None = None,
        impact_paths: tuple[str, ...] = (),
        dependency_count: int = 0,
        release_required: bool = False,
        override_reason: str | None = None,
    ) -> WorkflowResult:
        normalized_goal = goal.strip()
        if not normalized_goal:
            raise WorkflowError("CONFIG_INVALID", "goal cannot be empty", 2)
        start_phase = WORKFLOW_START_PHASE.get(workflow_name)
        if start_phase is None:
            raise WorkflowError(
                "CONFIG_INVALID",
                f"unsupported workflow: {workflow_name}",
                2,
            )

        existing = self.store.find_active_run(
            self.config.project_id, workflow_name, normalized_goal
        )
        if existing is not None:
            return self._result(existing)

        selected_target = target_branch or self.config.target_branch
        requested_profile_input = profiles or _default_profiles(self.config.project_type.value)
        router = ProfileRouter(self.store.database, self.config)
        try:
            selected_profiles = router.select_profiles(requested_profile_input, impact_paths)
        except ValueError as exc:
            raise WorkflowError("CONFIG_INVALID", str(exc), 2) from exc
        if len(selected_profiles) > self.config.max_parallel_agents:
            raise WorkflowError("CONFIG_INVALID", "profiles exceed max_parallel_agents", 2)
        if self.config.schema_version != "1.0":
            try:
                RepositoryGovernanceService(
                    self.config.root, config=self.config
                ).require_ready(target_branch=selected_target)
            except RepositoryGovernanceError as exc:
                raise WorkflowError(exc.code, str(exc), 40) from exc

        now = _utc_now()
        run_id = new_id("RUN")
        task, action = self._task_and_action(
            run_id,
            start_phase,
            state_version=0,
            goal=normalized_goal,
        )
        run = WorkflowRun(
            id=run_id,
            project_id=self.config.project_id,
            workflow_name=workflow_name,
            goal=normalized_goal,
            workflow_phase=start_phase,
            run_status=RunStatus.RUNNING,
            state_version=0,
            risk_level=self.config.risk_level,
            checkpoint=_checkpoint(action),
            config_hash=self.store.project_config_hash(self.config.project_id),
            created_at=now,
            updated_at=now,
            profiles=selected_profiles,
            target_branch=selected_target,
            max_parallel_agents=self.config.max_parallel_agents,
        )
        created = self.store.create_run(run, task)
        if self.config.schema_version != "1.0":
            router.route(
                run_id=created.id,
                requested_profiles=requested_profile_input,
                impact_paths=impact_paths,
                source_commit=None,
                workflow_name=workflow_name,
                dependency_count=dependency_count,
                release_required=release_required,
                override_reason=override_reason,
            )
        self._allocate_or_block(created, task, base_ref="HEAD")
        return self._result(self.store.get_run(created.id))

    def status(self, run_id: str) -> WorkflowResult:
        try:
            return self._result(self.store.get_run(run_id))
        except WorkflowNotFoundError as exc:
            raise WorkflowError("RECOVERY_UNAVAILABLE", str(exc), 30) from exc

    def complete_task(self, run_id: str, completion: TaskCompletion) -> WorkflowResult:
        run = self._get_run(run_id)
        self._require_migration_revalidation(run)
        task = self._get_task(completion.task_id)
        completion_json = completion.model_dump_json()
        if task.run_id != run.id:
            raise WorkflowError("CONFIG_INVALID", "task does not belong to workflow run", 2)
        if task.status is TaskStatus.COMPLETED:
            if task.task_group_id is not None and task.head_commit == completion.commit_sha:
                return self._result(run)
            if task.output_ref == completion_json:
                return self._result(run)
            raise WorkflowError(
                "STATE_CONFLICT",
                "task already completed with different evidence",
                30,
            )

        actions = _actions_from_checkpoint(run.checkpoint)
        action = next(
            (candidate for candidate in actions if candidate.task_id == completion.task_id),
            None,
        )
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
            and not path.startswith(f".codex-os/artifacts/{run.id}/")
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
                repository_artifacts = {
                    path: digest
                    for path, digest in completion.artifact_paths_and_hashes.items()
                    if not path.startswith(f".codex-os/artifacts/{run.id}/")
                }
                evidence = verifier.verify(
                    completion.model_copy(
                        update={"artifact_paths_and_hashes": repository_artifacts}
                    )
                )
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
            if self.config.schema_version != "1.0":
                try:
                    self.evidence_store.record_task_evidence(
                        run_id=run.id,
                        task_id=task.id,
                        artifacts=completion.artifacts,
                        checks=completion.checks,
                    )
                except EvidenceError as exc:
                    raise WorkflowError(exc.code, str(exc), 40) from exc

        if task.task_group_id is not None:
            if completion.commit_sha is None:
                raise WorkflowError("GIT_EVIDENCE_REQUIRED", "task Commit is required", 40)
            if run.workflow_phase is WorkflowPhase.RELEASE:
                from codex_ai_os.application.release import ReleaseCandidateService

                ReleaseCandidateService(self.config.root).bind_candidate_commit(
                    run.id, task.id, completion.commit_sha
                )
            try:
                handoff_id = self.coordination_store.submit_handoff(
                    task_id=task.id,
                    source_commit=completion.commit_sha,
                    artifact_refs=completion.artifact_paths_and_hashes,
                    checks=tuple(check.name for check in completion.checks),
                )
                remaining = tuple(
                    candidate for candidate in actions if candidate.task_id != task.id
                )
                checkpoint = _coordination_checkpoint(
                    run.checkpoint,
                    remaining,
                    task_group_id=task.task_group_id,
                    waiting_handoff=handoff_id,
                    last_commit_sha=completion.commit_sha,
                )
                updated = self.store.update_checkpoint(run=run, checkpoint=checkpoint)
            except (CoordinationError, WorkflowConflictError) as exc:
                code = exc.code if isinstance(exc, CoordinationError) else "STATE_CONFLICT"
                raise WorkflowError(code, str(exc), 30) from exc
            return self._result(updated)

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
        g4_evidence: G4ApprovalInput | None = None,
        invocation: InvocationContext | None = None,
    ) -> WorkflowResult:
        run = self._get_run(run_id)
        self._require_migration_revalidation(run)
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
        trusted_reviewer = invocation.principal if invocation is not None else reviewer.strip()
        if g4_evidence is not None and invocation is not None:
            g4_evidence = g4_evidence.model_copy(
                update={
                    "release_authority": g4_evidence.release_authority.model_copy(
                        update={"authorized_by": trusted_reviewer}
                    )
                }
            )

        bundle = None
        if approved and self.config.schema_version != "1.0":
            source_commit = str(run.checkpoint.get("last_commit_sha") or "")
            if not source_commit:
                raise WorkflowError(
                    "EVIDENCE_INCOMPLETE", "gate has no commit-bound source evidence", 40
                )
            try:
                bundle = self.evidence_store.build_gate_bundle(
                    run_id=run.id,
                    gate=gate,
                    state_version=run.state_version,
                    source_commit=source_commit,
                )
                self.evidence_store.require_complete(bundle)
            except EvidenceError as exc:
                raise WorkflowError(exc.code, str(exc), 40) from exc

        publication: dict[str, Any] | None = None
        if approved and gate is Gate.G4 and self.config.schema_version != "1.0":
            if g4_evidence is None:
                raise WorkflowError(
                    "RELEASE_AUTHORITY_REQUIRED",
                    "G4 requires PR, merge, version, and explicit release authorization evidence",
                    20,
                )
            if self.g4_publisher is None:
                raise WorkflowError(
                    "READ_ONLY_OPERATION",
                    "G4 publication is unavailable from a read-only workflow engine",
                    40,
                )
            try:
                publication = asdict(
                    self.g4_publisher.authorize_and_publish(run, g4_evidence)
                )
            except ReleaseGovernanceError as exc:
                raise WorkflowError(exc.code, str(exc), 50) from exc

        if approved and gate is Gate.G2 and self.config.schema_version != "1.0":
            assert bundle is not None
            return self._approve_g2_coordination(
                run,
                reviewer=trusted_reviewer,
                reason=reason,
                evidence_bundle_id=bundle.id,
                evidence_bundle_hash=bundle.bundle_hash,
            )

        if approved and gate is Gate.G3 and self.config.schema_version != "1.0":
            assert bundle is not None
            return self._approve_g3_coordination(
                run,
                reviewer=trusted_reviewer,
                reason=reason,
                evidence_bundle_id=bundle.id,
                evidence_bundle_hash=bundle.bundle_hash,
            )

        next_version = run.state_version + 1
        next_task: TaskRecord | None = None
        if approved:
            target_phase = PHASE_AFTER_APPROVAL[gate]
            if target_phase is WorkflowPhase.COMPLETED:
                target_status = RunStatus.COMPLETED
                next_action = NextAction(kind=ActionKind.COMPLETE)
                checkpoint = _checkpoint(
                    next_action,
                    approved_gate=gate,
                    release_publication=publication,
                )
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
                reviewer=trusted_reviewer,
                reason=reason,
                target_phase=target_phase,
                target_status=target_status,
                checkpoint=checkpoint,
                next_task=next_task,
                evidence_bundle_id=bundle.id if bundle is not None else None,
                evidence_bundle_hash=bundle.bundle_hash if bundle is not None else None,
                release_authority=(
                    g4_evidence.model_dump(mode="json") if g4_evidence is not None else None
                ),
            )
        except WorkflowConflictError as exc:
            raise WorkflowError("STATE_CONFLICT", str(exc), 30) from exc
        if next_task is not None:
            base_ref = str(run.checkpoint.get("last_commit_sha") or "HEAD")
            self._allocate_or_block(updated, next_task, base_ref=base_ref)
        return self._result(self.store.get_run(updated.id))

    def review_handoff(
        self, review: HandoffReviewInput
    ) -> WorkflowResult:
        handoff_task = self._task_for_handoff(review.handoff_id)
        run = self._get_run(handoff_task.run_id)
        self._require_migration_revalidation(run)
        service = CoordinationService(self.config.root)
        try:
            if self.config.schema_version == "1.2":
                if review.expected_handoff_version is None or review.idempotency_key is None:
                    raise WorkflowError(
                        "CONFIG_INVALID",
                        "API 1.2 Handoff review requires expected_handoff_version "
                        "and idempotency_key",
                        2,
                    )
                decision = service.prepare_review(review)
                if decision.decision == "accepted":
                    if decision.operation_id is None:
                        raise WorkflowError(
                            "RECOVERY_UNAVAILABLE",
                            "accepted Handoff has no integration operation",
                            40,
                        )
                    action = NextAction(
                        kind=ActionKind.HOST_OPERATION,
                        operation_id=decision.operation_id,
                        task_id=decision.task_id,
                        task_group_id=decision.task_group_id,
                        dependencies=(decision.handoff_id,),
                        prompt="Execute or reconcile the accepted Handoff integration merge.",
                        risk_level=RiskLevel.HIGH,
                        requires_repository_change=True,
                    )
                    checkpoint = _coordination_checkpoint(
                        run.checkpoint,
                        (action,),
                        task_group_id=decision.task_group_id,
                        waiting_handoff=decision.handoff_id,
                    )
                    updated = self.store.update_checkpoint(
                        run=run,
                        checkpoint=checkpoint,
                        status=RunStatus.RUNNING,
                    )
                    return self._result(updated)
                integration = service.decision_result(decision)
            else:
                integration = service.review_and_integrate(review)
        except CoordinationError as exc:
            raise WorkflowError(exc.code, str(exc), 40) from exc
        return self._apply_integration_result(integration)

    def execute_host_operation(
        self,
        operation_id: str,
        *,
        expected_operation_version: int,
        idempotency_key: str,
        invocation: InvocationContext,
    ) -> WorkflowResult:
        try:
            operation = self.operations.get(operation_id)
            if operation.idempotency_key != idempotency_key:
                raise HostOperationError(
                    "IDEMPOTENCY_CONFLICT",
                    "host operation idempotency key does not match persisted intent",
                )
            acquired = self.operations.acquire(
                operation_id,
                expected_version=expected_operation_version,
                lease_owner=invocation.principal,
            )
            if acquired.status is HostOperationStatus.SUCCEEDED:
                if acquired.run_id is None:
                    raise HostOperationError(
                        "RECOVERY_UNAVAILABLE", "completed operation has no Workflow run"
                    )
                return self.status(acquired.run_id)
            if acquired.kind is HostOperationKind.INTEGRATION_PREPARE:
                return self._execute_integration_prepare_operation(
                    acquired, lease_owner=invocation.principal
                )
            if acquired.kind is HostOperationKind.INTEGRATION_MERGE:
                return self._execute_integration_merge_operation(
                    acquired, lease_owner=invocation.principal
                )
            if acquired.kind is HostOperationKind.RELEASE_PREPARE:
                return self._execute_release_prepare_operation(
                    acquired, lease_owner=invocation.principal
                )
            if acquired.kind is HostOperationKind.VERIFICATION_PREPARE:
                return self._execute_verification_prepare_operation(
                    acquired, lease_owner=invocation.principal
                )
            self.operations.mark_failed(
                operation_id,
                expected_version=acquired.state_version,
                lease_owner=invocation.principal,
                error_code="OPERATION_KIND_UNSUPPORTED",
            )
            raise HostOperationError(
                "CONFIG_INVALID", f"unsupported Host Operation kind: {acquired.kind.value}"
            )
        except HostOperationError as exc:
            raise WorkflowError(exc.code, str(exc), 40) from exc

    def _execute_integration_prepare_operation(
        self, operation: HostOperation, *, lease_owner: str
    ) -> WorkflowResult:
        if operation.run_id is None:
            self.operations.mark_failed(
                operation.operation_id,
                expected_version=operation.state_version,
                lease_owner=lease_owner,
                error_code="RECOVERY_UNAVAILABLE",
            )
            raise WorkflowError(
                "RECOVERY_UNAVAILABLE", "integration prepare has no Workflow run", 40
            )
        request = operation.request
        source_commit = request.get("source_commit")
        tasks_value = request.get("tasks")
        if not isinstance(source_commit, str) or not isinstance(tasks_value, list):
            self.operations.mark_failed(
                operation.operation_id,
                expected_version=operation.state_version,
                lease_owner=lease_owner,
                error_code="RECOVERY_UNAVAILABLE",
            )
            raise WorkflowError(
                "RECOVERY_UNAVAILABLE", "integration prepare request is incomplete", 40
            )
        try:
            tasks = tuple(
                TaskBlueprint.model_validate(item)
                for item in cast(list[object], tasks_value)
            )
            integration_path = CoordinationService(
                self.config.root
            ).ensure_integration_worktree(operation.run_id, base_commit=source_commit)
            group = self._ensure_task_group(
                run_id=operation.run_id,
                name="implementation",
                phase=WorkflowPhase.IMPLEMENTATION,
                base_commit=source_commit,
                blueprints=tasks,
            )
            current = self._get_run(operation.run_id)
            actions = self._schedule_group(current, group.id)
            checkpoint = _coordination_checkpoint(
                current.checkpoint,
                actions,
                task_group_id=group.id,
                waiting_handoff=None,
                integration_worktree=integration_path.as_posix(),
            )
            checkpoint.pop("pending_host_operation_id", None)
            updated = self.store.update_checkpoint(run=current, checkpoint=checkpoint)
        except (CoordinationError, WorkflowConflictError, ValidationError) as exc:
            code = exc.code if isinstance(exc, CoordinationError) else "STATE_CONFLICT"
            if code == "WORKTREE_BLOCKED":
                self.operations.mark_outcome_unknown(
                    operation.operation_id,
                    expected_version=operation.state_version,
                    lease_owner=lease_owner,
                    error_code=code,
                )
                raise WorkflowError("RECONCILIATION_REQUIRED", str(exc), 40) from exc
            self.operations.mark_failed(
                operation.operation_id,
                expected_version=operation.state_version,
                lease_owner=lease_owner,
                error_code=code,
            )
            raise WorkflowError(code, str(exc), 40) from exc
        self.operations.mark_succeeded(
            operation.operation_id,
            expected_version=operation.state_version,
            lease_owner=lease_owner,
            result={
                "integration_worktree": integration_path.as_posix(),
                "integration_branch": updated.integration_branch,
                "integration_head": updated.integration_head,
                "task_group_id": group.id,
                "task_ids": list(self._task_ids_for_group(group.id)),
            },
        )
        return self._result(updated)

    def _execute_integration_merge_operation(
        self, operation: HostOperation, *, lease_owner: str
    ) -> WorkflowResult:
        try:
            integration = CoordinationService(self.config.root).execute_merge_operation(
                operation
            )
        except CoordinationError as exc:
            if exc.code == "REMOTE_UNREACHABLE":
                self.operations.mark_outcome_unknown(
                    operation.operation_id,
                    expected_version=operation.state_version,
                    lease_owner=lease_owner,
                    error_code=exc.code,
                )
                raise WorkflowError("RECONCILIATION_REQUIRED", str(exc), 40) from exc
            self.operations.mark_failed(
                operation.operation_id,
                expected_version=operation.state_version,
                lease_owner=lease_owner,
                error_code=exc.code,
            )
            raise WorkflowError(exc.code, str(exc), 40) from exc
        result = self._apply_integration_result(integration)
        self.operations.mark_succeeded(
            operation.operation_id,
            expected_version=operation.state_version,
            lease_owner=lease_owner,
            result={
                "handoff_id": integration.handoff_id,
                "status": integration.status,
                "integration_branch": integration.integration_branch,
                "integration_head": integration.integration_head,
                "merge_commit": integration.merge_commit,
                "conflict_paths": list(integration.conflict_paths),
            },
        )
        return result

    def _execute_release_prepare_operation(
        self, operation: HostOperation, *, lease_owner: str
    ) -> WorkflowResult:
        from codex_ai_os.application.release import ReleaseCandidateService

        if operation.run_id is None:
            self.operations.mark_failed(
                operation.operation_id,
                expected_version=operation.state_version,
                lease_owner=lease_owner,
                error_code="RECOVERY_UNAVAILABLE",
            )
            raise WorkflowError(
                "RECOVERY_UNAVAILABLE", "release prepare has no Workflow run", 40
            )
        source_commit = operation.request.get("integration_source_commit")
        if not isinstance(source_commit, str) or not source_commit:
            self.operations.mark_failed(
                operation.operation_id,
                expected_version=operation.state_version,
                lease_owner=lease_owner,
                error_code="RECOVERY_UNAVAILABLE",
            )
            raise WorkflowError(
                "RECOVERY_UNAVAILABLE", "release prepare request is incomplete", 40
            )
        definition = PHASE_DEFINITIONS[WorkflowPhase.RELEASE]
        blueprint = TaskBlueprint(
            key="release-governed",
            agent=definition.agent,
            skill=definition.skill,
            prompt=definition.prompt,
            allowed_paths=definition.allowed_paths,
        )
        try:
            group = self._ensure_task_group(
                run_id=operation.run_id,
                name=WorkflowPhase.RELEASE.value,
                phase=WorkflowPhase.RELEASE,
                base_commit=source_commit,
                blueprints=(blueprint,),
            )
            current = self._get_run(operation.run_id)
            actions = self._schedule_group(current, group.id)
            candidate = ReleaseCandidateService(
                self.config.root,
                execution_service=self.release_execution_service,
            ).create(operation.run_id, operation=operation)
            checkpoint = _coordination_checkpoint(
                current.checkpoint,
                actions,
                task_group_id=group.id,
                waiting_handoff=None,
            )
            checkpoint["release_candidate_id"] = candidate.id
            checkpoint["release_manifest_path"] = candidate.manifest_path
            checkpoint["release_manifest_hash"] = candidate.manifest_hash
            checkpoint.pop("pending_host_operation_id", None)
            updated = self.store.update_checkpoint(run=current, checkpoint=checkpoint)
        except (CoordinationError, WorkflowConflictError, WorkflowError) as exc:
            code = (
                exc.code
                if isinstance(exc, (CoordinationError, WorkflowError))
                else "STATE_CONFLICT"
            )
            if code in {"OPERATION_RECONCILE_REQUIRED", "RECONCILIATION_REQUIRED"}:
                self.operations.mark_outcome_unknown(
                    operation.operation_id,
                    expected_version=operation.state_version,
                    lease_owner=lease_owner,
                    error_code=code,
                )
                raise WorkflowError("RECONCILIATION_REQUIRED", str(exc), 40) from exc
            self.operations.mark_failed(
                operation.operation_id,
                expected_version=operation.state_version,
                lease_owner=lease_owner,
                error_code=code,
            )
            raise WorkflowError(code, str(exc), 40) from exc
        self.operations.mark_succeeded(
            operation.operation_id,
            expected_version=operation.state_version,
            lease_owner=lease_owner,
            result={
                "candidate_id": candidate.id,
                "integration_source_commit": candidate.source_commit,
                "candidate_commit": candidate.candidate_commit,
                "manifest_path": candidate.manifest_path,
                "manifest_hash": candidate.manifest_hash,
                "artifact_root": candidate.artifact_root,
                "artifacts": candidate.artifacts,
            },
        )
        return self._result(updated)

    def _execute_verification_prepare_operation(
        self, operation: HostOperation, *, lease_owner: str
    ) -> WorkflowResult:
        if operation.run_id is None:
            self.operations.mark_failed(
                operation.operation_id,
                expected_version=operation.state_version,
                lease_owner=lease_owner,
                error_code="RECOVERY_UNAVAILABLE",
            )
            raise WorkflowError(
                "RECOVERY_UNAVAILABLE",
                "verification prepare operation has no Workflow run",
                40,
            )
        if self.verification_cache_preparer is None:
            raise WorkflowError(
                "CONFIG_INVALID",
                "verification prepare cannot run from read-only workflow access",
                40,
            )
        try:
            prepared = self.verification_cache_preparer.prepare(operation)
        except VerificationCachePrepareError as exc:
            if exc.outcome_unknown:
                self.operations.mark_outcome_unknown(
                    operation.operation_id,
                    expected_version=operation.state_version,
                    lease_owner=lease_owner,
                    error_code=exc.code,
                )
                raise WorkflowError("RECONCILIATION_REQUIRED", str(exc), 40) from exc
            self.operations.mark_failed(
                operation.operation_id,
                expected_version=operation.state_version,
                lease_owner=lease_owner,
                error_code=exc.code,
            )
            raise WorkflowError(exc.code, str(exc), 40) from exc
        self.operations.mark_succeeded(
            operation.operation_id,
            expected_version=operation.state_version,
            lease_owner=lease_owner,
            result=prepared,
        )
        return self.status(operation.run_id)

    def _apply_integration_result(self, integration: IntegrationResult) -> WorkflowResult:
        run = self._get_run(integration.task_group.run_id)
        if integration.task_group.status == "completed":
            phase = WorkflowPhase(integration.task_group.phase)
            if phase is WorkflowPhase.IMPLEMENTATION:
                return self._advance_to_coordinated_phase(
                    run, WorkflowPhase.VERIFY, integration
                )
            if phase is WorkflowPhase.VERIFY:
                return self._advance_group_to_gate(run, Gate.G3, integration)
            if phase is WorkflowPhase.RELEASE:
                return self._advance_to_coordinated_phase(
                    run, WorkflowPhase.MEMORY, integration
                )
            if phase is WorkflowPhase.MEMORY:
                return self._advance_group_to_gate(run, Gate.G4, integration)
            raise WorkflowError(
                "STATE_CONFLICT", f"unsupported coordinated phase join: {phase.value}", 30
            )

        if integration.status == "rejected":
            task = self._get_task(integration.task_group.ready_task_ids[0]) if (
                integration.task_group.ready_task_ids
            ) else self._task_for_handoff(integration.handoff_id)
            actions = (self._action_for_coordinated_task(task),)
            status = RunStatus.RUNNING
        elif integration.status in {"blocked", "conflicted"}:
            actions = ()
            status = RunStatus.BLOCKED
        else:
            actions = self._schedule_group(run, integration.task_group.id)
            status = RunStatus.RUNNING
        checkpoint = _coordination_checkpoint(
            run.checkpoint,
            actions,
            task_group_id=integration.task_group.id,
            waiting_handoff=None,
            blocker=(
                {"code": "MERGE_CONFLICT", "paths": list(integration.conflict_paths)}
                if integration.status == "conflicted"
                else None
            ),
        )
        try:
            updated = self.store.update_checkpoint(
                run=run,
                checkpoint=checkpoint,
                status=status,
            )
        except WorkflowConflictError as exc:
            raise WorkflowError("STATE_CONFLICT", str(exc), 30) from exc
        return self._result(updated, integration_result=integration)

    def _approve_g2_coordination(
        self,
        run: WorkflowRun,
        *,
        reviewer: str,
        reason: str,
        evidence_bundle_id: str,
        evidence_bundle_hash: str,
    ) -> WorkflowResult:
        source_commit = str(run.checkpoint.get("last_commit_sha") or "")
        initial_checkpoint: dict[str, Any] = {
            "next_action": None,
            "next_actions": [],
            "approved_gate": Gate.G2.value,
            "pending_gate": None,
            "last_commit_sha": source_commit,
        }
        try:
            router = ProfileRouter(self.store.database, self.config)
            routing_input = router.input_for_run(run.id)
            routing = router.route(
                run_id=run.id,
                requested_profiles=run.profiles,
                impact_paths=routing_input.impact_paths,
                source_commit=source_commit,
                workflow_name=run.workflow_name,
                dependency_count=routing_input.dependency_count,
                release_required=routing_input.release_required,
                override_reason=routing_input.override_reason,
                require_task_mapping=True,
            )
            approved = self.store.approval_transition(
                run=run,
                gate=Gate.G2,
                approved=True,
                reviewer=reviewer,
                reason=reason,
                target_phase=WorkflowPhase.IMPLEMENTATION,
                target_status=RunStatus.RUNNING,
                checkpoint=initial_checkpoint,
                next_task=None,
                evidence_bundle_id=evidence_bundle_id,
                evidence_bundle_hash=evidence_bundle_hash,
                host_operation_kind=HostOperationKind.INTEGRATION_PREPARE,
                host_operation_idempotency_key=(
                    f"{run.id}:{run.state_version}:integration_prepare"
                ),
                host_operation_request={
                    "schema_version": RUNTIME_VERSIONS.api,
                    "run_id": run.id,
                    "source_commit": source_commit,
                    "routing_decision_hash": routing.decision_hash,
                    "policy_hash": routing.policy_hash,
                    "tasks": [task.model_dump(mode="json") for task in routing.tasks],
                    "evidence_bundle_hash": evidence_bundle_hash,
                },
            )
        except (WorkflowConflictError, CoordinationError, ValueError) as exc:
            code = (
                exc.code
                if isinstance(exc, CoordinationError)
                else (
                    "ROUTING_PATH_UNMAPPED"
                    if "ROUTING_PATH_UNMAPPED" in str(exc)
                    else "STATE_CONFLICT"
                )
            )
            raise WorkflowError(code, str(exc), 40) from exc
        return self._result(approved)

    def _approve_g3_coordination(
        self,
        run: WorkflowRun,
        *,
        reviewer: str,
        reason: str,
        evidence_bundle_id: str,
        evidence_bundle_hash: str,
    ) -> WorkflowResult:
        initial_checkpoint: dict[str, Any] = {
            "next_action": None,
            "next_actions": [],
            "approved_gate": Gate.G3.value,
            "pending_gate": None,
            "last_commit_sha": run.integration_head,
        }
        try:
            cache_binding = self._release_cache_binding(run.id)
            approved = self.store.approval_transition(
                run=run,
                gate=Gate.G3,
                approved=True,
                reviewer=reviewer,
                reason=reason,
                target_phase=WorkflowPhase.RELEASE,
                target_status=RunStatus.RUNNING,
                checkpoint=initial_checkpoint,
                next_task=None,
                evidence_bundle_id=evidence_bundle_id,
                evidence_bundle_hash=evidence_bundle_hash,
                host_operation_kind=HostOperationKind.RELEASE_PREPARE,
                host_operation_idempotency_key=(
                    f"{run.id}:{run.state_version}:release_prepare"
                ),
                host_operation_request={
                    "schema_version": RUNTIME_VERSIONS.api,
                    "run_id": run.id,
                    "integration_source_commit": run.integration_head,
                    "evidence_bundle_hash": evidence_bundle_hash,
                    "verification_cache": cache_binding,
                    "execution_image": RUNTIME_VERSIONS.execution_image,
                    "software_version": RUNTIME_VERSIONS.software,
                },
            )
            return self._result(approved)
        except (WorkflowConflictError, CoordinationError) as exc:
            code = exc.code if isinstance(exc, CoordinationError) else "STATE_CONFLICT"
            raise WorkflowError(code, str(exc), 40) from exc

    def _release_cache_binding(self, run_id: str) -> dict[str, object] | None:
        with self.store.database.read_connection() as connection:
            row = connection.execute(
                "SELECT operation_id, request_hash, result_json "
                "FROM host_operations WHERE run_id = ? "
                "AND kind = 'verification_prepare' AND status = 'succeeded' "
                "ORDER BY ended_at DESC, operation_id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            if self.config.git_push_policy is GitPushPolicy.FIXTURE_LOCAL_ONLY:
                return None
            raise WorkflowError(
                "SANDBOX_DEPENDENCIES_UNAVAILABLE",
                "G3 release preparation requires a succeeded verification cache operation",
                40,
            )
        result_value: object = json.loads(str(row["result_json"]))
        if not isinstance(result_value, dict):
            raise WorkflowError(
                "DEPENDENCY_UNVERIFIED", "verification cache result is invalid", 40
            )
        result = cast(dict[str, object], result_value)
        lock_hash = result.get("uv_lock_hash")
        if not isinstance(lock_hash, str):
            raise WorkflowError(
                "DEPENDENCY_UNVERIFIED", "verification cache has no lock binding", 40
            )
        cache = (
            self.config.root
            / ".codex-os"
            / "cache"
            / "verification"
            / lock_hash
            / "verification-cache-manifest.json"
        )
        if not cache.is_file():
            raise WorkflowError(
                "SANDBOX_DEPENDENCIES_UNAVAILABLE",
                "verification cache manifest is missing",
                40,
            )
        return {
            "operation_id": str(row["operation_id"]),
            "operation_request_hash": str(row["request_hash"]),
            "uv_lock_hash": lock_hash,
            "manifest_hash": hashlib.sha256(cache.read_bytes()).hexdigest(),
        }

    def _advance_to_coordinated_phase(
        self,
        run: WorkflowRun,
        phase: WorkflowPhase,
        integration: IntegrationResult,
    ) -> WorkflowResult:
        return self._start_coordinated_phase(
            run,
            phase,
            integration.integration_head,
            integration_result=integration,
            transition_required=True,
        )

    def _start_coordinated_phase(
        self,
        run: WorkflowRun,
        phase: WorkflowPhase,
        base_commit: str,
        *,
        integration_result: IntegrationResult | None = None,
        transition_required: bool = False,
    ) -> WorkflowResult:
        if not base_commit:
            raise WorkflowError("GIT_EVIDENCE_INVALID", "integration HEAD is missing", 40)
        definition = PHASE_DEFINITIONS[phase]
        blueprint = TaskBlueprint(
            key=f"{phase.value}-governed",
            agent=definition.agent,
            skill=definition.skill,
            prompt=definition.prompt,
            allowed_paths=definition.allowed_paths,
        )
        try:
            group, _planned = self.coordination_store.create_group(
                run_id=run.id,
                name=phase.value,
                phase=phase.value,
                base_commit=base_commit,
                blueprints=(blueprint,),
            )
            actions = self._schedule_group(run, group.id)
            checkpoint = _coordination_checkpoint(
                run.checkpoint,
                actions,
                task_group_id=group.id,
                waiting_handoff=None,
                last_commit_sha=base_commit,
            )
            if transition_required:
                updated = self.store.advance_after_task_group(
                    run=run,
                    checkpoint=checkpoint,
                    integration_head=base_commit,
                    target_phase=phase,
                    target_status=RunStatus.RUNNING,
                )
            else:
                updated = self.store.update_checkpoint(run=run, checkpoint=checkpoint)
        except (WorkflowConflictError, CoordinationError) as exc:
            code = exc.code if isinstance(exc, CoordinationError) else "STATE_CONFLICT"
            raise WorkflowError(code, str(exc), 40) from exc
        return self._result(updated, integration_result=integration_result)

    def _advance_group_to_gate(
        self, run: WorkflowRun, gate: Gate, integration: IntegrationResult
    ) -> WorkflowResult:
        action = NextAction(
            kind=ActionKind.APPROVAL,
            gate=gate,
            prompt=f"Review evidence and approve or reject {gate.value}.",
            risk_level=run.risk_level,
        )
        checkpoint = _checkpoint(
            action,
            pending_gate=gate,
            last_completed_task=integration.handoff_id,
            last_commit_sha=integration.integration_head,
        )
        try:
            updated = self.store.advance_after_task_group(
                run=run,
                checkpoint=checkpoint,
                integration_head=integration.integration_head,
                target_phase=WorkflowPhase(integration.task_group.phase),
                target_status=RunStatus.NEEDS_APPROVAL,
            )
        except WorkflowConflictError as exc:
            raise WorkflowError("STATE_CONFLICT", str(exc), 30) from exc
        return self._result(updated, integration_result=integration)

    def resume(self, run_id: str) -> WorkflowResult:
        run = self._get_run(run_id)
        if run.migration_revalidation_required:
            try:
                repository = RepositoryGovernanceService(
                    self.config.root, config=self.config
                ).require_ready(target_branch=run.target_branch)
                bundle_ids = self.evidence_store.audit_migrated_run(run.id)
                run = self.store.complete_migration_revalidation(
                    run=run,
                    repository_check_hash=repository.check_hash,
                    evidence_bundle_ids=bundle_ids,
                )
            except (RepositoryGovernanceError, EvidenceError) as exc:
                raise WorkflowError(
                    "MIGRATION_REVALIDATION_REQUIRED", str(exc), 40
                ) from exc
            except WorkflowConflictError as exc:
                raise WorkflowError("STATE_CONFLICT", str(exc), 30) from exc
        if run.run_status in {RunStatus.RUNNING, RunStatus.NEEDS_APPROVAL}:
            return self._result(run)
        if run.run_status in {RunStatus.COMPLETED, RunStatus.CANCELLED}:
            raise WorkflowError("RECOVERY_UNAVAILABLE", "terminal workflow cannot resume", 30)

        recoverable_operations = self.operations.recoverable(
            run.id, limit=run.max_parallel_agents
        )
        if recoverable_operations:
            actions = tuple(
                self._action_for_host_operation(operation)
                for operation in recoverable_operations
            )
            checkpoint = _operations_checkpoint(
                run.checkpoint,
                actions,
                resumed_from=run.run_status.value,
            )
            try:
                updated = self.store.resume_transition(
                    run=run,
                    checkpoint=checkpoint,
                    next_task=None,
                )
            except WorkflowConflictError as exc:
                raise WorkflowError("STATE_CONFLICT", str(exc), 30) from exc
            return self._result(updated)

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

    def pause(self, run_id: str, *, reason: str) -> WorkflowResult:
        run = self._get_run(run_id)
        self._require_migration_revalidation(run)
        normalized = reason.strip()
        if not normalized:
            raise WorkflowError("CONFIG_INVALID", "pause reason is required", 2)
        if run.run_status is not RunStatus.RUNNING:
            raise WorkflowError("STATE_CONFLICT", "only a running workflow can pause", 30)
        try:
            updated = self.store.pause_transition(run=run, reason=normalized)
        except WorkflowConflictError as exc:
            raise WorkflowError("STATE_CONFLICT", str(exc), 30) from exc
        return self._result(updated)

    @staticmethod
    def _require_migration_revalidation(run: WorkflowRun) -> None:
        if run.migration_revalidation_required:
            raise WorkflowError(
                "MIGRATION_REVALIDATION_REQUIRED",
                "resume the workflow to run repository and Gate evidence revalidation",
                40,
            )

    def _result(
        self,
        run: WorkflowRun,
        *,
        integration_result: IntegrationResult | None = None,
    ) -> WorkflowResult:
        actions = _actions_from_checkpoint(run.checkpoint)
        tasks = {task.id: task for task in self.store.active_tasks(run.id)}
        hydrated: list[NextAction] = []
        for action in actions:
            task = tasks.get(action.task_id or "")
            updates: dict[str, Any] = {"expected_state_version": run.state_version}
            if task is not None and action.kind is ActionKind.MODEL_TASK:
                updates.update(
                    branch=task.branch,
                    worktree=task.worktree,
                    task_group_id=task.task_group_id,
                    expected_task_version=task.state_version,
                )
            if action.kind is ActionKind.HOST_OPERATION and action.operation_id is not None:
                operation = self.operations.get(action.operation_id)
                updates["expected_operation_version"] = operation.state_version
            action = action.model_copy(update=updates)
            hydrated.append(action)
        next_actions = tuple(hydrated)
        next_action = next_actions[0] if len(next_actions) == 1 else None
        active_task = next(iter(tasks.values()), None)
        return WorkflowResult(
            run=run,
            next_action=next_action,
            active_task=active_task,
            next_actions=next_actions,
            integration_result=integration_result,
        )

    def _action_for_host_operation(self, operation: HostOperation) -> NextAction:
        dependencies = tuple(
            item
            for item in (operation.handoff_id, operation.release_id, operation.task_id)
            if item is not None
        )
        verb = (
            "Reconcile"
            if operation.status is HostOperationStatus.RECONCILE_REQUIRED
            else "Execute or replay"
        )
        return NextAction(
            kind=ActionKind.HOST_OPERATION,
            operation_id=operation.operation_id,
            task_id=operation.task_id,
            task_group_id=operation.task_group_id,
            dependencies=dependencies,
            prompt=f"{verb} persisted {operation.kind.value} Host Operation.",
            risk_level=RiskLevel.HIGH,
            requires_repository_change=operation.kind
            in {
                HostOperationKind.INTEGRATION_PREPARE,
                HostOperationKind.INTEGRATION_MERGE,
                HostOperationKind.RELEASE_PREPARE,
                HostOperationKind.RELEASE_PUBLISH,
                HostOperationKind.DATABASE_MIGRATE,
            },
            expected_state_version=operation.expected_state_version,
            expected_task_version=operation.expected_task_version,
            expected_operation_version=operation.state_version,
        )

    def _schedule_group(self, run: WorkflowRun, group_id: str) -> tuple[NextAction, ...]:
        planned = self.coordination_store.ready_tasks(
            group_id, limit=run.max_parallel_agents
        )
        actions: list[NextAction] = []
        base_ref = run.integration_head or run.base_commit or "HEAD"
        for item in planned:
            task = self._get_task(item.id)
            if task.worktree is None:
                self._allocate_or_block(run, task, base_ref=base_ref)
                task = self._get_task(item.id)
            actions.append(
                NextAction(
                    kind=ActionKind.MODEL_TASK,
                    task_id=task.id,
                    task_group_id=task.task_group_id,
                    agent=item.agent,
                    skill=item.skill,
                    prompt=item.prompt,
                    input_artifacts=(
                        "docs/ARCHITECTURE.md",
                        "docs/API_SPEC.md",
                        "docs/DATABASE.md",
                    ),
                    output_schema={
                        "type": "object",
                        "required": ["summary", "artifacts", "checks"],
                    },
                    allowed_paths=item.allowed_paths,
                    dependencies=item.depends_on_task_ids,
                    branch=task.branch,
                    worktree=task.worktree,
                    risk_level=RiskLevel.HIGH,
                    requires_repository_change=True,
                    expected_task_version=task.state_version,
                    expected_state_version=run.state_version + 1,
                )
            )
        return tuple(actions)

    def _ensure_task_group(
        self,
        *,
        run_id: str,
        name: str,
        phase: WorkflowPhase,
        base_commit: str,
        blueprints: tuple[TaskBlueprint, ...],
    ) -> TaskGroupView:
        rows: list[Any] = []
        with self.store.database.read_connection() as connection:
            group = connection.execute(
                "SELECT id, base_commit FROM task_groups "
                "WHERE run_id = ? AND name = ? AND phase = ?",
                (run_id, name, phase.value),
            ).fetchone()
            if group is not None:
                rows = connection.execute(
                    "SELECT task_key, agent, skill, prompt, allowed_paths_json "
                    "FROM tasks WHERE task_group_id = ? ORDER BY task_key",
                    (str(group["id"]),),
                ).fetchall()
        if group is None:
            created, _planned = self.coordination_store.create_group(
                run_id=run_id,
                name=name,
                phase=phase.value,
                base_commit=base_commit,
                blueprints=blueprints,
            )
            return created
        expected = {
            item.key: (
                item.agent,
                item.skill,
                item.prompt,
                tuple(item.allowed_paths),
            )
            for item in blueprints
        }
        actual = {
            str(row["task_key"]): (
                str(row["agent"]),
                str(row["skill"]),
                str(row["prompt"]),
                tuple(cast(list[str], json.loads(str(row["allowed_paths_json"])))),
            )
            for row in rows
        }
        if str(group["base_commit"]) != base_commit or actual != expected:
            raise CoordinationError(
                "STATE_VERSION_CONFLICT", "persisted task group differs from operation intent"
            )
        return self.coordination_store.group_view(str(group["id"]))

    def _task_ids_for_group(self, group_id: str) -> tuple[str, ...]:
        with self.store.database.read_connection() as connection:
            rows = connection.execute(
                "SELECT id FROM tasks WHERE task_group_id = ? ORDER BY created_at, id",
                (group_id,),
            ).fetchall()
        return tuple(str(row["id"]) for row in rows)

    def _action_for_coordinated_task(self, task: TaskRecord) -> NextAction:
        if task.skill is None or task.prompt is None:
            raise WorkflowError("RECOVERY_UNAVAILABLE", "coordinated task contract is incomplete")
        return NextAction(
            kind=ActionKind.MODEL_TASK,
            task_id=task.id,
            task_group_id=task.task_group_id,
            agent=task.agent,
            skill=task.skill,
            prompt=task.prompt,
            input_artifacts=("docs/ARCHITECTURE.md", "docs/API_SPEC.md", "docs/DATABASE.md"),
            output_schema={"type": "object", "required": ["artifacts", "checks"]},
            allowed_paths=task.allowed_paths,
            dependencies=self._task_dependencies(task.id),
            branch=task.branch,
            worktree=task.worktree,
            risk_level=RiskLevel.HIGH,
            requires_repository_change=True,
            expected_task_version=task.state_version,
        )

    def _task_dependencies(self, task_id: str) -> tuple[str, ...]:
        with self.store.database.read_connection() as connection:
            rows = connection.execute(
                "SELECT depends_on_task_id FROM task_dependencies "
                "WHERE task_id = ? ORDER BY depends_on_task_id",
                (task_id,),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def _task_for_handoff(self, handoff_id: str) -> TaskRecord:
        with self.store.database.connection() as connection:
            row = connection.execute(
                "SELECT task_id FROM handoffs WHERE id = ?", (handoff_id,)
            ).fetchone()
        if row is None:
            raise WorkflowError("RECOVERY_UNAVAILABLE", "Handoff task not found")
        return self._get_task(str(row["task_id"]))

    def _allocate_or_block(
        self,
        run: WorkflowRun,
        task: TaskRecord,
        *,
        base_ref: str,
    ) -> None:
        if self.worktrees is None:
            raise WorkflowError(
                "READ_ONLY_OPERATION",
                "Worktree allocation is unavailable from a read-only workflow engine",
                40,
            )
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
            expected_task_version=task.state_version,
            expected_state_version=state_version,
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
            expected_task_version=task.state_version,
            expected_state_version=run.state_version,
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
    release_publication: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "next_action": action.model_dump(mode="json"),
        "pending_gate": pending_gate.value if pending_gate is not None else None,
        "approved_gate": approved_gate.value if approved_gate is not None else None,
        "last_completed_task": last_completed_task,
        "last_commit_sha": last_commit_sha,
        "evidence_refs": list(evidence_refs),
        "resumed_from": resumed_from,
        "release_publication": release_publication,
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


def _actions_from_checkpoint(checkpoint: dict[str, Any]) -> tuple[NextAction, ...]:
    raw_actions = checkpoint.get("next_actions")
    if raw_actions is None:
        action = _action_from_checkpoint(checkpoint)
        return (action,) if action is not None else ()
    if not isinstance(raw_actions, list):
        raise WorkflowError("RECOVERY_UNAVAILABLE", "invalid next actions checkpoint", 30)
    actions: list[NextAction] = []
    for raw in cast(list[object], raw_actions):
        if not isinstance(raw, dict):
            raise WorkflowError("RECOVERY_UNAVAILABLE", "invalid next action entry", 30)
        try:
            actions.append(NextAction.model_validate(cast(dict[str, Any], raw)))
        except ValidationError as exc:
            raise WorkflowError(
                "RECOVERY_UNAVAILABLE", f"invalid next action: {exc}", 30
            ) from exc
    return tuple(actions)


def _coordination_checkpoint(
    previous: dict[str, Any],
    actions: tuple[NextAction, ...],
    *,
    task_group_id: str,
    waiting_handoff: str | None,
    last_commit_sha: str | None = None,
    integration_worktree: str | None = None,
    blocker: dict[str, object] | None = None,
) -> dict[str, Any]:
    serialized = [action.model_dump(mode="json") for action in actions]
    return {
        **previous,
        "next_action": serialized[0] if len(serialized) == 1 else None,
        "next_actions": serialized,
        "task_group_id": task_group_id,
        "waiting_handoff": waiting_handoff,
        "last_commit_sha": last_commit_sha or previous.get("last_commit_sha"),
        "integration_worktree": (
            integration_worktree or previous.get("integration_worktree")
        ),
        "blocker": blocker,
        "pending_gate": None,
    }


def _operations_checkpoint(
    previous: dict[str, Any],
    actions: tuple[NextAction, ...],
    *,
    resumed_from: str,
) -> dict[str, Any]:
    serialized = [action.model_dump(mode="json") for action in actions]
    return {
        **previous,
        "next_action": serialized[0] if len(serialized) == 1 else None,
        "next_actions": serialized,
        "pending_gate": None,
        "blocker": None,
        "resumed_from": resumed_from,
    }


def _default_profiles(project_type: str) -> tuple[str, ...]:
    if project_type == "frontend":
        return ("frontend-project",)
    if project_type == "fullstack":
        return ("backend-project", "frontend-project")
    return ("backend-project",)


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
