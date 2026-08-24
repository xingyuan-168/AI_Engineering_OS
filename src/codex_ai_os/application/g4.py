"""GitHub PR verification and explicitly authorized 0.2.0 publication."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urlsplit

from codex_ai_os.domain.governance import G4ApprovalInput
from codex_ai_os.domain.workflow import WorkflowRun
from codex_ai_os.infrastructure.config import load_project_config
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.documents import DocumentManager

CommandRunner = Callable[[list[str], Path, float], subprocess.CompletedProcess[bytes]]


class ReleaseGovernanceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class G4PublicationResult:
    pr_number: int
    pr_url: str
    pr_head_commit: str
    merge_commit: str
    tag: str
    github_release_id: str
    github_release_url: str
    final_manifest_path: str
    final_manifest_hash: str


class G4Publisher(Protocol):
    def authorize_and_publish(
        self, run: WorkflowRun, approval: G4ApprovalInput
    ) -> G4PublicationResult: ...


class GitHubReleaseGovernanceService:
    """Fail closed unless the merged GitHub PR and publication are both verified."""

    def __init__(self, project_root: Path, *, runner: CommandRunner | None = None) -> None:
        self.root = project_root.resolve()
        self.config = load_project_config(self.root)
        self.database = Database(self.root / ".codex-os" / "state" / "state.db")
        self.database.migrate()
        self.runner = runner or _run_command

    def authorize_and_publish(
        self, run: WorkflowRun, approval: G4ApprovalInput
    ) -> G4PublicationResult:
        if approval.version != "0.2.0":
            raise ReleaseGovernanceError("VERSION_MISMATCH", "G4 version must be 0.2.0")
        if run.integration_branch is None or run.integration_head is None:
            raise ReleaseGovernanceError(
                "RELEASE_INCOMPLETE", "workflow has no verified integration branch and head"
            )
        if urlsplit(approval.pr_url).hostname not in self.config.github_hosts:
            raise ReleaseGovernanceError(
                "GITHUB_PR_INVALID", "PR URL must use an allowed GitHub host"
            )

        release = self._release_record(run.id)
        self._command("git", "fetch", "--prune", "origin", run.target_branch)
        pr = self._json_command(
            "gh",
            "pr",
            "view",
            str(approval.pr_number),
            "--json",
            "number,url,state,headRefName,headRefOid,baseRefName,mergeCommit,mergedAt",
        )
        merge = pr.get("mergeCommit")
        actual_merge = (
            cast(dict[str, object], merge).get("oid") if isinstance(merge, dict) else None
        )
        expected = {
            "number": approval.pr_number,
            "url": approval.pr_url,
            "state": "MERGED",
            "headRefName": run.integration_branch,
            "headRefOid": run.integration_head,
            "baseRefName": run.target_branch,
        }
        mismatches = [name for name, value in expected.items() if pr.get(name) != value]
        if actual_merge != approval.merge_commit.casefold():
            mismatches.append("mergeCommit")
        if not pr.get("mergedAt"):
            mismatches.append("mergedAt")
        if mismatches:
            raise ReleaseGovernanceError(
                "GITHUB_PR_INVALID",
                f"GitHub PR evidence does not match the workflow: {sorted(set(mismatches))}",
            )

        self._require_ancestor(str(release["source_commit"]), run.integration_head)
        self._require_ancestor(run.integration_head, approval.merge_commit)
        self._require_ancestor(approval.merge_commit, f"origin/{run.target_branch}")

        tag = f"v{approval.version}"
        self._ensure_tag(tag, approval.merge_commit, approval.release_authority.authorized_by)
        release_data = self._ensure_github_release(tag, Path(str(release["artifact_root"])))
        release_id = str(release_data.get("id") or "")
        release_url = str(release_data.get("url") or "")
        if not release_id or not release_url or release_data.get("isDraft") is True:
            self._mark_blocked(run.id, "GitHub Release evidence is incomplete")
            raise ReleaseGovernanceError(
                "GITHUB_RELEASE_BLOCKED", "GitHub Release was not published successfully"
            )

        final_path, final_hash = self._write_final_manifest(
            run=run,
            approval=approval,
            release=dict(release),
            tag=tag,
            release_id=release_id,
            release_url=release_url,
        )
        now = _utc_now()
        authority = {
            **approval.release_authority.model_dump(mode="json"),
            "final_manifest_path": final_path,
            "final_manifest_hash": final_hash,
            "github_release_url": release_url,
        }
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE release_records
                SET status = 'published', pr_number = ?, pr_url_hash = ?, pr_head = ?,
                    pr_base = ?, pr_head_commit = ?, merge_commit = ?, tag = ?,
                    github_release_id = ?, authorization_json = ?,
                    state_version = state_version + 1, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    approval.pr_number,
                    hashlib.sha256(approval.pr_url.encode()).hexdigest(),
                    run.integration_branch,
                    run.target_branch,
                    run.integration_head,
                    approval.merge_commit.casefold(),
                    tag,
                    release_id,
                    json.dumps(authority, sort_keys=True, separators=(",", ":")),
                    now,
                    run.id,
                ),
            )
            connection.execute(
                """
                UPDATE version_records SET status = 'released', source_commit = ?, updated_at = ?
                WHERE id = ?
                """,
                (approval.merge_commit.casefold(), now, str(release["version_record_id"])),
            )
            connection.commit()
        return G4PublicationResult(
            pr_number=approval.pr_number,
            pr_url=approval.pr_url,
            pr_head_commit=run.integration_head,
            merge_commit=approval.merge_commit.casefold(),
            tag=tag,
            github_release_id=release_id,
            github_release_url=release_url,
            final_manifest_path=final_path,
            final_manifest_hash=final_hash,
        )

    def _release_record(self, run_id: str) -> sqlite3.Row:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM release_records
                WHERE run_id = ? AND status IN ('candidate', 'g4_ready', 'authorized', 'blocked')
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise ReleaseGovernanceError(
                "RELEASE_INCOMPLETE", "G4 requires a persisted Release Candidate"
            )
        return row

    def _require_ancestor(self, ancestor: str, descendant: str) -> None:
        result = self._raw("git", "merge-base", "--is-ancestor", ancestor, descendant)
        if result.returncode != 0:
            raise ReleaseGovernanceError(
                "GIT_EVIDENCE_INVALID", f"{ancestor} is not an ancestor of {descendant}"
            )

    def _ensure_tag(self, tag: str, commit: str, authorized_by: str) -> None:
        current = self._raw("git", "rev-parse", f"refs/tags/{tag}^{{}}")
        if current.returncode == 0:
            if _stdout(current).casefold() != commit.casefold():
                raise ReleaseGovernanceError(
                    "TAG_CONFLICT", f"existing {tag} points to a different Commit"
                )
        else:
            self._command(
                "git",
                "tag",
                "-a",
                tag,
                commit,
                "-m",
                f"AI Engineering OS {tag} authorized by {authorized_by}",
            )
        remote = self._raw("git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}*")
        peeled = [
            line.split()[0]
            for line in _stdout(remote).splitlines()
            if line.endswith(f"refs/tags/{tag}^{{}}")
        ]
        if peeled and peeled[0].casefold() != commit.casefold():
            raise ReleaseGovernanceError(
                "TAG_CONFLICT", f"remote {tag} points to a different Commit"
            )
        if not _stdout(remote):
            self._command("git", "push", "origin", f"refs/tags/{tag}")

    def _ensure_github_release(self, tag: str, artifact_root: Path) -> dict[str, object]:
        viewed = self._raw("gh", "release", "view", tag, "--json", "id,url,isDraft,tagName")
        if viewed.returncode == 0:
            return _decode_json(viewed)
        assets = [str(path) for path in sorted(artifact_root.iterdir()) if path.is_file()]
        self._command(
            "gh",
            "release",
            "create",
            tag,
            "--verify-tag",
            "--title",
            f"AI Engineering OS {tag}",
            "--notes",
            "Governed release approved through G4.",
            *assets,
        )
        return self._json_command(
            "gh", "release", "view", tag, "--json", "id,url,isDraft,tagName"
        )

    def _write_final_manifest(
        self,
        *,
        run: WorkflowRun,
        approval: G4ApprovalInput,
        release: dict[str, object],
        tag: str,
        release_id: str,
        release_url: str,
    ) -> tuple[str, str]:
        relative = f".codex-os/artifacts/{run.id}/release-manifest.json"
        payload = {
            "schema_version": "1.1",
            "requirement_baseline": "REQ-1.6.2",
            "software_version": approval.version,
            "plugin_api_version": "1.1",
            "config_schema_version": "1.1",
            "sqlite_schema_version": "0006",
            "source_commit": release["source_commit"],
            "integration_head": run.integration_head,
            "pull_request": {"number": approval.pr_number, "url": approval.pr_url},
            "merge_commit": approval.merge_commit.casefold(),
            "tag": tag,
            "github_release": {"id": release_id, "url": release_url},
            "config_hash": run.config_hash,
            "manifest_hash": release["manifest_hash"],
            "sbom_hash": release["sbom_hash"],
            "checksums_hash": release["checksums_hash"],
            "rollback_hash": release["rollback_hash"],
            "authorization": approval.release_authority.model_dump(mode="json"),
        }
        content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        target = self.root / relative
        if target.is_file() and target.read_text(encoding="utf-8") != content:
            raise ReleaseGovernanceError(
                "RELEASE_SOURCE_CHANGED", "final Release Manifest already differs"
            )
        if not target.is_file():
            DocumentManager(self.root).write_atomic(relative, content, overwrite=False)
        return relative, hashlib.sha256(content.encode()).hexdigest()

    def _mark_blocked(self, run_id: str, reason: str) -> None:
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE release_records SET status = 'blocked', authorization_json = ?, "
                "updated_at = ? WHERE run_id = ?",
                (json.dumps({"blocker": reason}), _utc_now(), run_id),
            )
            connection.commit()

    def _json_command(self, *arguments: str) -> dict[str, object]:
        return _decode_json(self._command(*arguments))

    def _command(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        result = self._raw(*arguments)
        if result.returncode != 0:
            raise ReleaseGovernanceError(
                "GITHUB_RELEASE_BLOCKED",
                f"command failed: {' '.join(arguments[:3])}: {_stderr(result)}",
            )
        return result

    def _raw(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return self.runner(list(arguments), self.root, 60.0)
        except (OSError, subprocess.SubprocessError) as exc:
            return subprocess.CompletedProcess(list(arguments), 1, b"", str(exc).encode())


def _run_command(
    command: list[str], cwd: Path, timeout: float
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _decode_json(result: subprocess.CompletedProcess[bytes]) -> dict[str, object]:
    try:
        value: object = json.loads(_stdout(result))
    except json.JSONDecodeError as exc:
        raise ReleaseGovernanceError("GITHUB_PR_INVALID", "GitHub returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ReleaseGovernanceError("GITHUB_PR_INVALID", "GitHub returned a non-object response")
    return cast(dict[str, object], value)


def _stdout(result: subprocess.CompletedProcess[bytes]) -> str:
    return result.stdout.decode("utf-8", errors="strict").strip()


def _stderr(result: subprocess.CompletedProcess[bytes]) -> str:
    return result.stderr.decode("utf-8", errors="replace").strip()[:1000]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
