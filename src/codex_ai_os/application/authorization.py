"""Single governance authorization kernel (ADR-0011).

Every write-class and execute-class operation must obtain its decision from
this kernel. The kernel reuses the compiled :class:`EffectiveGovernancePolicy`
(role boundaries, protected paths) and the shared governed-path matcher from
the domain layer; it does not define a second set of path rules.

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
    ASK = "ask"
    DENY = "deny"


class AuthorizationOperation(StrEnum):
    WRITE = "write"
    EXECUTE = "execute"
    TRANSITION = "transition"


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    principal: str
    operation: str
    tool: str
    source: str = "internal"
    paths: tuple[str, ...] = ()
    command: str | None = None
    workflow_id: str | None = None
    task_id: str | None = None
    request_id: str = ""


@dataclass(frozen=True, slots=True)
class AuthorizationOutcome:
    decision: AuthorizationDecision
    rule_id: str
    reason: str
    policy_hash: str
    denied_paths: tuple[str, ...] = field(default_factory=tuple)
    ask_paths: tuple[str, ...] = field(default_factory=tuple)

    @property
    def allowed(self) -> bool:
        return self.decision is AuthorizationDecision.ALLOW


# Host-side dangerous command rules (ADR-0011): the single authoritative
# source for host command screening. The plugin hook mirrors this list only
# as an explicit degraded fallback when the runtime cannot be reached.
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
        re.compile(r"\bsed\b[^;\r\n|]*?(?:^|\s)-i(?:\s|$)", re.I),
        "HOST_SED_IN_PLACE",
        "In-place file rewriting with sed -i must run through the governed task worktree tools.",
    ),
    (
        re.compile(
            r"\b(?:python(?:3)?\s+-m\s+)?pip(?:3)?\s+(?:install|wheel)\b|"
            r"\b(?:npm|pnpm|yarn)\s+(?:i|install|add|build)\b|"
            r"\bpoetry\s+install\b|\bcargo\s+(?:install|build)\b",
            re.I,
        ),
        "HOST_PACKAGE_INSTALL_BUILD",
        "Project dependency installation and builds must run through the governed OCI environment.",
    ),
    (
        re.compile(
            r"\b(?:docker|podman)(?:-compose|\s+compose)\b[^\r\n]*\bdown\b"
            r"[^\r\n]*(?:\s-v(?:\s|$)|--volumes?\b)",
            re.I,
        ),
        "HOST_COMPOSE_VOLUME_DELETE",
        "Compose volume deletion is forbidden from the Agent path.",
    ),
    (
        re.compile(r"\b(?:docker|podman)\s+volume\s+(?:rm|prune)\b", re.I),
        "HOST_VOLUME_DELETE",
        "Persistent OCI volume deletion requires an independent operator workflow.",
    ),
    (
        re.compile(
            r"\b(?:docker|podman)\s+(?:system|container)\s+prune\b"
            r"[^\r\n]*(?:-a\b|--all\b|--volumes?\b)",
            re.I,
        ),
        "HOST_BROAD_PRUNE",
        "Broad OCI prune operations are forbidden from the Agent path.",
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

        policy_hash = self._policy.policy_hash
        boundary = self._policy.role_boundaries.get(request.principal)
        if boundary is None:
            return AuthorizationOutcome(
                AuthorizationDecision.DENY,
                "ROLE_NOT_AUTHORIZED",
                f"unknown governance role: {request.principal}",
                policy_hash,
            )
        try:
            operation = AuthorizationOperation(request.operation)
        except ValueError:
            return AuthorizationOutcome(
                AuthorizationDecision.DENY,
                "OPERATION_INVALID",
                f"unsupported authorization operation: {request.operation}",
                policy_hash,
            )
        if operation is AuthorizationOperation.TRANSITION:
            return AuthorizationOutcome(
                AuthorizationDecision.ALLOW,
                "TRANSITION_ENGINE_GOVERNED",
                "workflow transitions are governed by the workflow engine",
                policy_hash,
            )
        if operation is AuthorizationOperation.EXECUTE:
            return self._authorize_execute(request, boundary.can_execute_command)
        return self._authorize_write(
            request,
            boundary.can_write,
            task_allowed_paths=task_allowed_paths,
        )

    def _authorize_execute(
        self,
        request: AuthorizationRequest,
        can_execute: bool,
    ) -> AuthorizationOutcome:
        policy_hash = self._policy.policy_hash
        if not can_execute:
            return AuthorizationOutcome(
                AuthorizationDecision.DENY,
                "ROLE_BOUNDARY_EXECUTE",
                f"role {request.principal} may not execute host commands",
                policy_hash,
            )
        command = request.command or ""
        for pattern, rule_id, reason in HOST_COMMAND_RULES:
            if pattern.search(command):
                return AuthorizationOutcome(
                    AuthorizationDecision.DENY,
                    rule_id,
                    reason,
                    policy_hash,
                )
        return AuthorizationOutcome(
            AuthorizationDecision.ALLOW,
            "HOST_COMMAND_ALLOWED",
            "command passed host screening; OCI sandbox policy still applies",
            policy_hash,
        )

    def _authorize_write(
        self,
        request: AuthorizationRequest,
        can_write: bool,
        *,
        task_allowed_paths: tuple[str, ...],
    ) -> AuthorizationOutcome:
        policy_hash = self._policy.policy_hash
        if not can_write:
            return AuthorizationOutcome(
                AuthorizationDecision.DENY,
                "ROLE_BOUNDARY_WRITE",
                f"role {request.principal} may not produce repository writes",
                policy_hash,
            )
        denied: list[str] = []
        ask: list[str] = []
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
            if (
                any(
                    policy_pattern_matches(pattern, normalized)
                    for pattern in self._policy.approval_gated_paths
                )
                and request.source != "internal"
            ):
                # Maintenance mode gates governance rule files behind human
                # approval. Internal runtime use-cases (deterministic code
                # paths such as project init) cannot self-approve, so their
                # explicit writes pass while every external source must ask.
                ask.append(path)
        if denied:
            return AuthorizationOutcome(
                AuthorizationDecision.DENY,
                "PATH_POLICY_VIOLATION",
                "one or more paths violate protected/scope policy",
                policy_hash,
                denied_paths=tuple(denied),
            )
        if ask:
            return AuthorizationOutcome(
                AuthorizationDecision.ASK,
                "GOVERNANCE_RULE_APPROVAL",
                "governance rule files require explicit human approval",
                policy_hash,
                ask_paths=tuple(ask),
            )
        return AuthorizationOutcome(
            AuthorizationDecision.ALLOW,
            "PATH_POLICY_ALLOWED",
            "all paths passed governance screening",
            policy_hash,
        )


__all__ = [
    "HOST_COMMAND_RULES",
    "AuthorizationDecision",
    "AuthorizationOperation",
    "AuthorizationOutcome",
    "AuthorizationRequest",
    "GovernanceAuthorizationKernel",
]
