from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time

import pytest

from codex_ai_os.adapters.git import GitRunner


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=True, encoding="utf-8"
    ).stdout.strip()


@pytest.mark.skipif(os.name != "nt", reason="Windows subprocess pipe inheritance regression")
def test_git_timeout_without_finish_scope_owns_ssh_tree(tmp_path, monkeypatch):
    ssh = shlex.join([sys.executable.replace("\\", "/"), "-c", "import time; time.sleep(3)"])
    monkeypatch.setenv("GIT_SSH_COMMAND", ssh)
    monkeypatch.setenv("GIT_SSH_VARIANT", "ssh")
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        GitRunner(tmp_path).run("ls-remote", "git@github.com:fixture/no-network.git", timeout=0.2)
    assert time.monotonic() - start < 2


def test_missing_executable_is_unavailable_not_a_test_failure(tmp_path):
    from codex_ai_os.adapters.process import ExecutionStopped, owned_run

    with pytest.raises(ExecutionStopped) as error:
        owned_run([str(tmp_path / "does-not-exist.exe")], cwd=tmp_path, timeout=5)
    assert error.value.status == "unavailable"
    assert error.value.details["exception_type"] == "FileNotFoundError"


def test_control_setup_is_included_in_command_budget(tmp_path):
    from codex_ai_os.adapters.process import ExecutionStopped, owned_run

    marker = tmp_path / "command-started"
    with pytest.raises(ExecutionStopped) as error:
        owned_run(
            [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
            cwd=tmp_path,
            timeout=0.1,
            on_spawn=lambda pid: time.sleep(0.15),
        )
    assert error.value.status == "timed_out" and not marker.exists()


def test_literal_shell_words_preserve_boundaries():
    from codex_ai_os.application.command_syntax import shell_commands

    assert shell_commands("git.exe -C 'D:/中文 dir' status").commands == (
        ("git.exe", "-C", "D:/中文 dir", "status"),
    )
    assert shell_commands("pwsh -Command git.exe -C 'D:/中文 dir' status").commands == (
        ("git.exe", "-C", "D:/中文 dir", "status"),
    )
    assert shell_commands("git -C '' status").commands[0][2] == ""
    assert shell_commands("Write-Output 'git status'; # git version\n git --version").commands == (
        ("Write-Output", "git status"),
        ("git", "--version"),
    )
    assert shell_commands("$data = @'\ngit status\n'@\n git --version").commands == (
        ("$data", "=", "<literal-here-string>"),
        ("git", "--version"),
    )
