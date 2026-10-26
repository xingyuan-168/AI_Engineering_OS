"""Network-stage timing, cancellation and literal diagnostics regressions."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from codex_ai_os.adapters.process import COMMAND_CANCEL, ExecutionStopped, owned_run
from codex_ai_os.application.authorization import (
    AuthorizationRequest,
    GovernanceAuthorizationKernel,
)
from codex_ai_os.application.command_syntax import requires_expansion, shell_commands
from codex_ai_os.application.finish_execution import process_identity
from codex_ai_os.application.governance_policy import GovernancePolicyCompiler
from codex_ai_os.core.github_remote import RemoteCheck, check_remote, probe_remotes


class NetworkGit:
    root = Path("synthetic-context")

    def __init__(self):
        self.probes = []

    def run(self, *args, **kwargs):
        value, code = "", 0
        if args == ("remote",):
            value = "origin\n"
        elif args[0] == "config":
            code = 1
        elif args[:2] == ("remote", "get-url"):
            value = "https://github.com/synthetic/fixture.git\n"
        elif args[0] == "ls-remote":
            self.probes.append(kwargs["timeout"])
            value = "a" * 40 + "\trefs/heads/main\n"
        return subprocess.CompletedProcess(args, code, value, "")


def test_configuration_time_does_not_consume_network_budget(monkeypatch):
    import codex_ai_os.core.github_remote as module

    clock = [0.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    git = NetworkGit()
    original = git.run

    def run(*args, **kwargs):
        if args[0] != "ls-remote":
            clock[0] += 0.1
        return original(*args, **kwargs)

    git.run = run
    result = check_remote(git, {"github.com"})
    assert not result.errors
    assert result.details["configuration_seconds"] > 0
    assert git.probes == [5]


def test_three_destinations_are_concurrent_and_duplicate_is_probed_once():
    barrier = threading.Barrier(3)
    calls = []

    class Git:
        root = Path("same-effective-context")

        def run(self, *args, **kwargs):
            calls.append((args[2], kwargs["timeout"]))
            barrier.wait(timeout=3)
            return subprocess.CompletedProcess(args, 0, "a" * 40 + "\trefs/heads/main\n", "")

    urls = tuple("https://github.com/synthetic/" + str(i) for i in range(3))
    prepared = RemoteCheck("origin", urls, "github.com", (), push_urls=urls)
    results = probe_remotes([(Git(), prepared), (Git(), prepared)])
    assert len(calls) == 3
    assert all(0 < budget <= 5 for _, budget in calls)
    assert all(not result.errors for result in results)


def test_probe_timeout_reports_unknown_reachability_and_preserves_budget():
    class Git(NetworkGit):
        def run(self, *args, **kwargs):
            if args[0] == "ls-remote":
                raise subprocess.TimeoutExpired("synthetic git", kwargs["timeout"])
            return super().run(*args, **kwargs)

    result = check_remote(Git(), {"github.com"})
    assert result.errors[0][0] == "NETWORK_BUDGET_EXHAUSTED"
    assert "unconfirmed" in result.errors[0][1]
    assert result.details["budget_seconds"] == 5
    assert result.details["probes"][0]["exit_code"] is None


def test_parallel_probes_cancel_and_reap_all_owned_processes(tmp_path):
    pids, cancel = [], threading.Event()

    class Git:
        root = tmp_path

        def run(self, *args, **kwargs):
            return owned_run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=tmp_path,
                timeout=kwargs["timeout"],
                on_spawn=pids.append,
            )

    urls = ("https://github.com/synthetic/a", "https://github.com/synthetic/b")
    prepared = RemoteCheck("origin", urls, "github.com", (), push_urls=urls)
    timer = threading.Timer(0.5, cancel.set)
    token = COMMAND_CANCEL.set(cancel)
    try:
        timer.start()
        with pytest.raises(ExecutionStopped) as error:
            probe_remotes([(Git(), prepared)])
        assert error.value.status == "cancelled"
        assert pids and all(process_identity(pid) is None for pid in pids)
    finally:
        timer.cancel()
        timer.join()
        COMMAND_CANCEL.reset(token)


@pytest.mark.parametrize(
    "command",
    [
        "git config --get-regexp '^(branch\\..*\\.remote)$'",
        "git.exe -c 'core.sshCommand=ssh -4 -p 443 -o HostName=ssh.github.com' "
        "ls-remote origin HEAD",
        "git.exe -ccore.quotePath=false status",
        "git -C 'D:/中文 %dir' status",
    ],
)
def test_literal_git_diagnostics_pass_without_execution(tmp_path, command):
    kernel = GovernanceAuthorizationKernel(GovernancePolicyCompiler(tmp_path).compile())
    result = kernel.authorize(
        AuthorizationRequest(operation="execute", tool="Bash", command=command)
    )
    assert result.allowed, result


@pytest.mark.parametrize(
    "command",
    [
        "git -C $env:TARGET status",
        'git -C "$env:TARGET" status',
        "git -c 'core.sshCommand=python arbitrary.py' ls-remote origin HEAD",
        "git -c core.hooksPath=unverified status",
        "git -c 'core.sshCommand=ssh -o ProxyCommand=unverified' ls-remote origin HEAD",
        "git -c core.quotePath=false push --force origin main",
    ],
)
def test_unresolved_context_and_real_force_push_still_deny(tmp_path, command):
    kernel = GovernanceAuthorizationKernel(GovernancePolicyCompiler(tmp_path).compile())
    assert not kernel.authorize(
        AuthorizationRequest(operation="execute", tool="Bash", command=command)
    ).allowed


def test_quote_metadata_survives_word_boundaries():
    words = shell_commands("git config --get-regexp 'value$'; git -C $env:TARGET status").commands
    assert not requires_expansion(words[0][-1])
    assert requires_expansion(words[1][2])


def test_plugin_identity_probe_does_not_reduce_authorization_budget(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "plugins/ai-engineering-os/hooks/pre_tool_use.py"
    spec = importlib.util.spec_from_file_location("budget_regression", path)
    assert spec and spec.loader
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    clock, budgets = [0.0], []
    monkeypatch.setattr(hook, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    class Selector:
        @staticmethod
        def select_runtime(root, timeout):
            clock[0] = 3
            return ["codex-os"]

        @staticmethod
        def controlled_run(command, **kwargs):
            budgets.append(kwargs["timeout"])
            return subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr(hook, "_selector", lambda: (Selector, path.parents[1]))
    assert hook.run_hook({"tool_name": "Bash", "tool_input": {"command": "git status"}}) == {}
    assert budgets == [10]


def test_finish_skill_samples_include_remote_delivery_target():
    root = Path(__file__).resolve().parents[2]
    skill = root / "plugins/ai-engineering-os/skills/finish-checklist/SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert "remote_ref=" in text and "--remote-ref" in text
    assert "normally pushed" not in text
