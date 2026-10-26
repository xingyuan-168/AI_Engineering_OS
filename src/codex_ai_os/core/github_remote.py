"""Parse remote hosts, canonicalizing only GitHub's exact official endpoints."""

import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
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
class RemoteObservation:
    url: str
    refs: tuple[tuple[str, str], ...]
    checked_at: str
    elapsed_seconds: float
    exit_code: int | None
    error: tuple[str, str] | None = None
    stderr: str = ""
    valid_refs: bool = True


@dataclass(frozen=True)
class RemoteCheck:
    selected: str | None
    urls: tuple[str, ...]
    host: str | None
    errors: tuple[tuple[str, str], ...]
    upstream: str | None = None
    push_urls: tuple[str, ...] = ()
    observations: tuple[RemoteObservation, ...] = ()
    details: dict = field(default_factory=dict)


def prepare_remote(git, hosts, *, remote: str | None = None, timeout: float = 5) -> RemoteCheck:
    """Resolve and validate every destination before spending the network budget."""
    started = time.monotonic()
    deadline = started + timeout
    selected = None
    urls: tuple[str, ...] = ()
    upstream = None
    push_urls: tuple[str, ...] = ()

    def result(errors=()):
        return RemoteCheck(
            selected,
            urls,
            remote_host(urls[0]) if urls and not errors else None,
            errors,
            upstream or None,
            push_urls,
            details={"phase": "configuration", "configuration_seconds": time.monotonic() - started},
        )

    def run(*args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DiagnosticError(
                "Git configuration check budget exhausted",
                code="GIT_REMOTE_CHECK_TIMEOUT",
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
        push_urls = tuple(dict.fromkeys(push.stdout.splitlines()))
        values = list(push_urls)
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
        for url in urls:
            host = remote_host(url)
            if host is None or host not in allowed:
                # Do not echo URL credentials from rejected URLs.
                raise DiagnosticError(
                    "selected push/upstream URL is not an allowed GitHub endpoint",
                    code="GITHUB_REMOTE_NOT_ALLOWED",
                )
        return result()
    except DiagnosticError as exc:
        if isinstance(exc, ExecutionStopped):
            exc.details["remote_check"] = {
                "phase": "configuration",
                "budget_seconds": timeout,
                "elapsed_seconds": time.monotonic() - started,
            }
            raise
        return result(((exc.code, str(exc)),))
    except subprocess.TimeoutExpired:
        return result((("GIT_REMOTE_CHECK_TIMEOUT", "Git configuration exceeded its budget"),))
    except OSError as exc:
        return result((("GIT_UNAVAILABLE", "Git could not run: " + str(exc)),))
    except subprocess.SubprocessError as exc:
        return result((("GIT_REMOTE_CHECK_FAILED", "Git configuration failed: " + str(exc)),))


def display_url(url: str) -> str:
    """Diagnostics never expose URL passwords, query credentials or fragments."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme and parsed.hostname:
            host = parsed.hostname
            authority = host + (":" + str(parsed.port) if parsed.port else "")
            return parsed._replace(netloc=authority, query="", fragment="").geturl()
    except ValueError:
        return "<invalid remote URL>"
    return url


def _probe(git, url: str, deadline: float, budget: float) -> RemoteObservation:
    started = time.monotonic()
    checked_at = datetime.now(UTC).isoformat()
    refs: list[tuple[str, str]] = []
    valid, exit_code, error, stderr = True, None, None, ""
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired("git ls-remote", 0)
        command = git.run("ls-remote", "--", url, timeout=remaining)
        exit_code = command.returncode
        stderr = command.stderr.replace(url, display_url(url))
        if exit_code:
            error = (
                "GITHUB_REMOTE_UNREACHABLE",
                "Git remote probe failed; see exit code and stderr",
            )
        else:
            for line in command.stdout.splitlines():
                oid, separator, ref = line.partition("\t")
                if not separator or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid):
                    valid = False
                else:
                    refs.append((ref, oid))
    except ExecutionStopped as exc:
        exc.details["remote_check"] = {
            "phase": "network",
            "probe_url": display_url(url),
            "elapsed_seconds": time.monotonic() - started,
            "budget_seconds": budget,
            "reachability": "unconfirmed",
        }
        raise
    except subprocess.TimeoutExpired:
        error = (
            "NETWORK_BUDGET_EXHAUSTED",
            "network budget exhausted; reachability is unconfirmed",
        )
    except OSError as exc:
        error = ("GIT_UNAVAILABLE", "Git probe could not start: " + str(exc))
    except DiagnosticError as exc:
        error = (exc.code, str(exc))
    return RemoteObservation(
        url,
        tuple(refs),
        checked_at,
        time.monotonic() - started,
        exit_code,
        error,
        stderr,
        valid,
    )


def probe_remotes(requests, *, timeout: float = 5) -> list[RemoteCheck]:
    """One bounded batch; all owned commands finish or terminate before returning."""
    jobs = {}
    for git, prepared in requests:
        if not prepared.errors:
            for url in prepared.urls:
                # Checkout context includes its Git configuration, SSH and credential rules.
                key = (str(getattr(git, "root", id(git))), url)
                jobs.setdefault(key, (git, url))
    started = time.monotonic()
    deadline = started + timeout
    observations, failures = {}, []
    if len(jobs) == 1:
        key, (git, url) = next(iter(jobs.items()))
        observations[key] = _probe(git, url, deadline, timeout)
    elif jobs:
        with ThreadPoolExecutor(max_workers=min(3, len(jobs))) as pool:
            pending = {
                key: pool.submit(copy_context().run, _probe, git, url, deadline, timeout)
                for key, (git, url) in jobs.items()
            }
            for key, future in pending.items():
                try:
                    observations[key] = future.result()
                except ExecutionStopped as exc:
                    failures.append(exc)
        if failures:
            # Prefer the original deadline failure over a subsequent cancellation.
            raise next((exc for exc in failures if exc.status == "timed_out"), failures[0])
    results = []
    for git, prepared in requests:
        observed = tuple(
            observations[(str(getattr(git, "root", id(git))), url)]
            for url in prepared.urls
            if (str(getattr(git, "root", id(git))), url) in observations
        )
        errors = prepared.errors + tuple(item.error for item in observed if item.error)
        details = dict(
            prepared.details,
            phase="network" if observed else "configuration",
            budget_seconds=timeout,
            network_seconds=time.monotonic() - started,
            probes=[
                {
                    "url": display_url(item.url),
                    "elapsed_seconds": item.elapsed_seconds,
                    "exit_code": item.exit_code,
                    "stderr": item.stderr,
                    "error_code": item.error[0] if item.error else None,
                }
                for item in observed
            ],
        )
        results.append(replace(prepared, errors=errors, observations=observed, details=details))
    return results


def check_remote(git, hosts, *, remote: str | None = None, timeout: float = 5) -> RemoteCheck:
    prepared = prepare_remote(git, hosts, remote=remote, timeout=timeout)
    return probe_remotes([(git, prepared)], timeout=timeout)[0]
