"""Run commit-bound quality checks through the configured ExecutionService."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from codex_ai_os.adapters.docker import (
    MountKind,
    SandboxMount,
    SandboxRequest,
    command_hash,
)
from codex_ai_os.application.execution import ExecutionService, ExecutionServiceError
from codex_ai_os.application.verification_cache import (
    VerificationCachePrepareError,
    validate_verification_cache,
)
from codex_ai_os.domain.config import RiskLevel
from codex_ai_os.domain.governance import CheckEvidenceInput, CheckStatus
from codex_ai_os.domain.ids import new_id
from codex_ai_os.domain.versions import RUNTIME_VERSIONS
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
    (
        "pytest",
        (
            "python",
            "-m",
            "pytest",
            "-q",
            "--cov=codex_ai_os",
            "--cov-branch",
            "--cov-report=term-missing",
            "--cov-fail-under=85",
        ),
    ),
    ("ruff", ("python", "-m", "ruff", "check", ".")),
    ("pyright", ("python", "-m", "pyright")),
    (
        "docs",
        ("python", "-m", "codex_ai_os.application.formal_checks", "docs", "/workspace"),
    ),
    (
        "secret-scan",
        ("python", "-m", "codex_ai_os.application.secret_scan", "/workspace"),
    ),
    (
        "bandit",
        (
            "python",
            "-m",
            "bandit",
            "-q",
            "-r",
            "src",
            "--severity-level",
            "high",
            "--confidence-level",
            "high",
        ),
    ),
    (
        "plugin-validator",
        ("python", "-m", "pytest", "-q", "tests/unit/test_plugin_skills.py"),
    ),
    (
        "skill-validator",
        (
            "python",
            "-m",
            "pytest",
            "-q",
            "tests/unit/test_plugin_skills.py",
            "-k",
            "skill_set",
        ),
    ),
    (
        "agent-validator",
        ("python", "-m", "pytest", "-q", "tests/unit/test_agent_profiles.py"),
    ),
    (
        "hook-fixture",
        ("python", "-m", "pytest", "-q", "tests/integration/test_plugin_hooks.py"),
    ),
    (
        "mcp-contract",
        (
            "python",
            "-m",
            "pytest",
            "-q",
            "tests/unit/test_mcp_v11.py",
            "tests/integration/test_mcp_server.py",
            "-k",
            "not plugin_launcher",
        ),
    ),
    (
        "build-install",
        (
            "python",
            "-m",
            "codex_ai_os.application.formal_checks",
            "build-install",
            "/workspace",
        ),
    ),
    (
        "dependency-audit",
        (
            "python",
            "-m",
            "codex_ai_os.application.offline_audit",
            "/cache/audit-snapshot.json",
            "/cache/requirements.txt",
            "/workspace/uv.lock",
        ),
    ),
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
    (
        "real-oci",
        ("python", "-m", "codex_ai_os.application.formal_checks", "real-oci"),
    ),
    (
        "image-sbom",
        (
            "python",
            "-m",
            "codex_ai_os.application.formal_checks",
            "image-sbom",
            "/cache",
        ),
    ),
    (
        "image-scan",
        (
            "python",
            "-m",
            "codex_ai_os.application.formal_checks",
            "image-scan",
            "/cache",
        ),
    ),
)

_OFFLINE_RUNNER = """
import os
import subprocess
import sys

site = "/deps/site"
install = subprocess.run(
    [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-compile",
        "--no-deps",
        "--no-index",
        "--require-hashes",
        "--find-links=/cache",
        "--target",
        site,
        "-r",
        "/cache/requirements.txt",
    ],
    check=False,
)
if install.returncode != 0:
    raise SystemExit(install.returncode)
command = list(sys.argv[1:])
if command and command[0] == "python":
    command[0] = sys.executable
