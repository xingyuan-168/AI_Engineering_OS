"""Commit-bound structured evidence persistence and gate bundle validation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from codex_ai_os.domain.governance import (
    ArtifactEvidenceInput,
    CheckEvidenceInput,
    ReviewEvidenceInput,
)
from codex_ai_os.domain.ids import new_id
from codex_ai_os.domain.versions import RUNTIME_VERSIONS
from codex_ai_os.domain.workflow import Gate
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.documents import DOCUMENT_METADATA, UNAPPROVED_PLACEHOLDER


class EvidenceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class GateBundle:
    id: str
    run_id: str
    gate: Gate
    state_version: int
    source_commit: str
    status: str
    artifact_paths: tuple[str, ...]
    check_names: tuple[str, ...]
    review_types: tuple[str, ...]
    missing: tuple[str, ...]
    bundle_hash: str


_REQUIRED_ARTIFACTS: dict[Gate, frozenset[str]] = {
    Gate.G0: frozenset({"docs/PROJECT_MASTER.md", "docs/SCOPE.md"}),
    Gate.G1: frozenset(
        {
            "docs/PRODUCT_REQUIREMENTS.md",
            "docs/USER_STORY.md",
            "docs/BUSINESS_RULES.md",
            "docs/SCOPE.md",
        }
    ),
    Gate.G2: frozenset(
        {
            "docs/OPEN_SOURCE_RESEARCH.md",
            "docs/TECH_STACK.md",
            "docs/ARCHITECTURE.md",
            "docs/API_SPEC.md",
            "docs/DATABASE.md",
            "docs/SECURITY.md",
        }
    ),
    Gate.G3: frozenset(),
    Gate.G4: frozenset(),
}
_REQUIRED_ARTIFACT_TYPES: dict[Gate, frozenset[str]] = {
    Gate.G0: frozenset(),
    Gate.G1: frozenset(),
    Gate.G2: frozenset(),
    Gate.G3: frozenset(),
    Gate.G4: frozenset(
        {"changelog", "release-manifest", "rollback", "sbom", "checksums", "memory"}
    ),
}
_REQUIRED_CHECKS: dict[Gate, frozenset[str]] = {
    Gate.G0: frozenset(),
    Gate.G1: frozenset(),
    Gate.G2: frozenset(),
    Gate.G3: frozenset(
        {
            "pytest",
            "ruff",
            "pyright",
            "secret-scan",
            "dependency-audit",
            "security-scan",
        }
    ),
    Gate.G4: frozenset({"release-manifest", "sbom", "checksums", "rollback"}),
}
_REQUIRED_REVIEWS: dict[Gate, frozenset[str]] = {
    Gate.G0: frozenset(),
    Gate.G1: frozenset(),
    Gate.G2: frozenset(),
    Gate.G3: frozenset({"code", "security"}),
    Gate.G4: frozenset({"release"}),
}


class EvidenceStore:
    def __init__(self, database: Database, project_root: Path) -> None:
        self.database = database
        self.root = project_root.resolve()

    def record_task_evidence(
        self,
        *,
        run_id: str,
        task_id: str,
        artifacts: tuple[ArtifactEvidenceInput, ...],
        checks: tuple[CheckEvidenceInput, ...],
    ) -> None:
        if not artifacts:
            raise EvidenceError(
                "EVIDENCE_INCOMPLETE", "repository task requires structured artifacts"
            )
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                task = connection.execute(
                    "SELECT run_id, worktree FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                if task is None or str(task["run_id"]) != run_id:
                    raise EvidenceError("EVIDENCE_STALE", "task/run evidence identity mismatch")
                worktree = Path(str(task["worktree"])).resolve() if task["worktree"] else self.root
                for artifact in artifacts:
                    self._verify_artifact(worktree, artifact)
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO artifact_evidence(
                            id, bundle_id, run_id, task_id, path, artifact_type,
                            content_hash, source_commit, status, created_at
                        ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_id("ARTEVIDENCE"),
                            run_id,
                            task_id,
                            artifact.path,
                            artifact.artifact_type,
                            artifact.sha256.casefold(),
                            artifact.source_commit.casefold(),
                            artifact.status.value,
                            _utc_now(),
                        ),
                    )
                for check in checks:
                    execution = connection.execute(
                        "SELECT task_id, exit_code, status FROM executions WHERE id = ?",
                        (check.execution_id,),
                    ).fetchone()
                    if (
                        execution is None
                        or str(execution["task_id"]) != task_id
                        or execution["exit_code"] != check.exit_code
                        or (check.status.value == "passed" and execution["status"] != "completed")
                    ):
                        raise EvidenceError(
                            "EVIDENCE_STALE", f"check execution is not verified: {check.name}"
                        )
                    self._verify_report(worktree, check.report_path, check.report_hash)
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO check_evidence(
                            id, bundle_id, run_id, task_id, check_name, command_hash,
                            execution_id, exit_code, report_path, report_hash,
                            source_commit, status, executed_at
                        ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_id("CHECKEVIDENCE"),
                            run_id,
                            task_id,
                            check.name,
                            check.command_hash.casefold(),
                            check.execution_id,
                            check.exit_code,
                            check.report_path,
                            check.report_hash.casefold(),
                            check.source_commit.casefold(),
                            check.status.value,
                            check.executed_at,
                        ),
                    )
                connection.commit()
            except (sqlite3.Error, EvidenceError):
                connection.rollback()
                raise

    def record_review(
        self,
        *,
        project_id: str,
        run_id: str,
        task_id: str | None,
        review: ReviewEvidenceInput,
    ) -> str:
        review_id = new_id("REVIEWEVIDENCE")
        with self.database.connection() as connection:
            worktree = self.root
            if task_id is not None:
                task = connection.execute(
                    "SELECT run_id, worktree FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                if task is None or str(task["run_id"]) != run_id:
                    raise EvidenceError("EVIDENCE_STALE", "review task/run identity mismatch")
                if task["worktree"]:
                    worktree = Path(str(task["worktree"])).resolve()
            self._verify_report(worktree, review.report_ref, review.report_hash)
            connection.execute(
                """
                INSERT INTO review_evidence(
                    id, bundle_id, project_id, run_id, task_id, review_type,
                    reviewer, reviewed_commit, decision, findings_json, risks_json,
                    report_ref, report_hash, created_at
                ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    project_id,
                    run_id,
                    task_id,
                    review.review_type,
                    review.reviewer,
                    review.reviewed_commit.casefold(),
                    review.decision.value,
                    _json(review.findings),
                    _json(review.risks),
                    review.report_ref,
                    review.report_hash.casefold(),
                    _utc_now(),
                ),
            )
            connection.commit()
        return review_id

    def audit_migrated_run(self, run_id: str) -> tuple[str, ...]:
        """Require every previously approved Gate to reference a complete 1.2 bundle."""
        self._promote_legacy_artifacts(run_id)
        self._rebuild_migrated_gate_bundles(run_id)
        with self.database.connection() as connection:
            approvals = connection.execute(
                """
                SELECT a.gate, a.evidence_bundle_id, a.evidence_bundle_hash,
                       b.status, b.bundle_hash
                FROM approvals a
                LEFT JOIN gate_evidence_bundles b ON b.id = a.evidence_bundle_id
                WHERE a.run_id = ? AND a.decision = 'approved'
                ORDER BY a.created_at, a.gate
                """,
                (run_id,),
            ).fetchall()
        missing: list[str] = []
        bundle_ids: list[str] = []
        for approval in approvals:
            bundle_id = approval["evidence_bundle_id"]
            expected_hash = approval["evidence_bundle_hash"]
            actual_hash = approval["bundle_hash"]
            if (
                bundle_id is None
                or expected_hash is None
                or approval["status"] != "complete"
                or actual_hash != expected_hash
            ):
                missing.append(str(approval["gate"]))
                continue
            bundle_ids.append(str(bundle_id))
        if missing:
            raise EvidenceError(
                "MIGRATION_REVALIDATION_REQUIRED",
                f"approved Gates require complete Plugin API {RUNTIME_VERSIONS.api} "
                "evidence bundles: "
                f"{sorted(set(missing))}",
            )
        return tuple(bundle_ids)

    def _promote_legacy_artifacts(self, run_id: str) -> None:
        """Re-verify legacy artifact rows before admitting them as 1.2 evidence."""
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT a.task_id, a.path, a.kind, a.content_hash, a.source_commit,
                       t.worktree
                FROM artifacts a
                JOIN tasks t ON t.id = a.task_id
                WHERE a.run_id = ? AND a.status = 'verified'
                  AND a.source_commit IS NOT NULL AND t.worktree IS NOT NULL
                ORDER BY a.created_at, a.path
                """,
                (run_id,),
            ).fetchall()
        verified: list[tuple[sqlite3.Row, ArtifactEvidenceInput]] = []
        for row in rows:
            artifact = ArtifactEvidenceInput(
                path=str(row["path"]),
                artifact_type=str(row["kind"]),
                sha256=str(row["content_hash"]),
                source_commit=str(row["source_commit"]),
                task_id=str(row["task_id"]),
            )
            self._verify_artifact(Path(str(row["worktree"])).resolve(), artifact)
            verified.append((row, artifact))
        if not verified:
            return
        with self.database.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                for row, artifact in verified:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO artifact_evidence(
                            id, bundle_id, run_id, task_id, path, artifact_type,
                            content_hash, source_commit, status, created_at
                        ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, 'verified', ?)
                        """,
                        (
                            new_id("ARTEVIDENCE"),
                            run_id,
                            str(row["task_id"]),
                            artifact.path,
                            artifact.artifact_type,
                            artifact.sha256.casefold(),
                            artifact.source_commit.casefold(),
                            _utc_now(),
                        ),
                    )
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
                raise

    def _rebuild_migrated_gate_bundles(self, run_id: str) -> None:
        with self.database.connection() as connection:
            approvals = connection.execute(
                """
                SELECT id, gate, state_version, created_at
                FROM approvals
                WHERE run_id = ? AND decision = 'approved'
                  AND (evidence_bundle_id IS NULL OR evidence_bundle_hash IS NULL)
                ORDER BY created_at, gate
                """,
                (run_id,),
            ).fetchall()
        for approval in approvals:
            with self.database.connection() as connection:
                source = connection.execute(
                    """
                    SELECT source_commit FROM artifacts
                    WHERE run_id = ? AND status = 'verified'
                      AND source_commit IS NOT NULL AND created_at <= ?
                    ORDER BY created_at DESC, source_commit DESC LIMIT 1
                    """,
                    (run_id, str(approval["created_at"])),
                ).fetchone()
            if source is None:
                continue
            bundle = self.build_gate_bundle(
                run_id=run_id,
                gate=Gate(str(approval["gate"])),
                state_version=int(approval["state_version"]),
                source_commit=str(source["source_commit"]),
            )
            if bundle.status != "complete" or bundle.missing:
                continue
            with self.database.connection() as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        """
                        UPDATE approvals
                        SET evidence_bundle_id = ?, evidence_bundle_hash = ?
                        WHERE id = ? AND evidence_bundle_id IS NULL
                          AND evidence_bundle_hash IS NULL
                        """,
                        (bundle.id, bundle.bundle_hash, str(approval["id"])),
                    )
                    connection.commit()
                except sqlite3.Error:
                    connection.rollback()
                    raise

    def build_gate_bundle(
        self,
        *,
        run_id: str,
        gate: Gate,
        state_version: int,
        source_commit: str,
    ) -> GateBundle:
        with self.database.connection() as connection:
            artifacts = connection.execute(
                """
                SELECT path, artifact_type, content_hash, source_commit, id
                FROM artifact_evidence
                WHERE run_id = ? AND status = 'verified'
                ORDER BY created_at DESC, path, content_hash
                """,
                (run_id,),
            ).fetchall()
            checks = connection.execute(
                """
                SELECT check_name, report_hash, source_commit, id FROM check_evidence
                WHERE run_id = ? AND status = 'passed' AND exit_code = 0
                ORDER BY executed_at DESC, check_name, report_hash
                """,
                (run_id,),
            ).fetchall()
            reviews = connection.execute(
                """
                SELECT review_type, report_hash, reviewed_commit, id FROM review_evidence
                WHERE run_id = ? AND decision = 'accepted'
                ORDER BY created_at DESC, review_type, report_hash
                """,
                (run_id,),
            ).fetchall()

            artifacts = self._latest_ancestor_rows(
                artifacts, source_commit, source_column="source_commit", key_column="path"
            )
            checks = self._latest_ancestor_rows(
                checks, source_commit, source_column="source_commit", key_column="check_name"
            )
            reviews = self._latest_ancestor_rows(
                reviews,
                source_commit,
                source_column="reviewed_commit",
                key_column="review_type",
            )

            artifact_paths = tuple(str(row["path"]) for row in artifacts)
            artifact_types = tuple(str(row["artifact_type"]) for row in artifacts)
            check_names = tuple(str(row["check_name"]) for row in checks)
            review_types = tuple(str(row["review_type"]) for row in reviews)
            document_findings = self._document_findings(run_id, gate, source_commit)
            missing = sorted(
                {f"artifact:{name}" for name in _REQUIRED_ARTIFACTS[gate] - set(artifact_paths)}
                | {
                    f"artifact_type:{name}"
                    for name in _REQUIRED_ARTIFACT_TYPES[gate] - set(artifact_types)
                }
                | {f"check:{name}" for name in _REQUIRED_CHECKS[gate] - set(check_names)}
                | {f"review:{name}" for name in _REQUIRED_REVIEWS[gate] - set(review_types)}
                | {f"document:{finding}" for finding in document_findings}
            )
            payload = {
                "run_id": run_id,
                "gate": gate.value,
                "state_version": state_version,
                "source_commit": source_commit.casefold(),
                "artifacts": [(str(row["path"]), str(row["content_hash"])) for row in artifacts],
                "checks": [(str(row["check_name"]), str(row["report_hash"])) for row in checks],
                "reviews": [
                    (str(row["review_type"]), str(row["report_hash"])) for row in reviews
                ],
                "missing": missing,
            }
            bundle_hash = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            bundle_id = new_id("GATEBUNDLE")
            status = "complete" if not missing else "building"
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE gate_evidence_bundles SET status = 'stale' "
                    "WHERE run_id = ? AND gate = ? AND status = 'complete'",
                    (run_id, gate.value),
                )
                connection.execute(
                    """
                    INSERT OR REPLACE INTO gate_evidence_bundles(
                        id, run_id, gate, state_version, source_commit, status,
                        required_artifacts_json, bundle_hash, created_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        bundle_id,
                        run_id,
                        gate.value,
                        state_version,
                        source_commit.casefold(),
                        status,
                        _json(sorted(_REQUIRED_ARTIFACTS[gate])),
                        bundle_hash,
                        _utc_now(),
                        _utc_now() if status == "complete" else None,
                    ),
                )
                if status == "complete":
                    artifact_ids = [str(row["id"]) for row in artifacts]
                    check_ids = [str(row["id"]) for row in checks]
                    review_ids = [str(row["id"]) for row in reviews]
                    for table, identifiers in (
                        ("artifact_evidence", artifact_ids),
                        ("check_evidence", check_ids),
                        ("review_evidence", review_ids),
                    ):
                        for identifier in identifiers:
                            connection.execute(
                                f"UPDATE {table} SET bundle_id = ? WHERE id = ?",
                                (bundle_id, identifier),
                            )
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
                raise
        return GateBundle(
            id=bundle_id,
            run_id=run_id,
            gate=gate,
            state_version=state_version,
            source_commit=source_commit.casefold(),
            status=status,
            artifact_paths=artifact_paths,
            check_names=check_names,
            review_types=review_types,
            missing=tuple(missing),
            bundle_hash=bundle_hash,
        )

    @staticmethod
    def require_complete(bundle: GateBundle) -> None:
        if bundle.status != "complete" or bundle.missing:
            raise EvidenceError(
                "EVIDENCE_INCOMPLETE",
                f"{bundle.gate.value} evidence is incomplete: {list(bundle.missing)}",
            )

    def _verify_artifact(self, worktree: Path, artifact: ArtifactEvidenceInput) -> None:
        if artifact.path.startswith(".codex-os/artifacts/"):
            self._verify_report(worktree, artifact.path, artifact.sha256)
            return
        normalized = Path(artifact.path.replace("\\", "/"))
        if normalized.is_absolute() or ".." in normalized.parts:
            raise EvidenceError("EVIDENCE_STALE", f"evidence path is unsafe: {artifact.path}")
        result = subprocess.run(
            [
                "git",
                "-C",
                str(worktree),
                "show",
                f"{artifact.source_commit}:{artifact.path}",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if result.returncode != 0:
            raise EvidenceError(
                "EVIDENCE_STALE", f"artifact is not in source Commit: {artifact.path}"
            )
        actual = hashlib.sha256(result.stdout).hexdigest()
        if actual.casefold() != artifact.sha256.casefold():
            raise EvidenceError("EVIDENCE_STALE", f"evidence hash mismatch: {artifact.path}")

    def _document_findings(
        self, run_id: str, gate: Gate, source_commit: str
    ) -> tuple[str, ...]:
        paths = tuple(
            path for path in _REQUIRED_ARTIFACTS[gate] if path.casefold().endswith(".md")
        )
        if not paths:
            return ()
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT worktree FROM tasks
                WHERE run_id = ? AND worktree IS NOT NULL AND (
                    head_commit = ? OR (
                        json_valid(output_ref)
                        AND json_extract(output_ref, '$.commit_sha') = ?
                    )
                )
                ORDER BY updated_at DESC LIMIT 1
                """,
                (run_id, source_commit.casefold(), source_commit.casefold()),
            ).fetchone()
        if row is None:
            return ("source Worktree is unavailable",)
        worktree = Path(str(row["worktree"])).resolve()
        findings: list[str] = []
        for relative in paths:
            path = (worktree / relative).resolve()
            if not path.is_relative_to(worktree) or not path.is_file():
                findings.append(f"{relative} is missing")
                continue
            text = path.read_text(encoding="utf-8")
            match = DOCUMENT_METADATA.search(text)
            if match is None:
                findings.append(f"{relative} has no governance metadata")
                continue
            try:
                raw: object = json.loads(match.group(1))
            except json.JSONDecodeError:
                findings.append(f"{relative} has invalid governance metadata")
                continue
            if not isinstance(raw, dict):
                findings.append(f"{relative} metadata is not an object")
                continue
            mapping = cast(dict[object, object], raw)
            metadata: dict[str, object] = {
                str(key): value for key, value in mapping.items()
            }
            if metadata.get("schema_version") != RUNTIME_VERSIONS.document_schema:
                findings.append(
                    f"{relative} schema_version is not "
                    f"{RUNTIME_VERSIONS.document_schema}"
                )
            if metadata.get("document_version") != RUNTIME_VERSIONS.software:
                findings.append(
                    f"{relative} document_version is not {RUNTIME_VERSIONS.software}"
                )
            if str(metadata.get("status", "")).casefold() not in {
                "accepted",
                "approved",
                "released",
                "review-ready",
            }:
                findings.append(f"{relative} status is not approval-ready")
            if not str(metadata.get("owner", "")).strip():
                findings.append(f"{relative} owner is missing")
            refs = metadata.get("requirement_refs")
            ref_values = cast(list[object], refs) if isinstance(refs, list) else []
            if not ref_values or not all(
                isinstance(item, str) and item for item in ref_values
            ):
                findings.append(f"{relative} requirement_refs are invalid")
            if UNAPPROVED_PLACEHOLDER.search(text):
                findings.append(f"{relative} contains an unapproved placeholder")
        return tuple(findings)

    def _latest_ancestor_rows(
        self,
        rows: list[sqlite3.Row],
        source_commit: str,
        *,
        source_column: str,
        key_column: str,
    ) -> list[sqlite3.Row]:
        selected: list[sqlite3.Row] = []
        keys: set[str] = set()
        for row in rows:
            key = str(row[key_column])
            if key in keys or not self._is_ancestor(str(row[source_column]), source_commit):
                continue
            keys.add(key)
            selected.append(row)
        return sorted(selected, key=lambda row: str(row[key_column]))

    def _is_ancestor(self, ancestor: str, descendant: str) -> bool:
        if ancestor.casefold() == descendant.casefold():
            return True
        result = subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=15,
        )
        return result.returncode == 0

    def _verify_report(self, worktree: Path, relative: str, expected_hash: str) -> None:
        base = self.root if relative.startswith(".codex-os/artifacts/") else worktree
        candidate = (base / relative).resolve()
        if not candidate.is_relative_to(base) or not candidate.is_file():
            raise EvidenceError("EVIDENCE_STALE", f"evidence path is missing or unsafe: {relative}")
        actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if actual.casefold() != expected_hash.casefold():
            raise EvidenceError("EVIDENCE_STALE", f"evidence hash mismatch: {relative}")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
