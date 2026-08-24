"""Formal GitHub readiness and repository hygiene enforcement."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from codex_ai_os.domain.config import GitPushPolicy, ProjectConfig
from codex_ai_os.domain.governance import RepositoryCheckReport, RepositoryFinding
from codex_ai_os.domain.ids import new_id
from codex_ai_os.infrastructure.config import load_project_config
from codex_ai_os.infrastructure.database import Database

GitRunner = Callable[[list[str], Path, float], subprocess.CompletedProcess[bytes]]

_EXCLUDED_TREES = {
    ".git",
    ".venv",
    ".worktrees",
    "node_modules",
    "site-packages",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}
_AIOS_EXCLUDED = {"state", "logs", "cache", "tmp", "artifacts"}
_FORBIDDEN_DIRECTORIES = {
    "old",
    "backup",
    "copy",
    "final",
    "new",
    "temp",
    "tmp",
    "debug",
    "src_backup",
}
_REQUIRED_IGNORE_RULES = {
    ".codex-os/state/",
    ".codex-os/logs/",
    ".codex-os/cache/",
    ".codex-os/tmp/",
    ".codex-os/artifacts/",
    ".worktrees/",
    ".venv/",
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".ruff_cache/",
    ".env",
    "node_modules/",
    "dist/",
    "build/",
    "*.log",
}
_TRACKED_POLLUTION = re.compile(
    r"(^|/)(?:__pycache__|\.pytest_cache|\.ruff_cache|node_modules|dist|build|"
    r"\.codex-os/(?:state|logs|cache|tmp|artifacts))(?:/|$)|"
    r"(?:\.py[co]|\.log|\.bak|\.env)$",
    re.IGNORECASE,
)
_FORBIDDEN_FILE = re.compile(
    r"^(?:final|backup)\.py$|^fix_.+_v\d+\.py$|^test_new\.py$|\.bak$",
    re.IGNORECASE,
)
_SECRET = re.compile(
    r"(?i)(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"(?:password|token|secret|api[_-]?key)\s*[=:]\s*[^\s$<{][^\s]*)"
)


class RepositoryGovernanceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RepositoryGovernanceService:
    def __init__(
        self,
        project_root: Path,
        *,
        runner: GitRunner | None = None,
        config: ProjectConfig | None = None,
    ) -> None:
        self.root = project_root.resolve()
        self.config = config or load_project_config(self.root)
        self.runner = runner or _run_git
        self.database = Database(self.root / ".codex-os" / "state" / "state.db")

    def check(
        self,
        *,
        target_branch: str | None = None,
        run_id: str | None = None,
        persist: bool = True,
    ) -> RepositoryCheckReport:
        target = target_branch or self.config.target_branch
        fixture = self.config.git_push_policy is GitPushPolicy.FIXTURE_LOCAL_ONLY
        mode = "fixture_local_only" if fixture else "formal"
        findings: list[RepositoryFinding] = []
        head: str | None = None
        upstream: str | None = None
        remote_name: str | None = None
        remote_host: str | None = None

        top = self._git("rev-parse", "--show-toplevel")
        if top.returncode != 0:
            findings.append(_finding("NOT_GIT_REPOSITORY", "project is not a Git repository"))
        else:
            reported = Path(_stdout(top)).resolve()
            if reported != self.root:
                findings.append(
                    _finding(
                        "GIT_ROOT_MISMATCH",
                        f"Git top-level differs from configured project root: {reported}",
                    )
                )
            head_result = self._git("rev-parse", "HEAD")
            if head_result.returncode == 0:
                head = _stdout(head_result)
            else:
                findings.append(_finding("HEAD_MISSING", "repository has no committed HEAD"))

            conflicts = self._git("diff", "--name-only", "--diff-filter=U")
            if conflicts.returncode != 0 or _stdout(conflicts):
                findings.append(
                    _finding("UNRESOLVED_CONFLICT", "repository contains unresolved conflicts")
                )
            dirty = self._git("status", "--porcelain", "--untracked-files=normal")
            if dirty.returncode != 0 or _stdout(dirty):
                findings.append(_finding("WORKTREE_DIRTY", "project worktree is not clean"))

            if not fixture:
                remote_name = "origin"
                remote = self._git("remote", "get-url", remote_name)
                if remote.returncode != 0:
                    findings.append(
                        _finding("GITHUB_REMOTE_REQUIRED", "origin remote is required")
                    )
                else:
                    remote_url = _stdout(remote)
                    remote_host = _github_host(remote_url)
                    if remote_host not in self.config.github_hosts:
                        findings.append(
                            _finding(
                                "GITHUB_REMOTE_REQUIRED",
                                "remote must use an allowed GitHub HTTPS or SSH host",
                            )
                        )
                    reachable = self._git("ls-remote", "--exit-code", remote_name)
                    if reachable.returncode != 0:
                        findings.append(
                            _finding("REMOTE_UNREACHABLE", "GitHub remote is unreachable")
                        )

                upstream_result = self._git("rev-parse", "--abbrev-ref", "@{upstream}")
                if upstream_result.returncode != 0:
                    findings.append(_finding("UPSTREAM_MISSING", "current branch has no upstream"))
                else:
                    upstream = _stdout(upstream_result)
                    if head is not None:
                        pushed = self._git("merge-base", "--is-ancestor", head, "@{upstream}")
                        if pushed.returncode != 0:
                            findings.append(
                                _finding("HEAD_NOT_PUSHED", "current HEAD is not in upstream")
                            )

                target_result = self._git(
                    "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{target}"
                )
                if target_result.returncode != 0:
                    findings.append(
                        _finding(
                            "TARGET_BRANCH_MISSING",
                            f"target branch does not exist on origin: {target}",
                        )
                    )

        findings.extend(self._hygiene_findings())
        ordered = tuple(
            sorted(findings, key=lambda item: (item.code, item.path or "", item.message))
        )
        payload = {
            "mode": mode,
            "root": self.root.as_posix(),
            "target_branch": target,
            "head_commit": head,
            "remote_name": remote_name,
            "remote_host": remote_host,
            "upstream_ref": upstream,
            "findings": [item.model_dump(mode="json") for item in ordered],
        }
        check_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        hygiene_codes = {
            "FORBIDDEN_DIRECTORY",
            "FORBIDDEN_FILE",
            "TRACKED_POLLUTION",
            "GITIGNORE_INCOMPLETE",
            "PATH_ESCAPE",
            "SECRET_DETECTED",
        }
        report = RepositoryCheckReport(
            repository_ready=not any(item.blocking for item in ordered),
            mode=mode,
            root=self.root.as_posix(),
            target_branch=target,
            head_commit=head,
            remote_name=remote_name,
            remote_host=remote_host,
            upstream_ref=upstream,
            hygiene_ok=not any(item.code in hygiene_codes for item in ordered),
            findings=ordered,
            check_hash=check_hash,
            checked_at=_utc_now(),
        )
        if persist:
            self._persist(report, run_id=run_id)
        return report

    def require_ready(self, *, target_branch: str | None = None) -> RepositoryCheckReport:
        report = self.check(target_branch=target_branch)
        if not report.repository_ready:
            first = next(item for item in report.findings if item.blocking)
            raise RepositoryGovernanceError(first.code, first.message)
        return report

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return self.runner(["git", "-C", str(self.root), *arguments], self.root, 30.0)
        except (OSError, subprocess.SubprocessError) as exc:
            return subprocess.CompletedProcess([], 1, b"", str(exc).encode())

    def _hygiene_findings(self) -> list[RepositoryFinding]:
        findings: list[RepositoryFinding] = []
        gitignore = self.root / ".gitignore"
        if not gitignore.is_file():
            findings.append(_finding("GITIGNORE_INCOMPLETE", ".gitignore is required"))
        else:
            rules = {
                line.strip().replace("\\", "/")
                for line in gitignore.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            }
            missing = sorted(_REQUIRED_IGNORE_RULES - rules)
            if missing:
                findings.append(
                    _finding(
                        "GITIGNORE_INCOMPLETE",
                        f".gitignore is missing required rules: {missing}",
                        path=".gitignore",
                    )
                )

        tracked_result = self._git("ls-files", "-z")
        tracked = (
            [item for item in tracked_result.stdout.decode(errors="replace").split("\0") if item]
            if tracked_result.returncode == 0
            else []
        )
        for relative in tracked:
            normalized = relative.replace("\\", "/")
            if _TRACKED_POLLUTION.search(normalized):
                findings.append(
                    _finding(
                        "TRACKED_POLLUTION",
                        "generated, runtime, log, environment, or dependency content is tracked",
                        path=normalized,
                    )
                )
            candidate = self.root / relative
            if candidate.is_file() and candidate.stat().st_size <= 2 * 1024 * 1024:
                try:
                    content = candidate.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                if _SECRET.search(content):
                    findings.append(
                        _finding(
                            "SECRET_DETECTED",
                            "tracked content matches a Secret pattern",
                            path=normalized,
                        )
                    )

        for current, directories, files in os.walk(self.root, topdown=True, followlinks=False):
            current_path = Path(current)
            relative_dir = current_path.relative_to(self.root)
            kept: list[str] = []
            for name in directories:
                relative = relative_dir / name
                folded = name.casefold()
                if folded in _EXCLUDED_TREES or (
                    relative.parts[:1] == (".codex-os",) and folded in _AIOS_EXCLUDED
                ):
                    continue
                candidate = current_path / name
                if _is_link_like(candidate):
                    try:
                        resolved = candidate.resolve(strict=True)
                    except OSError:
                        resolved = candidate
                    if not resolved.is_relative_to(self.root):
                        findings.append(
                            _finding(
                                "PATH_ESCAPE",
                                "symlink or junction escapes the project root",
                                path=relative.as_posix(),
                            )
                        )
                    continue
                if folded in _FORBIDDEN_DIRECTORIES or re.fullmatch(r"v\d+", folded):
                    findings.append(
                        _finding(
                            "FORBIDDEN_DIRECTORY",
                            "copy-style or temporary version directory is forbidden",
                            path=relative.as_posix(),
                        )
                    )
                kept.append(name)
            directories[:] = kept
            for name in files:
                if _FORBIDDEN_FILE.search(name):
                    findings.append(
                        _finding(
                            "FORBIDDEN_FILE",
                            "copy-style, temporary, or backup file is forbidden",
                            path=(relative_dir / name).as_posix(),
                        )
                    )
        return findings

    def _persist(self, report: RepositoryCheckReport, *, run_id: str | None) -> None:
        if not self.database.path.is_file():
            return
        self.database.migrate()
        with self.database.connection() as connection:
            project = connection.execute(
                "SELECT id FROM projects WHERE id = ?", (self.config.project_id,)
            ).fetchone()
            if project is None:
                return
            audit_id = new_id("REPOAUDIT")
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO repository_audits(
                        id, project_id, run_id, mode, remote_name, remote_host,
                        remote_url_hash, target_branch, head_commit, upstream_ref,
                        repository_ready, hygiene_ok, check_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        audit_id,
                        self.config.project_id,
                        run_id,
                        report.mode,
                        report.remote_name,
                        report.remote_host,
                        report.target_branch,
                        report.head_commit,
                        report.upstream_ref,
                        int(report.repository_ready),
                        int(report.hygiene_ok),
                        report.check_hash,
                        report.checked_at,
                    ),
                )
                for finding in report.findings:
                    connection.execute(
                        """
                        INSERT INTO repository_findings(
                            id, audit_id, code, severity, path, details_json,
                            blocking, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_id("REPOFINDING"),
                            audit_id,
                            finding.code,
                            finding.severity,
                            finding.path,
                            json.dumps(
                                {"message": finding.message},
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            int(finding.blocking),
                            report.checked_at,
                        ),
                    )
                connection.execute(
                    "UPDATE projects SET repository_ready = ?, target_branch = ?, "
                    "updated_at = ? WHERE id = ?",
                    (
                        int(report.repository_ready),
                        report.target_branch,
                        report.checked_at,
                        self.config.project_id,
                    ),
                )
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
                raise


def _finding(
    code: str,
    message: str,
    *,
    path: str | None = None,
    severity: str = "error",
    blocking: bool = True,
) -> RepositoryFinding:
    return RepositoryFinding(
        code=code,
        severity=severity,
        message=message,
        path=path,
        blocking=blocking,
    )


def _github_host(remote_url: str) -> str | None:
    value = remote_url.strip()
    if re.fullmatch(r"git@[^:]+:[^/]+/[^/]+(?:\.git)?", value):
        return value.split("@", 1)[1].split(":", 1)[0].casefold()
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "ssh"} or parsed.username not in {None, "git"}:
        return None
    if parsed.password is not None or not parsed.hostname:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        return None
    return parsed.hostname.casefold()


def _stdout(process: subprocess.CompletedProcess[bytes]) -> str:
    return process.stdout.decode("utf-8", errors="replace").strip()


def _run_git(
    arguments: list[str], cwd: Path, timeout: float
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _is_link_like(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction is not None and is_junction())


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
