from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
from uuid import uuid4

import pytest

from codex_ai_os.adapters.git import GitRunner
from codex_ai_os.adapters.process import COMMAND_CANCEL, ExecutionStopped, owned_run
from codex_ai_os.application.finish_execution import process_identity, query_execution, run_finish
from codex_ai_os.infrastructure.errors import DiagnosticError


def wait_for(operation, *, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = operation()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError("fixture deadline exceeded")


def command_tree(root):
    pidfile = root / "owned_pids.txt"
    child = "import time; time.sleep(60)"
    code = (
        "import subprocess,sys,os,time,pathlib; "
        f"p=subprocess.Popen([sys.executable,'-c',{child!r}]); "
        f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())+','+str(p.pid)); "
        "print('before interruption',flush=True); time.sleep(60)"
    )
    return [sys.executable, "-c", code], pidfile


def test_timeout_stops_owned_descendants_and_preserves_unrelated_process(tmp_path):
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    command, pidfile = command_tree(tmp_path)
    try:
        with pytest.raises(ExecutionStopped) as stopped:
            owned_run(command, cwd=tmp_path, timeout=1)
        assert stopped.value.status == "timed_out"
        assert "before interruption" in stopped.value.details["stdout"]
        for pid in map(int, pidfile.read_text().split(",")):
            assert process_identity(pid) is None
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_completed_failure_remains_queryable_after_configuration_disappears(governed_repo):
    command = f'"{sys.executable}" -c "import sys; print(\'failed fixture\'); sys.exit(3)"'
    record = run_finish(
        governed_repo, base_ref="HEAD", test_command=command, memory_not_needed=True
    )
    assert record["execution_status"] == "completed" and not record["allowed"]
    assert record["steps"]["test_command"]["exit_code"] == 3
    (governed_repo / ".codex-os/project.yaml").unlink()
    assert query_execution(governed_repo, record["run_id"])["decision"]["allowed"] is False


def test_cancel_preserves_partial_result(governed_repo):
    cancel = threading.Event()
    command, pidfile = command_tree(governed_repo)
    # list is an internal process adapter input; public test_command stays a shell string.
    public_command = (
        subprocess.list2cmdline(command) if os.name == "nt" else __import__("shlex").join(command)
    )
    result = []
    worker = threading.Thread(
        target=lambda: result.append(
            run_finish(
                governed_repo,
                base_ref="HEAD",
                test_command=public_command,
                memory_not_needed=True,
                cancel=cancel,
            )
        )
    )
    worker.start()
    wait_for(pidfile.exists)
    cancel.set()
    worker.join(timeout=10)
    assert not worker.is_alive()
    record = result[0]
    assert record["execution_status"] == "cancelled" and record["decision"] is None
    assert record["steps"]["task_diff"]["status"] == "passed"
    assert record["steps"]["ruff"]["status"] == "not_run"
    for pid in map(int, pidfile.read_text().split(",")):
        assert process_identity(pid) is None


@pytest.mark.parametrize("fail_after", [0, 4])
def test_persistence_failure_never_starts_user_command(governed_repo, monkeypatch, fail_after):
    from codex_ai_os.application import finish_execution as execution

    original = execution.atomic_text
    calls = 0

    def fail(path, content):
        nonlocal calls
        calls += 1
        if calls > fail_after:
            raise OSError("synthetic disk failure")
        original(path, content)

    monkeypatch.setattr(execution, "atomic_text", fail)
    marker = governed_repo / "must_not_start.txt"
    command = (
        f'"{sys.executable}" -c "from pathlib import Path; Path(\'{marker.as_posix()}\').touch()"'
    )
    if fail_after == 0:
        with pytest.raises(DiagnosticError) as error:
            run_finish(governed_repo, base_ref="HEAD", test_command=command)
        assert error.value.code == "RUN_WRITE_FAILED"
    else:
        record = run_finish(governed_repo, base_ref="HEAD", test_command=command)
        assert record["execution_status"] == "failed" and record["decision"] is None
        assert record["error"]["code"] == "RUN_WRITE_FAILED"
        process = record["steps"]["test_command"].get("process")
        if process:
            assert process_identity(process["pid"]) is None
    assert not marker.exists()


def test_final_write_failure_cannot_return_completed(governed_repo, monkeypatch):
    from codex_ai_os.application import finish_execution as execution

    original = execution.atomic_text

    def fail(path, content):
        if json.loads(content)["execution_status"] == "completed":
            raise OSError("synthetic final write failure")
        original(path, content)

    monkeypatch.setattr(execution, "atomic_text", fail)
    record = run_finish(governed_repo, base_ref="HEAD", test_command=None)
    assert record["execution_status"] == "failed" and record["decision"] is None
    assert record["error"]["code"] == "RUN_WRITE_FAILED"


