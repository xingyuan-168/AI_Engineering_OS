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
- **Finish**: the declared test command (when given), ruff when configured,
  Git whitespace checks, repository hygiene, disposable files cleaned up,
  memory written or explicitly not needed, and no directory-copy versioning.
  Attested-but-unverifiable facts (--tests-passed/--docs-synced) are gone.

Gate checks that cannot be observed deterministically (for example "the
requirement scope is clear") stay process discipline in AGENTS.md; the
gates only judge observable facts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

from codex_ai_os.adapters.git import GitRunner
from codex_ai_os.adapters.process import ExecutionStopped, raise_if_cancelled
from codex_ai_os.core.github_remote import RemoteCheck, check_remote
from codex_ai_os.infrastructure.files import atomic_text

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
RESEARCH_EXEMPT_CHANGE_CLASSES = frozenset({"bugfix", "typo", "tests_only", "small_change"})

FRONTEND_GATED_IMPACTS = frozenset({"new_page", "new_interaction_flow", "major_ui_refactor"})
FRONTEND_EXEMPT_IMPACTS = frozenset({"none", "copy_change", "css_fix", "component_bugfix"})

VALID_DECISIONS = frozenset({"use", "fork", "extract", "build"})
_REQUIREMENT_ID_FIELD = re.compile(r"(?m)^\s*requirement_id\s*:\s*(.+?)\s*$")
_SUMMARY_FIELD = re.compile(r"(?m)^\s*summary\s*:\s*(.+?)\s*$")
_DECISION_FIELD = re.compile(r"(?m)^\s*decision\s*:\s*(\S+)\s*$")
_REASON_FIELD = re.compile(r"(?m)^\s*reason\s*:\s*(.+?)\s*$")
# Scope accepts either an inline CSV form ('scope: a, b') or a block list
# ('scope:' followed by '- item' lines); at least one entry is required.
_SCOPE_LIST_FIELD = re.compile(r"(?ms)^\s*scope\s*:\s*$\n(?:\s+-\s+\S.*\n?)+")
_SCOPE_INLINE_FIELD = re.compile(r"(?m)^\s*scope\s*:\s*(\S[^\n]*)$")
_UPDATED_AT_FIELD = re.compile(r"(?m)^\s*updated_at\s*:\s*(\d{4}-\d{2}-\d{2})\s*$")
_CANDIDATE_HEADING = re.compile(r"(?m)^###\s+\S")
_NO_CANDIDATE_STATEMENT = re.compile(
    r"没有合适候选|无合适候选|no\s+suitable\s+candidate", re.IGNORECASE
)

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
    details: dict | None = None


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
    requirement_id: str | None = None,
    research_path: str = RESEARCH_DOCUMENT,
    github_hosts: frozenset[str] | tuple[str, ...] = ("github.com",),
    runner: GitRunner | None = None,
    remote: str | None = None,
    remote_check: RemoteCheck | None = None,
) -> GateDecision:
    """Evaluate whether formal source implementation may start.

    Research-gated change classes must pass the current "requirement_id";
    the gate derives the research check from "research_path" (the document
    must record that requirement id, a summary, at least one candidate or
    an explicit no-candidate statement, and a Decision with a non-empty
    reason). There is no boolean bypass.
    """

    root = root.resolve()
    normalized_class = _normalize_choice(
        change_class,
        RESEARCH_REQUIRED_CHANGE_CLASSES | RESEARCH_EXEMPT_CHANGE_CLASSES,
        "change_class",
    )
    git = runner or GitRunner(root)
    findings: list[GateFinding] = []

    checked = remote_check or check_remote(git, github_hosts, remote=remote)
    findings.extend(_github_findings(git, github_hosts, remote=remote, result=checked))
    if not checked.errors and not checked.upstream:
        findings.append(
            GateFinding(
                "UPSTREAM_NOT_CONFIGURED",
                "branch has no upstream; remote reachability does not prove push permission",
                blocking=False,
            )
        )
    findings.extend(hygiene_findings(root, git))
    findings.extend(_research_findings(root, normalized_class, requirement_id, research_path))
    return _decide(GateName.CODE_START, findings)


