"""Fail-closed path governance for the authorization kernel (ADR-0016).

The policy is deliberately small: protected path patterns only. It answers
"may this operation touch these paths" - nothing else. There are no
principal roles: Codex is one professional actor, and the kernel judges
operations, not job titles. Gate decisions live in ``codex_ai_os.core.gates``
as stateless evaluators.
"""

from __future__ import annotations

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


class PathAccessDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class EffectiveGovernancePolicy:
    protected_paths: tuple[str, ...]
    fail_closed: bool
    default_deny: bool

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
        return PathAccessDecision.ALLOW


# Governance facts of the runtime itself: only human-approved maintenance may
# change them from an external (hook) source.
GOVERNANCE_RULE_PATHS = (
    "AGENTS.md",
    ".codex-os/project.yaml",
    "plugins/ai-engineering-os/**",
)
# User assets and runtime state: never writable through governed channels.
# output/ is intentionally NOT protected: it is the final-deliverable
# directory, and its purity is governed by Finish/repository hygiene instead
# of a write ban (ADR-0016).
SENSITIVE_PROTECTED_PATHS = (
    "input/**",
    ".git/**",
    ".codex-os/state/**",
    "**.env",
    "**/credentials/**",
)
BASELINE_PROTECTED_PATHS = GOVERNANCE_RULE_PATHS + SENSITIVE_PROTECTED_PATHS

BASELINE_POLICY = {"fail_closed": True, "default_deny": True}


class GovernancePolicyCompiler:
    """Compile the effective fail-closed policy for one project."""

    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()

    def compile(
        self,
        *,
        additional_protected_paths: tuple[str, ...] = (),
    ) -> EffectiveGovernancePolicy:
        project_paths = tuple(
            _normalize_policy_pattern(path) for path in additional_protected_paths
        )
        protected_paths = tuple(
            sorted(set(BASELINE_PROTECTED_PATHS) | set(project_paths))
        )
        return EffectiveGovernancePolicy(
            protected_paths=protected_paths,
            fail_closed=True,
            default_deny=True,
        )

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
    "EffectiveGovernancePolicy",
    "GovernancePolicyCompiler",
    "GovernancePolicyError",
    "PathAccessDecision",
    "policy_pattern_matches",
]