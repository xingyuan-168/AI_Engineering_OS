"""Stateless governance gate evaluators (ADR-0016).

Three gates answer one question each: is this allowed right now, and if not,
why? They are pure functions of their inputs plus observable repository
state - same inputs produce the same decision, and no gate owns hidden
state or lifecycle transitions.

- **Code Start**: GitHub remote present and reachable, precise copy-style
  dirt detection, no unresolved conflicts, layered open-source research.
  Uncommitted user work never blocks; without a GitHub remote the gate
  blocks formal source implementation while reading input/, analysis,
  research, planning, and documentation remain allowed.
- **Frontend Approval**: only substantive frontend work requires an
  approved prototype plus UI spec; copy changes, CSS fixes, and component
  bug fixes are exempt.
- **Finish**: task tests passed, affected documents synced, repository
  hygiene, disposable files cleaned up, memory written or explicitly not
  needed, and no directory-copy versioning.

Gate checks that cannot be observed deterministically (for example "the
requirement scope is clear") stay process discipline in AGENTS.md; the
gates only judge observable facts.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from codex_ai_os.adapters.git import GitRunner

RESEARCH_DOCUMENT = "docs/OPEN_SOURCE_RESEARCH.md"
PROTOTYPE_PATH = "docs/design/PROTOTYPE.html"
UI_SPEC_PATH = "docs/design/UI_SPEC.md"

RESEARCH_REQUIRED_CHANGE_CLASSES = frozenset(
    {
        "new_project",
        "new_module",
        "major_feature",
        "new_stack",
        "new_integration",
        "mature_wheel_candidate",
    }
)
RESEARCH_EXEMPT_CHANGE_CLASSES = frozenset(
    {"bugfix", "typo", "tests_only", "small_change"}
)

FRONTEND_GATED_IMPACTS = frozenset(
    {"new_page", "new_interaction_flow", "major_ui_refactor"}
)
FRONTEND_EXEMPT_IMPACTS = frozenset({"none", "copy_change", "component_bugfix"})

DECISION_HEADING = re.compile(r"(?m)^##\s+Decision\b")

# Trees never scanned for copy-style dirt: VCS/runtime state plus the
# user-owned input/ material (ADR-0016 directory contract).
_EXCLUDED_TREE_NAMES = frozenset(
    {
        ".git",
        ".venv",
        ".worktrees",
        ".codex",
        ".codex-os",
        ".idea",
        ".vscode",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "__pycache__",
        "node_modules",
        "site-packages",
        "htmlcov",
        "dist",
        "build",
        "input",
    }
)
_COPY_DIRECTORY_NAMES = frozenset(
    {"old", "backup", "copy", "final", "temp", "tmp", "debug", "new", "src_backup"}
)
_COPY_SUFFIX_DIRECTORY = re.compile(
    r"(?:.*_(?:backup|old|copy|final)|project_(?:backup|final)|src_v\d+)",
    re.IGNORECASE,
)
_ROOT_VERSION_DIRECTORY = re.compile(r"v\d+", re.IGNORECASE)
_COPY_FILE = re.compile(
    r"(?:^(?:final|backup|old|copy)\.[^./]+$|\.bak$|(?:_(?:backup|old|copy|final)|fix_.+_v\d+|_v\d+)\.[^./]+$)",
    re.IGNORECASE,
)
_TRACKED_POLLUTION = re.compile(
    r"(^|/)(?:__pycache__|\.pytest_cache|\.ruff_cache|\.mypy_cache|node_modules|"
    r"dist|build|\.codex-os/(?:state|logs|cache|tmp|artifacts))(?:/|$)|"
    r"(?:\.py[co]|\.log|\.bak|\.env)$",
    re.IGNORECASE,
)
_DISPOSABLE_NAME = re.compile(
    r"(?:\.(?:tmp|temp|orig|rej|pyc|log)$|^(?:tmp|temp|debug|scratch|oneoff|one_off)[\w.-]*)",
    re.IGNORECASE,
)


class GateError(ValueError):
    """Raised when a gate input is invalid."""


class GateName(StrEnum):
    CODE_START = "code_start"
    FRONTEND = "frontend"
    FINISH = "finish"


@dataclass(frozen=True, slots=True)
class GateFinding:
    """One observable fact that supports a gate decision."""

    code: str
    message: str
    path: str | None = None
    blocking: bool = True


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Deterministic answer for one gate evaluation."""

    gate: GateName
    allowed: bool
    findings: tuple[GateFinding, ...]

    @property
    def blocked_by(self) -> tuple[str, ...]:
        return tuple(finding.code for finding in self.findings if finding.blocking)


