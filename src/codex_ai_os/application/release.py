"""Release-Worktree candidate assembly with sandboxed build and managed artifacts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import tomllib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from codex_ai_os.adapters.docker import MountKind, SandboxMount, SandboxRequest
from codex_ai_os.application.execution import ExecutionService, ExecutionServiceError
from codex_ai_os.application.workflow import WorkflowEngine, WorkflowError
from codex_ai_os.domain.config import RiskLevel
from codex_ai_os.domain.ids import new_id
from codex_ai_os.domain.workflow import RunStatus, WorkflowPhase
from codex_ai_os.infrastructure.config import load_project_config
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.documents import DocumentManager
from codex_ai_os.infrastructure.worktrees import WorktreeStore


@dataclass(frozen=True, slots=True)
class ReleaseCandidate:
    id: str
    version: str
    source_commit: str
    release_worktree: str
    manifest_path: str
    manifest_hash: str
    artifact_root: str
    artifacts: dict[str, str]
    sbom_path: str
    sbom_hash: str
    checksums_path: str
    checksums_hash: str
    rollback_path: str
    rollback_hash: str
    created: bool


class ReleaseCandidateService:
    VERSION = "0.2.0"
    REQUIREMENT_BASELINE = "REQ-1.6.2"
    PLUGIN_API = "1.1"
    CONFIG_SCHEMA = "1.1"
    SQLITE_SCHEMA = "0006"

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

    def create(self, run_id: str) -> ReleaseCandidate:
        result = WorkflowEngine(self.root).status(run_id)
        if (
            result.run.workflow_phase is not WorkflowPhase.RELEASE
            or result.run.run_status is not RunStatus.RUNNING
            or result.active_task is None
        ):
            raise WorkflowError(
                "RELEASE_INCOMPLETE",
                "release candidate requires an active Release task after G3 approval",
                30,
            )
        assignment = self.worktrees.get_for_task(result.active_task.id)
        if assignment is None or assignment.status != "active":
            raise WorkflowError("WORKTREE_BLOCKED", "Release task has no active Worktree", 40)
        source_commit = self._git_head(assignment.path)
        existing = self._existing(run_id)
        if existing is not None:
            if existing.source_commit != source_commit:
                raise WorkflowError(
                    "RELEASE_SOURCE_CHANGED",
                    "existing Release Candidate was built from a different Commit",
                    40,
                )
            return existing

        artifact_root = self.root / ".codex-os" / "artifacts" / run_id
        artifact_root.mkdir(parents=True, exist_ok=True)
        self._build(run_id, result.active_task.id, assignment.to_spec(), artifact_root)
        built = {
            path.name: _sha256(path)
            for path in sorted(artifact_root.iterdir())
            if path.is_file() and path.suffix in {".whl", ".gz", ".zip"}
        }
        if not built:
            raise WorkflowError("RELEASE_INCOMPLETE", "sandbox build produced no artifacts", 40)

        lock_hash = _sha256(assignment.path / "uv.lock")
        sbom = self._sbom(assignment.path, source_commit, built)
        sbom_relative = f".codex-os/artifacts/{run_id}/sbom.cdx.json"
        sbom_content = json.dumps(sbom, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        DocumentManager(self.root).write_atomic(sbom_relative, sbom_content, overwrite=False)
        sbom_hash = hashlib.sha256(sbom_content.encode()).hexdigest()

        checksum_lines = [f"{digest}  {name}" for name, digest in sorted(built.items())]
        checksum_lines.append(f"{sbom_hash}  sbom.cdx.json")
        checksums_content = "\n".join(checksum_lines) + "\n"
        checksums_relative = f".codex-os/artifacts/{run_id}/checksums.sha256"
        DocumentManager(self.root).write_atomic(
            checksums_relative, checksums_content, overwrite=False
        )
        checksums_hash = hashlib.sha256(checksums_content.encode()).hexdigest()

        rollback_relative = "release/rollback.md"
        rollback_content = (
            "# AI Engineering OS 0.2.0 Rollback\n\n"
            f"Source Commit: `{source_commit}`\n\n"
            "Rollback uses a new `git revert` Commit and the verified pre-0004 SQLite backup; "
            "never rewrite pushed history or run reverse DROP migrations. If schema recovery is "
            "required, enter read-only maintenance mode, verify backup checksum/integrity, restore "
            "the complete database, and re-run repository/Gate validation before resuming.\n"
        )
        DocumentManager(assignment.path).write_atomic(
            rollback_relative, rollback_content, overwrite=False
        )
        rollback_hash = hashlib.sha256(rollback_content.encode()).hexdigest()

        manifest_relative = "release/manifest.json"
        manifest: dict[str, object] = {
            "schema_version": "1.1",
            "requirement_baseline": self.REQUIREMENT_BASELINE,
            "software_version": self.VERSION,
            "plugin_api_version": self.PLUGIN_API,
            "config_schema_version": self.CONFIG_SCHEMA,
            "sqlite_schema_version": self.SQLITE_SCHEMA,
            "git_tag": f"v{self.VERSION}",
            "project_id": result.run.project_id,
            "run_id": run_id,
            "release_task_id": result.active_task.id,
            "source_commit": source_commit,
            "config_hash": result.run.config_hash,
            "dependency_lock_hash": lock_hash,
            "artifacts": built,
            "sbom": {"path": sbom_relative, "sha256": sbom_hash},
            "checksums": {"path": checksums_relative, "sha256": checksums_hash},
            "rollback": {"path": rollback_relative, "sha256": rollback_hash},
            "github": {"pr": None, "merge_commit": None, "release_id": None},
            "memory_record_ids": list[str](),
        }
        manifest_content = json.dumps(
            manifest, ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
        DocumentManager(assignment.path).write_atomic(
            manifest_relative, manifest_content, overwrite=False
        )
        manifest_hash = hashlib.sha256(manifest_content.encode()).hexdigest()
        candidate = ReleaseCandidate(
            id=f"RC-{run_id}",
            version=self.VERSION,
            source_commit=source_commit,
            release_worktree=assignment.path.as_posix(),
            manifest_path=manifest_relative,
            manifest_hash=manifest_hash,
            artifact_root=artifact_root.as_posix(),
            artifacts=built,
            sbom_path=sbom_relative,
            sbom_hash=sbom_hash,
            checksums_path=checksums_relative,
            checksums_hash=checksums_hash,
            rollback_path=rollback_relative,
            rollback_hash=rollback_hash,
            created=True,
        )
        self._persist(result.run.id, result.active_task.id, assignment.id, lock_hash, candidate)
        return candidate

    def _build(self, run_id: str, task_id: str, worktree: object, root: Path) -> None:
        from codex_ai_os.adapters.worktree import WorktreeSpec

        if not isinstance(worktree, WorktreeSpec):
            raise TypeError("worktree must be a WorktreeSpec")
        request = SandboxRequest(
            execution_id=new_id("EXEC-RELEASE"),
            task_id=task_id,
            worktree=worktree,
            command=("python", "-m", "build", "--outdir", "/artifacts"),
            risk_level=RiskLevel.HIGH,
            mounts=(SandboxMount(MountKind.ARTIFACTS, root),),
        )
        try:
            self.execution.execute(request)
        except ExecutionServiceError as exc:
            raise WorkflowError(exc.code, str(exc), 50) from exc

    def _sbom(
        self, worktree: Path, source_commit: str, artifacts: dict[str, str]
    ) -> dict[str, object]:
        lock = cast(
            dict[str, object],
            tomllib.loads((worktree / "uv.lock").read_text(encoding="utf-8")),
        )
        packages: list[dict[str, object]] = []
        raw_packages = lock.get("package")
        if isinstance(raw_packages, list):
            for raw in cast(list[object], raw_packages):
                if not isinstance(raw, dict):
                    continue
                package = cast(dict[object, object], raw)
                name = package.get("name")
                version = package.get("version")
                if isinstance(name, str) and isinstance(version, str):
                    packages.append(
                        {
                            "type": "library",
                            "name": name,
                            "version": version,
                            "purl": f"pkg:pypi/{name}@{version}",
                        }
                    )
        return {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "serialNumber": f"urn:uuid:{uuid.uuid4()}",
            "version": 1,
            "metadata": {
                "component": {
                    "type": "application",
                    "name": "codex-ai-engineering-os",
                    "version": self.VERSION,
                    "properties": [
                        {"name": "source_commit", "value": source_commit},
                        {"name": "artifacts", "value": json.dumps(artifacts, sort_keys=True)},
                    ],
                }
            },
            "components": packages,
        }

    def _existing(self, run_id: str) -> ReleaseCandidate | None:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT r.*, w.path AS worktree_path FROM release_records r
                LEFT JOIN worktrees w ON w.id = r.release_worktree_id
                WHERE r.run_id = ? AND r.status = 'candidate'
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        manifest_path = str(row["manifest_path"])
        worktree = Path(str(row["worktree_path"]))
        manifest = json.loads((worktree / manifest_path).read_text(encoding="utf-8"))
        return ReleaseCandidate(
            id=f"RC-{run_id}",
            version=self.VERSION,
            source_commit=str(row["source_commit"]),
            release_worktree=worktree.as_posix(),
            manifest_path=manifest_path,
            manifest_hash=str(row["manifest_hash"]),
            artifact_root=str(row["artifact_root"]),
            artifacts={str(k): str(v) for k, v in manifest["artifacts"].items()},
            sbom_path=str(row["sbom_path"]),
            sbom_hash=str(row["sbom_hash"]),
            checksums_path=str(row["checksums_path"]),
            checksums_hash=str(row["checksums_hash"]),
            rollback_path=str(row["rollback_path"]),
            rollback_hash=str(row["rollback_hash"]),
            created=False,
        )

    def _persist(
        self,
        run_id: str,
        task_id: str,
        worktree_id: str,
        lock_hash: str,
        candidate: ReleaseCandidate,
    ) -> None:
        now = _utc_now()
        version_id = new_id("VERSION")
        release_id = new_id("RELEASE")
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO version_records(
                        id, project_id, requirement_baseline, software_version,
                        plugin_api_version, config_schema_version, sqlite_schema_version,
                        git_tag, source_commit, config_hash, dependency_lock_hash,
                        status, created_at, updated_at
                    ) SELECT ?, project_id, ?, ?, ?, ?, ?, ?, ?, config_hash, ?,
                             'candidate', ?, ? FROM workflow_runs WHERE id = ?
                    """,
                    (
                        version_id,
                        self.REQUIREMENT_BASELINE,
                        self.VERSION,
                        self.PLUGIN_API,
                        self.CONFIG_SCHEMA,
                        self.SQLITE_SCHEMA,
                        f"v{self.VERSION}",
                        candidate.source_commit,
                        lock_hash,
                        now,
                        now,
                        run_id,
                    ),
                )
                actual_version = connection.execute(
                    "SELECT id FROM version_records WHERE project_id = ? AND software_version = ?",
                    (self.config.project_id, self.VERSION),
                ).fetchone()
                if actual_version is None:
                    raise WorkflowError("RELEASE_INCOMPLETE", "version record was not created", 40)
                connection.execute(
                    """
                    INSERT INTO release_records(
                        id, run_id, task_id, version_record_id, status,
                        release_worktree_id, manifest_path, manifest_hash, artifact_root,
                        sbom_path, sbom_hash, checksums_path, checksums_hash,
                        rollback_path, rollback_hash, source_commit, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        release_id,
                        run_id,
                        task_id,
                        str(actual_version["id"]),
                        worktree_id,
                        candidate.manifest_path,
                        candidate.manifest_hash,
                        candidate.artifact_root,
                        candidate.sbom_path,
                        candidate.sbom_hash,
                        candidate.checksums_path,
                        candidate.checksums_hash,
                        candidate.rollback_path,
                        candidate.rollback_hash,
                        candidate.source_commit,
                        now,
                        now,
                    ),
                )
                connection.commit()
            except (sqlite3.Error, WorkflowError):
                connection.rollback()
                raise

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
            raise WorkflowError("GIT_EVIDENCE_INVALID", "cannot resolve Release HEAD", 40)
        return completed.stdout.decode("utf-8", errors="strict").strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
