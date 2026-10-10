from types import SimpleNamespace

import pytest

from codex_ai_os.application.repository import _github_findings as repository_findings
from codex_ai_os.core.gates import _github_findings as gate_findings
from codex_ai_os.core.github_remote import remote_host


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/org/repo.git",
        "https://github.com:443/org/repo.git",
        "git@github.com:org/repo.git",
        "ssh://git@github.com/org/repo.git",
        "ssh://git@github.com:22/org/repo.git",
        "git@ssh.github.com:org/repo.git",
        "ssh://git@ssh.github.com/org/repo.git",
        "ssh://git@ssh.github.com:443/org/repo.git",
        "ssh://git@SSH.GITHUB.COM:443/org/repo.git",
    ],
)
@pytest.mark.parametrize("check", [gate_findings, repository_findings])
@pytest.mark.parametrize("reachable", [True, False])
def test_official_endpoints_still_check_reachability(url, check, reachable):
    calls = []

    class Git:
        def run(self, *args, timeout=30.0):
            calls.append(args)
            if args == ("remote",):
                return SimpleNamespace(returncode=0, stdout="origin\n", stderr="")
            if args[0] == "config":
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            if args[:2] == ("remote", "get-url"):
                return SimpleNamespace(returncode=0, stdout=url, stderr="")
            return SimpleNamespace(
                returncode=128 if args[0] == "ls-remote" and not reachable else 0,
                stdout="",
                stderr="offline",
            )

    assert remote_host(url) == "github.com"
    findings = check(Git(), frozenset({"github.com"}))
    assert ("ls-remote", "--", url) in calls
    assert bool(findings) == (not reachable)
    if findings:
        assert "UNREACHABLE" in findings[0].code


@pytest.mark.parametrize(
    "url",
    [
        "ssh://git@evil.example/org/repo.git",
        "git@evil.example:org/repo.git",
        "ssh://git@ssh.github.com.evil.example:443/org/repo.git",
        "ssh://git@fakegithub.com/org/repo.git",
        "ssh://git@github.com.evil/org/repo.git",
        "ssh://git@ssh.github.com:444/org/repo.git",
        "https://ssh.github.com/org/repo.git",
        "ssh://git@github.com:443/org/repo.git",
        "ssh://git@ssh.github.com:bad/org/repo.git",
        "ssh://git@ssh.github.com:99999/org/repo.git",
        "ssh://[broken/org/repo.git",
        # Synthetic URL credential, never a real account.
        "ssh://git:password@ssh.github.com:443/org/repo.git",  # pragma: allowlist secret
        "file:///github.com/org/repo",
        "ssh://git@github.com@evil.example/org/repo.git",
        "git@*.github.com:org/repo.git",
    ],
)
@pytest.mark.parametrize("check", [gate_findings, repository_findings])
def test_reject_nonofficial_endpoints(url, check):
    calls = []

    class Git:
        def run(self, *args, timeout=30.0):
            calls.append(args)
            if args == ("remote",):
                return SimpleNamespace(returncode=0, stdout="origin\n", stderr="")
            if args[0] == "config":
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout=url, stderr="")

    assert check(Git(), frozenset({"github.com"}))
    assert not any(args[0] == "ls-remote" for args in calls)
