"""Run commit-bound quality checks through the configured ExecutionService."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from codex_ai_os.adapters.docker import SandboxRequest
from codex_ai_os.application.execution import ExecutionService, ExecutionServiceError
from codex_ai_os.domain.config import RiskLevel
from codex_ai_os.domain.governance import CheckEvidenceInput, CheckStatus
from codex_ai_os.domain.ids import new_id
from codex_ai_os.infrastructure.config import load_project_config
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.documents import DocumentManager
from codex_ai_os.infrastructure.worktrees import WorktreeStore


@dataclass(frozen=True, slots=True)
class VerificationResult:
    run_id: str
    task_id: str
    source_commit: str
    valid: bool
    checks: tuple[CheckEvidenceInput, ...]
    blockers: tuple[str, ...]


DEFAULT_CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pytest", ("python", "-m", "pytest", "-q")),
    ("ruff", ("ruff", "check", ".")),
    ("pyright", ("pyright",)),
    ("secret-scan", ("python", "-m", "detect_secrets", "scan")),
    ("dependency-audit", ("python", "-m", "pip_audit")),
    (
        "security-scan",
        (
            "python",
            "-m",
            "pytest",
            "-q",
            "tests/unit/test_docker_sandbox.py",
            "tests/unit/test_git_evidence.py",
        ),
    ),
)


class VerificationService:
    def __init__(
        self,
        project_root: Path,
        *,
        execution_service: ExecutionService | None = None,
    ) -> None:
        self.root = project_root.resolve()
        self.config = load_project_config(self.root)
        self.database = Database(self.root / ".codex-os" / "state" / "state.db")
        self.database.migrate()
        self.worktrees = WorktreeStore(self.database)
        self.execution = execution_service or ExecutionService(self.root)

    def run(
        self,
        *,
        run_id: str,
        task_id: str,
        checks: tuple[tuple[str, tuple[str, ...]], ...] = DEFAULT_CHECKS,
    ) -> VerificationResult:
        assignment = self.worktrees.get_for_task(task_id)
        if assignment is None or assignment.run_id != run_id or assignment.status != "active":
            raise ExecutionServiceError(
                "WORKTREE_BLOCKED", "verification requires the active assigned Worktree"
            )
        source_commit = self._git_head(assignment.path)
        evidence: list[CheckEvidenceInput] = []
        blockers: list[str] = []
        for name, command in checks:
            execution_id = new_id("EXEC")
            request = SandboxRequest(
                execution_id=execution_id,
                task_id=task_id,
                worktree=assignment.to_spec(),
                command=command,
                risk_level=RiskLevel.HIGH,
            )
            try:
                record = self.execution.execute(request)
            except ExecutionServiceError as exc:
                blockers.append(f"{name}:{exc.code}:{exc}")
                continue
            report = {
                "schema_version": "1.1",
                "run_id": run_id,
                "task_id": task_id,
                "source_commit": source_commit,
                "check": name,
                "execution_id": record.id,
                "command_hash": record.command_hash,
                "exit_code": record.exit_code,
                "stdout_ref": record.stdout_ref,
                "stderr_ref": record.stderr_ref,
                "image_digest": record.image_digest,
                "started_at": record.started_at,
                "ended_at": record.ended_at,
            }
            content = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            relative = (
                f".codex-os/artifacts/{run_id}/verification/{execution_id}-{name}.json"
            )
            manager = DocumentManager(self.root)
            manager.write_atomic(relative, content, overwrite=False)
            report_hash = hashlib.sha256(content.encode()).hexdigest()
            evidence.append(
                CheckEvidenceInput(
                    name=name,
                    command_hash=record.command_hash,
                    execution_id=record.id,
                    exit_code=record.exit_code or 0,
                    report_path=relative,
                    report_hash=report_hash,
                    source_commit=source_commit,
                    executed_at=record.ended_at or record.started_at,
                    status=CheckStatus.PASSED,
                )
            )
        return VerificationResult(
            run_id=run_id,
            task_id=task_id,
            source_commit=source_commit,
            valid=not blockers and len(evidence) == len(checks),
            checks=tuple(evidence),
            blockers=tuple(blockers),
        )

    @staticmethod
    def _git_head(worktree: Path) -> str:
        completed = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if completed.returncode != 0:
            raise ExecutionServiceError("GIT_EVIDENCE_INVALID", "cannot resolve Worktree HEAD")
        return completed.stdout.decode("utf-8", errors="strict").strip()
