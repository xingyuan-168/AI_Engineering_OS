"""Single governance authorization kernel (ADR-0011, slimmed by ADR-0016).

Every write-class and execute-class operation obtains its decision from this
kernel. The kernel judges operations, not principals: there are no roles and
no workflow transitions anymore. It reuses the compiled EffectiveGovernancePolicy
(protected paths) and the shared governed-path matcher from the domain layer;
it does not define a second set of path rules.

The kernel handles exactly five concerns: dangerous shared-Git commands,
destructive commands in the user's main worktree, the user's "input/" assets,
a small set of governance rule files, and the reasonable allowance for trusted
disposable worktrees (which the hook applies by skipping this kernel outside
the main worktree).

The kernel never raises for policy outcomes: any failure to prove an
operation safe resolves to DENY (fail-closed), with a stable rule id and
reason for audit trails.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

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
# operations. Normal engineering commands (pip/npm/yarn/poetry/cargo
# installs, builds, sed -i) are never screened; destructive-but-local
# operations stay listed here and the hook only consults the kernel outside
# disposable areas, which restores the worktree allowance.
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
        re.compile(r"\bgit\s+branch\b[^\r\n]*(?:^|\s)-D(?:\s|$)", re.I),
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
        command = request.command or ""
        for pattern, rule_id, reason in HOST_COMMAND_RULES:
            if pattern.search(command):
                return AuthorizationOutcome(
                    AuthorizationDecision.DENY,
                    rule_id,
                    reason,
                )
        return AuthorizationOutcome(
            AuthorizationDecision.ALLOW,
            "HOST_COMMAND_ALLOWED",
            "command passed host screening",
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
            if task_allowed_paths and not governed_path_allowed(
                normalized, task_allowed_paths
            ):
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
