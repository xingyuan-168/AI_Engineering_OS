"""Formal GitHub readiness and repository hygiene (governance-core, ADR-0016).

The checkout's root input/ contains protected user material and is not scanned.
output/ must stay a pure deliverable area (no caches, temp files, backups,
or source copies). Legacy document trees such as docs/archive must not
exist because Git history is the only archive. Copy-style version
directories and files, tracked pollution, and unresolved conflicts come
from the shared gate hygiene check so the repository check and the gates
always agree.
"""

from __future__ import annotations

from pathlib import Path

from codex_ai_os.adapters.git import GitRunner, ignored_untracked_paths
from codex_ai_os.core.gates import GateFinding, hygiene_findings, output_purity_findings
from codex_ai_os.core.github_remote import check_remote
from codex_ai_os.domain.config import GitPushPolicy, ProjectConfig
from codex_ai_os.domain.governance import RepositoryCheckReport, RepositoryFinding
from codex_ai_os.infrastructure.config import load_project_config

# Legacy archive trees must not exist in the worktree; Git history is the
# only archive. input/ is deliberately absent: it is protected user input.
_FORBIDDEN_LEGACY_DOC_TREES = ("docs/archive",)
_HYGIENE_CODES = frozenset(
    {
        "COPY_STYLE_DIRECTORY",
        "COPY_STYLE_FILE",
        "PROJECT_SOURCE_COPY",
        "PROVENANCE_CHECK_FAILED",
        "TRACKED_CHECK_FAILED",
        "CONFLICT_CHECK_FAILED",
        "UNRESOLVED_CONFLICT",
        "TRACKED_POLLUTION",
        "GITIGNORE_INCOMPLETE",
        "OUTPUT_IMPURE",
        "LEGACY_DOC_TREE",
        "SECRET_DETECTED",
    }
)
# The fifteen runtime artifact families that must never reach Git. The
# check delegates to Git, including negations and nested rules; no files are
# created for the representative paths. Finish also checks its actual UUID paths.
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
        remote: str | None = None,
    ) -> None:
        self.root = project_root.resolve()
        self.config = config or load_project_config(self.root)
        self.runner = runner or GitRunner(self.root)
        self.remote = remote
        self.remote_check = None

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
            findings.append(GateFinding("NOT_GIT_REPOSITORY", "project is not a Git repository"))
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
            if not fixture:
                self.remote_check = check_remote(git, self.config.github_hosts, remote=self.remote)
                findings.extend(
                    GateFinding(code, message) for code, message in self.remote_check.errors
                )
                remote_host = self.remote_check.host

        ordered = _convert(findings)
        report = RepositoryCheckReport(
            repository_ready=not any(item.blocking for item in ordered),
            mode=mode,
            root=self.root.as_posix(),
            head_commit=head,
            remote_host=remote_host,
            hygiene_ok=not any(item.blocking and item.code in _HYGIENE_CODES for item in ordered),
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
    return [GateFinding(code, message) for code, message in check_remote(git, github_hosts).errors]


def _configured_remote_host(git: GitRunner) -> str | None:
    return check_remote(git, {"github.com"}).host


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
        probes = {
            name: name + "/.aios-ignore-probe"
            for name in (*_GITIGNORE_BASENAMES, *_GITIGNORE_PATHS)
        }
        probes.update({name: name.replace("*", ".aios-ignore-probe") for name in _GITIGNORE_GLOBS})
        ignored = ignored_untracked_paths(GitRunner(root), list(probes.values()))
    except (OSError, ValueError) as exc:
        return [
            GateFinding(
                "GITIGNORE_INCOMPLETE",
                "cannot verify runtime ignore rules: " + str(exc),
                path=".gitignore",
                details=getattr(exc, "details", None),
            )
        ]
    missing = [name for name, probe in probes.items() if probe not in ignored]
    if not missing:
        return []
    return [
        GateFinding(
            "GITIGNORE_INCOMPLETE",
            ".gitignore must also ignore: " + ", ".join(missing),
            path=".gitignore",
        )
    ]


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
    return output_purity_findings(root)


def _convert(findings: list[GateFinding]) -> tuple[RepositoryFinding, ...]:
    ordered = sorted(findings, key=lambda item: (not item.blocking, item.code, item.path or ""))
    return tuple(
        RepositoryFinding(
            code=item.code,
            severity="error" if item.blocking else "warning",
            message=item.message,
            path=item.path,
            blocking=item.blocking,
            details=item.details,
        )
        for item in ordered
    )


__all__ = [
    "RepositoryGovernanceError",
    "RepositoryGovernanceService",
]
