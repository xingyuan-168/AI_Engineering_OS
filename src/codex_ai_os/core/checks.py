"""Thin, real finish checks (ADR-0016).

The finish gate used to trust caller-supplied attestations (--tests-passed,
--docs-synced). It now runs only checks that can be verified in place: the
declared test command (when given), ruff when the project configures it,
"git diff --check", repository hygiene, the pending memory-candidate count,
and a second-layer Code Start re-verification over the complete task diff
from an explicit baseline, including committed code. Professional judgments such
as "the docs are consistent with the change" remain Codex's duty and are
deliberately not re-implemented here.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tomllib
from pathlib import Path

from codex_ai_os.adapters.git import GitRunner
from codex_ai_os.adapters.process import ExecutionStopped, owned_run
from codex_ai_os.core.gates import GateFinding, disposable_findings, hygiene_findings
from codex_ai_os.infrastructure.errors import DiagnosticError
from codex_ai_os.infrastructure.memory import CANDIDATE_DIRECTORY

TEST_TIMEOUT_SECONDS = 900
RUFF_TIMEOUT_SECONDS = 300


def run_thin_checks(
    root: Path,
    *,
    test_command: str | None,
    change_class: str | None = None,
    requirement_id: str | None = None,
    base_ref: str | None = None,
    runner: GitRunner | None = None,
    remote: str | None = None,
    observer=None,
) -> list[GateFinding]:
    """Run only the finish checks that can be verified in place."""

    root = root.resolve()
    git = runner or GitRunner(root)
    findings: list[GateFinding] = []

    def step(name, operation):
        return observer.step(name, operation) if observer else operation()

    def task_diff():
        base = _resolve_base(git, base_ref)
        return base, _formal_dirty_paths(root, git, base)

    try:
        base, formal = step("task_diff", task_diff)
    except DiagnosticError as exc:
        if isinstance(exc, ExecutionStopped) or exc.code == "RUN_WRITE_FAILED":
            raise
        return [GateFinding(exc.code, str(exc), path=exc.path)]
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        return [GateFinding("FINISH_DIFF_FAILED", str(exc))]
    command_runner = observer.command if observer else owned_run
    findings.extend(
        step(
            "test_command",
            lambda: _test_command_findings(root, test_command, command_runner=command_runner),
        )
    )
    findings.extend(step("ruff", lambda: _ruff_findings(root, command_runner=command_runner)))
    findings.extend(step("git_diff", lambda: _diff_check_findings(git, base)))
    findings.extend(step("hygiene", lambda: hygiene_findings(root, git)))
    findings.extend(step("disposables", lambda: disposable_findings(git)))
    findings.extend(step("memory_candidates", lambda: _memory_candidate_findings(root)))
    findings.extend(
        step(
            "code_start",
            lambda: _code_start_recheck_findings(
                root,
                git,
                change_class=change_class,
                requirement_id=requirement_id,
                formal=formal,
                remote=remote,
            ),
        )
    )
    return findings


def _resolve_base(git: GitRunner, base_ref: str | None) -> str:
    if not base_ref:
        raise DiagnosticError(
            "supply the task's starting commit or EMPTY_TREE", code="FINISH_BASE_REQUIRED"
        )
    if base_ref == "EMPTY_TREE":
        empty = git.run("hash-object", "-t", "tree", "--stdin")
        if empty.returncode != 0:
            raise DiagnosticError("cannot resolve empty tree", code="FINISH_BASE_INVALID")
        return empty.stdout.strip()
    resolved = git.run("rev-parse", "--verify", "--end-of-options", base_ref + "^{commit}")
    if resolved.returncode != 0:
        raise DiagnosticError("base must resolve to a commit", code="FINISH_BASE_INVALID")
    sha = resolved.stdout.strip()
    if git.run("merge-base", "--is-ancestor", sha, "HEAD").returncode != 0:
        raise DiagnosticError("base is not an ancestor of HEAD", code="FINISH_BASE_INVALID")
    return sha


def _formal_dirty_paths(root: Path, git: GitRunner, base_ref: str) -> list[str]:
    from codex_ai_os.infrastructure.config import load_project_config, resolve_runtime_root

    code_paths = load_project_config(resolve_runtime_root(root).project_root).code_paths
    paths: set[str] = set()
    has_head = git.run("rev-parse", "--verify", "HEAD").returncode == 0
    for args in (
        (
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            base_ref,
            *(("HEAD",) if has_head else ("--cached",)),
        ),
        ("diff", "--name-only", "-z", "--no-renames", "--cached"),
        ("diff", "--name-only", "-z", "--no-renames"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ):
        result = git.run(*args)
        if result.returncode != 0:
            raise DiagnosticError(
                "cannot inspect the complete task change", code="FINISH_DIFF_FAILED"
            )
        paths.update(path for path in result.stdout.split("\0") if path)
    return sorted(
        path
        for path in paths
        if any(
            path.casefold() == prefix.casefold()
            or path.casefold().startswith(prefix.casefold() + "/")
            for prefix in code_paths
        )
    )


def _code_start_recheck_findings(
    root: Path,
    git: GitRunner,
    *,
    change_class: str | None,
    requirement_id: str | None,
    formal: list[str],
    remote: str | None = None,
) -> list[GateFinding]:
    """Second Code Start layer: formal code changed, so prove the basis.

    Covers the indirect-write hole (``python generate.py`` and friends): the
    Hook screens the write attempt, this re-check screens the result. Already
    merged parallel-task work is covered by the first layer at write time
    plus the worktree cleanup merge proof; no extra state is kept.
    """

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
        from codex_ai_os.infrastructure.config import load_project_config, resolve_runtime_root

        config = load_project_config(resolve_runtime_root(root).project_root)
        decision = evaluate_code_start(
            root,
            change_class=change_class,
            requirement_id=requirement_id,
            runner=git,
            github_hosts=config.github_hosts,
            remote=remote,
        )
    except ExecutionStopped:
        raise
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
    root: Path, test_command: str | None, *, command_runner=owned_run
) -> list[GateFinding]:
    if test_command is None or not test_command.strip():
        return [GateFinding("TEST_COMMAND_SKIPPED", "no test command declared", blocking=False)]
    try:
        completed = command_runner(
            test_command,
            cwd=root,
            timeout=TEST_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return [
            GateFinding(
                "TEST_COMMAND_TIMEOUT",
                "test command did not finish within " + str(TEST_TIMEOUT_SECONDS) + " seconds",
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


def _ruff_findings(root: Path, *, command_runner=owned_run) -> list[GateFinding]:
    configuration = next(
        (
            root / name
            for name in (".ruff.toml", "ruff.toml", "pyproject.toml")
            if (root / name).exists()
        ),
        None,
    )
    if configuration is None:
        return [GateFinding("RUFF_SKIPPED", "project does not configure Ruff", blocking=False)]
    try:
        settings = tomllib.loads(configuration.read_text(encoding="utf-8"))
        configured = configuration.name != "pyproject.toml" or "ruff" in settings.get("tool", {})
    except OSError as exc:
        return [GateFinding("RUFF_UNAVAILABLE", "cannot read Ruff configuration: " + str(exc))]
    except (ValueError, TypeError) as exc:
        return [GateFinding("RUFF_CONFIG_INVALID", "invalid Ruff configuration: " + str(exc))]
    if not configured:
        return [GateFinding("RUFF_SKIPPED", "project does not configure Ruff", blocking=False)]
    interpreter = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not interpreter.is_file():
        interpreter = Path(sys.executable)
    if interpreter == Path(sys.executable) and importlib.util.find_spec("ruff") is None:
        return [
            GateFinding(
                "RUFF_UNAVAILABLE",
                "project configures Ruff but this interpreter cannot import it",
                blocking=True,
            )
        ]
    try:
        completed = command_runner(
            [str(interpreter), "-m", "ruff", "check", "."],
            cwd=root,
            timeout=RUFF_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return [GateFinding("RUFF_TIMEOUT", "ruff check exceeded its time budget")]
    except OSError as exc:
        return [GateFinding("RUFF_UNAVAILABLE", "ruff check failed to run: " + str(exc))]
    if completed.returncode == 0:
        return []
    if "No module named ruff" in (completed.stderr or ""):
        return [GateFinding("RUFF_UNAVAILABLE", "project interpreter cannot import Ruff")]
    output = (completed.stdout or "").strip() or (completed.stderr or "").strip()
    detail = next(
        (line for line in output.splitlines() if line.strip()),
        "ruff reported problems",
    )
    return [GateFinding("RUFF_FAILED", detail)]


def _diff_check_findings(git: GitRunner, base_ref: str) -> list[GateFinding]:
    has_head = git.run("rev-parse", "--verify", "HEAD").returncode == 0
    findings = []
    for stage, args in (
        ("committed", (base_ref, "HEAD" if has_head else "--cached")),
        ("staged", ("--cached",)),
        ("unstaged", ()),
    ):
        try:
            result = git.run("diff", "--check", *args)
        except (OSError, subprocess.SubprocessError, DiagnosticError) as exc:
            if isinstance(exc, ExecutionStopped):
                raise
            findings.append(
                GateFinding(
                    "GIT_DIFF_CHECK_FAILED",
                    "git diff --check could not execute",
                    details={
                        "stage": stage,
                        "exception": str(exc),
                        "exception_type": type(exc).__name__,
                        **getattr(exc, "details", {}),
                    },
                )
            )
            continue
        details = {
            "stage": stage,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        if result.returncode in {0, 2} and result.stdout:
            findings.append(
                GateFinding(
                    "GIT_DIFF_CHECK",
                    "git diff --check found whitespace or conflict-marker problems",
                    details=details,
                )
            )
        elif result.returncode:
            findings.append(
                GateFinding(
                    "GIT_DIFF_CHECK_FAILED",
                    "git diff --check could not complete",
                    details=details,
                )
            )
    return findings


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
            str(len(pending)) + " memory candidate(s) await accept/reject (non-blocking reminder)",
            path=", ".join(pending[:3]),
            blocking=False,
        )
    ]


__all__ = [
    "RUFF_TIMEOUT_SECONDS",
    "TEST_TIMEOUT_SECONDS",
    "run_thin_checks",
]
