"""Parse remote hosts, canonicalizing only GitHub's exact official endpoints."""

import re
import subprocess
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from codex_ai_os.adapters.process import ExecutionStopped
from codex_ai_os.infrastructure.errors import DiagnosticError


def remote_host(remote_url: str) -> str | None:
    value = remote_url.strip()
    if re.fullmatch(r"git@[^:]+:[^/]+/[^/]+(?:\.git)?", value):
        host = value.split("@", 1)[1].split(":", 1)[0].casefold()
        return "github.com" if host == "ssh.github.com" else host
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "ssh"} or parsed.username not in {None, "git"}:
            return None
        if parsed.password is not None or not parsed.hostname:
            return None
        host = parsed.hostname.casefold()
        port = parsed.port
    except ValueError:
        return None
    if host == "ssh.github.com":
        if parsed.scheme != "ssh" or port not in {None, 443}:
            return None
        return "github.com"
    if host == "github.com":
        allowed_ports = {None, 443} if parsed.scheme == "https" else {None, 22}
        if port not in allowed_ports:
            return None
    return host


@dataclass(frozen=True)
class RemoteCheck:
    selected: str | None
    urls: tuple[str, ...]
    host: str | None
    errors: tuple[tuple[str, str], ...]
    upstream: str | None = None


def check_remote(git, hosts, *, remote: str | None = None, timeout: float = 5) -> RemoteCheck:
    """One request-local selection and real probe; no origin fallback or stored cache."""
    deadline = time.monotonic() + timeout
    phase = "configuration"
    selected = None
    urls: tuple[str, ...] = ()
    upstream = None

    def run(*args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DiagnosticError(
                phase + " check budget exhausted",
                code="NETWORK_BUDGET_EXHAUSTED"
                if phase == "network"
                else "GIT_REMOTE_CHECK_TIMEOUT",
            )
        return git.run(*args, timeout=remaining)

    def config(key):
        result = run("config", "--get", key)
        if result.returncode not in {0, 1}:
            raise DiagnosticError(
                "cannot read Git remote configuration", code="GIT_REMOTE_CHECK_FAILED"
            )
        return result.stdout.rstrip("\r\n") if result.returncode == 0 else ""

    try:
        top = run("rev-parse", "--show-toplevel")
        if top.returncode:
            return RemoteCheck(None, (), None, (("NOT_GIT_REPOSITORY", "not a Git repository"),))
        branch_result = run("symbolic-ref", "--quiet", "--short", "HEAD")
        branch = branch_result.stdout.rstrip("\r\n") if branch_result.returncode == 0 else ""
        upstream = config(f"branch.{branch}.remote") if branch else ""
        selected = (
            remote
            or (config(f"branch.{branch}.pushRemote") if branch else "")
            or config("remote.pushDefault")
            or (upstream if upstream != "." else "")
        )
        remotes_result = run("remote")
        if remotes_result.returncode:
            raise DiagnosticError("cannot list Git remotes", code="GIT_REMOTE_CHECK_FAILED")
        remotes = remotes_result.stdout.splitlines()
        if not selected:
            if len(remotes) == 1:
                selected = remotes[0]
            elif not remotes:
                raise DiagnosticError("no push remote configured", code="GITHUB_REMOTE_MISSING")
            else:
                raise DiagnosticError(
                    "multiple remotes; specify remote or Git push configuration",
                    code="GITHUB_REMOTE_AMBIGUOUS",
                )
        if selected not in remotes:
            raise DiagnosticError(
                "selected remote does not exist: " + selected, code="GITHUB_REMOTE_MISSING"
            )
        push = run("remote", "get-url", "--push", "--all", selected)
        if push.returncode or not push.stdout.strip():
            raise DiagnosticError("cannot read selected push URLs", code="GITHUB_REMOTE_MISSING")
        values = push.stdout.splitlines()
        if upstream == ".":
            merge = config(f"branch.{branch}.merge")
            if not merge or run("rev-parse", "--verify", "--end-of-options", merge).returncode:
                raise DiagnosticError(
                    "local upstream reference is missing", code="UPSTREAM_INVALID"
                )
        elif upstream:
            fetch = run("remote", "get-url", "--all", upstream)
            if fetch.returncode or not fetch.stdout.strip():
                raise DiagnosticError("cannot read upstream URLs", code="UPSTREAM_INVALID")
            values.extend(fetch.stdout.splitlines())
        urls = tuple(dict.fromkeys(values))
        allowed = {h.casefold() for h in hosts}
        # Local configuration lookup has its own bound; all actual probes share
        # one network budget, including the controlled command startup overhead.
        phase = "network"
        deadline = time.monotonic() + timeout
        for url in urls:
            host = remote_host(url)
            if host is None or host not in allowed:
                # Do not echo URL credentials from rejected URLs.
                raise DiagnosticError(
                    "selected push/upstream URL is not an allowed GitHub endpoint",
                    code="GITHUB_REMOTE_NOT_ALLOWED",
                )
            result = run("ls-remote", "--", url)
            if result.returncode:
                raise DiagnosticError(
                    "selected push/upstream remote is unreachable", code="GITHUB_REMOTE_UNREACHABLE"
                )
        return RemoteCheck(selected, urls, remote_host(urls[0]), (), upstream or None)
    except DiagnosticError as exc:
        if isinstance(exc, ExecutionStopped):
            raise
        return RemoteCheck(selected, urls, None, ((exc.code, str(exc)),), upstream or None)
    except subprocess.TimeoutExpired:
        return RemoteCheck(
            selected,
            urls,
            None,
            (
                (
                    "NETWORK_BUDGET_EXHAUSTED"
                    if phase == "network"
                    else "GIT_REMOTE_CHECK_TIMEOUT",
                    phase + " command exceeded its shared budget",
                ),
            ),
            upstream,
        )
    except OSError as exc:
        return RemoteCheck(
            selected, urls, None, (("GIT_UNAVAILABLE", "Git could not run: " + str(exc)),), upstream
        )
    except subprocess.SubprocessError as exc:
        return RemoteCheck(
            selected,
            urls,
            None,
            (("GITHUB_REMOTE_UNREACHABLE", "remote probe failed: " + str(exc)),),
            upstream,
        )
