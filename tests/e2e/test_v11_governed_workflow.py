from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from codex_ai_os.adapters.docker import SandboxRequest, SandboxResult, command_hash
from codex_ai_os.application.execution import ExecutionService
from codex_ai_os.application.g4 import GitHubReleaseGovernanceService
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.release import ReleaseCandidateService
from codex_ai_os.application.verification import DEFAULT_CHECKS, VerificationService
from codex_ai_os.application.workflow import WorkflowEngine, WorkflowResult
from codex_ai_os.domain.config import GitPushPolicy, ProjectType
from codex_ai_os.domain.coordination import HandoffReviewInput
from codex_ai_os.domain.governance import (
    ArtifactEvidenceInput,
    CheckEvidenceInput,
    G4ApprovalInput,
    ReleaseAuthority,
    ReviewDecision,
    ReviewEvidenceInput,
)
from codex_ai_os.domain.invocation import InvocationContext, InvocationSource
from codex_ai_os.domain.workflow import (
    ChangeKind,
    Gate,
    NextAction,
    PushStatus,
    TaskCompletion,
)
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.evidence import EvidenceStore
from codex_ai_os.infrastructure.memory import MemoryStore


class _SuccessfulSandbox:
    def run(self, request: SandboxRequest) -> SandboxResult:
        for mount in request.mounts:
            if mount.kind.value == "artifacts":
                mount.source.mkdir(parents=True, exist_ok=True)
                (mount.source / "codex_ai_engineering_os-0.2.0-py3-none-any.whl").write_bytes(
                    b"governed-wheel"
                )
        now = datetime.now(UTC).isoformat()
        return SandboxResult(
            execution_id=request.execution_id,
            command_hash=command_hash(request.command),
            image_digest=request.image.rsplit("@", 1)[1],
            container_name=f"codex-os-{request.execution_id.lower()}",
            exit_code=0,
            stdout="passed\n",
            stderr="",
            worktree_dirty=False,
            started_at=now,
            ended_at=now,
        )