def test_finish_timeout_and_start_failure_are_incomplete(governed_repo, monkeypatch):
    from codex_ai_os.application import finish_execution as execution
    from codex_ai_os.core import checks

    monkeypatch.setattr(checks, "TEST_TIMEOUT_SECONDS", 1)
    command, _ = command_tree(governed_repo)
    public_command = (
        subprocess.list2cmdline(command) if os.name == "nt" else __import__("shlex").join(command)
    )
    record = run_finish(governed_repo, base_ref="HEAD", test_command=public_command)
    assert record["execution_status"] == "timed_out" and record["decision"] is None
    assert record["steps"]["ruff"]["status"] == "not_run"

    def unavailable(*args, **kwargs):
        raise OSError("synthetic command launch failure")

    monkeypatch.setattr(execution, "owned_run", unavailable)
    record = run_finish(governed_repo, base_ref="HEAD", test_command="unused")
    assert record["execution_status"] == "unavailable" and record["decision"] is None


@pytest.mark.skipif(os.name != "nt", reason="Windows Job assignment contract")
def test_job_assignment_failure_never_releases_bootstrap(tmp_path, monkeypatch):
    from codex_ai_os.adapters import process

    pids = []

    def fail(self, child):
        pids.append(child.pid)
        raise OSError("synthetic Job assignment failure")

    monkeypatch.setattr(process.WindowsJob, "assign", fail)
    marker = tmp_path / "must_not_start"
    command = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
    with pytest.raises(OSError, match="Job assignment"):
        owned_run(command, cwd=tmp_path, timeout=1)
    assert not marker.exists() and pids
    assert all(process_identity(pid) is None for pid in pids)


def test_git_ssh_descendants_share_finish_cancel_signal(governed_repo, monkeypatch):
    command, pidfile = command_tree(governed_repo)
    # SSH stdout belongs to Git's wire protocol. Leave it silent until cancellation.
    command[2] = command[2].replace("print('before interruption',flush=True); ", "")
    # Git interprets GIT_SSH_COMMAND through its shell, including on Windows.
    monkeypatch.setenv("GIT_SSH_COMMAND", shlex.join(command))
    monkeypatch.setenv("GIT_SSH_VARIANT", "ssh")
    cancel, stopped = threading.Event(), []

    def worker():
        token = COMMAND_CANCEL.set(cancel)
        try:
            GitRunner(governed_repo).run(
                "ls-remote", "git@github.com:example/fixture.git", timeout=20
            )
        except ExecutionStopped as exc:
            stopped.append(exc.status)
        finally:
            COMMAND_CANCEL.reset(token)

    thread = threading.Thread(target=worker)
    thread.start()
    wait_for(pidfile.exists)
    cancel.set()
    thread.join(timeout=10)
    assert not thread.is_alive() and stopped == ["cancelled"]
    assert all(process_identity(int(pid)) is None for pid in pidfile.read_text().split(","))


@pytest.mark.parametrize("disconnect", [False, True])
def test_real_mcp_cancellation_and_disconnect(governed_repo, disconnect):
    server = subprocess.Popen(
        [sys.executable, "-m", "codex_ai_os", "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    incoming = queue.Queue()
    assert server.stdin is not None and server.stdout is not None and server.stderr is not None
    output = server.stdout
    error_stream = server.stderr
    diagnostics = []
    threading.Thread(target=lambda: diagnostics.extend(error_stream), daemon=True).start()
    threading.Thread(target=lambda: [incoming.put(line) for line in output], daemon=True).start()

    def send(message):
        assert server.stdin is not None
        server.stdin.write(json.dumps(message) + "\n")
        server.stdin.flush()

    def response(request_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            message = json.loads(incoming.get(timeout=10))
            if message.get("id") == request_id:
                return message
        raise AssertionError("no MCP response")

    run_id = str(uuid4())
    command, pidfile = command_tree(governed_repo)
    command = (
        subprocess.list2cmdline(command) if os.name == "nt" else __import__("shlex").join(command)
    )
    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "reliability-test", "version": "1"},
                },
            }
        )
        assert "result" in response(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "governance_check",
                    "arguments": {
                        "project_root": str(governed_repo),
                        "stage": "finish",
                        "base_ref": "HEAD",
                        "memory_not_needed": True,
                        "run_id": run_id,
                        "test_command": command,
                    },
                },
            }
        )
        wait_for(pidfile.exists)
        if disconnect:
            server.stdin.close()
        else:
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": 2, "reason": "fixture cancellation"},
                }
            )
        record_path = governed_repo / ".codex-os/state/check-runs" / (run_id + ".json")
        try:
            record = wait_for(
                lambda: (
                    value
                    if (value := json.loads(record_path.read_text()))["execution_status"]
                    != "running"
                    else None
                )
            )
        except AssertionError as exc:
            raise AssertionError("MCP did not settle: " + "".join(diagnostics)) from exc
        assert record["execution_status"] == "cancelled" and record["decision"] is None
        for pid in map(int, pidfile.read_text().split(",")):
            assert process_identity(pid) is None
        if not disconnect:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "governance_check",
                        "arguments": {
                            "project_root": str(governed_repo),
                            "stage": "finish",
                            "action": "status",
                            "run_id": run_id,
                        },
                    },
                }
            )
            queried = response(3)["result"]["structuredContent"]
            assert queried["data"]["execution_status"] == "cancelled"
    finally:
        if not server.stdin.closed:
            server.stdin.close()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.terminate()
            server.wait(timeout=5)
        server.stdout.close()
        server.stderr.close()
