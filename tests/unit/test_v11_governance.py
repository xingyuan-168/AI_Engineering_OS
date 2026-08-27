# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from codex_ai_os.adapters.docker import SandboxRequest, SandboxResult, command_hash
from codex_ai_os.application.execution import ExecutionService, ExecutionServiceError
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.repository import RepositoryGovernanceService
from codex_ai_os.application.verification import VerificationService
from codex_ai_os.application.workflow import WorkflowEngine, WorkflowError
from codex_ai_os.domain.config import GitPushPolicy, ProjectType
from codex_ai_os.domain.operations import HostOperationKind
from codex_ai_os.domain.workflow import PHASE_DEFINITIONS, ActionKind, WorkflowPhase
from codex_ai_os.infrastructure.config import load_project_config
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.documents import DocumentManager, PathDeniedError
from codex_ai_os.infrastructure.memory import MemoryStore, MemoryStoreError


def test_backend_task_contract_allows_dependency_lock_updates() -> None:
    allowed_paths = PHASE_DEFINITIONS[WorkflowPhase.IMPLEMENTATION].allowed_paths

    assert "pyproject.toml" in allowed_paths
    assert "uv.lock" in allowed_paths


def test_fixture_repository_check_detects_hygiene_and_tracked_pollution(tmp_path: Path) -> None:
    root = _fixture_project(tmp_path / "fixture")
    clean = RepositoryGovernanceService(root).check()
    assert clean.repository_ready is True
    assert clean.hygiene_ok is True

    forbidden = root / "backup"
    forbidden.mkdir()
    (forbidden / "final.py").write_text("VALUE = 1\n", encoding="utf-8")
    dirty = RepositoryGovernanceService(root).check()
    codes = {finding.code for finding in dirty.findings}
    assert {"WORKTREE_DIRTY", "FORBIDDEN_DIRECTORY", "FORBIDDEN_FILE"} <= codes

    _git(root, "add", "-f", "backup/final.py")
    _git(root, "commit", "-m", "test: add forbidden fixture")
    tracked = RepositoryGovernanceService(root).check()
    assert tracked.repository_ready is False


def test_formal_repository_check_accepts_github_and_rejects_other_hosts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "formal"
    ProjectInitializer().initialize(
        root,
        project_id="PROJECT-FORMAL",
        name="Formal",
        project_type=ProjectType.BACKEND,
        schema_version="1.1",
    )
    happy = RepositoryGovernanceService(root, runner=_GitRunner(root, github=True)).check()
    assert happy.repository_ready is True
    assert happy.remote_host == "github.com"
    assert happy.upstream_ref == "origin/main"

    rejected = RepositoryGovernanceService(root, runner=_GitRunner(root, github=False)).check()
    codes = {finding.code for finding in rejected.findings}
    assert "GITHUB_REMOTE_REQUIRED" in codes
    assert "REMOTE_UNREACHABLE" in codes


def test_repository_check_reports_git_preconditions_pollution_and_secrets(
    tmp_path: Path,
) -> None:
    root = _fixture_project(tmp_path / "broken-repository")
    formal_config = load_project_config(root).model_copy(
        update={"git_push_policy": GitPushPolicy.REMOTE_REQUIRED}
    )
    report = RepositoryGovernanceService(
        root, runner=_BrokenGitRunner(root), config=formal_config
    ).check()
    codes = {finding.code for finding in report.findings}
    assert {
        "HEAD_MISSING",
        "UNRESOLVED_CONFLICT",
        "WORKTREE_DIRTY",
        "GITHUB_REMOTE_REQUIRED",
        "UPSTREAM_MISSING",
        "TARGET_BRANCH_MISSING",
    } <= codes

    (root / "v2").mkdir()
    (root / "v2" / "backup.py").write_text("VALUE = 1\n", encoding="utf-8")
    secret = root / "credential.txt"
    secret.write_text("token=fixture-detected-secret-value-123456\n", encoding="utf-8")
    log = root / "tracked.log"
    log.write_text("tracked log\n", encoding="utf-8")
    _git(root, "add", "v2/backup.py", "credential.txt")
    _git(root, "add", "-f", "tracked.log")
    _git(root, "commit", "-m", "test: add repository governance violations")
    polluted = RepositoryGovernanceService(root).check()
    polluted_codes = {finding.code for finding in polluted.findings}
    assert {
        "FORBIDDEN_DIRECTORY",
        "FORBIDDEN_FILE",
        "SECRET_DETECTED",
        "TRACKED_POLLUTION",
    } <= polluted_codes

    (root / ".gitignore").write_text("*.log\n", encoding="utf-8")
    incomplete_ignore = RepositoryGovernanceService(root).check()
    assert "GITIGNORE_INCOMPLETE" in {
        finding.code for finding in incomplete_ignore.findings
    }