environment = dict(os.environ)
environment["PYTHONPATH"] = f"/workspace/src:{site}"
environment["PATH"] = f"{site}/bin:" + environment.get("PATH", "")
raise SystemExit(subprocess.run(command, env=environment, check=False).returncode)
""".strip()


class VerificationService:
    def __init__(
        self,
        project_root: Path,
        *,
        execution_service: ExecutionService | None = None,
        bootstrap_dependencies: bool | None = None,
    ) -> None:
        self.root = project_root.resolve()
        self.config = load_project_config(self.root)
        self.database = Database(self.root / ".codex-os" / "state" / "state.db")
        self.database.migrate()
        self.worktrees = WorktreeStore(self.database)
        self.execution = execution_service or ExecutionService(self.root)
        self.bootstrap_dependencies = (
            execution_service is None
            if bootstrap_dependencies is None
            else bootstrap_dependencies
        )

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
        wheelhouse = (
            self._wheelhouse(assignment.path) if self.bootstrap_dependencies else None
        )
        git_mounts = (
            self._git_metadata(assignment.path, task_id, source_commit)
            if self.bootstrap_dependencies
            else None
        )
        for name, command in checks:
            if re.fullmatch(r"[a-z][a-z0-9-]{1,63}", name) is None:
                raise ExecutionServiceError(
                    "CONFIG_INVALID", f"invalid verification check name: {name}"
                )
            execution_id = new_id("EXEC")
            request = SandboxRequest(
                execution_id=execution_id,
                task_id=task_id,
                worktree=assignment.to_spec(),
                command=(
                    ("python", "-c", _OFFLINE_RUNNER, *command)
                    if wheelhouse is not None
                    else command
                ),
                risk_level=RiskLevel.HIGH,
                mounts=(
                    (
                        SandboxMount(MountKind.CACHE, wheelhouse, read_only=True),
                        SandboxMount(MountKind.GIT_METADATA, git_mounts[0], read_only=True),
                        SandboxMount(MountKind.GIT_POINTER, git_mounts[1], read_only=True),
                    )
                    if wheelhouse is not None and git_mounts is not None
                    else ()
                ),
            )
            try:
                record = self.execution.execute(request)
            except ExecutionServiceError as exc:
                if exc.record is not None:
                    record = exc.record
                    blockers.append(f"{name}:{exc.code}:{exc}")
                else:
                    blockers.append(f"{name}:{exc.code}:{exc}")
                    ended_at = datetime.now(UTC).isoformat()
                    report = {
                        "schema_version": RUNTIME_VERSIONS.api,
                        "run_id": run_id,
                        "task_id": task_id,
                        "source_commit": source_commit,
                        "check": name,
                        "execution_id": execution_id,
                        "command_hash": command_hash(request.command),
                        "exit_code": 1,
                        "status": CheckStatus.FAILED.value,
                        "execution_status": "failed",
                        "error_code": exc.code,
                        "stdout_ref": None,
                        "stderr_ref": None,
                        "image_digest": request.image.rsplit("@", 1)[-1],
                        "started_at": ended_at,
                        "ended_at": ended_at,
                    }
                    evidence.append(
                        self._write_check_evidence(
                            report=report,
                            run_id=run_id,
                            name=name,
                            source_commit=source_commit,
                        )
                    )
                    continue
            exit_code = record.exit_code if record.exit_code is not None else 1
            status = (
                CheckStatus.PASSED
                if record.status == "completed" and exit_code == 0
                else CheckStatus.FAILED
            )
            if status is CheckStatus.FAILED and not any(
                blocker.startswith(f"{name}:") for blocker in blockers
            ):
                error_code = record.error_code or "EXECUTION_FAILED"
                blockers.append(
                    f"{name}:{error_code}:sandbox command failed with exit code {exit_code}"
                )
            report = {
                "schema_version": RUNTIME_VERSIONS.api,
                "run_id": run_id,
                "task_id": task_id,
                "source_commit": source_commit,
                "check": name,
                "execution_id": record.id,
                "command_hash": record.command_hash,
                "exit_code": exit_code,
                "status": status.value,
                "execution_status": record.status,
                "error_code": record.error_code,
                "stdout_ref": record.stdout_ref,
                "stderr_ref": record.stderr_ref,
                "image_digest": record.image_digest,
                "started_at": record.started_at,
                "ended_at": record.ended_at,
            }
            evidence.append(
                self._write_check_evidence(
                    report=report,
                    run_id=run_id,
                    name=name,
                    source_commit=source_commit,
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

    def _wheelhouse(self, source_root: Path) -> Path:
        lock = source_root / "uv.lock"
        if not lock.is_file():
            raise ExecutionServiceError(
                "SANDBOX_DEPENDENCIES_UNAVAILABLE", "uv.lock is required for verification"
            )
        lock_hash = hashlib.sha256(lock.read_bytes()).hexdigest()
        wheelhouse = self.root / ".codex-os" / "cache" / "verification" / lock_hash
        try:
            validate_verification_cache(wheelhouse, lock_path=lock)
        except (
            VerificationCachePrepareError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ExecutionServiceError(
                getattr(exc, "code", "DEPENDENCY_UNVERIFIED"), str(exc)
            ) from exc
        return wheelhouse

    def _write_check_evidence(
        self,
        *,
        report: Mapping[str, object],
        run_id: str,
        name: str,
        source_commit: str,
    ) -> CheckEvidenceInput:
        content = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        execution_id = str(report["execution_id"])
        relative = (
            f".codex-os/artifacts/{run_id}/verification/{execution_id}-{name}.json"
        )
        DocumentManager(self.root).write_atomic(relative, content, overwrite=False)
        return CheckEvidenceInput(
            name=name,
            command_hash=str(report["command_hash"]),
            execution_id=execution_id,
            exit_code=cast(int, report["exit_code"]),
            report_path=relative,
            report_hash=hashlib.sha256(content.encode()).hexdigest(),
            source_commit=source_commit,
            started_at=str(report["started_at"]),
            ended_at=str(report["ended_at"]),
            executed_at=str(report["ended_at"] or report["started_at"]),
            status=CheckStatus(str(report["status"])),
        )

    def _git_metadata(
        self, source_root: Path, task_id: str, source_commit: str
    ) -> tuple[Path, Path]:
        clone = (
            self.root
            / ".codex-os"
            / "cache"
            / "verification"
            / "git"
            / task_id
            / source_commit
        )
        git_directory = clone / ".git"
        if not git_directory.is_dir():
            if clone.exists():
                raise ExecutionServiceError(
                    "GIT_METADATA_INVALID",
                    f"incomplete verification Git metadata cache: {clone}",
                )
            clone.parent.mkdir(parents=True, exist_ok=True)
            self._run_git(
                "clone",
                "--no-checkout",
                "--local",
                "--",
                str(source_root),
                str(clone),
            )
            self._run_git("-C", str(clone), "read-tree", source_commit)
            self._run_git(
                "-C", str(clone), "update-ref", "--no-deref", "HEAD", source_commit
            )
        head = self._run_git("--git-dir", str(git_directory), "rev-parse", "HEAD")
        if head != source_commit:
            raise ExecutionServiceError(
                "GIT_METADATA_INVALID", "verification Git metadata does not match source commit"
            )
        self._run_git("--git-dir", str(git_directory), "fsck", "--no-dangling")
        pointer = clone / "worktree.git"
        expected_pointer = "gitdir: /git-metadata\n"
        if pointer.is_file():
            if pointer.read_text(encoding="utf-8") != expected_pointer:
                raise ExecutionServiceError(
                    "GIT_METADATA_INVALID", "verification Git pointer was modified"
                )
        else:
            relative = pointer.relative_to(self.root).as_posix()
            DocumentManager(self.root).write_atomic(relative, expected_pointer, overwrite=False)
        return git_directory, pointer

    @staticmethod
    def _run_git(*arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=120,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise ExecutionServiceError(
                "GIT_METADATA_INVALID", detail or "verification Git metadata command failed"
            )
        return completed.stdout.decode("utf-8", errors="strict").strip()

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