def formal_write_blockers(
    root: Path,
    *,
    github_hosts: frozenset[str] | tuple[str, ...] = ("github.com",),
    runner: GitRunner | None = None,
    network_timeout: float = 5,
) -> GateDecision:
    """Objective Code Start subset enforced at the write boundary.

    The PreToolUse hook calls this for formal source writes: the gate
    re-derives the observable facts - GitHub readiness and repository
    hygiene - on every call, with no stored state. Requirement-scoped
    research stays a task-start concern because the hook is stateless and
    cannot know the current requirement.
    """

    root = root.resolve()
    git = runner or GitRunner(root)
    findings: list[GateFinding] = []
    findings.extend(_github_findings(git, github_hosts, timeout=network_timeout))
    findings.extend(hygiene_findings(root, git))
    return _decide(GateName.CODE_START, findings)


def evaluate_frontend(
    root: Path,
    *,
    impact: str,
    scope: str = "default",
    prototype_path: str = PROTOTYPE_PATH,
    ui_spec_path: str = UI_SPEC_PATH,
) -> GateDecision:
    """Evaluate whether frontend implementation may start.

    Only substantive frontend work (FRONTEND_GATED_IMPACTS) requires an
    existing prototype, an existing UI spec, and an approval fact recorded
    in the UI spec metadata for the exact scope; copy changes, CSS fixes,
    and component bug fixes pass immediately. Caller-supplied approved
    flags do not exist on this gate.
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
        if not _frontend_approval_fact(root / ui_spec_path, scope, root / prototype_path):
            findings.append(
                GateFinding(
                    "FRONTEND_APPROVAL_MISSING",
                    "no approved frontend approval for scope '"
                    + scope.strip()
                    + "' in "
                    + ui_spec_path
                    + " (record it with approval_record); approvals are read from "
                    "the Git-tracked UI spec, never from call arguments",
                    path=ui_spec_path,
                )
            )
    return _decide(GateName.FRONTEND, findings)


_APPROVAL_BLOCK = re.compile(r"(?ms)^approval:\s*$\n((?:[ \t]+[^\n]*\n?)+)")
_APPROVAL_FIELD = re.compile(
    r"(?m)^[ \t]+(type|scope|status|approved_by|approved_at|prototype_sha256|spec_sha256):"
    r"[ \t]*(\S[^\n]*)$"
)


def _frontend_approval_fact(ui_spec_path: Path, scope: str, prototype: Path) -> bool:
    """True when the UI spec records an approved frontend fact for the scope.

    The approval lives in the Git-tracked UI spec metadata so it survives
    database resets and moves with the repository; SQLite only mirrors it
    as an index. Different scopes never inherit each other's approval.
    """

    if not ui_spec_path.is_file():
        return False
    try:
        text = ui_spec_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    blocks = list(_APPROVAL_BLOCK.finditer(text))
    if len(blocks) != 1 or not prototype.is_file():
        return False
    block = blocks[0]
    try:
        fields = {
            match.group(1): json.loads(match.group(2))
            if match.group(2).startswith('"')
            else match.group(2).strip()
            for match in _APPROVAL_FIELD.finditer(block.group(1))
        }
        prototype_digest = _digest(prototype.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError):
        return False
    return (
        fields.get("type", "").casefold() == "frontend"
        and fields.get("scope", "") == scope.strip()
        and fields.get("status", "").casefold() == "approved"
        and bool(fields.get("approved_by"))
        and bool(fields.get("approved_at"))
        and fields.get("spec_sha256") == _spec_digest(text)
        and fields.get("prototype_sha256") == prototype_digest
    )


def _digest(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _spec_digest(text: str) -> str:
    return _digest(_APPROVAL_BLOCK.sub("", text).strip() + "\n")


def write_frontend_approval(
    ui_spec_path: Path,
    *,
    scope: str,
    approved_by: str,
    approved_on: str,
    decision: str = "approved",
) -> None:
    """Record (or replace) the frontend approval fact in the UI spec.

    The minimal metadata block is the durable approval fact; the runtime
    database mirrors it as an index only.
    """

    path = Path(ui_spec_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.is_file() else "# UI Spec\n"
    if decision not in {"approved", "rejected"}:
        raise GateError("unsupported frontend decision")
    prototype = path.with_name("PROTOTYPE.html")
    if decision == "approved" and (not path.is_file() or not prototype.is_file()):
        raise GateError("approval requires the reviewed prototype and UI spec")
    if not text.endswith("\n"):
        text += "\n"
    block = (
        "approval:\n"
        "  type: frontend\n"
        "  scope: " + json.dumps(scope.strip(), ensure_ascii=False) + "\n"
        "  status: " + decision + "\n"
        "  approved_by: " + json.dumps(approved_by.strip(), ensure_ascii=False) + "\n"
        "  approved_at: " + approved_on + "\n"
        "  spec_sha256: " + _spec_digest(text) + "\n"
        "  prototype_sha256: "
        + (_digest(prototype.read_text(encoding="utf-8")) if prototype.is_file() else "missing")
        + "\n"
    )
    match = _APPROVAL_BLOCK.search(text)
    if match is not None:
        text = text[: match.start()] + block + text[match.end() :]
    else:
        text = text.rstrip("\n") + "\n\n" + block
    atomic_text(path, text)


def evaluate_finish(
    root: Path,
    *,
    test_command: str | None,
    memory_written: bool = False,
    memory_not_needed: bool = False,
    change_class: str | None = None,
    requirement_id: str | None = None,
    base_ref: str | None = None,
    runner: GitRunner | None = None,
    remote: str | None = None,
    observer=None,
) -> GateDecision:
    """Evaluate whether a task may finish.

    Only verifiable checks run here (declared test command, configured
    linters, Git checks, hygiene, pending memory candidates, and the
    second-layer Code Start re-verification when formal code changed);
    unverifiable attestations were removed (ADR-0016). Memory remains a
    caller fact.
    """

    # Deferred import: core.checks reuses gate helpers defined in this module.
    from codex_ai_os.core.checks import run_thin_checks

    findings = run_thin_checks(
        root,
        test_command=test_command,
        change_class=change_class,
        requirement_id=requirement_id,
        base_ref=base_ref,
        runner=runner,
        remote=remote,
        observer=observer,
    )
    if not (memory_written or memory_not_needed):
        findings.append(
            GateFinding(
                "MEMORY_MISSING",
                "memory must be written or explicitly marked as not needed",
            )
        )
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
    git: GitRunner,
    github_hosts: frozenset[str] | tuple[str, ...],
    *,
    timeout: float = 5,
    remote: str | None = None,
    result: RemoteCheck | None = None,
) -> list[GateFinding]:
    checked = result or check_remote(git, github_hosts, remote=remote, timeout=timeout)
    return [GateFinding(code, message) for code, message in checked.errors]


def hygiene_findings(root: Path, git: GitRunner) -> list[GateFinding]:
    """Copy-style dirt, tracked pollution, and unresolved conflicts.

    Shared by the gates and the repository governance check; skips the
    user-owned input/ tree by design.
    """

    findings: list[GateFinding] = []
    tracked = git.run("ls-files", "-z")
    tracked_paths = (
        {p for p in tracked.stdout.split("\0") if p} if tracked.returncode == 0 else set()
    )
    findings.extend(_copy_style_findings(root, git=git, tracked=tracked_paths))
    if tracked.returncode == 0:
        for entry in tracked.stdout.split("\0"):
            normalized = entry
            if normalized and _TRACKED_POLLUTION.search(normalized):
                findings.append(
                    GateFinding(
                        "TRACKED_POLLUTION",
                        "generated, runtime, dependency, or log content is tracked",
                        path=normalized,
                    )
                )
    else:
        findings.append(GateFinding("TRACKED_CHECK_FAILED", "cannot inspect Git tracked paths"))
    conflicts = git.run("diff", "--name-only", "-z", "--diff-filter=U")
    if conflicts.returncode != 0:
        findings.append(
            GateFinding("CONFLICT_CHECK_FAILED", "unresolved-conflict check failed to run")
        )
    else:
        for entry in conflicts.stdout.split("\0"):
            if entry:
                findings.append(
                    GateFinding(
                        "UNRESOLVED_CONFLICT",
                        "repository contains unresolved merge conflicts",
                        path=entry,
                    )
                )
    findings.extend(output_purity_findings(root))
    return findings


def output_purity_findings(root: Path) -> list[GateFinding]:
    """Deliverables cannot contain caches or scratch, regardless of task ownership."""
    output = root / "output"
    forbidden = {"backup", "copy", "old", "final", "temp", "tmp", "debug", "cache", "__pycache__"}
    impure = re.compile(
        r"(?:\.(?:tmp|temp|log|bak|pyc|pyo|orig|rej)$|"
        r"^(?:tmp|temp|debug|scratch|oneoff|one_off)[\w.-]*$)",
        re.I,
    )
    findings = []
    for current, directories, files in os.walk(output, followlinks=False):
        for name in (*directories, *files):
            if name != ".gitkeep" and (name.casefold() in forbidden or impure.search(name)):
                findings.append(
                    GateFinding(
                        "OUTPUT_IMPURE",
                        "output/ only holds final deliverables",
                        path=(Path(current) / name).relative_to(root).as_posix(),
                    )
                )
        directories[:] = [name for name in directories if name.casefold() not in forbidden]
    return findings


def _copy_style_findings(root: Path, *, git=None, tracked=None) -> list[GateFinding]:
    from codex_ai_os.core.source_evidence import SourceEvidence

    findings: list[GateFinding] = []
    if not root.is_dir():
        return findings
    evidence = SourceEvidence(root, git or GitRunner(root), tracked or set())

    def finding(path, code):
        try:
            proof = evidence.classify(root / path)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            if isinstance(exc, ExecutionStopped):
                raise
            findings.append(GateFinding("PROVENANCE_CHECK_FAILED", str(exc), path=path))
            return
        findings.append(
            GateFinding(
                code,
                "verified source/generated path"
                if proof
                else "copy-style candidate lacks verified source/generated provenance; "
                "review the evidence and preserve user assets",
                path=path,
                blocking=proof is None,
                details=proof
                or {
                    "missing_evidence": [
                        "CMake source/build identity, exact generated probe and ignored Git state",
                        "or pinned submodule/FETCH_HEAD origin plus unchanged upstream content",
                    ]
                },
            )
        )

    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        raise_if_cancelled()
        current_path = Path(current)
        relative = current_path.relative_to(root)
        depth = len(relative.parts)
        kept: list[str] = []
        for name in directories:
            if depth == 0 and name.casefold() == "input":
                continue
            if name.casefold() in _EXCLUDED_TREE_NAMES:
                continue
            kept.append(name)
            copy_path = (relative / name).as_posix()
            is_copy_directory = name.casefold() in _COPY_DIRECTORY_NAMES or (
                _COPY_SUFFIX_DIRECTORY.fullmatch(name) is not None
            )
            if is_copy_directory:
                finding(copy_path, "COPY_STYLE_DIRECTORY")
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
                finding((relative / name).as_posix(), "COPY_STYLE_FILE")
            else:
                try:
                    if evidence.first_party_copy(current_path / name):
                        findings.append(
                            GateFinding(
                                "PROJECT_SOURCE_COPY",
                                "project source duplicated in build/vendor tree",
                                path=(relative / name).as_posix(),
                            )
                        )
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    if isinstance(exc, ExecutionStopped):
                        raise
                    findings.append(
                        GateFinding(
                            "PROVENANCE_CHECK_FAILED", str(exc), path=(relative / name).as_posix()
                        )
                    )
    return findings


def disposable_findings(git: GitRunner) -> list[GateFinding]:
    status = git.run("status", "--porcelain", "-z", "--untracked-files=normal")
    findings: list[GateFinding] = []
    if status.returncode != 0:
        findings.append(GateFinding("STATUS_CHECK_FAILED", "git status check failed to run"))
        return findings
    entries = iter(status.stdout.split("\0"))
    for line in entries:
        entry = line[3:]
        if "R" in line[:2] or "C" in line[:2]:
            next(entries, None)  # porcelain -z emits destination, then source
        if not entry:
            continue
        name = entry.rstrip("/").rsplit("/", 1)[-1]
        if _DISPOSABLE_NAME.search(name):
            findings.append(
                GateFinding(
                    "DISPOSABLE_FILE_PRESENT",
                    "possible disposable artifact; native review must establish task ownership",
                    path=entry,
                    blocking=False,
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


def _scope_entries(requirement: str) -> list[str]:
    """Extract scope entries from the inline CSV or block-list form."""

    block = _SCOPE_LIST_FIELD.search(requirement)
    if block is not None:
        entries = [line.strip().lstrip("-").strip() for line in block.group(0).splitlines()[1:]]
        return [entry for entry in entries if entry]
    inline = _SCOPE_INLINE_FIELD.search(requirement)
    if inline is None:
        return []
    return [item.strip() for item in inline.group(1).split(",") if item.strip()]


def _research_findings(
    root: Path, change_class: str, requirement_id: str | None, research_path: str
) -> list[GateFinding]:
    """Requirement-scoped research check over minimal Markdown metadata.

    Accepted shape (empty values never pass)::

        ## Requirement
        requirement_id: REQ-xxx
        summary: ...

        ## Candidates
        ### Project A
        ...

        ## Decision
        decision: build
        reason: ...
    """

    if change_class not in RESEARCH_REQUIRED_CHANGE_CLASSES:
        return []
    if requirement_id is None or not requirement_id.strip():
        return [
            GateFinding(
                "OPEN_SOURCE_RESEARCH_MISSING",
                "change class '" + change_class + "' requires the current "
                "requirement_id so one research document cannot act as a "
                "blanket pass",
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
                + " recording requirement '"
                + requirement_id.strip()
                + "'",
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
    findings: list[GateFinding] = []
    requirement = _markdown_section(text, "Requirement")
    if requirement is None:
        return [
            GateFinding(
                "OPEN_SOURCE_RESEARCH_INCOMPLETE",
                research_path + " must contain a '## Requirement' section with requirement_id, "
                "summary, scope, and updated_at metadata",
                path=research_path,
            )
        ]
    recorded = _REQUIREMENT_ID_FIELD.search(requirement)
    if recorded is None or recorded.group(1).strip() != requirement_id.strip():
        findings.append(
            GateFinding(
                "OPEN_SOURCE_RESEARCH_STALE",
                "research records requirement '"
                + (recorded.group(1).strip() if recorded is not None else "none")
                + "' but the current requirement is '"
                + requirement_id.strip()
                + "'; old research never unlocks new requirements",
                path=research_path,
            )
        )
    summary = _SUMMARY_FIELD.search(requirement)
    if summary is None or not summary.group(1).strip():
        findings.append(
            GateFinding(
                "OPEN_SOURCE_RESEARCH_INCOMPLETE",
                "the '## Requirement' section needs a non-empty 'summary:' field",
                path=research_path,
            )
        )
    if not _scope_entries(requirement):
        findings.append(
            GateFinding(
                "OPEN_SOURCE_RESEARCH_INCOMPLETE",
                "the '## Requirement' section needs a 'scope:' field with at "
                "least one entry (inline 'scope: a, b' or a '- item' list)",
                path=research_path,
            )
        )
    updated_at = _UPDATED_AT_FIELD.search(requirement)
    valid_date = False
    if updated_at is not None:
        try:
            date.fromisoformat(updated_at.group(1))
            valid_date = True
        except ValueError:
            valid_date = False
    if not valid_date:
        findings.append(
            GateFinding(
                "OPEN_SOURCE_RESEARCH_INCOMPLETE",
                "the '## Requirement' section needs 'updated_at:' as a valid YYYY-MM-DD date",
                path=research_path,
            )
        )
    candidates = _markdown_section(text, "Candidates")
    if candidates is None or (
        _CANDIDATE_HEADING.search(candidates) is None
        and _NO_CANDIDATE_STATEMENT.search(candidates) is None
    ):
        findings.append(
            GateFinding(
                "OPEN_SOURCE_RESEARCH_INCOMPLETE",
                "the '## Candidates' section needs at least one '### ' candidate "
                "or an explicit no-suitable-candidate statement",
                path=research_path,
            )
        )
    decision_section = _markdown_section(text, "Decision")
    decision = _DECISION_FIELD.search(decision_section) if decision_section is not None else None
    reason = _REASON_FIELD.search(decision_section) if decision_section is not None else None
    if decision is None or decision.group(1).casefold() not in VALID_DECISIONS:
        findings.append(
            GateFinding(
                "OPEN_SOURCE_RESEARCH_INCOMPLETE",
                "the '## Decision' section needs 'decision:' set to one of "
                + str(sorted(VALID_DECISIONS)),
                path=research_path,
            )
        )
    if reason is None or not reason.group(1).strip():
        findings.append(
            GateFinding(
                "OPEN_SOURCE_RESEARCH_INCOMPLETE",
                "the '## Decision' section needs a non-empty 'reason:' field",
                path=research_path,
            )
        )
    return findings


def _markdown_section(text: str, heading: str) -> str | None:
    """Return the body under a '## <heading>' section; None when absent."""

    pattern = re.compile(r"(?ms)^##\s+" + re.escape(heading) + r"\s*$\n(.*?)(?=^##\s|\Z)")
    match = pattern.search(text)
    return match.group(1) if match is not None else None


__all__ = [
    "FRONTEND_EXEMPT_IMPACTS",
    "FRONTEND_GATED_IMPACTS",
    "PROTOTYPE_PATH",
    "RESEARCH_DOCUMENT",
    "RESEARCH_EXEMPT_CHANGE_CLASSES",
    "RESEARCH_REQUIRED_CHANGE_CLASSES",
    "UI_SPEC_PATH",
    "VALID_DECISIONS",
    "GateDecision",
    "GateError",
    "GateFinding",
    "GateName",
    "disposable_findings",
    "evaluate_code_start",
    "evaluate_finish",
    "evaluate_frontend",
    "formal_write_blockers",
    "hygiene_findings",
    "write_frontend_approval",
]
