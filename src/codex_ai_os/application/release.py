"""Release-Worktree candidate assembly with sandboxed build and managed artifacts."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import subprocess
import tomllib
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import cast

from codex_ai_os.adapters.docker import MountKind, SandboxMount, SandboxRequest
from codex_ai_os.application.execution import ExecutionService, ExecutionServiceError
from codex_ai_os.application.plugin_packaging import (
    PluginPackageError,
    validate_candidate_plugin_package,
)
from codex_ai_os.application.verification_cache import (
    VerificationCachePrepareError,
    validate_verification_cache,
)
from codex_ai_os.application.workflow import WorkflowEngine, WorkflowError
from codex_ai_os.domain.config import RiskLevel
from codex_ai_os.domain.ids import new_id
from codex_ai_os.domain.operations import HostOperation, HostOperationKind, HostOperationStatus
from codex_ai_os.domain.versions import RUNTIME_VERSIONS
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
    candidate_commit: str | None
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


@dataclass(frozen=True, slots=True)
class PluginPackageMetadata:
    source_manifest_version: str
    packaged_version: str
    packaged_manifest_hash: str


_RELEASE_BUILD_RUNNER = """
import datetime
import json
import os
import pathlib
import subprocess
import sys
import zipfile

epoch = int(sys.argv[1])
site = "/deps/site"
install = subprocess.run(
    [
        sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
        "--no-compile", "--no-deps", "--no-index", "--require-hashes",
        "--find-links=/cache", "--target", site, "-r", "/cache/requirements.txt",
    ],
    check=False,
)
if install.returncode != 0:
    raise SystemExit(install.returncode)
environment = dict(os.environ)
environment["PYTHONPATH"] = site
environment["SOURCE_DATE_EPOCH"] = str(epoch)
environment["PYTHONHASHSEED"] = "0"
built = subprocess.run(
    [sys.executable, "-m", "build", "--no-isolation", "--outdir", "/artifacts"],
    env=environment,
    check=False,
)
if built.returncode != 0:
    raise SystemExit(built.returncode)
plugin_version = sys.argv[2]
plugin_source = pathlib.Path("/artifacts/.plugin-source.zip")
if not plugin_source.is_file():
    raise SystemExit("staged plugin source archive is missing")
archive = pathlib.Path(f"/artifacts/ai-engineering-os-plugin-{plugin_version}.zip")
stamp = datetime.datetime.fromtimestamp(max(epoch, 315532800), datetime.UTC)
date_time = (stamp.year, stamp.month, stamp.day, stamp.hour, stamp.minute, stamp.second)
with zipfile.ZipFile(plugin_source) as source, zipfile.ZipFile(
    archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
) as bundle:
    for source_info in sorted(source.infolist(), key=lambda item: item.filename):
        if source_info.is_dir():
            continue
        prefix = "plugins/ai-engineering-os/"
        if not source_info.filename.startswith(prefix):
            raise SystemExit("staged plugin archive entry escaped its source root")
        relative = source_info.filename[len(prefix):]
        if not relative or ".." in pathlib.PurePosixPath(relative).parts:
            raise SystemExit("staged plugin archive entry is invalid")
        content = source.read(source_info)
        if relative == ".codex-plugin/plugin.json":
            manifest = json.loads(content.decode("utf-8"))
            manifest["version"] = plugin_version
            content = (json.dumps(
                manifest, ensure_ascii=False, indent=2, sort_keys=True
            ) + "\\n").encode("utf-8")
        info = zipfile.ZipInfo(f"ai-engineering-os/{relative}", date_time=date_time)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        bundle.writestr(info, content)
