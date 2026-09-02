from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import yaml

from codex_ai_os.application.environment_operations import EnvironmentOperationService
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.domain.config import GitPushPolicy, ProjectType, RiskLevel, SandboxBackend
from codex_ai_os.domain.invocation import InvocationContext, InvocationSource
from codex_ai_os.domain.operations import HostOperationStatus
from codex_ai_os.domain.workflow import (
    RunStatus,
    TaskRecord,
    TaskStatus,
    WorkflowPhase,
    WorkflowRun,
)
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.workflows import WorkflowStore

_IMAGE_DIGEST = "a" * 64


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: list[str], _cwd: Path, _timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        if argv[1:3] == ["system", "df"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps({"Images": [], "BuildCache": [], "Volumes": []}).encode(),
                b"",
            )
        if "config" in argv and "--images" in argv:
            return subprocess.CompletedProcess(argv, 0, b"fixture-image\n", b"")
        if argv[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, b"sha256:fixture\n", b"")
        if "exec" in argv:
            return subprocess.CompletedProcess(argv, 0, b"stable-probe\n", b"")
        return subprocess.CompletedProcess(argv, 0, b"ok\n", b"")


def _governed_project(root: Path) -> tuple[str, str, int, int]:
    ProjectInitializer().initialize(
        root,
        project_id="PROJECT-ENV-OPS",
        name="Environment operations",
        project_type=ProjectType.BACKEND,
        git_push_policy=GitPushPolicy.FIXTURE_LOCAL_ONLY,
    )
    (root / "docker" / "app.Dockerfile").write_text(
        f"FROM python:3.12-bookworm@sha256:{_IMAGE_DIGEST}\n",
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / "compose.yaml").write_text(
        "name: environment-operations\n"
        "services:\n"
        "  app:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: docker/app.Dockerfile\n"
        "    healthcheck:\n"
        "      test: [CMD, python, --version]\n"
        "    networks: [default]\n"
        "networks:\n"
        "  default:\n"
        "    internal: true\n",
        encoding="utf-8",
    )
    (root / ".codex-os" / "environment.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.2",
                "environment_mode": "oci-first",
                "oci_backend": "podman",
                "compose_files": ["compose.yaml"],
                "dockerfiles": ["docker/app.Dockerfile"],
                "dependency_locks": ["uv.lock"],
                "services": [
                    {
                        "name": "app",
                        "dockerfile": "docker/app.Dockerfile",
                        "smoke_command": ["python", "--version"],
                    }
                ],
                "persistent_mounts": [],
                "shared_assets": [],
                "host_budget": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "fixture@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=root, check=True, capture_output=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    now = datetime.now(UTC).isoformat()
    run = WorkflowRun(
        id="RUN-ENV-OPS",
        project_id="PROJECT-ENV-OPS",
        workflow_name="feature-development",
        goal="verify OCI environment",
        workflow_phase=WorkflowPhase.VERIFY,
        run_status=RunStatus.RUNNING,
        state_version=0,
        risk_level=RiskLevel.HIGH,
        checkpoint={"last_commit_sha": commit},
        config_hash="f" * 64,
        created_at=now,
        updated_at=now,
    )
    task = TaskRecord(
        id="TASK-ENV-OPS",
        run_id=run.id,
        agent="qa",
        status=TaskStatus.RUNNING,
        input_hash="e" * 64,
        state_version=0,
        created_at=now,
        updated_at=now,
        allowed_paths=(".codex-os/artifacts/",),
    )
    WorkflowStore(Database(root / ".codex-os" / "state" / "state.db")).create_run(run, task)
    return run.id, task.id, run.state_version, task.state_version


def test_prepare_and_verify_are_persisted_replayable_and_never_delete_volumes(
    tmp_path: Path,
) -> None:
    run_id, task_id, run_version, task_version = _governed_project(tmp_path)
    runner = RecordingRunner()
    service = EnvironmentOperationService(tmp_path, runner=runner)

    scheduled = service.prepare(
        run_id=run_id,
        task_id=task_id,
        expected_state_version=run_version,
        expected_task_version=task_version,
        idempotency_key="environment-prepare",
        network_approval_ref="APPROVAL-L2-ENV",
        expires_at="2099-01-01T00:00:00+00:00",
    ).operation
    replay = service.prepare(
        run_id=run_id,
        task_id=task_id,
        expected_state_version=run_version,
        expected_task_version=task_version,
        idempotency_key="environment-prepare",
        network_approval_ref="APPROVAL-L2-ENV",
        expires_at="2099-01-01T00:00:00+00:00",
    ).operation
    assert replay.operation_id == scheduled.operation_id

    acquired = service.operations.acquire(
        scheduled.operation_id,
        expected_version=scheduled.state_version,
        lease_owner="fixture",
    )
    prepared = service.execute_acquired(acquired, lease_owner="fixture")
    assert prepared.status is HostOperationStatus.SUCCEEDED
    assert prepared.result["network_approval_ref"] == "APPROVAL-L2-ENV"

    verify = service.verify(
        run_id=run_id,
        task_id=task_id,
        expected_state_version=run_version,
        expected_task_version=task_version,
        idempotency_key="environment-verify",
        prepare_operation_id=prepared.operation_id,
    ).operation
    acquired_verify = service.operations.acquire(
        verify.operation_id,
        expected_version=verify.state_version,
        lease_owner="fixture",
    )
    verified = service.execute_acquired(acquired_verify, lease_owner="fixture")

    assert verified.status is HostOperationStatus.SUCCEEDED
    assert verified.result["network"] == "offline-internal-only"
    assert Path(tmp_path / str(verified.result["report_path"])).is_file()
    compose_calls = [call for call in runner.calls if len(call) > 1 and call[1] == "compose"]
    assert any("--pull" in call and "never" in call for call in compose_calls)
    assert all("-v" not in call for call in compose_calls)
    assert all("volume" not in call for call in runner.calls)


def test_legacy_adoption_preserves_existing_compose(tmp_path: Path) -> None:
    run_id, task_id, run_version, task_version = _governed_project(tmp_path)
    project_path = tmp_path / ".codex-os" / "project.yaml"
    project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project.pop("environment_mode")
    project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
    worktree = tmp_path / "adoption-worktree"
    (worktree / ".codex-os").mkdir(parents=True)
    (worktree / ".codex-os" / "project.yaml").write_text(
        project_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (worktree / "compose.yaml").write_text("services: {existing: {}}\n", encoding="utf-8")
    database = Database(tmp_path / ".codex-os" / "state" / "state.db")
    with database.connection() as connection:
        connection.execute(
            "UPDATE tasks SET branch = ?, worktree = ?, allowed_paths_json = ? WHERE id = ?",
            (
                "agent/environment/TASK-ENV-OPS",
                worktree.as_posix(),
                json.dumps(
                    [
                        ".codex-os/",
                        ".dockerignore",
                        "compose.yaml",
                        "docker/",
                        "docs/",
                        "AGENTS.md",
                    ]
                ),
                task_id,
            ),
        )
        connection.commit()
    service = EnvironmentOperationService(tmp_path, runner=RecordingRunner())

    result = service.adopt(
        run_id=run_id,
        task_id=task_id,
        expected_state_version=run_version,
        expected_task_version=task_version,
        idempotency_key="environment-adopt",
        oci_backend=SandboxBackend.DOCKER,
        invocation=InvocationContext.local(InvocationSource.CLI),
    )

    assert result.operation.status is HostOperationStatus.SUCCEEDED
    assert (worktree / "compose.yaml").read_text(encoding="utf-8") == "services: {existing: {}}\n"
    adopted = yaml.safe_load((worktree / ".codex-os" / "project.yaml").read_text())
    assert adopted["environment_mode"] == "oci-first"
    assert "compose.yaml" not in result.created_paths