def evaluate_code_start(
    root: Path,
    *,
    change_class: str,
    research_done: bool | None = None,
    research_path: str = RESEARCH_DOCUMENT,
    github_hosts: frozenset[str] | tuple[str, ...] = ("github.com",),
    runner: GitRunner | None = None,
) -> GateDecision:
    """Evaluate whether formal source implementation may start.

    "research_done" overrides the repository-derived research check when
    provided; when None the gate derives it from "research_path" (the file
    must exist and contain a "## Decision" section).
    """

    root = root.resolve()
    normalized_class = _normalize_choice(
        change_class,
        RESEARCH_REQUIRED_CHANGE_CLASSES | RESEARCH_EXEMPT_CHANGE_CLASSES,
        "change_class",
    )
    git = runner or GitRunner(root)
    findings: list[GateFinding] = []

    findings.extend(_github_findings(git, github_hosts))
    findings.extend(_hygiene_findings(root, git))
    findings.extend(_research_findings(root, normalized_class, research_done, research_path))
    return _decide(GateName.CODE_START, findings)


def evaluate_frontend(
    root: Path,
    *,
    impact: str,
    approved: bool = False,
    prototype_path: str = PROTOTYPE_PATH,
    ui_spec_path: str = UI_SPEC_PATH,
) -> GateDecision:
    """Evaluate whether frontend implementation may start.

    Only substantive frontend work (FRONTEND_GATED_IMPACTS) requires an
    existing prototype, an existing UI spec, and explicit approval; copy
    changes, CSS fixes, and component bug fixes pass immediately.
    """

    root = root.resolve()
    normalized_impact = _normalize_choice(
        impact, FRONTEND_GATED_IMPACTS | FRONTEND_EXEMPT_IMPACTS, "impact"
    )
    findings: list[GateFinding] = []
    if normalized_impact in FRONTEND_GATED_IMPACTS:
        if not (root / prototype_path).is_file():
            findings.append(
                GateFinding(
                    "FRONTEND_PROTOTYPE_MISSING",
                    "frontend impact '" + normalized_impact + "' requires " + prototype_path,
                    path=prototype_path,
                )
            )
        if not (root / ui_spec_path).is_file():
            findings.append(
                GateFinding(
                    "FRONTEND_UI_SPEC_MISSING",
                    "frontend impact '" + normalized_impact + "' requires " + ui_spec_path,
                    path=ui_spec_path,
                )
            )
        if not approved:
            findings.append(
                GateFinding(
                    "FRONTEND_APPROVAL_MISSING",
                    "user approval for the prototype and UI spec is required "
                    "(record it with approval_record)",
                )
            )
    return _decide(GateName.FRONTEND, findings)


def evaluate_finish(
    root: Path,
    *,
    tests_passed: bool,
    docs_synced: bool,
    memory_written: bool = False,
    memory_not_needed: bool = False,
    runner: GitRunner | None = None,
) -> GateDecision:
    """Evaluate whether a task may finish.

    Repository-side checks (hygiene, disposable leftovers, conflicts) are
    observed here; task-side facts (tests, document sync, memory) are
    supplied by the caller that ran the task.
    """

    root = root.resolve()
    git = runner or GitRunner(root)
    findings: list[GateFinding] = []
    if not tests_passed:
        findings.append(
            GateFinding("TESTS_NOT_PASSED", "task-related tests have not passed")
        )
    if not docs_synced:
        findings.append(
            GateFinding("DOCS_NOT_SYNCED", "affected documents have not been synced")
        )
    if not (memory_written or memory_not_needed):
        findings.append(
            GateFinding(
                "MEMORY_MISSING",
                "memory must be written or explicitly marked as not needed",
            )
        )
    findings.extend(_hygiene_findings(root, git))
    findings.extend(_disposable_findings(git))
    return _decide(GateName.FINISH, findings)