def test_repository_git_runner_exception_is_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "runner-error"
    ProjectInitializer().initialize(
        root,
        project_id="PROJECT-RUNNER-ERROR",
        name="Runner error",
        project_type=ProjectType.BACKEND,
        schema_version="1.1",
    )

    def raising_runner(
        _command: list[str], _cwd: Path, _timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        raise OSError("git unavailable")

    report = RepositoryGovernanceService(root, runner=raising_runner).check()
    assert report.repository_ready is False
    assert "NOT_GIT_REPOSITORY" in {finding.code for finding in report.findings}


def test_document_governance_reports_metadata_placeholder_staleness_and_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    ProjectInitializer().initialize(
        root,
        project_id="PROJECT-DOCS-V11",
        name="Docs",
        project_type=ProjectType.BACKEND,
        schema_version="1.1",
    )
    manager = DocumentManager(root)
    metadata = (
        '<!-- codex-os-document: {"schema_version":"1.1",'
        '"document_version":"0.1.0","status":"approved","owner":"docs",'
        '"requirement_refs":["REQ-1.6.2"],"expires_at":"2020-01-01T00:00:00Z"} -->'
    )
    (root / "docs" / "API_SPEC.md").write_text(
        f"{metadata}\n# API\n\nTODO\n\n[missing](NOPE.md)\n", encoding="utf-8"
    )
    (root / "debug").mkdir()
    report = manager.check("backend")
    assert "docs/API_SPEC.md" in report.placeholder_findings
    assert "docs/API_SPEC.md" in report.version_mismatches
    assert "docs/API_SPEC.md" in report.stale_documents
    assert any("missing section" in finding for finding in report.impact_findings)
    assert any("NOPE.md" in link for link in report.broken_links)
    assert "debug" in report.forbidden_directories

    with pytest.raises(PathDeniedError, match="absolute"):
        manager.resolve(root / "outside.md")
    with pytest.raises(PathDeniedError, match="escapes"):
        manager.resolve("../outside.md")
    assert manager.write_atomic("docs/API_SPEC.md", "unchanged", overwrite=False) is False


def test_memory_lifecycle_filters_invalidation_supersession_and_rejection(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    result = ProjectInitializer().initialize(
        root,
        project_id="PROJECT-MEMORY-V11",
        name="Memory",
        project_type=ProjectType.BACKEND,
        schema_version="1.1",
    )
    content = root / "docs" / "MEMORY_NOTE.md"
    source = root / "docs" / "ADR" / "0001-source.md"
    content.write_text("# Durable decision\n\nUse additive migrations.\n", encoding="utf-8")
    source.write_text("# Source\n\nAccepted.\n", encoding="utf-8")
    store = MemoryStore(Database(result.database_path), root, result.config.project_id)

    with pytest.raises(MemoryStoreError, match="unsupported"):
        store.create_candidate(
            record_type="chat",
            title="bad",
            content_ref="docs/MEMORY_NOTE.md",
            source_refs=("docs/ADR/0001-source.md",),
            confidence=1.0,
        )
    with pytest.raises(MemoryStoreError, match="confidence"):
        store.create_candidate(
            record_type="decision",
            title="bad",
            content_ref="docs/MEMORY_NOTE.md",
            source_refs=("docs/ADR/0001-source.md",),
            confidence=2.0,
        )
    low = store.create_candidate(
        record_type="decision",
        title="Low confidence",
        content_ref="docs/MEMORY_NOTE.md",
        source_refs=("docs/ADR/0001-source.md",),
        confidence=0.2,
    )
    with pytest.raises(MemoryStoreError, match="confidence"):
        store.activate(low.id)

    first = store.create_candidate(
        record_type="decision",
        title="Additive migration decision",
        content_ref="docs/MEMORY_NOTE.md",
        source_refs=("docs/ADR/0001-source.md",),
        confidence=0.9,
        tags=("database", "governance"),
    )
    assert store.activate(first.id).status == "active"
    assert [item.id for item in store.search("additive", tags=("database",))] == [first.id]
    assert store.search("", source_ref="docs/MEMORY_NOTE.md")[0].id == first.id
    with pytest.raises(MemoryStoreError, match="limit"):
        store.search("", limit=0)
    with pytest.raises(MemoryStoreError, match="statuses"):
        store.search("", statuses=("deleted",))

    source.write_text("# Source\n\nChanged.\n", encoding="utf-8")
    assert store.invalidate_changed_sources() == (first.id,)
    assert store.get(first.id).status == "needs_review"
    source.write_text("# Source\n\nChanged.\n", encoding="utf-8")
    second = store.create_candidate(
        record_type="decision",
        title="Replacement decision",
        content_ref="docs/MEMORY_NOTE.md",
        source_refs=("docs/ADR/0001-source.md",),
        confidence=1.0,
    )
    store.activate(second.id)
    assert store.supersede(first.id, second.id, reviewer="owner", reason="new ADR").status == (
        "superseded"
    )
    revoked = store.review(
        second.id, reviewer="owner", decision="revoke", reason="rollback"
    )
    assert revoked.status == "revoked"
    with pytest.raises(MemoryStoreError, match="not found"):
        store.get("MEMORY-MISSING")

    content.write_text("token=not-a-real-but-detected-secret-value\n", encoding="utf-8")
    with pytest.raises(MemoryStoreError, match="secret"):
        store.create_candidate(
            record_type="failure",
            title="Secret",
            content_ref="docs/MEMORY_NOTE.md",
            source_refs=("docs/ADR/0001-source.md",),
            confidence=1.0,
        )


def test_migrated_workflow_blocks_transitions_until_resume_revalidation(
    tmp_path: Path,
) -> None:
    root = _fixture_project(tmp_path / "migration")
    engine = WorkflowEngine(root)
    started = engine.start("Revalidate an active migrated workflow")
    database = engine.store.database
    with database.connection() as connection:
        connection.execute(
            "UPDATE workflow_runs SET migration_revalidation_required = 1 WHERE id = ?",
            (started.run.id,),
        )
        connection.commit()

    with pytest.raises(WorkflowError) as blocked:
        engine.pause(started.run.id, reason="maintenance")
    assert blocked.value.code == "MIGRATION_REVALIDATION_REQUIRED"

    resumed = engine.resume(started.run.id)
    assert resumed.run.run_status.value == "running"
    assert resumed.run.migration_revalidation_required is False
    assert resumed.run.checkpoint["migration_revalidation"]["evidence_bundle_ids"] == []


def test_migrated_workflow_refuses_legacy_approval_without_strong_bundle(
    tmp_path: Path,
) -> None:
    root = _fixture_project(tmp_path / "legacy-approval")
    engine = WorkflowEngine(root)
    started = engine.start("Reject legacy approval evidence")
    database = engine.store.database
    with database.connection() as connection:
        connection.execute(
            "UPDATE workflow_runs SET migration_revalidation_required = 1 WHERE id = ?",
            (started.run.id,),
        )
        connection.execute(
            """
            INSERT INTO approvals(
                id, run_id, gate, decision, reviewer, reason,
                evidence_refs_json, state_version, created_at
            ) VALUES ('APPROVAL-LEGACY', ?, 'G0', 'approved', 'legacy',
                      'legacy evidence', '[]', 1, '2026-08-24T00:00:00+00:00')
            """,
            (started.run.id,),
        )
        connection.commit()

    with pytest.raises(WorkflowError) as blocked:
        engine.resume(started.run.id)
    assert blocked.value.code == "MIGRATION_REVALIDATION_REQUIRED"
    assert "G0" in str(blocked.value)


def test_resume_rebuilds_recoverable_host_operation_actions(tmp_path: Path) -> None:
    root = _fixture_project(tmp_path / "host-operation-resume")
    engine = WorkflowEngine(root)
    started = engine.start("Resume recoverable host operations")
    operations = tuple(
        engine.operations.ensure_pending(
            project_id=started.run.project_id,
            run_id=started.run.id,
            kind=HostOperationKind.INTEGRATION_MERGE,
            idempotency_key=f"resume-operation-{index}",
            request={"index": index},
        )
        for index in range(5)
    )
    with engine.store.database.connection() as connection:
        connection.execute(
            "UPDATE workflow_runs SET run_status = 'blocked' WHERE id = ?",
            (started.run.id,),
        )
        connection.commit()

    resumed = engine.resume(started.run.id)

    assert resumed.run.run_status.value == "running"
    assert resumed.next_action is None
    assert [action.kind for action in resumed.next_actions] == [ActionKind.HOST_OPERATION] * 4
    assert [action.operation_id for action in resumed.next_actions] == [
        operation.operation_id for operation in operations[:4]
    ]
    assert [action.expected_operation_version for action in resumed.next_actions] == [
        0,
        0,
        0,
        0,
    ]


def test_verification_requires_lock_bound_offline_wheelhouse(tmp_path: Path) -> None:
    root = _fixture_project(tmp_path / "verification-cache")
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    service = VerificationService(root, bootstrap_dependencies=True)

    with pytest.raises(ExecutionServiceError) as missing:
        service._wheelhouse(root)
    assert missing.value.code == "SANDBOX_DEPENDENCIES_UNAVAILABLE"

    lock_hash = hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest()
    wheelhouse = root / ".codex-os" / "cache" / "verification" / lock_hash
    wheelhouse.mkdir(parents=True)
    (wheelhouse / "requirements.txt").write_text(
        "fixture==1.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8"
    )
    (wheelhouse / "fixture-1.0-py3-none-any.whl").write_bytes(b"fixture")
    (wheelhouse / "audit-snapshot.json").write_text("{}", encoding="utf-8")
    assert service._wheelhouse(root) == wheelhouse


class _FailingSandbox:
    def __init__(self, *, exit_code: int) -> None:
        self.exit_code = exit_code

    def run(self, request: SandboxRequest) -> SandboxResult:
        return SandboxResult(
            execution_id=request.execution_id,
            command_hash=command_hash(request.command),
            image_digest=request.image.rsplit("@", 1)[1],
            container_name=f"codex-os-{request.execution_id.lower()}",
            exit_code=self.exit_code,
            stdout="",
            stderr="tests failed\n",
            worktree_dirty=False,
            started_at="2026-08-27T00:00:01+00:00",
            ended_at="2026-08-27T00:00:02+00:00",
        )


def test_verification_records_failed_check_evidence_for_nonzero_exit(
    tmp_path: Path,
) -> None:
    root = _fixture_project(tmp_path / "verification-failed-check")
    engine = WorkflowEngine(root)
    started = engine.start("Record failed verification evidence")
    assert started.active_task is not None
    execution = ExecutionService(root, sandbox=_FailingSandbox(exit_code=7))
    service = VerificationService(root, execution_service=execution)

    result = service.run(
        run_id=started.run.id,
        task_id=started.active_task.id,
        checks=(("pytest", ("python", "-m", "pytest", "-q")),),
    )

    assert result.valid is False
    assert result.blockers == (
        "pytest:EXECUTION_FAILED:sandbox command failed with exit code 7",
    )
    assert len(result.checks) == 1
    check = result.checks[0]
    assert check.status.value == "failed"
    assert check.exit_code == 7
    report = json.loads((root / check.report_path).read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["exit_code"] == 7


def test_verification_builds_commit_bound_container_git_metadata(tmp_path: Path) -> None:
    root = _fixture_project(tmp_path / "verification-git")
    service = VerificationService(root, bootstrap_dependencies=True)
    source_commit = service._git_head(root)

    metadata, pointer = service._git_metadata(root, "TASK-GIT-METADATA", source_commit)

    assert metadata.is_dir()
    assert pointer.read_text(encoding="utf-8") == "gitdir: /git-metadata\n"
    assert service._run_git("--git-dir", str(metadata), "rev-parse", "HEAD") == source_commit
    assert service._git_metadata(root, "TASK-GIT-METADATA", source_commit) == (
        metadata,
        pointer,
    )


class _GitRunner:
    def __init__(self, root: Path, *, github: bool) -> None:
        self.root = root
        self.github = github

    def __call__(
        self, command: list[str], _cwd: Path, _timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        args = command[3:]
        if args == ["rev-parse", "--show-toplevel"]:
            return _result(command, self.root.as_posix())
        if args == ["rev-parse", "HEAD"]:
            return _result(command, "a" * 40)
        if args[:2] == ["diff", "--name-only"] or args[:2] == ["status", "--porcelain"]:
            return _result(command)
        if args == ["remote", "get-url", "origin"]:
            remote = "git@github.com:example/ai-os.git" if self.github else "https://gitlab.com/x/y"
            return _result(command, remote)
        if args[:2] == ["ls-remote", "--exit-code"]:
            return _result(command, returncode=0 if self.github else 1)
        if args == ["rev-parse", "--abbrev-ref", "@{upstream}"]:
            return _result(command, "origin/main")
        if args[:2] == ["merge-base", "--is-ancestor"]:
            return _result(command)
        if args[:3] == ["show-ref", "--verify", "--quiet"]:
            return _result(command)
        if args == ["ls-files", "-z"]:
            return _result(command, ".gitignore\0")
        raise AssertionError(f"unexpected git command: {command}")


class _BrokenGitRunner:
    def __init__(self, root: Path) -> None:
        self.root = root

    def __call__(
        self, command: list[str], _cwd: Path, _timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        args = command[3:]
        if args == ["rev-parse", "--show-toplevel"]:
            return _result(command, self.root.as_posix())
        if args == ["rev-parse", "HEAD"]:
            return _result(command, returncode=1)
        if args[:2] == ["diff", "--name-only"]:
            return _result(command, "src/conflicted.py")
        if args[:2] == ["status", "--porcelain"]:
            return _result(command, " M src/dirty.py")
        if args == ["remote", "get-url", "origin"]:
            return _result(command, returncode=1)
        if args == ["rev-parse", "--abbrev-ref", "@{upstream}"]:
            return _result(command, returncode=1)
        if args[:3] == ["show-ref", "--verify", "--quiet"]:
            return _result(command, returncode=1)
        if args == ["ls-files", "-z"]:
            return _result(command, returncode=1)
        raise AssertionError(f"unexpected git command: {command}")


def _fixture_project(root: Path) -> Path:
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    ProjectInitializer().initialize(
        root,
        project_id="PROJECT-REPO-FIXTURE",
        name="Fixture",
        project_type=ProjectType.BACKEND,
        git_push_policy=GitPushPolicy.FIXTURE_LOCAL_ONLY,
        schema_version="1.1",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "chore: initialize fixture")
    return root


def _result(
    command: list[str], stdout: str = "", *, returncode: int = 0
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(command, returncode, stdout.encode(), b"")


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
