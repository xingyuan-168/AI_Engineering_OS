from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from codex_ai_os.adapters.git import GitRunner
from codex_ai_os.application import finish_execution as execution
from codex_ai_os.application.repository import _gitignore_findings
from codex_ai_os.infrastructure.errors import DiagnosticError


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=True, encoding="utf-8"
    ).stdout.strip()


def test_concurrent_queries_leave_record_and_lock_unchanged(governed_repo):
    from concurrent.futures import ThreadPoolExecutor

    record = execution.FinishExecution(governed_repo, base_ref="HEAD", command=None)
    paths = (record.path, record.path.with_suffix(".lock"))
    before = [p.read_bytes() for p in paths[:1]]
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            states = list(
                pool.map(
                    lambda _: execution.query_execution(governed_repo, record.run_id)[
                        "execution_status"
                    ],
                    range(12),
                )
            )
        assert states == ["running"] * 12
        assert [p.read_bytes() for p in paths[:1]] == before
        record.close()
        unlocked = [p.read_bytes() for p in paths]
        execution.query_execution(governed_repo, record.run_id)
        assert [p.read_bytes() for p in paths] == unlocked
    finally:
        record.close()


def test_ignore_negation_blocks_before_finish_command(governed_repo):
    (governed_repo / ".gitignore").write_text(
        Path(__file__).parents[2].joinpath(".gitignore").read_text(encoding="utf-8")
        + "\n!.codex-os/state/\n",
        encoding="utf-8",
    )
    assert any(f.blocking for f in _gitignore_findings(governed_repo))
    with pytest.raises(DiagnosticError, match="ignored"):
        execution.run_finish(governed_repo, base_ref="HEAD", test_command=None)
    assert not (governed_repo / ".codex-os/state/check-runs").exists()


def test_write_failure_does_not_leave_a_live_process_running_forever(governed_repo, monkeypatch):
    original = execution.atomic_text

    def fail_terminal(path, content):
        if json.loads(content)["execution_status"] != "running":
            raise OSError(28, "synthetic storage full", str(path))
        original(path, content)

    with monkeypatch.context() as scoped:
        scoped.setattr(execution, "atomic_text", fail_terminal)
        result = execution.run_finish(
            governed_repo, base_ref="HEAD", test_command=None, memory_not_needed=True
        )
    path = governed_repo / ".codex-os/state/check-runs" / (result["run_id"] + ".json")
    before = path.read_bytes()
    status = execution.query_execution(governed_repo, result["run_id"])
    assert status["execution_status"] == "interrupted" and status["decision"] is None
    assert path.read_bytes() == before
    assert result["error"]["details"]["errno"] == 28


def test_status_queries_active_and_closed_invocation_from_another_process(governed_repo):
    record = execution.FinishExecution(governed_repo, base_ref="HEAD", command=None)
    try:
        before = record.path.read_bytes()
        assert (
            execution.query_execution(governed_repo, record.run_id)["execution_status"] == "running"
        )
        record.close()
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "codex_ai_os.cli.app",
                "finish",
                str(governed_repo),
                "--status",
                "--run-id",
                record.run_id,
                "--json",
            ],
            capture_output=True,
            check=True,
            encoding="utf-8",
            timeout=15,
        )
        assert json.loads(result.stdout)["data"]["execution_status"] == "interrupted"
        assert record.path.read_bytes() == before
        (governed_repo / ".codex-os/project.yaml").unlink()
        (governed_repo / ".gitignore").unlink()
        assert (
            execution.query_execution(governed_repo, record.run_id)["execution_status"]
            == "interrupted"
        )
    finally:
        record.close()


def test_legacy_running_record_is_explicitly_unverifiable(governed_repo):
    record = execution.FinishExecution(governed_repo, base_ref="HEAD", command=None)
    try:
        data = json.loads(record.path.read_text(encoding="utf-8"))
        del data["record_version"]
        record.path.write_text(json.dumps(data), encoding="utf-8")
        result = execution.query_execution(governed_repo, record.run_id)
        assert result["execution_status"] == "unavailable"
        assert result["error"]["code"] == "RUN_LIVENESS_UNVERIFIABLE"
    finally:
        record.close()


def test_terminal_record_wins_lock_release_race(governed_repo, monkeypatch):
    record = execution.FinishExecution(governed_repo, base_ref="HEAD", command=None)
    try:

        def released(path):
            result = dict(record.record, execution_status="completed", decision={"allowed": False})
            record.path.write_text(json.dumps(result), encoding="utf-8")
            return False

        monkeypatch.setattr(execution, "execution_lock_active", released)
        result = execution.query_execution(governed_repo, record.run_id)
        assert result["execution_status"] == "completed" and result["decision"]["allowed"] is False
    finally:
        record.close()


def test_tracked_record_rejects_storage(governed_repo):
    from uuid import uuid4

    run_id = str(uuid4())
    directory = governed_repo / ".codex-os/state/check-runs"
    directory.mkdir(parents=True)
    path = directory / (run_id + ".json")
    path.write_text("{}", encoding="utf-8")
    git(governed_repo, "add", "-f", str(path))
    with pytest.raises(DiagnosticError, match="ignored"):
        execution.FinishExecution(governed_repo, base_ref="HEAD", command=None, run_id=run_id)


def test_nested_ignore_rule_is_checked_before_creating_a_record(governed_repo):
    directory = governed_repo / ".codex-os/state"
    directory.mkdir(parents=True, exist_ok=True)
    (governed_repo / ".gitignore").write_text(
        ".codex-os/state/*\n!.codex-os/state/.gitignore\n", encoding="utf-8"
    )
    (directory / ".gitignore").write_text("!check-runs/\n", encoding="utf-8")
    with pytest.raises(DiagnosticError) as error:
        execution.FinishExecution(governed_repo, base_ref="HEAD", command=None)
    assert error.value.code == "RUN_PATH_NOT_IGNORED"
    assert not (directory / "check-runs").exists()


def test_git_ignore_failure_does_not_create_records(governed_repo, monkeypatch):
    original = GitRunner.run

    def fail(self, *args, **kwargs):
        if args[0] == "check-ignore":
            return subprocess.CompletedProcess(args, 128, "", "synthetic index error")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(GitRunner, "run", fail)
    with pytest.raises(DiagnosticError) as error:
        execution.FinishExecution(governed_repo, base_ref="HEAD", command=None)
    assert error.value.code == "GIT_IGNORE_CHECK_FAILED"
    assert not (governed_repo / ".codex-os/state/check-runs").exists()


def test_active_invocation_lock_does_not_require_platform_pid_metadata(governed_repo, monkeypatch):
    monkeypatch.setattr(execution, "process_identity", lambda pid: None)
    record = execution.FinishExecution(governed_repo, base_ref="HEAD", command=None)
    try:
        assert (
            execution.query_execution(governed_repo, record.run_id)["execution_status"] == "running"
        )
    finally:
        record.close()


def test_lock_reparse_point_is_rejected_before_open(governed_repo, monkeypatch):
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path.name == ".lock" or original(path))
    with pytest.raises(DiagnosticError) as error:
        execution.FinishExecution(governed_repo, base_ref="HEAD", command=None)
    assert error.value.code == "RUN_PATH_UNSAFE"
    assert not (governed_repo / ".codex-os/state/check-runs").exists()
