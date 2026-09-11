"""Thin, real finish checks (ADR-0016).

The finish gate used to trust caller-supplied attestations (--tests-passed,
--docs-synced). It now runs only checks that can be verified in place: the
declared test command (when given), ruff when the project configures it,
"git diff --check", repository hygiene, the pending memory-candidate count,
and a second-layer Code Start re-verification when uncommitted or staged
changes touch the formal code paths (P0-001). Professional judgments such
as "the docs are consistent with the change" remain Codex's duty and are
deliberately not re-implemented here.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from codex_ai_os.adapters.git import GitRunner
from codex_ai_os.core.gates import GateFinding, disposable_findings, hygiene_findings
from codex_ai_os.infrastructure.memory import CANDIDATE_DIRECTORY

TEST_TIMEOUT_SECONDS = 900
RUFF_TIMEOUT_SECONDS = 300


def run_thin_checks(
    root: Path,
    *,
    test_command: str | None,
    change_class: str | None = None,
    requirement_id: str | None = None,
    runner: GitRunner | None = None,
) -> list[GateFinding]:
    """Run only the finish checks that can be verified in place."""

    root = root.resolve()
    git = runner or GitRunner(root)
    findings: list[GateFinding] = []
    findings.extend(_test_command_findings(root, test_command))
    findings.extend(_ruff_findings(root))
    findings.extend(_diff_check_findings(git))
    findings.extend(hygiene_findings(root, git))
    findings.extend(disposable_findings(git))
    findings.extend(_memory_candidate_findings(root))
    findings.extend(
        _code_start_recheck_findings(
            root, git, change_class=change_class, requirement_id=requirement_id
        )
    )
    return findings


def _formal_dirty_paths(root: Path, git: GitRunner) -> list[str]:
    """Uncommitted or staged paths that fall under the formal code paths."""

    status = git.run("status", "--porcelain", "--untracked-files=normal")
    if status.returncode != 0:
        return []
    # Deferred import: infrastructure.config reads the project contract.
    from codex_ai_os.infrastructure.config import load_project_config

    try:
        code_paths = load_project_config(root).code_paths
    except Exception:  # pragma: no cover - config errors surface elsewhere
        return []
    dirty: list[str] = []
    for line in status.stdout.splitlines():
        entry = line[3:].strip().strip('"').replace("\\", "/")
        if not entry:
            continue
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        if any(
            entry == prefix or entry.startswith(prefix + "/") for prefix in code_paths
        ):
            dirty.append(entry)
    return dirty


def _code_start_recheck_findings(
    root: Path,
    git: GitRunner,
    *,
    change_class: str | None,
    requirement_id: str | None,
) -> list[GateFinding]:
    """Second Code Start layer: formal code changed, so prove the basis.

    Covers the indirect-write hole (``python generate.py`` and friends): the
    Hook screens the write attempt, this re-check screens the result. Already
    merged parallel-task work is covered by the first layer at write time
    plus the worktree cleanup merge proof; no extra state is kept.
    """

    formal = _formal_dirty_paths(root, git)
    if not formal:
        return []
    if change_class is None or not change_class.strip():
        return [
            GateFinding(
                "CODE_START_UNVERIFIED",
                "formal code paths changed ("
                + ", ".join(formal[:3])
                + ") but no Code Start basis was verified; pass --change-class "
                "(and --requirement-id when the class requires research)",
                path=formal[0],
            )
        ]
    # Deferred import: gates re-exports the evaluator defined in this package.
    from codex_ai_os.core.gates import evaluate_code_start

    try:
        decision = evaluate_code_start(
            root,
            change_class=change_class,
            requirement_id=requirement_id,
            runner=git,
        )
    except Exception as exc:  # invalid change class: fail closed
        return [
            GateFinding(
                "CODE_START_UNVERIFIED",
                "Code Start re-verification failed: " + str(exc),
                path=formal[0],
            )
        ]
    return list(decision.findings)


def _test_command_findings(
    root: Path, test_command: str | None
) -> list[GateFinding]:
    if test_command is None or not test_command.strip():
        return []
    try:
        completed = subprocess.run(
            test_command,
            shell=True,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=TEST_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return [
            GateFinding(
                "TEST_COMMAND_TIMEOUT",
                "test command did not finish within "
                + str(TEST_TIMEOUT_SECONDS)
                + " seconds",
            )
        ]
    if completed.returncode == 0:
        return []
    detail = "test command failed with exit code " + str(completed.returncode)
    output = (completed.stdout or "").strip() or (completed.stderr or "").strip()
    if output:
        tail = output.splitlines()[-3:]
        detail += ": " + " | ".join(tail)
    return [GateFinding("TEST_COMMAND_FAILED", detail)]


def _ruff_findings(root: Path) -> list[GateFinding]:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return []
    try:
        configured = "[tool.ruff]" in pyproject.read_text(encoding="utf-8")
    except OSError:
        return []
    if not configured:
        return []
    if importlib.util.find_spec("ruff") is None:
        return [
            GateFinding(
                "RUFF_UNAVAILABLE",
                "pyproject.toml configures ruff but this interpreter cannot import it",
                blocking=False,
            )
        ]
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "."],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=RUFF_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return [GateFinding("RUFF_TIMEOUT", "ruff check exceeded its time budget")]
    except OSError as exc:
        return [GateFinding("RUFF_FAILED", "ruff check failed to run: " + str(exc))]
    if completed.returncode == 0:
        return []
    output = (completed.stdout or "").strip() or (completed.stderr or "").strip()
    detail = next(
        (line for line in output.splitlines() if line.strip()),
        "ruff reported problems",
    )
    return [GateFinding("RUFF_FAILED", detail)]


def _diff_check_findings(git: GitRunner) -> list[GateFinding]:
    completed = git.run("diff", "--check")
    if completed.returncode != 0:
        return [
            GateFinding("GIT_DIFF_CHECK_FAILED", "git diff --check failed to run")
        ]
    issues = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not issues:
        return []
    first_path = issues[0].split(":", 1)[0] if ":" in issues[0] else None
    return [
        GateFinding(
            "GIT_DIFF_CHECK",
            str(len(issues))
            + " whitespace or conflict-marker problem(s) flagged by git diff --check",
            path=first_path,
        )
    ]


def _memory_candidate_findings(root: Path) -> list[GateFinding]:
    directory = root / CANDIDATE_DIRECTORY
    if not directory.is_dir():
        return []
    pending = sorted(entry.name for entry in directory.iterdir() if entry.is_file())
    if not pending:
        return []
    return [
        GateFinding(
            "MEMORY_CANDIDATES_PENDING",
            str(len(pending))
            + " memory candidate(s) await accept/reject (non-blocking reminder)",
            path=", ".join(pending[:3]),
            blocking=False,
        )
    ]


__all__ = [
    "RUFF_TIMEOUT_SECONDS",
    "TEST_TIMEOUT_SECONDS",
    "run_thin_checks",
]
