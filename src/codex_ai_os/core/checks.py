"""Thin, real finish checks (ADR-0016).

The finish gate used to trust caller-supplied attestations (--tests-passed,
--docs-synced). It now runs only checks that can be verified in place: the
declared test command (when given), ruff when the project configures it,
"git diff --check", repository hygiene, and the pending memory-candidate
count. Professional judgments such as "the docs are consistent with the
change" remain Codex's duty and are deliberately not re-implemented here.
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
    return findings


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
