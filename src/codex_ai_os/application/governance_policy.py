"""Fail-closed path and role governance for the authorization kernel (ADR-0016).

The governance-core policy is deliberately small: protected path patterns,
role boundaries, and a compiled policy hash. It answers "may this principal
write these paths" — nothing else. Gate decisions live in
``codex_ai_os.core.gates`` as stateless evaluators.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from codex_ai_os.domain.governance import is_unsafe_repository_path

# Internal alias: lexical repository-path rules live in the domain layer.
_unsafe_repository_path = is_unsafe_repository_path


class GovernancePolicyError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GovernanceMode(StrEnum):
    RUNTIME = "runtime"
    MAINTENANCE = "maintenance"


class PathAccessDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


@dataclass(frozen=True, slots=True)
class RoleBoundary:
    can_write: bool
    can_execute_command: bool
    prohibited_actions: frozenset[str] = frozenset()

    def tighten(self, restriction: RoleBoundary) -> RoleBoundary:
        """Compose a project restriction without granting baseline capabilities."""

        return RoleBoundary(
            can_write=self.can_write and restriction.can_write,
            can_execute_command=(
                self.can_execute_command and restriction.can_execute_command
            ),
            prohibited_actions=self.prohibited_actions | restriction.prohibited_actions,
        )


@dataclass(frozen=True, slots=True)
class EffectiveGovernancePolicy:
    role_boundaries: dict[str, RoleBoundary]
    protected_paths: tuple[str, ...]
    approval_gated_paths: tuple[str, ...]
    fail_closed: bool
    default_deny: bool
    policy_hash: str

    def role_boundary(self, role: str) -> RoleBoundary:
        try:
            return self.role_boundaries[role]
        except KeyError as exc:
            raise GovernancePolicyError(
                "ROLE_NOT_AUTHORIZED", f"unknown governance role: {role}"
            ) from exc

    def path_access(
        self,
        value: str,
        *,
        mutating: bool,
    ) -> PathAccessDecision:
        """Evaluate a structured path operation; read-only access is never string-blocked."""

        if not mutating:
            return PathAccessDecision.ALLOW
        path = _normalize_governed_path(value)
        if any(_policy_pattern_matches(pattern, path) for pattern in self.protected_paths):
            return PathAccessDecision.DENY
        if any(
            _policy_pattern_matches(pattern, path)
            for pattern in self.approval_gated_paths
        ):
            return PathAccessDecision.APPROVAL_REQUIRED
        return PathAccessDecision.ALLOW


# Governance facts of the runtime itself: only human-approved maintenance may
# change them from an external (hook) source.
GOVERNANCE_RULE_PATHS = (
    "AGENTS.md",
    ".codex-os/project.yaml",
    "plugins/ai-engineering-os/**",
)
# User assets and runtime state: never writable through governed channels.
SENSITIVE_PROTECTED_PATHS = (
    "input/**",
    ".git/**",
    ".codex-os/state/**",
    "output/**",
    "**.env",
    "**/credentials/**",
)
BASELINE_PROTECTED_PATHS = GOVERNANCE_RULE_PATHS + SENSITIVE_PROTECTED_PATHS

BASELINE_POLICY = {"fail_closed": True, "default_deny": True}

_PRODUCER_PROHIBITIONS = frozenset({"self-approve", "delete-core-data"})
BASELINE_ROLE_BOUNDARIES: dict[str, RoleBoundary] = {
    "project-manager": RoleBoundary(True, False, _PRODUCER_PROHIBITIONS),
    "product-manager": RoleBoundary(True, False, _PRODUCER_PROHIBITIONS),
    "architect": RoleBoundary(True, True, _PRODUCER_PROHIBITIONS),
    "backend-engineer": RoleBoundary(True, True, _PRODUCER_PROHIBITIONS),
    "frontend-engineer": RoleBoundary(True, True, _PRODUCER_PROHIBITIONS),
    "database-engineer": RoleBoundary(True, True, _PRODUCER_PROHIBITIONS),
    "qa": RoleBoundary(False, True, frozenset({"accept-own-work"})),
    "reviewer": RoleBoundary(False, False, frozenset({"produce-reviewed-change"})),
    "memory-manager": RoleBoundary(True, False, frozenset({"self-approve"})),
}


class GovernancePolicyCompiler:
    """Compile the effective fail-closed policy for one project."""

    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()

    def compile(
        self,
        *,
        mode: GovernanceMode = GovernanceMode.RUNTIME,
        role_restrictions: Mapping[str, RoleBoundary] | None = None,
        additional_protected_paths: tuple[str, ...] = (),
    ) -> EffectiveGovernancePolicy:
        role_boundaries = _effective_role_boundaries(role_restrictions or {})
        project_paths = tuple(
            _normalize_policy_pattern(path) for path in additional_protected_paths
        )
        if mode is GovernanceMode.MAINTENANCE:
            protected_paths = tuple(
                sorted(set(SENSITIVE_PROTECTED_PATHS) | set(project_paths))
            )
            approval_gated_paths = GOVERNANCE_RULE_PATHS
        else:
            protected_paths = tuple(
                sorted(set(BASELINE_PROTECTED_PATHS) | set(project_paths))
            )
            approval_gated_paths = ()
        payload = {
            "roles": {
                role: {
                    "can_write": boundary.can_write,
                    "can_execute_command": boundary.can_execute_command,
                    "prohibited_actions": sorted(boundary.prohibited_actions),
                }
                for role, boundary in sorted(role_boundaries.items())
            },
            "mode": mode.value,
            "protected_paths": list(protected_paths),
            "approval_gated_paths": list(approval_gated_paths),
            **BASELINE_POLICY,
        }
        policy_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return EffectiveGovernancePolicy(
            role_boundaries=role_boundaries,
            protected_paths=protected_paths,
            approval_gated_paths=approval_gated_paths,
            fail_closed=True,
            default_deny=True,
            policy_hash=policy_hash,
        )


def _effective_role_boundaries(
    restrictions: Mapping[str, RoleBoundary],
) -> dict[str, RoleBoundary]:
    unknown = set(restrictions) - set(BASELINE_ROLE_BOUNDARIES)
    if unknown:
        raise GovernancePolicyError(
            "ROLE_NOT_AUTHORIZED",
            f"project governance references unknown roles: {sorted(unknown)}",
        )
    return {
        role: baseline.tighten(restrictions.get(role, baseline))
        for role, baseline in BASELINE_ROLE_BOUNDARIES.items()
    }


def _normalize_policy_pattern(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    literal = normalized.replace("**", "x").replace("*", "x")
    if _unsafe_repository_path(literal):
        raise GovernancePolicyError(
            "CONFIG_INVALID", f"unsafe governance path pattern: {value}"
        )
    return PurePosixPath(normalized).as_posix()


def _normalize_governed_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    if _unsafe_repository_path(normalized):
        raise GovernancePolicyError(
            "PATH_POLICY_VIOLATION", f"unsafe governed path: {value}"
        )
    return PurePosixPath(normalized).as_posix()


def _policy_pattern_matches(pattern: str, path: str) -> bool:
    # Windows governed files must remain protected when checked elsewhere.
    pattern, path = pattern.casefold(), path.casefold()
    if pattern.startswith("**/"):
        tail = pattern[3:]
        if tail.endswith("/**"):
            directory = tail[:-3]
            return directory in path.split("/")
        return fnmatchcase(path, f"*{tail}")
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(f"{prefix}/")
    if pattern.startswith("**."):
        return path.endswith(pattern[2:])
    return fnmatchcase(path, pattern)


# Public alias for the authorization kernel (ADR-0011).
policy_pattern_matches = _policy_pattern_matches


__all__ = [
    "BASELINE_POLICY",
    "BASELINE_PROTECTED_PATHS",
    "BASELINE_ROLE_BOUNDARIES",
    "EffectiveGovernancePolicy",
    "GovernanceMode",
    "GovernancePolicyCompiler",
    "GovernancePolicyError",
    "PathAccessDecision",
    "RoleBoundary",
    "policy_pattern_matches",
]