plugin_source.unlink()
""".strip()


class ReleaseCandidateService:
    VERSION = RUNTIME_VERSIONS.software
    REQUIREMENT_BASELINE = RUNTIME_VERSIONS.requirement_baseline
    PLUGIN_API = RUNTIME_VERSIONS.api
    CONFIG_SCHEMA = RUNTIME_VERSIONS.config_schema
    SQLITE_SCHEMA = RUNTIME_VERSIONS.sqlite_schema

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
        self.require_offline_cache = execution_service is None

    def create(
        self,
        run_id: str,
        *,
        operation: HostOperation | None = None,
        expected_task_version: int | None = None,
    ) -> ReleaseCandidate:
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
        if (
            expected_task_version is not None
            and result.active_task.state_version != expected_task_version
        ):
            raise WorkflowError(
                "STATE_VERSION_CONFLICT",
                "Release task version changed before candidate lookup",
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
            self._validate_existing(existing)
            return existing

        if operation is None:
            raise WorkflowError(
                "APPROVAL_REQUIRED",
                "new API 1.2 candidates require the persisted release_prepare operation",
                40,
            )
        if (
            operation.kind is not HostOperationKind.RELEASE_PREPARE
            or operation.status is not HostOperationStatus.RUNNING
            or operation.run_id != run_id
        ):
            raise WorkflowError(
                "RECOVERY_UNAVAILABLE", "release_prepare operation is not active", 40
            )
        integration_source = operation.request.get("integration_source_commit")
        if integration_source != source_commit or result.run.integration_head != source_commit:
            raise WorkflowError(
                "RELEASE_SOURCE_CHANGED",
                "Release Worktree HEAD does not match the authorized integration Commit",
                40,
            )
        lock_hash = _sha256(assignment.path / "uv.lock")
        wheelhouse, cache_manifest_hash = self._wheelhouse(
            assignment.path, operation=operation
        )
        source_date_epoch = self._git_epoch(assignment.path, source_commit)
        release_root = self.root / ".codex-os" / "artifacts" / run_id / "release"
        candidate_root = release_root / "candidate"
        operation_token = hashlib.sha256(operation.operation_id.encode()).hexdigest()[:12]
        staging = release_root / f".staging-{operation_token}-{operation.attempt_count}"
        stale_staging = tuple(release_root.glob(f".staging-{operation_token}-*"))
        if stale_staging:
            raise WorkflowError(
                "OPERATION_RECONCILE_REQUIRED",
                "release staging residue exists; inspect and retain it before retry",
                40,
            )
        if candidate_root.exists():
            if operation.attempt_count <= 1:
                raise WorkflowError(
                    "OPERATION_RECONCILE_REQUIRED",
                    "unindexed candidate exists on the first attempt",
                    40,
                )
            recovered = self._recover_unindexed_candidate(
                run_id=run_id,
                task_id=result.active_task.id,
                worktree_id=assignment.id,
                worktree=assignment.path,
                source_commit=source_commit,
                lock_hash=lock_hash,
                artifact_root=candidate_root,
            )
            return recovered
        staging.mkdir(parents=True)
        source_plugin_version = self._stage_plugin_source(
            assignment.path,
            source_commit=source_commit,
            staging=staging,
        )
        self._build(
            result.active_task.id,
            assignment.to_spec(),
            staging,
            wheelhouse=wheelhouse,
            source_date_epoch=source_date_epoch,
        )
        plugin_archive = staging / f"ai-engineering-os-plugin-{self.VERSION}.zip"
        try:
            plugin_evidence = validate_candidate_plugin_package(
                {
                    "source_plugin_manifest_version": source_plugin_version,
                    "packaged_plugin_version": RUNTIME_VERSIONS.plugin,
                    "packaged_plugin_manifest_hash": self._plugin_manifest_hash(
                        plugin_archive
                    ),
                },
                plugin_archive,
                expected_version=RUNTIME_VERSIONS.plugin,
            )
        except PluginPackageError as exc:
            raise WorkflowError("RELEASE_INCOMPLETE", str(exc), 40) from exc
        plugin_metadata = PluginPackageMetadata(
            source_manifest_version=source_plugin_version,
            packaged_version=plugin_evidence.version,
            packaged_manifest_hash=plugin_evidence.manifest_hash,
        )
        built = {
            path.name: _sha256(path)
            for path in sorted(staging.iterdir())
            if path.is_file()
            and (path.suffix in {".whl", ".zip"} or path.name.endswith(".tar.gz"))
        }
        if (
            not any(name.endswith(".whl") for name in built)
            or not any(name.endswith(".tar.gz") for name in built)
            or not any(name.endswith("plugin-0.2.0.zip") for name in built)
        ):
            raise WorkflowError(
                "RELEASE_INCOMPLETE",
                "sandbox build must produce wheel, sdist, and plugin package",
                40,
            )

        sbom = self._sbom(assignment.path, source_commit, built)
        sbom_relative = f".codex-os/artifacts/{run_id}/release/candidate/sbom.cdx.json"
        sbom_content = json.dumps(sbom, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        (staging / "sbom.cdx.json").write_text(sbom_content, encoding="utf-8", newline="\n")
        sbom_hash = hashlib.sha256(sbom_content.encode()).hexdigest()

        checksum_lines = [f"{digest}  {name}" for name, digest in sorted(built.items())]
        checksum_lines.append(f"{sbom_hash}  sbom.cdx.json")
        checksums_content = "\n".join(checksum_lines) + "\n"
        checksums_relative = (
            f".codex-os/artifacts/{run_id}/release/candidate/checksums.sha256"
        )
        (staging / "checksums.sha256").write_text(
            checksums_content, encoding="utf-8", newline="\n"
        )
        checksums_hash = hashlib.sha256(checksums_content.encode()).hexdigest()

        if _sha256(staging / "sbom.cdx.json") != sbom_hash or _sha256(
            staging / "checksums.sha256"
        ) != checksums_hash:
            raise WorkflowError("RELEASE_INCOMPLETE", "staging hash verification failed", 40)
        release_root.mkdir(parents=True, exist_ok=True)
        staging.rename(candidate_root)

        rollback_relative = "release/rollback.md"
        rollback_content = (
            f"# AI Engineering OS {self.VERSION} Rollback\n\n"
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
            "schema_version": RUNTIME_VERSIONS.document_schema,
            "requirement_baseline": self.REQUIREMENT_BASELINE,
            "software_version": self.VERSION,
            "plugin_version": RUNTIME_VERSIONS.plugin,
            "source_plugin_manifest_version": plugin_metadata.source_manifest_version,
            "packaged_plugin_version": plugin_metadata.packaged_version,
            "packaged_plugin_manifest_hash": plugin_metadata.packaged_manifest_hash,
            "plugin_api_version": self.PLUGIN_API,
            "config_schema_version": self.CONFIG_SCHEMA,
            "document_schema_version": RUNTIME_VERSIONS.document_schema,
            "profile_schema_version": RUNTIME_VERSIONS.profile_schema,
            "sqlite_schema_version": self.SQLITE_SCHEMA,
            "execution_image": RUNTIME_VERSIONS.execution_image,
            "git_tag": RUNTIME_VERSIONS.git_tag,
            "project_id": result.run.project_id,
            "run_id": run_id,
            "release_task_id": result.active_task.id,
            "integration_source_commit": source_commit,
            "candidate_commit": None,
            "config_hash": result.run.config_hash,
            "dependency_lock_hash": lock_hash,
            "verification_cache_manifest_hash": cache_manifest_hash,
            "source_date_epoch": source_date_epoch,
            "registry_index_digest": RUNTIME_VERSIONS.execution_image.rsplit("@sha256:", 1)[1],
            "platform_digest": None,
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
            candidate_commit=None,
            release_worktree=assignment.path.as_posix(),
            manifest_path=manifest_relative,
            manifest_hash=manifest_hash,
            artifact_root=candidate_root.as_posix(),
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

    def _recover_unindexed_candidate(
        self,
        *,
        run_id: str,
        task_id: str,
        worktree_id: str,
        worktree: Path,
        source_commit: str,
        lock_hash: str,
        artifact_root: Path,
    ) -> ReleaseCandidate:
        manifest_path = worktree / "release" / "manifest.json"
        rollback_path = worktree / "release" / "rollback.md"
        sbom_path = artifact_root / "sbom.cdx.json"
        checksums_path = artifact_root / "checksums.sha256"
        required = (manifest_path, rollback_path, sbom_path, checksums_path)
        if not all(path.is_file() for path in required):
            raise WorkflowError(
                "OPERATION_RECONCILE_REQUIRED",
                "unindexed candidate is incomplete and cannot be trusted",
                40,
            )
        try:
            manifest_value: object = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorkflowError(
                "OPERATION_RECONCILE_REQUIRED", "candidate manifest cannot be reconciled", 40
            ) from exc
        if not isinstance(manifest_value, dict):
            raise WorkflowError(
                "OPERATION_RECONCILE_REQUIRED", "candidate manifest is not an object", 40
            )
        manifest = cast(dict[str, object], manifest_value)
        artifacts_value = manifest.get("artifacts")
        if (
            manifest.get("run_id") != run_id
            or manifest.get("release_task_id") != task_id
            or manifest.get("integration_source_commit") != source_commit
            or manifest.get("dependency_lock_hash") != lock_hash
            or not isinstance(artifacts_value, dict)
        ):
            raise WorkflowError(
                "OPERATION_RECONCILE_REQUIRED", "candidate manifest binding drifted", 40
            )
        artifacts: dict[str, str] = {}
        for raw_name, raw_hash in cast(dict[object, object], artifacts_value).items():
            if (
                not isinstance(raw_name, str)
                or raw_name != Path(raw_name).name
                or not isinstance(raw_hash, str)
            ):
                raise WorkflowError(
                    "OPERATION_RECONCILE_REQUIRED", "candidate artifact entry is invalid", 40
                )
            path = artifact_root / raw_name
            if not path.is_file() or _sha256(path) != raw_hash:
                raise WorkflowError(
                    "OPERATION_RECONCILE_REQUIRED",
                    f"candidate artifact cannot be reconciled: {raw_name}",
                    40,
                )
            artifacts[raw_name] = raw_hash
        candidate = ReleaseCandidate(
            id=f"RC-{run_id}",
            version=self.VERSION,
            source_commit=source_commit,
            candidate_commit=None,
            release_worktree=worktree.as_posix(),
            manifest_path="release/manifest.json",
            manifest_hash=_sha256(manifest_path),
            artifact_root=artifact_root.as_posix(),
            artifacts=artifacts,
            sbom_path=sbom_path.relative_to(self.root).as_posix(),
            sbom_hash=_sha256(sbom_path),
            checksums_path=checksums_path.relative_to(self.root).as_posix(),
            checksums_hash=_sha256(checksums_path),
            rollback_path="release/rollback.md",
            rollback_hash=_sha256(rollback_path),
            created=False,
        )
        self._validate_existing(candidate)
        self._persist(run_id, task_id, worktree_id, lock_hash, candidate)
        return candidate

    def bind_candidate_commit(self, run_id: str, task_id: str, commit: str) -> None:
        """Bind the immutable commit that first contains the candidate manifest."""

        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT id, candidate_commit, candidate_manifest_path, "
                "candidate_manifest_hash, state_version FROM release_records "
                "WHERE run_id = ? AND task_id = ? AND status = 'candidate'",
                (run_id, task_id),
            ).fetchone()
        if row is None:
            raise WorkflowError(
                "RELEASE_INCOMPLETE", "release task has no candidate record", 40
            )
        existing = str(row["candidate_commit"] or "")
        if existing:
            if existing != commit:
                raise WorkflowError(
                    "RELEASE_SOURCE_CHANGED",
                    "candidate record is already bound to a different Commit",
                    40,
                )
            return
        manifest_path = str(row["candidate_manifest_path"])
        manifest = subprocess.run(
            ["git", "-C", str(self.root), "show", f"{commit}:{manifest_path}"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if manifest.returncode != 0 or hashlib.sha256(manifest.stdout).hexdigest() != str(
            row["candidate_manifest_hash"]
        ):
            raise WorkflowError(
                "GIT_EVIDENCE_INVALID",
                "candidate Commit does not contain the persisted manifest",
                40,
            )
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                changed = connection.execute(
                    "UPDATE release_records SET candidate_commit = ?, "
                    "state_version = state_version + 1, updated_at = ? "
                    "WHERE id = ? AND state_version = ? AND candidate_commit IS NULL",
                    (commit, _utc_now(), str(row["id"]), int(row["state_version"])),
                ).rowcount
                if changed != 1:
                    raise WorkflowError(
                        "STATE_VERSION_CONFLICT", "candidate record changed during binding", 40
                    )
                connection.commit()
            except (sqlite3.Error, WorkflowError):
                connection.rollback()
                raise

    def _build(
        self,
        task_id: str,
        worktree: object,
        root: Path,
        *,
        wheelhouse: Path | None,
        source_date_epoch: int,
    ) -> None:
        from codex_ai_os.adapters.worktree import WorktreeSpec

        if not isinstance(worktree, WorktreeSpec):
            raise TypeError("worktree must be a WorktreeSpec")
        request = SandboxRequest(
            execution_id=new_id("EXEC-RELEASE"),
            task_id=task_id,
            worktree=worktree,
            command=(
                "python",
                "-c",
                _RELEASE_BUILD_RUNNER,
                str(source_date_epoch),
                RUNTIME_VERSIONS.plugin,
            ),
            risk_level=RiskLevel.HIGH,
            mounts=(
                (SandboxMount(MountKind.ARTIFACTS, root),)
                if wheelhouse is None
                else (
                    SandboxMount(MountKind.ARTIFACTS, root),
                    SandboxMount(MountKind.CACHE, wheelhouse, read_only=True),
                )
            ),
        )
        try:
            self.execution.execute(request)
        except ExecutionServiceError as exc:
            raise WorkflowError(exc.code, str(exc), 50) from exc

    def _stage_plugin_source(
        self,
        worktree: Path,
        *,
        source_commit: str,
        staging: Path,
    ) -> str:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(worktree),
                "archive",
                "--format=zip",
                source_commit,
                "plugins/ai-engineering-os",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if completed.returncode != 0:
            raise WorkflowError(
                "GIT_EVIDENCE_INVALID",
                "cannot extract Plugin source from the authorized Commit",
                40,
            )
        try:
            with zipfile.ZipFile(io.BytesIO(completed.stdout)) as bundle:
                names: set[str] = set()
                manifest_bytes: bytes | None = None
                for info in bundle.infolist():
                    if info.is_dir():
                        continue
                    member = PurePosixPath(info.filename)
                    prefix = PurePosixPath("plugins/ai-engineering-os")
                    try:
                        relative = member.relative_to(prefix)
                    except ValueError as exc:
                        raise WorkflowError(
                            "GIT_EVIDENCE_INVALID",
                            "Plugin archive entry escaped its committed source root",
                            40,
                        ) from exc
                    if not relative.parts or ".." in relative.parts:
                        raise WorkflowError(
                            "GIT_EVIDENCE_INVALID",
                            "Plugin archive entry is invalid",
                            40,
                        )
                    file_type = (info.external_attr >> 16) & 0o170000
                    if file_type not in {0, 0o100000}:
                        raise WorkflowError(
                            "GIT_EVIDENCE_INVALID",
                            "Plugin archive contains a non-regular file",
                            40,
                        )
                    relative_name = relative.as_posix()
                    if relative_name in names:
                        raise WorkflowError(
                            "GIT_EVIDENCE_INVALID", "Plugin archive contains duplicates", 40
                        )
                    names.add(relative_name)
                    if relative_name == ".codex-plugin/plugin.json":
                        manifest_bytes = bundle.read(info)
        except zipfile.BadZipFile as exc:
            raise WorkflowError(
                "GIT_EVIDENCE_INVALID", "committed Plugin archive is invalid", 40
            ) from exc
        if not names or manifest_bytes is None:
            raise WorkflowError("RELEASE_INCOMPLETE", "Plugin source is empty", 40)
        try:
            value: object = json.loads(manifest_bytes.decode("utf-8", errors="strict"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise WorkflowError(
                "RELEASE_INCOMPLETE", "Plugin source manifest is invalid", 40
            ) from exc
        if not isinstance(value, dict):
            raise WorkflowError(
                "RELEASE_INCOMPLETE", "Plugin source manifest is not an object", 40
            )
        manifest = cast(dict[str, object], value)
        source_version = manifest.get("version")
        if manifest.get("name") != "ai-engineering-os" or not isinstance(
            source_version, str
        ):
            raise WorkflowError(
                "RELEASE_INCOMPLETE", "Plugin source identity is invalid", 40
            )
        if source_version.partition("+")[0] != RUNTIME_VERSIONS.plugin:
            raise WorkflowError(
                "VERSION_MISMATCH",
                "Plugin source base version does not match RuntimeVersions.plugin",
                40,
            )
        for reference in (manifest.get("skills"), manifest.get("mcpServers")):
            if not isinstance(reference, str):
                raise WorkflowError(
                    "RELEASE_INCOMPLETE",
                    "staged Plugin manifest references a missing companion asset",
                    40,
                )
            reference_name = PurePosixPath(reference.removeprefix("./")).as_posix()
            if reference.endswith("/"):
                prefix_name = f"{reference_name.rstrip('/')}/"
                present = any(name.startswith(prefix_name) for name in names)
            else:
                present = reference_name in names
            if not present:
                raise WorkflowError(
                    "RELEASE_INCOMPLETE",
                    "staged Plugin manifest references a missing companion asset",
                    40,
                )
        (staging / ".plugin-source.zip").write_bytes(completed.stdout)
        return source_version

    @staticmethod
    def _plugin_manifest_hash(archive: Path) -> str:
        try:
            with zipfile.ZipFile(archive) as bundle:
                content = bundle.read("ai-engineering-os/.codex-plugin/plugin.json")
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            raise PluginPackageError("packaged Plugin manifest is missing") from exc
        return hashlib.sha256(content).hexdigest()

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
        serial_input = json.dumps(
            {
                "project_id": self.config.project_id,
                "version": self.VERSION,
                "source_commit": source_commit,
                "artifacts": artifacts,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, serial_input)}",
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
            candidate_commit=(
                str(row["candidate_commit"])
                if row["candidate_commit"] is not None
                else None
            ),
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

    def _validate_existing(self, candidate: ReleaseCandidate) -> None:
        worktree = Path(candidate.release_worktree)
        manifest_path = worktree / candidate.manifest_path
        if not manifest_path.is_file() or _sha256(manifest_path) != candidate.manifest_hash:
            raise WorkflowError(
                "RELEASE_SOURCE_CHANGED", "candidate manifest hash drifted", 40
            )
        try:
            manifest_value: object = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorkflowError(
                "RELEASE_SOURCE_CHANGED", "candidate manifest is invalid", 40
            ) from exc
        if not isinstance(manifest_value, dict):
            raise WorkflowError(
                "RELEASE_SOURCE_CHANGED", "candidate manifest is not an object", 40
            )
        manifest = cast(dict[str, object], manifest_value)
        artifact_root = Path(candidate.artifact_root)
        for name, expected in candidate.artifacts.items():
            path = artifact_root / name
            if not path.is_file() or _sha256(path) != expected:
                raise WorkflowError(
                    "RELEASE_SOURCE_CHANGED", f"candidate artifact hash drifted: {name}", 40
                )
        plugin_archives = [
            artifact_root / name
            for name in candidate.artifacts
            if name == f"ai-engineering-os-plugin-{RUNTIME_VERSIONS.plugin}.zip"
        ]
        if len(plugin_archives) != 1:
            raise WorkflowError(
                "RELEASE_INCOMPLETE", "candidate must contain one versioned Plugin archive", 40
            )
        try:
            validate_candidate_plugin_package(
                manifest,
                plugin_archives[0],
                expected_version=RUNTIME_VERSIONS.plugin,
            )
        except PluginPackageError as exc:
            raise WorkflowError("RELEASE_SOURCE_CHANGED", str(exc), 40) from exc
        for path_value, expected in (
            (candidate.sbom_path, candidate.sbom_hash),
            (candidate.checksums_path, candidate.checksums_hash),
            (candidate.rollback_path, candidate.rollback_hash),
        ):
            path = self.root / path_value
            if not path.is_file():
                path = worktree / path_value
            if not path.is_file() or _sha256(path) != expected:
                raise WorkflowError(
                    "RELEASE_SOURCE_CHANGED", f"candidate evidence hash drifted: {path_value}", 40
                )

    def _wheelhouse(
        self, worktree: Path, *, operation: HostOperation
    ) -> tuple[Path | None, str | None]:
        if (
            not self.require_offline_cache
            and operation.request.get("verification_cache") is None
            and self.config.git_push_policy.value == "fixture_local_only"
        ):
            return None, None
        lock_path = worktree / "uv.lock"
        lock_hash = _sha256(lock_path)
        wheelhouse = self.root / ".codex-os" / "cache" / "verification" / lock_hash
        binding = operation.request.get("verification_cache")
        if not isinstance(binding, dict):
            raise WorkflowError(
                "SANDBOX_DEPENDENCIES_UNAVAILABLE",
                "release_prepare has no approved verification cache binding",
                40,
            )
        try:
            manifest = validate_verification_cache(wheelhouse, lock_path=lock_path)
        except (
            VerificationCachePrepareError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            raise WorkflowError(
                getattr(exc, "code", "DEPENDENCY_UNVERIFIED"), str(exc), 40
            ) from exc
        manifest_path = wheelhouse / "verification-cache-manifest.json"
        manifest_hash = _sha256(manifest_path)
        binding_value = cast(dict[object, object], binding)
        if (
            binding_value.get("uv_lock_hash") != lock_hash
            or binding_value.get("manifest_hash") != manifest_hash
            or binding_value.get("operation_id") != manifest.get("operation_id")
            or binding_value.get("operation_request_hash")
            != manifest.get("operation_request_hash")
        ):
            raise WorkflowError(
                "DEPENDENCY_UNVERIFIED", "verification cache binding drifted", 40
            )
        return wheelhouse, manifest_hash

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
                        status, created_at, updated_at, plugin_version,
                        document_schema_version, profile_schema_version, execution_image
                    ) SELECT ?, project_id, ?, ?, ?, ?, ?, ?, ?, config_hash, ?,
                             'candidate', ?, ?, ?, ?, ?, ?
                      FROM workflow_runs WHERE id = ?
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
                        RUNTIME_VERSIONS.plugin,
                        RUNTIME_VERSIONS.document_schema,
                        RUNTIME_VERSIONS.profile_schema,
                        RUNTIME_VERSIONS.execution_image,
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
                        rollback_path, rollback_hash, source_commit,
                        integration_source_commit, candidate_commit,
                        candidate_manifest_path, candidate_manifest_hash,
                        registry_index_digest, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?, ?, ?)
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
                        candidate.source_commit,
                        candidate.candidate_commit,
                        candidate.manifest_path,
                        candidate.manifest_hash,
                        RUNTIME_VERSIONS.execution_image.rsplit("@sha256:", 1)[1],
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

    @staticmethod
    def _git_epoch(worktree: Path, commit: str) -> int:
        completed = subprocess.run(
            ["git", "-C", str(worktree), "show", "-s", "--format=%ct", commit],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if completed.returncode != 0:
            raise WorkflowError(
                "GIT_EVIDENCE_INVALID", "cannot resolve reproducible build time", 40
            )
        try:
            value = int(completed.stdout.decode("ascii", errors="strict").strip())
        except (UnicodeError, ValueError) as exc:
            raise WorkflowError(
                "GIT_EVIDENCE_INVALID", "invalid reproducible build time", 40
            ) from exc
        if value < 315532800:
            raise WorkflowError(
                "GIT_EVIDENCE_INVALID", "source Commit predates supported ZIP timestamps", 40
            )
        return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
