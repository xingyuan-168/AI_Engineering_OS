from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from typer.testing import CliRunner

import codex_ai_os.cli.app as cli_app
from codex_ai_os.application.doctor import DoctorCheck, DoctorReport, DoctorService
from codex_ai_os.application.execution import ExecutionServiceError
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.workflow import WorkflowEngine
from codex_ai_os.domain.config import GitPushPolicy, ProjectType

runner = CliRunner()


def test_doctor_cli_reports_success_and_sandbox_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    healthy = DoctorReport(
        checks=(
            DoctorCheck("python", True, True, "3.12"),
            DoctorCheck("podman", False, True, "5.0"),
        )
    )
    def healthy_report(_self: DoctorService) -> DoctorReport:
        return healthy

    monkeypatch.setattr(cli_app.DoctorService, "run", healthy_report)
    passed = runner.invoke(cli_app.app, ["doctor", "--json"])
    assert passed.exit_code == 0
    assert passed.stdout and '"ok":true' in passed.stdout

    unavailable = DoctorReport(
        checks=(
            DoctorCheck("python", True, True, "3.12"),
            DoctorCheck("podman", False, False, "unavailable"),
        )
    )
    def unavailable_report(_self: DoctorService) -> DoctorReport:
        return unavailable

    monkeypatch.setattr(cli_app.DoctorService, "run", unavailable_report)
    failed = runner.invoke(cli_app.app, ["doctor", "--json"])
    assert failed.exit_code == 50
    assert "SANDBOX_UNAVAILABLE" in failed.stdout