class _GitHubRunner:
    def __init__(self, root: Path) -> None:
        self.root = root

    def __call__(
        self, command: list[str], _cwd: Path, _timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        if command[:3] == ["git", "fetch", "--prune"]:
            return self._result(command)
        if command[:2] == ["git", "merge-base"]:
            return self._result(command)
        if command[:3] == ["git", "rev-parse", "refs/tags/v0.2.0^{}"]:
            return self._result(command, returncode=1)
        if command[:2] == ["git", "tag"]:
            return self._result(command)
        if command[:3] == ["git", "ls-remote", "--tags"]:
            output = (
                f"{'a' * 40}\trefs/tags/v0.2.0\n"
                f"{'e' * 40}\trefs/tags/v0.2.0^{{}}\n"
            )
            return self._result(command, stdout=output)
        if command[:3] == ["gh", "pr", "view"]:
            with Database(self.root / ".codex-os" / "state" / "state.db").connection() as db:
                run = db.execute(
                    "SELECT integration_branch, integration_head, target_branch "
                    "FROM workflow_runs ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            return self._result(
                command,
                stdout=json.dumps(
                    {
                        "number": 42,
                        "url": "https://github.com/example/ai-os/pull/42",
                        "state": "MERGED",
                        "headRefName": run["integration_branch"],
                        "headRefOid": run["integration_head"],
                        "baseRefName": run["target_branch"],
                        "mergeCommit": {"oid": "e" * 40},
                        "mergedAt": "2026-08-24T00:00:00Z",
                    }
                ),
            )
        if command[:3] == ["gh", "release", "view"]:
            return self._result(
                command,
                stdout=json.dumps(
                    {
                        "id": "R_fixture",
                        "url": "https://github.com/example/ai-os/releases/tag/v0.2.0",
                        "isDraft": False,
                        "tagName": "v0.2.0",
                    }
                ),
            )
        raise AssertionError(f"unexpected publication command: {command}")

    @staticmethod
    def _result(
        command: list[str], *, returncode: int = 0, stdout: str = ""
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, returncode, stdout.encode(), b"")


def test_public_v11_multi_agent_workflow_reaches_g4_with_strong_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Governance Test")
    _git(root, "config", "user.email", "governance@example.invalid")
    ProjectInitializer().initialize(
        root,
        project_id="PROJECT-V11-E2E",
        name="Governed V11",
        project_type=ProjectType.FULLSTACK,
        git_push_policy=GitPushPolicy.FIXTURE_LOCAL_ONLY,
        schema_version="1.2",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "chore: initialize governed fixture")

    execution = ExecutionService(root, sandbox=_SuccessfulSandbox())
    engine = WorkflowEngine(
        root,
        g4_publisher=GitHubReleaseGovernanceService(root, runner=_GitHubRunner(root)),
    )
    current = engine.start(
        "Deliver the governed 0.2.0 runtime",
        profiles=("backend", "frontend", "large"),
        target_branch="main",
    )

    current, _ = _complete(
        engine,
        current,
        {
            "docs/PROJECT_MASTER.md": _doc(
                "Project Master", ("目标", "范围", "成功标准", "风险", "Routing Decision")
            ),
            "docs/SCOPE.md": _doc("Scope", ("范围内", "范围外", "成功标准")),
        },
    )
    current = engine.submit_approval(
        current.run.id, gate=Gate.G0, approved=True, reviewer="owner", reason="G0 verified"
    )

    current, _ = _complete(
        engine,
        current,
        {
            "docs/PRODUCT_REQUIREMENTS.md": _doc(
                "Requirements", ("范围", "成功标准", "验收标准")
            ),
            "docs/USER_STORY.md": _doc("User Stories", ("用户", "场景", "验收")),
            "docs/BUSINESS_RULES.md": _doc("Business Rules", ("规则", "异常", "验收")),
        },
    )
    current = engine.submit_approval(
        current.run.id, gate=Gate.G1, approved=True, reviewer="owner", reason="G1 verified"
    )

    current, _ = _complete(
        engine,
        current,
        {
            "docs/OPEN_SOURCE_RESEARCH.md": _doc(
                "Research", ("来源", "版本", "License", "风险")
            ),
            "docs/TECH_STACK.md": _doc("Stack", ("Python", "SQLite", "Podman")),
        },
    )
    current, design_commit = _complete(
        engine,
        current,
        {
            "docs/ARCHITECTURE.md": _doc(
                "Architecture", ("定位", "组件", "信任边界", "恢复")
            ),
            "docs/API_SPEC.md": _doc("API", ("接口", "错误", "兼容")),
            "docs/DATABASE.md": _doc("Database", ("迁移", "事务", "恢复")),
            "docs/SECURITY.md": _doc("Security", ("信任", "威胁", "风险")),
        },
    )
    current = engine.submit_approval(
        current.run.id, gate=Gate.G2, approved=True, reviewer="owner", reason="G2 verified"
    )
    assert current.run.integration_head == design_commit
    assert len(current.next_actions) == 3

    initial_actions = current.next_actions
    for action in initial_actions:
        assert action.worktree is not None
        if action.agent == "backend-engineer":
            files = {
                "src/codex_ai_os/application/generated_backend.py": "VALUE = 'backend'\n",
                "pyproject.toml": (
                    "[build-system]\nrequires = ['hatchling']\n"
                    "build-backend = 'hatchling.build'\n\n"
                    "[project]\nname = 'governed-fixture'\nversion = '0.2.0'\n"
                ),
                "uv.lock": "version = 1\nrevision = 3\npackage = []\n",
            }
        elif action.agent == "frontend-engineer":
            files = {"src/codex_ai_os/frontend/generated_frontend.py": "VALUE = 'frontend'\n"}
        else:
            files = {
                "tests/integration/test_generated_governance.py": (
                    "def test_generated_governance() -> None:\n    assert True\n"
                )
            }
        current, _ = _complete_action(engine, current, action, files)

    backend_handoff = _handoff_for_agent(engine, current.run.id, "backend-engineer")
    current = _review_handoff(root, engine, backend_handoff)
    assert len(current.next_actions) == 1
    database_action = current.next_actions[0]
    assert database_action.agent == "database-engineer"
    current, _ = _complete_action(
        engine,
        current,
        database_action,
        {"src/codex_ai_os/infrastructure/migrations/9999_fixture.sql": "SELECT 1;\n"},
    )

    for handoff_id in _ready_handoffs(engine, current.run.id):
        current = _review_handoff(root, engine, handoff_id)
    assert current.run.workflow_phase.value == "verify"
    assert len(current.next_actions) == 1

    verify_action = current.next_actions[0]
    verify_commit = _commit_files(
        verify_action,
        {"reports/TEST_RESULTS.json": '{"status":"passed"}\n'},
    )
    verification = VerificationService(root, execution_service=execution).run(
        run_id=current.run.id,
        task_id=str(verify_action.task_id),
        checks=DEFAULT_CHECKS,
    )
    current = _complete_committed_action(
        engine,
        current,
        verify_action,
        verify_commit,
        {"reports/TEST_RESULTS.json": "verification-report"},
        checks=verification.checks,
    )
    evidence = EvidenceStore(engine.store.database, root)
    _record_review(
        root, evidence, current.run.id, str(verify_action.task_id), verify_commit, "code"
    )
    _record_review(
        root, evidence, current.run.id, str(verify_action.task_id), verify_commit, "security"
    )
    current = _review_handoff(root, engine, _only_ready_handoff(engine, current.run.id))
    assert current.run.run_status.value == "needs_approval"
    current = engine.submit_approval(
        current.run.id, gate=Gate.G3, approved=True, reviewer="owner", reason="G3 verified"
    )

    release_action = current.next_actions[0]
    candidate = ReleaseCandidateService(root, execution_service=execution).create(current.run.id)
    release_worktree = Path(str(release_action.worktree))
    (release_worktree / "docs" / "CHANGELOG.md").write_text(
        _doc("Changelog", ("0.2.0", "Governance")), encoding="utf-8"
    )
    release_commit = _git_commit(release_worktree, "feat: assemble governed release")
    release_checks = VerificationService(root, execution_service=execution).run(
        run_id=current.run.id,
        task_id=str(release_action.task_id),
        checks=tuple(
            (name, ("python", "--version"))
            for name in ("release-manifest", "sbom", "checksums", "rollback")
        ),
    ).checks
    current = _complete_committed_action(
        engine,
        current,
        release_action,
        release_commit,
        {
            candidate.manifest_path: "release-manifest",
            candidate.rollback_path: "rollback",
            candidate.sbom_path: "sbom",
            candidate.checksums_path: "checksums",
            "docs/CHANGELOG.md": "changelog",
        },
        checks=release_checks,
    )
    current = _review_handoff(root, engine, _only_ready_handoff(engine, current.run.id))
    assert current.run.workflow_phase.value == "memory"

    memory_action = current.next_actions[0]
    memory_path = ".codex-os/memory/RELEASE_0_2_0.md"
    memory_commit = _commit_files(
        memory_action,
        {
            memory_path: "# Release Memory\n\nGoverned 0.2.0 evidence is reusable.\n",
            "docs/ADR/0001-governance.md": _doc(
                "ADR 0001 Governance", ("Decision", "Consequences")
            ),
        },
    )
    memory_store = MemoryStore(
        engine.store.database, Path(str(memory_action.worktree)), current.run.project_id
    )
    memory = memory_store.create_candidate(
        record_type="release",
        title="Governed 0.2.0 release",
        content_ref=memory_path,
        source_refs=("docs/ADR/0001-governance.md",),
        confidence=1.0,
        tags=("release", "governance"),
        run_id=current.run.id,
        task_id=str(memory_action.task_id),
    )
    assert memory_store.activate(memory.id).status == "active"
    current = _complete_committed_action(
        engine,
        current,
        memory_action,
        memory_commit,
        {memory_path: "memory", "docs/ADR/0001-governance.md": "adr"},
    )
    current = _review_handoff(root, engine, _only_ready_handoff(engine, current.run.id))
    _record_review(
        root,
        evidence,
        current.run.id,
        str(memory_action.task_id),
        memory_commit,
        "release",
    )
    merge_commit = "e" * 40
    completed = engine.submit_approval(
        current.run.id,
        gate=Gate.G4,
        approved=True,
        reviewer="release-owner",
        reason="G4 publication verified",
        g4_evidence=G4ApprovalInput(
            pr_number=42,
            pr_url="https://github.com/example/ai-os/pull/42",
            merge_commit=merge_commit,
            version="0.2.0",
            release_authority=ReleaseAuthority(
                authorized=True,
                scope="tag-and-github-release",
                authorized_by="release-owner",
            ),
        ),
    )
    assert completed.run.run_status.value == "completed"
    assert completed.run.checkpoint["release_publication"]["tag"] == "v0.2.0"


def _doc(title: str, sections: tuple[str, ...]) -> str:
    metadata = json.dumps(
        {
            "schema_version": "1.2",
            "document_version": "0.2.0",
            "status": "approved",
            "owner": "fixture-owner",
            "requirement_refs": ["REQ-1.6.2"],
        },
        separators=(",", ":"),
    )
    body = "\n\n".join(f"## {section}\n\nVerified content." for section in sections)
    return f"<!-- codex-os-document: {metadata} -->\n# {title}\n\n{body}\n"


def _complete(
    engine: WorkflowEngine, current: WorkflowResult, files: dict[str, str]
) -> tuple[WorkflowResult, str]:
    assert current.next_action is not None
    return _complete_action(engine, current, current.next_action, files)


def _complete_action(
    engine: WorkflowEngine,
    current: WorkflowResult,
    action: NextAction,
    files: dict[str, str],
) -> tuple[WorkflowResult, str]:
    commit = _commit_files(action, files)
    return (
        _complete_committed_action(
            engine,
            current,
            action,
            commit,
            {path: "document" for path in files},
        ),
        commit,
    )


def _commit_files(action: NextAction, files: dict[str, str]) -> str:
    assert action.worktree is not None
    worktree = Path(action.worktree)
    for relative, content in files.items():
        target = worktree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return _git_commit(worktree, f"feat: complete {action.task_id}")


def _complete_committed_action(
    engine: WorkflowEngine,
    current: WorkflowResult,
    action: NextAction,
    commit: str,
    paths_and_types: dict[str, str],
    *,
    checks: tuple[CheckEvidenceInput, ...] = (),
) -> WorkflowResult:
    assert action.worktree is not None
    assert action.task_id is not None
    assert action.branch is not None
    worktree = Path(action.worktree)
    artifacts: list[ArtifactEvidenceInput] = []
    hashes: dict[str, str] = {}
    for relative, artifact_type in paths_and_types.items():
        path = (
            engine.config.root / relative
            if relative.startswith(".codex-os/artifacts/")
            else worktree / relative
        )
        content = (
            path.read_bytes()
            if relative.startswith(".codex-os/artifacts/")
            else _git_bytes(worktree, "show", f"{commit}:{relative}")
        )
        digest = hashlib.sha256(content).hexdigest()
        hashes[relative] = digest
        artifacts.append(
            ArtifactEvidenceInput(
                path=relative,
                artifact_type=artifact_type,
                sha256=digest,
                source_commit=commit,
            )
        )
    return engine.complete_task(
        current.run.id,
        TaskCompletion(
            task_id=action.task_id,
            change_kind=ChangeKind.REPOSITORY,
            branch=action.branch,
            commit_sha=commit,
            push_status=PushStatus.LOCAL_ONLY,
            artifact_paths_and_hashes=hashes,
            artifacts=tuple(artifacts),
            checks=checks,
        ),
    )


def _review_handoff(root: Path, engine: WorkflowEngine, handoff_id: str) -> WorkflowResult:
    with engine.store.database.read_connection() as connection:
        row = connection.execute(
            "SELECT task_id, state_version FROM handoffs WHERE id = ?", (handoff_id,)
        ).fetchone()
        assert row is not None
        task = connection.execute(
            "SELECT head_commit FROM tasks WHERE id = ?", (str(row["task_id"]),)
        ).fetchone()
        assert task is not None
    relative = f".codex-os/artifacts/reviews/{handoff_id}.md"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Handoff Review\n\nAccepted.\n", encoding="utf-8")
    idempotency_key = f"e2e:handoff:{handoff_id}:{row['state_version']}"
    prepared = engine.review_handoff(
        HandoffReviewInput(
            handoff_id=handoff_id,
            expected_handoff_version=int(row["state_version"]),
            idempotency_key=idempotency_key,
            reviewer="independent-reviewer",
            reviewed_commit=str(task["head_commit"]),
            decision=ReviewDecision.ACCEPTED,
            reason="evidence accepted",
            report_ref=relative,
            report_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    )
    assert prepared.next_action is not None
    assert prepared.next_action.operation_id is not None
    assert prepared.next_action.expected_operation_version is not None
    return engine.execute_host_operation(
        prepared.next_action.operation_id,
        expected_operation_version=prepared.next_action.expected_operation_version,
        idempotency_key=idempotency_key,
        invocation=InvocationContext.local(InvocationSource.CLI),
    )


def _record_review(
    root: Path,
    evidence: EvidenceStore,
    run_id: str,
    task_id: str,
    commit: str,
    review_type: str,
) -> None:
    relative = f".codex-os/artifacts/reviews/{task_id}-{review_type}.md"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {review_type.title()} Review\n\nAccepted.\n", encoding="utf-8")
    evidence.record_review(
        project_id="PROJECT-V11-E2E",
        run_id=run_id,
        task_id=task_id,
        review=ReviewEvidenceInput(
            review_type=review_type,
            reviewer=f"{review_type}-reviewer",
            reviewed_commit=commit,
            decision=ReviewDecision.ACCEPTED,
            report_ref=relative,
            report_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
        ),
    )


def _handoff_for_agent(engine: WorkflowEngine, run_id: str, agent: str) -> str:
    with engine.store.database.connection() as connection:
        row = connection.execute(
            """
            SELECT h.id FROM handoffs h JOIN tasks t ON t.id = h.task_id
            WHERE h.run_id = ? AND t.agent = ? AND h.status = 'ready'
            """,
            (run_id, agent),
        ).fetchone()
    return str(row["id"])


def _ready_handoffs(engine: WorkflowEngine, run_id: str) -> tuple[str, ...]:
    with engine.store.database.connection() as connection:
        rows = connection.execute(
            "SELECT id FROM handoffs WHERE run_id = ? AND status = 'ready' ORDER BY created_at",
            (run_id,),
        ).fetchall()
    return tuple(str(row["id"]) for row in rows)


def _only_ready_handoff(engine: WorkflowEngine, run_id: str) -> str:
    handoffs = _ready_handoffs(engine, run_id)
    assert len(handoffs) == 1
    return handoffs[0]


def _git_commit(root: Path, message: str) -> str:
    _git(root, "add", ".")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _git(root: Path, *arguments: str) -> str:
    return _git_bytes(root, *arguments).decode("utf-8", errors="strict").strip()


def _git_bytes(root: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return result.stdout
