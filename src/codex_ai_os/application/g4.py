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

from codex_ai_os.application.plugin_packaging import (
    PluginPackageError,
    validate_candidate_plugin_package,
)
from codex_ai_os.domain.governance import G4ApprovalInput
from codex_ai_os.domain.versions import RUNTIME_VERSIONS
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
    external_reconciliation: dict[str, object]


class G4Publisher(Protocol):
    def verify_publication_intent(
        self, run: WorkflowRun, approval: G4ApprovalInput
    ) -> dict[str, object]: ...

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
        self.verify_publication_intent(run, approval)
        assert run.integration_head is not None
        release = dict(self._release_record(run.id))
        tag = f"v{approval.version}"
        self._ensure_tag(
            tag,
            approval.merge_commit,
            approval.release_authority.authorized_by,
            run_id=run.id,
            manifest_hash=str(release["candidate_manifest_hash"]),
        )
        release_data = self._ensure_draft_release(tag, approval.merge_commit)
        release_id = str(release_data.get("id") or "")
        release_url = str(release_data.get("url") or "")
        if not release_id or not release_url:
            self._mark_blocked(run.id, "GitHub Release evidence is incomplete")
            raise ReleaseGovernanceError(
                "GITHUB_RELEASE_BLOCKED", "GitHub draft Release evidence is incomplete"
            )

        base_assets = self._release_assets(release)
        base_reconciliation = self._reconcile_assets(
            tag,
            release_data,
            base_assets,
            allowed_extra={"release-manifest.json"},
        )
        final_path, final_hash = self._write_final_manifest(
            run=run,
            approval=approval,
            release=release,
            tag=tag,
            release_id=release_id,
            release_url=release_url,
            reconciled_assets=base_reconciliation,
        )
        final_target = (self.root / final_path).resolve(strict=True)
        all_assets = {**base_assets, "release-manifest.json": final_target}
        release_data = self._release_data(tag)
        all_reconciliation = self._reconcile_assets(tag, release_data, all_assets)
        published = self._publish_release(tag, release_data)
        release_id = str(published.get("id") or "")
        release_url = str(published.get("url") or "")
        if not release_id or not release_url or published.get("isDraft") is not False:
            raise ReleaseGovernanceError(
                "GITHUB_RELEASE_BLOCKED", "GitHub Release publication is not observable"
            )
        reconciliation: dict[str, object] = {
            "tag": {"name": tag, "commit": approval.merge_commit.casefold()},
            "release": {
                "id": release_id,
                "url": release_url,
                "is_draft": False,
                "tag_name": published.get("tagName"),
            },
            "assets": all_reconciliation,
        }
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
                    final_manifest_path = ?, final_manifest_hash = ?, reconciled_at = ?,
                    external_reconciliation_json = ?,
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
                    final_path,
                    final_hash,
                    now,
                    json.dumps(reconciliation, sort_keys=True, separators=(",", ":")),
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
            external_reconciliation=reconciliation,
        )

    def verify_publication_intent(
        self, run: WorkflowRun, approval: G4ApprovalInput
    ) -> dict[str, object]:
        if approval.version != RUNTIME_VERSIONS.software:
            raise ReleaseGovernanceError(
                "VERSION_MISMATCH",
                f"G4 version must be {RUNTIME_VERSIONS.software}",
            )
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
        return {
            "release_id": str(release["id"]),
            "approval": approval.model_dump(mode="json"),
            "integration_head": run.integration_head,
            "integration_branch": run.integration_branch,
            "target_branch": run.target_branch,
            "candidate_commit": release["candidate_commit"],
            "candidate_manifest_hash": release["candidate_manifest_hash"],
        }

    def _release_record(self, run_id: str) -> sqlite3.Row:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT r.*, w.path AS release_worktree_path
                FROM release_records r
                LEFT JOIN worktrees w ON w.id = r.release_worktree_id
                WHERE r.run_id = ? AND r.status IN (
                    'candidate', 'g4_ready', 'authorized', 'tagged', 'published', 'blocked'
                )
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

    def _ensure_tag(
        self,
        tag: str,
        commit: str,
        authorized_by: str,
        *,
        run_id: str = "unknown",
        manifest_hash: str = "unknown",
    ) -> None:
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
                (
                    f"AI Engineering OS {tag}\n\nRun: {run_id}\n"
                    f"Manifest: {manifest_hash}\nMerge Commit: {commit.casefold()}\n"
                    f"Authorized by: {authorized_by}"
                ),
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
        if _stdout(remote) and not peeled:
            raise ReleaseGovernanceError(
                "TAG_CONFLICT", f"remote {tag} is not an annotated tag"
            )
        if not _stdout(remote):
            self._command("git", "push", "origin", f"refs/tags/{tag}")

    def _ensure_draft_release(self, tag: str, merge_commit: str) -> dict[str, object]:
        viewed = self._release_view(tag)
        if viewed.returncode == 0:
            release = _decode_json(viewed)
            if release.get("tagName") != tag:
                raise ReleaseGovernanceError(
                    "GITHUB_RELEASE_BLOCKED", "GitHub Release tag does not match authorization"
                )
            return release
        if not _is_missing_release(viewed):
            raise ReleaseGovernanceError(
                "GITHUB_RELEASE_BLOCKED",
                f"cannot inspect GitHub Release before creation: {_stderr(viewed)}",
            )
        self._command(
            "gh",
            "release",
            "create",
            tag,
            "--verify-tag",
            "--draft",
            "--target",
            merge_commit.casefold(),
            "--title",
            f"AI Engineering OS {tag}",
            "--notes",
            "Governed release approved through G4.",
        )
        release = self._release_data(tag)
        if release.get("isDraft") is not True:
            raise ReleaseGovernanceError(
                "GITHUB_RELEASE_BLOCKED", "new GitHub Release was not created as a draft"
            )
        return release

    def _release_view(self, tag: str) -> subprocess.CompletedProcess[bytes]:
        return self._raw(
            "gh",
            "release",
            "view",
            tag,
            "--json",
            "id,url,isDraft,tagName,targetCommitish,assets",
        )

    def _release_data(self, tag: str) -> dict[str, object]:
        viewed = self._release_view(tag)
        if viewed.returncode != 0:
            raise ReleaseGovernanceError(
                "GITHUB_RELEASE_BLOCKED",
                f"cannot inspect GitHub Release: {_stderr(viewed)}",
            )
        return _decode_json(viewed)

    def _release_assets(self, release: dict[str, object]) -> dict[str, Path]:
        run_root = (
            self.root / ".codex-os" / "artifacts" / str(release["run_id"])
        ).resolve(strict=False)
        artifact_input = Path(str(release["artifact_root"]))
        if artifact_input.is_symlink():
            raise ReleaseGovernanceError(
                "PATH_ESCAPE", "Release artifact root cannot be a symbolic link"
            )
        artifact_root = artifact_input.resolve(strict=True)
        _require_within(artifact_root, run_root, "Release artifact root")
        assets: dict[str, Path] = {}
        for path in sorted(artifact_root.iterdir()):
            if path.is_symlink():
                raise ReleaseGovernanceError(
                    "PATH_ESCAPE", f"Release artifact cannot be a symbolic link: {path.name}"
                )
            if path.is_file():
                assets[path.name] = path.resolve(strict=True)
        reserved = {"candidate-manifest.json", "rollback.md", "release-manifest.json"}
        collision = reserved & set(assets)
        if collision:
            raise ReleaseGovernanceError(
                "RELEASE_INCOMPLETE",
                f"candidate assets use reserved publication names: {sorted(collision)}",
            )
        worktree = Path(str(release.get("release_worktree_path") or "")).resolve(
            strict=True
        )
        manifest_input = worktree / str(release["candidate_manifest_path"])
        rollback_input = worktree / str(release["rollback_path"])
        if manifest_input.is_symlink() or rollback_input.is_symlink():
            raise ReleaseGovernanceError(
                "PATH_ESCAPE", "Release evidence cannot be a symbolic link"
            )
        manifest = manifest_input.resolve(strict=True)
        rollback = rollback_input.resolve(strict=True)
        _require_within(manifest, worktree, "candidate Manifest")
        _require_within(rollback, worktree, "rollback document")
        publication_root = run_root / "release" / "publication"
        assets["candidate-manifest.json"] = _materialize_asset(
            manifest, publication_root / "candidate-manifest.json"
        )
        assets["rollback.md"] = _materialize_asset(
            rollback, publication_root / "rollback.md"
        )
        expected_hashes = {
            "candidate-manifest.json": str(release["candidate_manifest_hash"]),
            "rollback.md": str(release["rollback_hash"]),
            Path(str(release["sbom_path"])).name: str(release["sbom_hash"]),
            Path(str(release["checksums_path"])).name: str(release["checksums_hash"]),
        }
        for name, expected in expected_hashes.items():
            path = assets.get(name)
            if path is None or _sha256(path) != expected:
                raise ReleaseGovernanceError(
                    "RELEASE_SOURCE_CHANGED", f"Release asset hash drifted: {name}"
                )
        if not any(name.endswith(".whl") for name in assets):
            raise ReleaseGovernanceError("RELEASE_INCOMPLETE", "wheel asset is missing")
        if not any(name.endswith(".tar.gz") for name in assets):
            raise ReleaseGovernanceError("RELEASE_INCOMPLETE", "sdist asset is missing")
        plugin_name = f"ai-engineering-os-plugin-{RUNTIME_VERSIONS.plugin}.zip"
        if plugin_name not in assets:
            raise ReleaseGovernanceError("RELEASE_INCOMPLETE", "Plugin asset is missing")
        try:
            candidate_value: object = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ReleaseGovernanceError(
                "RELEASE_SOURCE_CHANGED", "candidate Manifest is invalid"
            ) from exc
        if not isinstance(candidate_value, dict):
            raise ReleaseGovernanceError(
                "RELEASE_SOURCE_CHANGED", "candidate Manifest is not an object"
            )
        plugin_archive = assets[plugin_name]
        try:
            validate_candidate_plugin_package(
                cast(dict[str, object], candidate_value),
                plugin_archive,
                expected_version=RUNTIME_VERSIONS.plugin,
            )
        except PluginPackageError as exc:
            raise ReleaseGovernanceError("RELEASE_SOURCE_CHANGED", str(exc)) from exc
        return assets

    def _reconcile_assets(
        self,
        tag: str,
        release: dict[str, object],
        expected: dict[str, Path],
        *,
        allowed_extra: set[str] | None = None,
    ) -> dict[str, dict[str, object]]:
        current = _asset_map(release)
        extras = set(current) - set(expected) - (allowed_extra or set())
        if extras:
            raise ReleaseGovernanceError(
                "GITHUB_RELEASE_ASSET_CONFLICT",
                f"GitHub Release contains unauthorized assets: {sorted(extras)}",
            )
        allow_upload = release.get("isDraft") is True
        reconciled: dict[str, dict[str, object]] = {}
        for name, path in sorted(expected.items()):
            if path.is_symlink() or not path.is_file():
                raise ReleaseGovernanceError(
                    "RELEASE_SOURCE_CHANGED", f"Release asset is not a regular file: {name}"
                )
            expected_hash = _sha256(path)
            asset = current.get(name)
            if asset is None:
                if not allow_upload:
                    raise ReleaseGovernanceError(
                        "GITHUB_RELEASE_ASSET_CONFLICT",
                        f"published GitHub Release is missing asset: {name}",
                    )
                self._command("gh", "release", "upload", tag, str(path))
                refreshed = self._release_data(tag)
                current = _asset_map(refreshed)
                asset = current.get(name)
                if asset is None:
                    raise ReleaseGovernanceError(
                        "GITHUB_RELEASE_BLOCKED", f"uploaded asset is not observable: {name}"
                    )
            actual_hash = self._remote_asset_hash(asset)
            if actual_hash != expected_hash:
                raise ReleaseGovernanceError(
                    "GITHUB_RELEASE_ASSET_CONFLICT",
                    f"GitHub Release asset hash differs: {name}",
                )
            reconciled[name] = {
                "sha256": expected_hash,
                "asset_id": asset.get("id"),
                "api_url": asset.get("apiUrl"),
                "size": asset.get("size"),
            }
        return reconciled

    def _remote_asset_hash(self, asset: dict[str, object]) -> str:
        api_url = asset.get("apiUrl")
        if not isinstance(api_url, str):
            raise ReleaseGovernanceError(
                "GITHUB_RELEASE_BLOCKED", "GitHub asset API URL is missing or untrusted"
            )
        parsed = urlsplit(api_url)
        allowed_hosts = {"api.github.com", *self.config.github_hosts}
        if (
            parsed.scheme != "https"
            or parsed.hostname not in allowed_hosts
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ReleaseGovernanceError(
                "GITHUB_RELEASE_BLOCKED", "GitHub asset API URL is missing or untrusted"
            )
        result = self._raw(
            "gh", "api", api_url, "-H", "Accept: application/octet-stream"
        )
        if result.returncode != 0:
            raise ReleaseGovernanceError(
                "GITHUB_RELEASE_BLOCKED", f"cannot download GitHub asset: {_stderr(result)}"
            )
        return hashlib.sha256(result.stdout).hexdigest()

    def _publish_release(
        self, tag: str, release: dict[str, object]
    ) -> dict[str, object]:
        if release.get("isDraft") is True:
            self._command("gh", "release", "edit", tag, "--draft=false")
        published = self._release_data(tag)
        if published.get("isDraft") is not False:
            raise ReleaseGovernanceError(
                "GITHUB_RELEASE_BLOCKED", "GitHub Release is still a draft"
            )
        return published

    def _write_final_manifest(
        self,
        *,
        run: WorkflowRun,
        approval: G4ApprovalInput,
        release: dict[str, object],
        tag: str,
        release_id: str,
        release_url: str,
        reconciled_assets: dict[str, dict[str, object]],
    ) -> tuple[str, str]:
        relative = f".codex-os/artifacts/{run.id}/release-manifest.json"
        payload = {
            "schema_version": RUNTIME_VERSIONS.document_schema,
            "requirement_baseline": RUNTIME_VERSIONS.requirement_baseline,
            "software_version": approval.version,
            "plugin_version": RUNTIME_VERSIONS.plugin,
            "plugin_api_version": RUNTIME_VERSIONS.api,
            "config_schema_version": RUNTIME_VERSIONS.config_schema,
            "document_schema_version": RUNTIME_VERSIONS.document_schema,
            "profile_schema_version": RUNTIME_VERSIONS.profile_schema,
            "sqlite_schema_version": RUNTIME_VERSIONS.sqlite_schema,
            "execution_image": RUNTIME_VERSIONS.execution_image,
            "source_commit": release["source_commit"],
            "integration_source_commit": release["integration_source_commit"],
            "candidate_commit": release["candidate_commit"],
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
            "registry_index_digest": release["registry_index_digest"],
            "platform_digest": release["platform_digest"],
            "reconciled_assets": reconciled_assets,
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_within(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ReleaseGovernanceError(
            "PATH_ESCAPE", f"{label} escapes its managed root"
        ) from exc


def _is_missing_release(result: subprocess.CompletedProcess[bytes]) -> bool:
    message = _stderr(result).casefold()
    return any(
        marker in message
        for marker in ("release not found", "no release found", "http 404", "status 404")
    )


def _asset_map(release: dict[str, object]) -> dict[str, dict[str, object]]:
    value = release.get("assets", [])
    if not isinstance(value, list):
        raise ReleaseGovernanceError(
            "GITHUB_RELEASE_BLOCKED", "GitHub Release assets response is invalid"
        )
    assets: dict[str, dict[str, object]] = {}
    for raw in cast(list[object], value):
        if not isinstance(raw, dict):
            raise ReleaseGovernanceError(
                "GITHUB_RELEASE_BLOCKED", "GitHub Release asset is invalid"
            )
        asset = cast(dict[str, object], raw)
        name = asset.get("name")
        if not isinstance(name, str) or not name or name in assets:
            raise ReleaseGovernanceError(
                "GITHUB_RELEASE_ASSET_CONFLICT", "GitHub Release asset names are invalid"
            )
        assets[name] = asset
    return assets


def _materialize_asset(source: Path, target: Path) -> Path:
    if source.is_symlink() or not source.is_file():
        raise ReleaseGovernanceError(
            "RELEASE_SOURCE_CHANGED", "Release evidence source is not a regular file"
        )
    content = source.read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.is_symlink() or not target.is_file() or target.read_bytes() != content:
            raise ReleaseGovernanceError(
                "RELEASE_SOURCE_CHANGED", f"materialized Release asset differs: {target.name}"
            )
        return target.resolve(strict=True)
    temporary = target.with_name(f".{target.name}.tmp")
    if temporary.exists():
        raise ReleaseGovernanceError(
            "OPERATION_RECONCILE_REQUIRED",
            f"Release asset staging residue exists: {temporary.name}",
        )
    temporary.write_bytes(content)
    temporary.replace(target)
    return target.resolve(strict=True)
