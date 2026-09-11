"""Formal GitHub readiness and repository hygiene (governance-core, ADR-0016).

input/ is protected user material: it is never scanned and never flagged.
output/ must stay a pure deliverable area (no caches, temp files, backups,
or source copies). Legacy document trees such as docs/archive must not
exist because Git history is the only archive. Copy-style version
directories and files, tracked pollution, and unresolved conflicts come
from the shared gate hygiene check so the repository check and the gates
always agree.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from codex_ai_os.adapters.git import GitRunner
from codex_ai_os.core.gates import GateFinding, hygiene_findings
from codex_ai_os.domain.config import GitPushPolicy, ProjectConfig
from codex_ai_os.domain.governance import RepositoryCheckReport, RepositoryFinding
from codex_ai_os.infrastructure.config import load_project_config

# Legacy archive trees must not exist in the worktree; Git history is the
# only archive. input/ is deliberately absent: it is protected user input.
_FORBIDDEN_LEGACY_DOC_TREES = ("docs/archive",)
_OUTPUT_IMPURE = re.compile(
    r"(?:\.(?:tmp|temp|log|bak|pyc|pyo|orig|rej)$"
    r"|^(?:tmp|temp|debug|scratch|oneoff|one_off)[\w.-]*$)",
    re.IGNORECASE,
)
_OUTPUT_FORBIDDEN_NAMES = frozenset(
    {"backup", "copy", "old", "final", "temp", "tmp", "debug", "cache", "__pycache__"}
)
_HYGIENE_CODES = frozenset(
    {
        "COPY_STYLE_DIRECTORY",
        "COPY_STYLE_FILE",
        "TRACKED_POLLUTION",
        "GITIGNORE_INCOMPLETE",
        "OUTPUT_IMPURE",
        "LEGACY_DOC_TREE",
        "SECRET_DETECTED",
    }
)
# The fifteen runtime artifact families that must never reach Git. The
# .gitignore check is a semantic checklist, not a full gitignore parser:
# equivalent spellings (parent directories, character-class globs, anchored
# forms) satisfy an entry (P1-6).
_GITIGNORE_BASENAMES = (
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
    "build",
    "dist",
    ".worktrees",
)
_GITIGNORE_GLOBS = ("*.pyc", "*.log")
_GITIGNORE_GLOB_EQUIVALENTS = {"*.pyc": ("*.py[cod]", "*.py[co]", "*.py?")}
_GITIGNORE_PATHS = (
    ".codex-os/state",
    ".codex-os/logs",
    ".codex-os/cache",
    ".codex-os/tmp",
    ".codex-os/artifacts",
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
        self.runner = runner or GitRunner(self.root)

    def check(self) -> RepositoryCheckReport:
        """Evaluate GitHub readiness, hygiene, and output purity."""

        git = self.runner
        fixture = self.config.git_push_policy is GitPushPolicy.FIXTURE_LOCAL_ONLY
        mode = "fixture_local_only" if fixture else "formal"
        findings: list[GateFinding] = []
        head: str | None = None
        remote_host: str | None = None

        top = git.run("rev-parse", "--show-toplevel")
        if top.returncode != 0:
            findings.append(
                GateFinding("NOT_GIT_REPOSITORY", "project is not a Git repository")
            )
        else:
            reported = Path(top.stdout.strip()).resolve()
            if reported != self.root:
                findings.append(
                    GateFinding(
                        "GIT_ROOT_MISMATCH",
                        f"Git top-level differs from configured project root: {reported}",
                    )
                )
            head_result = git.run("rev-parse", "HEAD")
            if head_result.returncode == 0:
                head = head_result.stdout.strip()
            else:
                findings.append(GateFinding("HEAD_MISSING", "repository has no committed HEAD"))

            findings.extend(hygiene_findings(self.root, git))
            findings.extend(_gitignore_findings(self.root))
            findings.extend(_legacy_tree_findings(self.root))
            findings.extend(_output_purity_findings(self.root))
            if not fixture:
                findings.extend(_github_findings(git, self.config.github_hosts))
                remote_host = _configured_remote_host(git)

        ordered = _convert(findings)
        report = RepositoryCheckReport(
            repository_ready=not any(item.blocking for item in ordered),
            mode=mode,
            root=self.root.as_posix(),
            head_commit=head,
            remote_host=remote_host,
            hygiene_ok=not any(item.code in _HYGIENE_CODES for item in ordered),
            findings=ordered,
        )
        return report

    def require_ready(self) -> RepositoryCheckReport:
        report = self.check()
        if not report.repository_ready:
            first = next(item for item in report.findings if item.blocking)
            raise RepositoryGovernanceError(first.code, first.message)
        return report


def _github_findings(git: GitRunner, github_hosts: frozenset[str]) -> list[GateFinding]:
    findings: list[GateFinding] = []
    remote = git.run("remote", "get-url", "origin")
    if remote.returncode != 0:
        findings.append(
            GateFinding(
                "GITHUB_REMOTE_REQUIRED",
                "origin remote is required before formal implementation",
            )
        )
        return findings
    url = remote.stdout.strip()
    host = _remote_host(url)
    if host is None or host not in {value.casefold() for value in github_hosts}:
        findings.append(
            GateFinding(
                "GITHUB_REMOTE_REQUIRED",
                "remote must use an allowed GitHub HTTPS or SSH host: " + repr(url),
            )
        )
        return findings
    reachable = git.run("ls-remote", "origin")
    if reachable.returncode != 0:
        detail = (reachable.stderr or reachable.stdout).strip().splitlines()
        findings.append(
            GateFinding(
                "REMOTE_UNREACHABLE",
                "GitHub remote is unreachable: "
                + (detail[-1] if detail else "git ls-remote exit " + str(reachable.returncode)),
            )
        )
    return findings


def _configured_remote_host(git: GitRunner) -> str | None:
    remote = git.run("remote", "get-url", "origin")
    if remote.returncode != 0:
        return None
    return _remote_host(remote.stdout.strip())


def _gitignore_findings(root: Path) -> list[GateFinding]:
    """Verify .gitignore covers the fifteen runtime artifact families."""

    path = root / ".gitignore"
    if not path.is_file():
        return [
            GateFinding(
                "GITIGNORE_INCOMPLETE",
                ".gitignore is missing; runtime artifacts would be trackable",
                path=".gitignore",
            )
        ]
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return [
            GateFinding(
                "GITIGNORE_INCOMPLETE",
                ".gitignore is unreadable: " + str(exc),
                path=".gitignore",
            )
        ]
    patterns = tuple(
        normalized for line in raw_lines for normalized in (_normalize_ignore(line),) if normalized
    )
    missing: list[str] = []
    for basename in _GITIGNORE_BASENAMES:
        if not any(_covers_basename(pattern, basename) for pattern in patterns):
            missing.append(basename)
    for glob_pattern in _GITIGNORE_GLOBS:
        if not any(_covers_glob(pattern, glob_pattern) for pattern in patterns):
            missing.append(glob_pattern)
    for required in _GITIGNORE_PATHS:
        if not any(_covers_path(pattern, required) for pattern in patterns):
            missing.append(required)
    if not missing:
        return []
    return [
        GateFinding(
            "GITIGNORE_INCOMPLETE",
            ".gitignore must also ignore: " + ", ".join(missing),
            path=".gitignore",
        )
    ]


def _normalize_ignore(raw: str) -> str:
    line = raw.strip()
    if not line or line.startswith("#") or line.startswith("!"):
        return ""
    line = line.replace("\\", "/").lstrip("/").rstrip("/")
    if line.endswith("/**"):
        line = line[:-3]
    return line


def _covers_basename(pattern: str, basename: str) -> bool:
    return pattern.split("/")[-1] == basename or pattern == "**/" + basename


def _covers_glob(pattern: str, glob_pattern: str) -> bool:
    if pattern == glob_pattern or pattern == "**/" + glob_pattern:
        return True
    return pattern in _GITIGNORE_GLOB_EQUIVALENTS.get(glob_pattern, ())


def _covers_path(pattern: str, required: str) -> bool:
    parent = required.rsplit("/", 1)[0]
    return pattern in {
        required,
        required + "/**",
        required + "/*",
        parent,
        parent + "/**",
        "**/" + required,
    }


def _legacy_tree_findings(root: Path) -> list[GateFinding]:
    findings: list[GateFinding] = []
    for tree in _FORBIDDEN_LEGACY_DOC_TREES:
        if (root / tree).is_dir():
            findings.append(
                GateFinding(
                    "LEGACY_DOC_TREE",
                    "legacy archive directory exists; Git history is the only archive",
                    path=tree,
                )
            )
    return findings


def _output_purity_findings(root: Path) -> list[GateFinding]:
    output_root = root / "output"
    if not output_root.is_dir():
        return []
    findings: list[GateFinding] = []
    for current, directories, files in os.walk(output_root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative = current_path.relative_to(root)
        kept: list[str] = []
        for name in directories:
            if name.casefold() in _OUTPUT_FORBIDDEN_NAMES:
                findings.append(
                    GateFinding(
                        "OUTPUT_IMPURE",
                        "output/ only holds final deliverables; remove caches and scratch",
                        path=(relative / name).as_posix(),
                    )
                )
                continue
            kept.append(name)
        directories[:] = kept
        for name in files:
            if name == ".gitkeep":
                continue
            if _OUTPUT_IMPURE.search(name) or name.casefold() in _OUTPUT_FORBIDDEN_NAMES:
                findings.append(
                    GateFinding(
                        "OUTPUT_IMPURE",
                        "output/ only holds final deliverables; remove temp and debug files",
                        path=(relative / name).as_posix(),
                    )
                )
    return findings


def _convert(findings: list[GateFinding]) -> tuple[RepositoryFinding, ...]:
    ordered = sorted(findings, key=lambda item: (not item.blocking, item.code, item.path or ""))
    return tuple(
        RepositoryFinding(
            code=item.code,
            severity="error" if item.blocking else "warning",
            message=item.message,
            path=item.path,
            blocking=item.blocking,
        )
        for item in ordered
    )


def _remote_host(remote_url: str) -> str | None:
    value = remote_url.strip()
    if re.fullmatch(r"git@[^:]+:[^/]+/[^/]+(?:\.git)?", value):
        return value.split("@", 1)[1].split(":", 1)[0].casefold()
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "ssh"} or parsed.username not in {None, "git"}:
        return None
    if parsed.password is not None or not parsed.hostname:
        return None
    return parsed.hostname.casefold()


__all__ = [
    "RepositoryGovernanceError",
    "RepositoryGovernanceService",
]