def test_repo_check_named_workflows_status_pause_resume_and_errors(tmp_path: Path) -> None:
    missing = runner.invoke(cli_app.app, ["repo-check", str(tmp_path), "--json"])
    assert missing.exit_code == 2
    assert "CONFIG_INVALID" in missing.stdout

    root = _project(tmp_path / "cli")
    checked = runner.invoke(cli_app.app, ["repo-check", str(root), "--json"])
    assert checked.exit_code == 0, checked.stdout

    goals = {
        "feature-development": "Add governed profile routing",
        "bug-fix": "Fix Handoff acceptance ordering",
        "release": "Assemble release candidate",
    }
    run_ids: list[str] = []
    for workflow, goal in goals.items():
        result = runner.invoke(
            cli_app.app,
            [
                "run",
                workflow,
                "--goal",
                goal,
                "--project-root",
                str(root),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.stdout
        run_ids.append(str(_payload(result.stdout)["run_id"]))

    status = runner.invoke(
        cli_app.app,
        ["status", str(root), "--run-id", run_ids[0], "--json"],
    )
    assert status.exit_code == 0
    assert _payload(status.stdout)["run_id"] == run_ids[0]
    missing_run = runner.invoke(
        cli_app.app,
        ["step", "RUN-MISSING", "--project-root", str(root), "--json"],
    )
    assert missing_run.exit_code == 30
    assert "RECOVERY_UNAVAILABLE" in missing_run.stdout

    WorkflowEngine(root).pause(run_ids[0], reason="operator checkpoint")
    resumed = runner.invoke(
        cli_app.app,
        ["resume", run_ids[0], "--project-root", str(root), "--json"],
    )
    assert resumed.exit_code == 0, resumed.stdout
    assert _payload(resumed.stdout)["run_status"] == "running"
    terminal_error = runner.invoke(
        cli_app.app,
        ["resume", "RUN-MISSING", "--project-root", str(root), "--json"],
    )
    assert terminal_error.exit_code == 30

    invalid_root = root / "not-a-directory"
    invalid_root.write_text("file\n", encoding="utf-8")
    invalid_init = runner.invoke(
        cli_app.app,
        ["init", str(invalid_root), "--project-id", "DIFFERENT", "--json"],
    )
    assert invalid_init.exit_code == 2
    assert "CONFIG_INVALID" in invalid_init.stdout


def test_cli_wrappers_cover_verification_handoff_cleanup_and_g4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path / "wrappers")
    current = WorkflowEngine(root).start("Provide wrapper result")
    assert current.active_task is not None
    task_id = current.active_task.id

    class _Engine:
        def __init__(self, _root: Path) -> None:
            pass

        def submit_approval(self, *_args: Any, **kwargs: Any) -> Any:
            assert kwargs["g4_evidence"].release_authority.authorized is True
            return current

        def review_handoff(self, _review: Any) -> Any:
            assert _review.expected_handoff_version == 7
            assert _review.idempotency_key == "handoff-review-idem"
            assert str(_review.reviewer).startswith("os:")
            assert _review.reviewer != "reviewer"
            return current

        def execute_host_operation(
            self,
            operation_id: str,
            *,
            expected_operation_version: int,
            idempotency_key: str,
            invocation: Any,
        ) -> Any:
            assert operation_id == "OP-1"
            assert expected_operation_version == 3
            assert idempotency_key == "host-op-idem"
            assert str(invocation.principal).startswith("os:")
            return current

    monkeypatch.setattr(cli_app, "WorkflowEngine", _Engine)
    approval = runner.invoke(
        cli_app.app,
        [
            "approve",
            current.run.id,
            "--gate",
            "G4",
            "--reason",
            "release evidence approved",
            "--pr-number",
            "42",
            "--pr-url",
            "https://github.com/example/ai-os/pull/42",
            "--merge-commit",
            "e" * 40,
            "--version",
            "0.2.0",
            "--release-authorized",
            "--release-authorized-by",
            "release-owner",
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert approval.exit_code == 0, approval.stdout

    report = root / "review.md"
    report.write_text("accepted\n", encoding="utf-8")
    handoff = runner.invoke(
        cli_app.app,
        [
            "handoff",
            "review",
            "HANDOFF-1",
            "--reviewer",
            "reviewer",
            "--reviewed-commit",
            "a" * 40,
            "--decision",
            "accepted",
            "--reason",
            "reviewed",
            "--report-ref",
            "review.md",
            "--report-hash",
            "b" * 64,
            "--expected-handoff-version",
            "7",
            "--idempotency-key",
            "handoff-review-idem",
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert handoff.exit_code == 0, handoff.stdout
    assert _payload(handoff.stdout)["warnings"]
    host_operation = runner.invoke(
        cli_app.app,
        [
            "host-operation",
            "execute",
            "OP-1",
            "--expected-operation-version",
            "3",
            "--idempotency-key",
            "host-op-idem",
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert host_operation.exit_code == 0, host_operation.stdout
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    prepared = runner.invoke(
        cli_app.app,
        [
            "verification",
            "prepare",
            current.run.id,
            "--expected-state-version",
            str(current.run.state_version),
            "--idempotency-key",
            "prepare-cli",
            "--network-approval-ref",
            "APPROVED-NETWORK",
            "--expires-at",
            "2026-08-28T00:00:00+00:00",
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert prepared.exit_code == 0, prepared.stdout
    assert _payload(prepared.stdout)["next_action"]["kind"] == "host_operation"
    migrated = runner.invoke(
        cli_app.app,
        [
            "database",
            "migrate",
            "--expected-schema-version",
            "0007",
            "--target-schema-version",
            "0007",
            "--idempotency-key",
            "migrate-cli",
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert migrated.exit_code == 0, migrated.stdout
    assert _payload(migrated.stdout)["data"]["operation"]["status"] == "succeeded"

    class _ReleaseCandidateService:
        def __init__(self, _root: Path) -> None:
            pass

        def create(self, run_id: str) -> Any:
            assert run_id == current.run.id
            return SimpleNamespace(
                id=f"RC-{run_id}",
                version="0.2.0",
                source_commit="a" * 40,
                release_worktree=str(root / ".worktrees" / "release"),
                manifest_path="release/manifest.json",
                manifest_hash="b" * 64,
                artifact_root=str(root / ".codex-os" / "artifacts" / run_id),
                artifacts={"dist.whl": "c" * 64},
                sbom_path=".codex-os/artifacts/sbom.cdx.json",
                sbom_hash="d" * 64,
                checksums_path=".codex-os/artifacts/checksums.sha256",
                checksums_hash="e" * 64,
                rollback_path="release/rollback.md",
                rollback_hash="f" * 64,
                created=True,
            )

    monkeypatch.setattr(cli_app, "ReleaseCandidateService", _ReleaseCandidateService)
    release = runner.invoke(
        cli_app.app,
        [
            "release",
            "candidate",
            current.run.id,
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert release.exit_code == 0, release.stdout
    assert _payload(release.stdout)["data"]["candidate_id"] == f"RC-{current.run.id}"

    memory_content = root / "docs" / "CLI_MEMORY.md"
    memory_source = root / "docs" / "ADR" / "0098-cli-source.md"
    memory_content.write_text(
        "# Durable CLI decision\n\nUse source-linked evidence.\n",
        encoding="utf-8",
    )
    memory_source.write_text("# Source\n\nAccepted decision.\n", encoding="utf-8")
    submitted = runner.invoke(
        cli_app.app,
        [
            "memory",
            "submit",
            "--record-type",
            "decision",
            "--title",
            "CLI source-linked decision",
            "--content-ref",
            "docs/CLI_MEMORY.md",
            "--source-ref",
            "docs/ADR/0098-cli-source.md",
            "--confidence",
            "0.95",
            "--tag",
            "cli",
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert submitted.exit_code == 0, submitted.stdout
    memory_id = str(_payload(submitted.stdout)["data"]["record"]["id"])
    reviewed = runner.invoke(
        cli_app.app,
        [
            "memory",
            "review",
            memory_id,
            "--reviewer",
            "owner",
            "--decision",
            "activate",
            "--reason",
            "source verified",
            "--expected-version",
            "0",
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert reviewed.exit_code == 0, reviewed.stdout
    searched = runner.invoke(
        cli_app.app,
        [
            "memory",
            "search",
            "CLI",
            "--record-type",
            "decision",
            "--status",
            "active",
            "--tag",
            "cli",
            "--source-ref",
            "docs/CLI_MEMORY.md",
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert searched.exit_code == 0, searched.stdout
    assert _payload(searched.stdout)["data"]["results"][0]["id"] == memory_id
    invalid_handoff = runner.invoke(
        cli_app.app,
        [
            "handoff",
            "review",
            "HANDOFF-1",
            "--reviewer",
            "reviewer",
            "--reviewed-commit",
            "bad",
            "--decision",
            "accepted",
            "--reason",
            "reviewed",
            "--report-ref",
            "review.md",
            "--report-hash",
            "bad",
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert invalid_handoff.exit_code == 40

    cleanup = runner.invoke(
        cli_app.app,
        [
            "worktree",
            "cleanup",
            "TASK-MISSING",
            "--merged-into",
            "main",
            "--reason",
            "merged",
            "--approved-by",
            "owner",
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert cleanup.exit_code == 40
    assert "RECOVERY_UNAVAILABLE" in cleanup.stdout

    class _Check:
        def model_dump(self, *, mode: str) -> dict[str, Any]:
            assert mode == "json"
            return {"name": "pytest", "status": "passed"}

    class _Verification:
        def __init__(self, _root: Path) -> None:
            pass

        def run(self, *, run_id: str, task_id: str) -> Any:
            return SimpleNamespace(
                run_id=run_id,
                task_id=task_id,
                source_commit="a" * 40,
                valid=True,
                checks=(_Check(),),
                blockers=(),
            )

    monkeypatch.setattr(cli_app, "VerificationService", _Verification)
    verified = runner.invoke(
        cli_app.app,
        [
            "verify",
            "--run-id",
            current.run.id,
            "--task-id",
            task_id,
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert verified.exit_code == 0

    class _FailedVerification(_Verification):
        def run(self, *, run_id: str, task_id: str) -> Any:
            result = super().run(run_id=run_id, task_id=task_id)
            result.valid = False
            result.blockers = ("pytest",)
            return result

    monkeypatch.setattr(cli_app, "VerificationService", _FailedVerification)
    failed = runner.invoke(
        cli_app.app,
        [
            "verify",
            "--run-id",
            current.run.id,
            "--task-id",
            task_id,
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert failed.exit_code == 50
    assert "EXECUTION_FAILED" in failed.stdout

    class _UnavailableVerification(_Verification):
        def run(self, *, run_id: str, task_id: str) -> Any:
            raise ExecutionServiceError("SANDBOX_UNAVAILABLE", "Podman unavailable")

    monkeypatch.setattr(cli_app, "VerificationService", _UnavailableVerification)
    unavailable = runner.invoke(
        cli_app.app,
        [
            "verify",
            "--run-id",
            current.run.id,
            "--task-id",
            task_id,
            "--project-root",
            str(root),
            "--json",
        ],
    )
    assert unavailable.exit_code == 50
    assert "SANDBOX_UNAVAILABLE" in unavailable.stdout


def _payload(output: str) -> dict[str, Any]:
    import json

    value: object = json.loads(output)
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _project(root: Path) -> Path:
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    ProjectInitializer().initialize(
        root,
        project_id=f"PROJECT-{root.name.upper()}",
        name="CLI fixture",
        project_type=ProjectType.BACKEND,
        git_push_policy=GitPushPolicy.FIXTURE_LOCAL_ONLY,
        schema_version="1.1",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "chore: initialize CLI fixture")
    return root


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