def _decide(gate: GateName, findings: list[GateFinding]) -> GateDecision:
    ordered = tuple(
        sorted(findings, key=lambda item: (not item.blocking, item.code, item.path or ""))
    )
    return GateDecision(
        gate=gate,
        allowed=not any(item.blocking for item in ordered),
        findings=ordered,
    )


def _normalize_choice(value: str, allowed: frozenset[str], label: str) -> str:
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    if normalized not in allowed:
        raise GateError(f"unsupported {label}: {value!r}")
    return normalized


def _github_findings(
    git: GitRunner, github_hosts: frozenset[str] | tuple[str, ...]
) -> list[GateFinding]:
    findings: list[GateFinding] = []
    top = git.run("rev-parse", "--show-toplevel")
    if top.returncode != 0:
        findings.append(
            GateFinding("NOT_GIT_REPOSITORY", "project is not a Git repository")
        )
        return findings
    remote = git.run("remote", "get-url", "origin")
    if remote.returncode != 0:
        findings.append(
            GateFinding(
                "GITHUB_REMOTE_MISSING",
                "no origin remote: formal src/ implementation is blocked; reading input/, "
                "analysis, research, planning, and documentation stay allowed until a "
                "GitHub repository is provided",
            )
        )
        return findings
    url = remote.stdout.strip()
    host = _remote_host(url)
    allowed_hosts = {value.casefold() for value in github_hosts}
    if host is None or host not in allowed_hosts:
        findings.append(
            GateFinding(
                "GITHUB_REMOTE_NOT_ALLOWED",
                "origin must use an allowed GitHub host "
                + str(sorted(allowed_hosts))
                + ": "
                + repr(url),
            )
        )
        return findings
    reachable = git.run("ls-remote", "origin")
    if reachable.returncode != 0:
        detail = (reachable.stderr or reachable.stdout).strip().splitlines()
        reason = detail[-1] if detail else "git ls-remote exit " + str(reachable.returncode)
        findings.append(
            GateFinding("GITHUB_REMOTE_UNREACHABLE", "origin is not reachable: " + reason)
        )
    return findings


def _hygiene_findings(root: Path, git: GitRunner) -> list[GateFinding]:
    findings: list[GateFinding] = []
    findings.extend(_copy_style_findings(root))
    tracked = git.run("ls-files", "-z")
    if tracked.returncode == 0:
        for entry in tracked.stdout.split("\0"):
            normalized = entry.replace("\\", "/").strip()
            if normalized and _TRACKED_POLLUTION.search(normalized):
                findings.append(
                    GateFinding(
                        "TRACKED_POLLUTION",
                        "generated, runtime, dependency, or log content is tracked",
                        path=normalized,
                    )
                )
    conflicts = git.run("diff", "--name-only", "--diff-filter=U")
    if conflicts.returncode != 0:
        findings.append(
            GateFinding("CONFLICT_CHECK_FAILED", "unresolved-conflict check failed to run")
        )
    else:
        for entry in conflicts.stdout.splitlines():
            if entry.strip():
                findings.append(
                    GateFinding(
                        "UNRESOLVED_CONFLICT",
                        "repository contains unresolved merge conflicts",
                        path=entry.strip(),
                    )
                )
    return findings


