"""Single governance authorization kernel (ADR-0011, slimmed by ADR-0016).

Every write-class and execute-class operation obtains its decision from this
kernel. The kernel judges operations, not principals: there are no roles and
no workflow transitions anymore. It reuses the compiled EffectiveGovernancePolicy
(protected paths) and the shared governed-path matcher from the domain layer;
it does not define a second set of path rules.

The kernel handles exactly five concerns: dangerous shared-Git commands,
destructive commands in the user's main worktree, the user's "input/" assets,
a small set of governance rule files, and the reasonable allowance for trusted
disposable worktrees. All checkouts use this same kernel; file cleanup
checks the actual targets, not just the caller's working directory.

The kernel never raises for policy outcomes: any failure to prove an
operation safe resolves to DENY (fail-closed), with a stable rule id and
reason for audit trails.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from codex_ai_os.application.cleanup_policy import check_cleanup, literal_tokens
from codex_ai_os.application.command_syntax import (
    CommandSyntaxError,
    executable_name,
    shell_commands,
)
from codex_ai_os.application.governance_policy import (
    EffectiveGovernancePolicy,
    policy_pattern_matches,
)
from codex_ai_os.domain.governance import (
    governed_path_allowed,
    is_unsafe_repository_path,
)


class AuthorizationDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class AuthorizationOperation(StrEnum):
    WRITE = "write"
    EXECUTE = "execute"


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    operation: str
    tool: str
    source: str = "internal"
    paths: tuple[str, ...] = ()
    command: str | None = None
    cwd: Path | None = None
    checkout: Path | None = None
    disposable: bool = False


@dataclass(frozen=True, slots=True)
class AuthorizationOutcome:
    decision: AuthorizationDecision
    rule_id: str
    reason: str
    denied_paths: tuple[str, ...] = field(default_factory=tuple)

    @property
    def allowed(self) -> bool:
        return self.decision is AuthorizationDecision.ALLOW


# Host-side dangerous command rules (ADR-0011, narrowed by ADR-0016): the
# single authoritative source for host command screening of MAIN-worktree
# operations. Normal engineering work stays native. Registered disposable
# checkouts get only narrow local Git allowances, never a kernel bypass.
HOST_COMMAND_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"\bgit\s+push\b[^\r\n]*(?:--force(?:-with-lease)?|(?:^|\s)-f(?:\s|$))", re.I),
        "HOST_GIT_FORCE_PUSH",
        "Force push is forbidden; correct published history with a new commit or git revert.",
    ),
    (
        re.compile(r"\bgit\s+reset\s+--hard\b", re.I),
        "HOST_GIT_RESET_HARD",
        "git reset --hard is forbidden because it can discard user changes.",
    ),
    (
        re.compile(r"\bgit\s+checkout\s+--(?:\s|$)", re.I),
        "HOST_GIT_CHECKOUT_DISCARD",
        "git checkout -- is forbidden because it can discard user changes.",
    ),
    (
        re.compile(r"\bgit\s+clean\b[^\r\n]*(?:^|\s)-[^\s]*f", re.I),
        "HOST_GIT_CLEAN_FORCE",
        "Forced git clean is forbidden because it can delete untracked user files.",
    ),
    (
        re.compile(r"\brm\s+-[^\s]*r[^\s]*f[^\r\n]*\s(?:/|~|\$HOME)(?:\s|$)", re.I),
        "HOST_RM_RECURSIVE_ROOT",
        "Recursive deletion of a broad root or home target is forbidden.",
    ),
    (
        re.compile(r"\b(?:rmdir|rd)\b(?=[^\r\n]*/s(?:\s|$))(?=[^\r\n]*/q(?:\s|$))[^\r\n]*", re.I),
        "HOST_RD_RECURSIVE_FORCE",
        "Recursive forced directory deletion is forbidden on the Windows host.",
    ),
    (
        re.compile(r"\b(?:del|erase)\b(?=[^\r\n]*/f(?:\s|$))(?=[^\r\n]*/s(?:\s|$))[^\r\n]*", re.I),
        "HOST_DEL_RECURSIVE_FORCE",
        "Recursive forced file deletion is forbidden on the Windows host.",
    ),
    (
        re.compile(
            r"\bRemove-Item\b(?=[^\r\n]*-(?:Recurse|r)(?:\s|$))"
            r"(?=[^\r\n]*-(?:Force|fo)(?:\s|$))[^\r\n]*",
            re.I,
        ),
        "HOST_POWERSHELL_RECURSIVE_FORCE",
        "PowerShell recursive forced deletion is forbidden on the Windows host.",
    ),
    (
        re.compile(r"\bgit\s+push\b[^\r\n]*(?:--delete(?:\s|$)|\s:[^\s]+)", re.I),
        "HOST_GIT_PUSH_DELETE_REF",
        "Deleting a remote ref with git push is forbidden.",
    ),
    (
        re.compile(r"\b(?i:git)\s+branch\b[^\r\n]*(?:^|\s)-D(?:\s|$)"),
        "HOST_GIT_BRANCH_FORCE_DELETE",
        "Forced local branch deletion is forbidden.",
    ),
    (
        re.compile(r"\bgit\s+update-ref\b[^\r\n]*(?:^|\s)(?:-d|--delete)(?:\s|$)", re.I),
        "HOST_GIT_UPDATE_REF_DELETE",
        "Deleting a Git ref directly is forbidden.",
    ),
    (
        re.compile(
            r"\b(?:docker|podman)(?:-compose|\s+compose)\b[^\r\n]*\bdown\b"
            r"[^\r\n]*(?:\s-v(?:\s|$)|--volumes?\b)",
            re.I,
        ),
        "HOST_COMPOSE_VOLUME_DELETE",
        "Compose volume deletion may destroy project persistence (database "
        "volumes); it is forbidden from the Agent path.",
    ),
    (
        re.compile(r"\b(?:docker|podman)\s+volume\s+(?:rm|prune)\b", re.I),
        "HOST_VOLUME_DELETE",
        "Docker/podman volume deletion may destroy project persistence; "
        "run it only with explicit human approval.",
    ),
    (
        re.compile(
            r"\b(?:docker|podman)\s+(?:system|container)\s+prune\b"
            r"[^\r\n]*(?:-a\b|--all\b|--volumes?\b)",
            re.I,
        ),
        "HOST_BROAD_PRUNE",
        "Broad prune operations may destroy project persistence; they are "
        "forbidden from the Agent path.",
    ),
)


class GovernanceAuthorizationKernel:
    """Fail-closed authorization over a compiled governance policy."""

    def __init__(self, policy: EffectiveGovernancePolicy) -> None:
        self._policy = policy

    @property
    def policy(self) -> EffectiveGovernancePolicy:
        return self._policy

    def authorize(
        self,
        request: AuthorizationRequest,
        *,
        task_allowed_paths: tuple[str, ...] = (),
    ) -> AuthorizationOutcome:
        """Return the authorization outcome for one structured operation."""

        try:
            operation = AuthorizationOperation(request.operation)
        except ValueError:
            return AuthorizationOutcome(
                AuthorizationDecision.DENY,
                "OPERATION_INVALID",
                f"unsupported authorization operation: {request.operation}",
            )
        if operation is AuthorizationOperation.EXECUTE:
            return self._authorize_execute(request)
        return self._authorize_write(
            request,
            task_allowed_paths=task_allowed_paths,
        )

    def _authorize_execute(
        self,
        request: AuthorizationRequest,
    ) -> AuthorizationOutcome:
        try:
            commands = shell_commands(request.command or "").commands
        except CommandSyntaxError as exc:
            return AuthorizationOutcome(AuthorizationDecision.DENY, "COMMAND_UNRESOLVED", str(exc))
        for command in commands:
            outcome = self._authorize_invocation(command, request)
            if not outcome.allowed or (
                len(commands) == 1 and outcome.rule_id == "CLEANUP_TARGET_CHECKED"
            ):
                return outcome
        return AuthorizationOutcome(
            AuthorizationDecision.ALLOW,
            "HOST_COMMAND_ALLOWED",
            "command passed host screening",
        )

    def _authorize_invocation(self, words, request):
        program = executable_name(words[0])
        args = list(words[1:])
        if program in {"remove-item", "rm", "rmdir", "rd", "del", "erase"} and request.cwd:
            rule, reason, targets = check_cleanup(
                shlex.join([program, *args]), request.cwd, request.checkout or request.cwd
            )
            return AuthorizationOutcome(
                AuthorizationDecision.ALLOW
                if rule == "CLEANUP_TARGET_CHECKED"
                else AuthorizationDecision.DENY,
                rule,
                reason,
                targets,
            )
        rule = None
        local = request.disposable
        if program == "git":
            cwd = request.cwd or Path.cwd()
            try:
                while args and args[0].startswith("-"):
                    option = args.pop(0)
                    if option == "-C" and args:
                        target = args.pop(0)
                        literal_tokens(target)
                        cwd = (cwd / target).resolve()
                        local = local and cwd == request.checkout
                    elif option in {
                        "--no-pager",
                        "--no-optional-locks",
                        "--literal-pathspecs",
                        "--no-replace-objects",
                    }:
                        continue
                    elif option in {"--version", "--help"} and not args:
                        break
                    else:
                        raise ValueError("Git context/options cannot be verified: " + option)
                if any(re.search(r"[$`%]|^@", value) for value in args):
                    raise ValueError("Git arguments require shell expansion")
            except ValueError as exc:
                return AuthorizationOutcome(
                    AuthorizationDecision.DENY, "GIT_TARGET_UNRESOLVED", str(exc)
                )
            verb = args.pop(0) if args else ""
            flags = args[: args.index("--")] if "--" in args else args

            def short(flag):
                return any(
                    a.startswith("-") and not a.startswith("--") and flag in a[1:] for a in flags
                )

            if verb == "push":
                if (
                    short("f")
                    or any(
                        a.split("=", 1)[0]
                        in {"--force", "--force-with-lease", "--force-if-includes"}
                        for a in flags
                    )
                    or any(a.startswith("+") for a in args)
                ):
                    rule = "HOST_GIT_FORCE_PUSH"
                elif "--delete" in flags or short("d") or any(a.startswith(":") for a in args):
                    rule = "HOST_GIT_PUSH_DELETE_REF"
            elif verb == "reset" and "--hard" in flags:
                rule = "HOST_GIT_RESET_HARD"
            elif verb == "checkout" and "--" in args:
                rule = "HOST_GIT_CHECKOUT_DISCARD"
            elif verb == "clean" and (short("f") or "--force" in flags):
                rule = "HOST_GIT_CLEAN_FORCE"
            elif verb == "branch" and short("D"):
                rule = "HOST_GIT_BRANCH_FORCE_DELETE"
            elif verb == "update-ref" and ("-d" in flags or "--delete" in flags):
                rule = "HOST_GIT_UPDATE_REF_DELETE"
            if local and rule in {
                "HOST_GIT_RESET_HARD",
                "HOST_GIT_CHECKOUT_DISCARD",
                "HOST_GIT_CLEAN_FORCE",
                "HOST_GIT_BRANCH_FORCE_DELETE",
            }:
                rule = None
        elif program in {"docker", "podman", "docker-compose", "podman-compose"}:
            if program.endswith("-compose"):
                args.insert(0, "compose")
            if args[:2] == ["compose", "down"] and any(a in {"-v", "--volumes"} for a in args[2:]):
                rule = "HOST_COMPOSE_VOLUME_DELETE"
            elif args[:1] == ["volume"] and args[1:2] in (["rm"], ["prune"]):
                rule = "HOST_VOLUME_DELETE"
            elif (
                args[:1] in (["system"], ["container"])
                and args[1:2] == ["prune"]
                and any(a in {"-a", "--all", "--volumes"} for a in args[2:])
            ):
                rule = "HOST_BROAD_PRUNE"
        if rule:
            reason = next(
                reason for _, identifier, reason in HOST_COMMAND_RULES if identifier == rule
            )
            return AuthorizationOutcome(AuthorizationDecision.DENY, rule, reason)
        return AuthorizationOutcome(
            AuthorizationDecision.ALLOW, "HOST_COMMAND_ALLOWED", "command passed host screening"
        )

    def _authorize_write(
        self,
        request: AuthorizationRequest,
        *,
        task_allowed_paths: tuple[str, ...],
    ) -> AuthorizationOutcome:
        denied: list[str] = []
        for raw_path in request.paths:
            path = str(raw_path).replace("\\", "/").strip()
            if is_unsafe_repository_path(path):
                denied.append(path)
                continue
            normalized = path
            if any(
                policy_pattern_matches(pattern, normalized)
                for pattern in self._policy.protected_paths
            ):
                denied.append(path)
                continue
            if task_allowed_paths and not governed_path_allowed(normalized, task_allowed_paths):
                denied.append(path)
                continue
        if denied:
            return AuthorizationOutcome(
                AuthorizationDecision.DENY,
                "PATH_POLICY_VIOLATION",
                "one or more paths violate protected/scope policy",
                denied_paths=tuple(denied),
            )
        return AuthorizationOutcome(
            AuthorizationDecision.ALLOW,
            "PATH_POLICY_ALLOWED",
            "all paths passed governance screening",
        )


__all__ = [
    "HOST_COMMAND_RULES",
    "AuthorizationDecision",
    "AuthorizationOperation",
    "AuthorizationOutcome",
    "AuthorizationRequest",
    "GovernanceAuthorizationKernel",
]