def _copy_style_findings(root: Path) -> list[GateFinding]:
    findings: list[GateFinding] = []
    if not root.is_dir():
        return findings
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative = current_path.relative_to(root)
        depth = len(relative.parts)
        kept: list[str] = []
        for name in directories:
            if name.casefold() in _EXCLUDED_TREE_NAMES:
                continue
            kept.append(name)
            copy_path = (relative / name).as_posix()
            is_copy_directory = name.casefold() in _COPY_DIRECTORY_NAMES or (
                _COPY_SUFFIX_DIRECTORY.fullmatch(name) is not None
            )
            if is_copy_directory:
                findings.append(
                    GateFinding(
                        "COPY_STYLE_DIRECTORY",
                        "copy-style version directory: keep history in Git, delete copies",
                        path=copy_path,
                    )
                )
            elif depth == 0 and _ROOT_VERSION_DIRECTORY.fullmatch(name):
                findings.append(
                    GateFinding(
                        "COPY_STYLE_DIRECTORY",
                        "top-level version directory reads as a source copy "
                        "(nested semantic versions such as api/v1/ are fine)",
                        path=copy_path,
                    )
                )
        directories[:] = kept
        for name in files:
            if _COPY_FILE.search(name):
                findings.append(
                    GateFinding(
                        "COPY_STYLE_FILE",
                        "copy-style version file: keep history in Git, delete copies",
                        path=(relative / name).as_posix(),
                    )
                )
    return findings


def _disposable_findings(git: GitRunner) -> list[GateFinding]:
    status = git.run("status", "--porcelain", "--untracked-files=normal")
    findings: list[GateFinding] = []
    if status.returncode != 0:
        findings.append(GateFinding("STATUS_CHECK_FAILED", "git status check failed to run"))
        return findings
    for line in status.stdout.splitlines():
        entry = line[3:].strip().strip('"').replace("\\", "/")
        if not entry:
            continue
        name = entry.rstrip("/").rsplit("/", 1)[-1]
        if _DISPOSABLE_NAME.search(name):
            findings.append(
                GateFinding(
                    "DISPOSABLE_FILE_PRESENT",
                    "disposable file or directory must be deleted before finish "
                    "(promote to scripts/ only if it is truly reusable)",
                    path=entry,
                )
            )
        else:
            findings.append(
                GateFinding(
                    "UNCOMMITTED_WORK",
                    "uncommitted user work is present and stays untouched by the gate",
                    path=entry,
                    blocking=False,
                )
            )
    return findings


def _research_findings(
    root: Path, change_class: str, research_done: bool | None, research_path: str
) -> list[GateFinding]:
    if change_class not in RESEARCH_REQUIRED_CHANGE_CLASSES:
        return []
    if research_done is not None:
        if research_done:
            return []
        return [
            GateFinding(
                "OPEN_SOURCE_RESEARCH_MISSING",
                "change class '" + change_class + "' requires recorded open-source research",
                path=research_path,
            )
        ]
    path = root / research_path
    if not path.is_file():
        return [
            GateFinding(
                "OPEN_SOURCE_RESEARCH_MISSING",
                "change class '"
                + change_class
                + "' requires "
                + research_path
                + " with a Decision section",
                path=research_path,
            )
        ]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return [
            GateFinding(
                "OPEN_SOURCE_RESEARCH_UNREADABLE",
                research_path + " cannot be read as UTF-8",
                path=research_path,
            )
        ]
    if DECISION_HEADING.search(text) is None:
        return [
            GateFinding(
                "OPEN_SOURCE_RESEARCH_INCOMPLETE",
                research_path
                + " must contain a '## Decision' section (use / fork / extract / build "
                "plus the reason)",
                path=research_path,
            )
        ]
    return []


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
    "FRONTEND_EXEMPT_IMPACTS",
    "FRONTEND_GATED_IMPACTS",
    "PROTOTYPE_PATH",
    "RESEARCH_DOCUMENT",
    "RESEARCH_EXEMPT_CHANGE_CLASSES",
    "RESEARCH_REQUIRED_CHANGE_CLASSES",
    "UI_SPEC_PATH",
    "GateDecision",
    "GateError",
    "GateFinding",
    "GateName",
    "evaluate_code_start",
    "evaluate_finish",
    "evaluate_frontend",
]
