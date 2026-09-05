"""Compile fail-closed core and Profile governance requirements."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath, PureWindowsPath

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from codex_ai_os.domain.config import EnvironmentMode
from codex_ai_os.domain.coordination import TaskBlueprint
from codex_ai_os.domain.profiles import ProfileTaskTemplate, ProjectProfile
from codex_ai_os.domain.workflow import Gate
from codex_ai_os.infrastructure.config import load_project_config
from codex_ai_os.infrastructure.profiles import load_project_profiles


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
class EvidenceRequirements:
    artifacts: frozenset[str] = frozenset()
    artifact_types: frozenset[str] = frozenset()
    checks: frozenset[str] = frozenset()
    reviews: frozenset[str] = frozenset()
    records: frozenset[str] = frozenset()
    source_commit_required: bool = True
    push_required: bool = True
    enforce_allowed_paths: bool = True

    def add(self, other: EvidenceRequirements) -> EvidenceRequirements:
        return EvidenceRequirements(
            artifacts=self.artifacts | other.artifacts,
            artifact_types=self.artifact_types | other.artifact_types,
            checks=self.checks | other.checks,
            reviews=self.reviews | other.reviews,
            records=self.records | other.records,
            source_commit_required=(
                self.source_commit_required or other.source_commit_required
            ),
            push_required=self.push_required or other.push_required,
            enforce_allowed_paths=(
                self.enforce_allowed_paths or other.enforce_allowed_paths
            ),
        )

    def payload(self) -> dict[str, object]:
        return {
            "artifacts": sorted(self.artifacts),
            "artifact_types": sorted(self.artifact_types),
            "checks": sorted(self.checks),
            "reviews": sorted(self.reviews),
            "records": sorted(self.records),
            "git": {
                "source_commit_required": self.source_commit_required,
                "push_required": self.push_required,
                "enforce_allowed_paths": self.enforce_allowed_paths,
            },
        }


class _GateGitModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_commit_required: bool = True
    push_required: bool = True
    enforce_allowed_paths: bool = True


class _GateRuleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(pattern=r"^1\.2$")
    gate: Gate
    documents: frozenset[str] = frozenset()
    required_artifacts: frozenset[str] = frozenset()
    required_artifact_types: frozenset[str] = frozenset()
    required_checks: frozenset[str] = frozenset()
    required_reviews: frozenset[str] = frozenset()
    required_records: frozenset[str] = frozenset()
    git: _GateGitModel = _GateGitModel()

    def requirements(self) -> EvidenceRequirements:
        return EvidenceRequirements(
            artifacts=self.documents | self.required_artifacts,
            artifact_types=self.required_artifact_types,
            checks=self.required_checks,
            reviews=self.required_reviews,
            records=self.required_records,
            source_commit_required=self.git.source_commit_required,
            push_required=self.git.push_required,
            enforce_allowed_paths=self.git.enforce_allowed_paths,
        )


@dataclass(frozen=True, slots=True)
class EffectiveGovernancePolicy:
    profiles: tuple[str, ...]
    gates: dict[Gate, EvidenceRequirements]
    additional_reviewers: tuple[str, ...]
    role_boundaries: dict[str, RoleBoundary]
    protected_paths: tuple[str, ...]
    approval_gated_paths: tuple[str, ...]
    fail_closed: bool
    default_deny: bool
    policy_hash: str

    def requirements_for(self, gate: Gate) -> EvidenceRequirements:
        return self.gates[gate]

    def gate_contract(
        self,
        gate: Gate,
        *,
        document_version: str,
        push_required: bool,
    ) -> dict[str, object]:
        requirements = self.requirements_for(gate)
        documents = {
            path: {
                "schema_version": "1.2",
                "document_version": document_version,
                "required_metadata": [
                    "schema_version",
                    "document_version",
                    "status",
                    "owner",
                    "requirement_refs",
                ],
                "allowed_statuses": [
                    "review-ready",
                    "accepted",
                    "approved",
                    "released",
                ],
                "placeholders": "forbid",
            }
            for path in sorted(requirements.artifacts)
            if path.casefold().endswith(".md")
        }
        payload: dict[str, object] = {
            "gate": gate.value,
            "policy_hash": self.policy_hash,
            "documents": documents,
            "required_artifacts": sorted(
                path
                for path in requirements.artifacts
                if not path.casefold().endswith(".md")
            ),
            "required_artifact_types": sorted(requirements.artifact_types),
            "required_checks": sorted(requirements.checks),
            "required_reviews": sorted(requirements.reviews),
            "required_records": sorted(requirements.records),
            "git": {
                "source_commit_required": requirements.source_commit_required,
                "push_required": requirements.push_required and push_required,
                "enforce_allowed_paths": requirements.enforce_allowed_paths,
            },
        }
        payload["requirements_hash"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return payload

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


# Adapted from the monotonic Baseline + Project policy in @cq/governance 0.1.0
# (MIT). The paths and roles below are AI Engineering OS facts, not CQ-OS paths.
BASELINE_POLICY = {"fail_closed": True, "default_deny": True}
GOVERNANCE_RULE_PATHS = (
    "AGENTS.md",
    ".codex-os/rules.md",
    ".codex-os/workflow.md",
    ".codex-os/execution-policy.yaml",
    ".codex-os/gates/**",
    "profiles/**",
    "plugins/ai-engineering-os/**",
)
SENSITIVE_PROTECTED_PATHS = (
    ".git/**",
    ".codex-os/state/**",
    "**.env",
    "**/credentials/**",
)
BASELINE_PROTECTED_PATHS = GOVERNANCE_RULE_PATHS + SENSITIVE_PROTECTED_PATHS

_PRODUCER_PROHIBITIONS = frozenset(
    {"self-approve", "delete-core-data", "publish-without-g4"}
)
BASELINE_ROLE_BOUNDARIES: dict[str, RoleBoundary] = {
    "project-manager": RoleBoundary(True, False, _PRODUCER_PROHIBITIONS),
    "product-manager": RoleBoundary(True, False, _PRODUCER_PROHIBITIONS),
    "architect": RoleBoundary(True, True, _PRODUCER_PROHIBITIONS),
    "backend-engineer": RoleBoundary(True, True, _PRODUCER_PROHIBITIONS),
    "frontend-engineer": RoleBoundary(True, True, _PRODUCER_PROHIBITIONS),
    "database-engineer": RoleBoundary(True, True, _PRODUCER_PROHIBITIONS),
    "qa": RoleBoundary(False, True, frozenset({"accept-own-evidence"})),
    "reviewer": RoleBoundary(False, False, frozenset({"produce-reviewed-change"})),
    "security-reviewer": RoleBoundary(
        False, False, frozenset({"produce-reviewed-change"})
    ),
    "release-manager": RoleBoundary(
        True, True, frozenset({"self-approve", "publish-without-g4"})
    ),
    "memory-manager": RoleBoundary(True, False, frozenset({"self-approve"})),
}


CORE_GATE_REQUIREMENTS: dict[Gate, EvidenceRequirements] = {}


class GovernancePolicyCompiler:
    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()
        self._profiles = self._load_profiles()
        self._baseline_gates = self.load_gate_rules(self._baseline_gate_dir())
        self._project_gates = self._load_project_gate_rules()
        self._environment_gates = self._load_environment_gate_rules()

    def compile(
        self,
        profile_names: tuple[str, ...],
        *,
        mode: GovernanceMode = GovernanceMode.RUNTIME,
        role_restrictions: Mapping[str, RoleBoundary] | None = None,
        additional_protected_paths: tuple[str, ...] = (),
    ) -> EffectiveGovernancePolicy:
        if not profile_names:
            raise GovernancePolicyError("CONFIG_INVALID", "governance policy requires a profile")
        unknown = set(profile_names) - set(self._profiles)
        if unknown:
            raise GovernancePolicyError(
                "CONFIG_INVALID",
                f"governance policy references unknown profiles: {sorted(unknown)}",
            )
        gates = dict(self._baseline_gates)
        for gate, project_requirement in self._project_gates.items():
            baseline = gates[gate]
            _require_monotonic_gate(gate, baseline, project_requirement)
            gates[gate] = project_requirement
        reviewers: list[str] = []
        for name in profile_names:
            profile = self._profiles[name]
            reviewers.extend(profile.additional_reviewers)
            for requirement in profile.gate_requirements:
                gate = Gate(requirement.gate)
                gates[gate] = gates[gate].add(
                    EvidenceRequirements(
                        artifacts=frozenset(requirement.artifacts),
                        artifact_types=frozenset(requirement.artifact_types),
                        checks=frozenset(requirement.checks),
                        reviews=frozenset(requirement.reviews),
                    )
                )
        for gate, environment_requirement in self._environment_gates.items():
            gates[gate] = gates[gate].add(environment_requirement)
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
            "profiles": list(profile_names),
            "gates": {gate.value: gates[gate].payload() for gate in Gate},
            "additional_reviewers": sorted(set(reviewers)),
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
            profiles=profile_names,
            gates=gates,
            additional_reviewers=tuple(sorted(set(reviewers))),
            role_boundaries=role_boundaries,
            protected_paths=protected_paths,
            approval_gated_paths=approval_gated_paths,
            fail_closed=True,
            default_deny=True,
            policy_hash=policy_hash,
        )

    def task_blueprints(
        self, profile_names: tuple[str, ...], impact_paths: tuple[str, ...]
    ) -> tuple[TaskBlueprint, ...]:
        if not impact_paths:
            raise GovernancePolicyError(
                "ROUTING_PATH_UNMAPPED",
                "approved impact paths are required before implementation tasks are created",
            )
        normalized = tuple(_normalize_impact_path(path) for path in impact_paths)
        templates: list[tuple[ProfileTaskTemplate, tuple[str, ...]]] = []
        seen_keys: set[str] = set()
        for profile_name in profile_names:
            profile = self._profiles.get(profile_name)
            if profile is None:
                raise GovernancePolicyError(
                    "CONFIG_INVALID", f"unknown project profile: {profile_name}"
                )
            for template in profile.task_templates:
                if template.key in seen_keys:
                    raise GovernancePolicyError(
                        "CONFIG_INVALID", f"duplicate task key across profiles: {template.key}"
                    )
                seen_keys.add(template.key)
                matched = tuple(
                    path
                    for path in normalized
                    if any(_matches(path, pattern) for pattern in template.impact_patterns)
                )
                if matched:
                    templates.append((template, matched))
        mapped = {path for _template, paths in templates for path in paths}
        unmapped = sorted(set(normalized) - mapped)
        if unmapped:
            raise GovernancePolicyError(
                "ROUTING_PATH_UNMAPPED",
                f"approved impact paths cannot be mapped safely: {unmapped}",
            )
        active_keys = {template.key for template, _paths in templates}
        blueprints: list[TaskBlueprint] = []
        for template, paths in templates:
            dependencies = [key for key in template.depends_on if key in active_keys]
            for prior in blueprints:
                if _paths_overlap(paths, prior.allowed_paths) and prior.key not in dependencies:
                    dependencies.append(prior.key)
            blueprints.append(
                TaskBlueprint(
                    key=template.key,
                    agent=template.agent,
                    skill=template.skill,
                    prompt=template.prompt,
                    allowed_paths=paths,
                    depends_on=tuple(dependencies),
                )
            )
        if not blueprints:
            raise GovernancePolicyError(
                "ROUTING_PATH_UNMAPPED", "approved impact paths produced no implementation tasks"
            )
        return tuple(blueprints)

    def _load_profiles(self) -> dict[str, ProjectProfile]:
        profile_dir = self.root / "profiles"
        if not profile_dir.is_dir():
            packaged_profiles = Path(__file__).parents[1] / "resources" / "profiles"
            repository_profiles = Path(__file__).parents[3] / "profiles"
            profile_dir = (
                packaged_profiles if packaged_profiles.is_dir() else repository_profiles
            )
        try:
            return load_project_profiles(profile_dir)
        except (OSError, ValueError) as exc:
            raise GovernancePolicyError(
                "CONFIG_INVALID", f"invalid governance profiles: {exc}"
            ) from exc

    def _baseline_gate_dir(self) -> Path:
        packaged = Path(__file__).parents[1] / "resources" / "gates"
        repository = Path(__file__).parents[3] / "gates"
        return packaged if packaged.is_dir() else repository

    def _load_project_gate_rules(self) -> dict[Gate, EvidenceRequirements]:
        directory = self.root / ".codex-os" / "gates"
        if not directory.exists():
            return dict(self._baseline_gates)
        return self.load_gate_rules(directory)

    def _load_environment_gate_rules(self) -> dict[Gate, EvidenceRequirements]:
        try:
            config = load_project_config(self.root)
        except (OSError, ValueError):
            return {}
        if config.environment_mode is not EnvironmentMode.OCI_FIRST:
            return {}
        baseline = self._baseline_gate_dir() / "oci-first"
        project = self.root / ".codex-os" / "gates" / "oci-first"
        baseline_rules = self.load_gate_rules(baseline, gates=(Gate.G2, Gate.G3, Gate.G4))
        if not project.is_dir():
            raise GovernancePolicyError(
                "CONFIG_INVALID", "OCI-first project is missing declarative environment Gate rules"
            )
        project_rules = self.load_gate_rules(project, gates=(Gate.G2, Gate.G3, Gate.G4))
        for gate, candidate in project_rules.items():
            _require_monotonic_gate(gate, baseline_rules[gate], candidate)
        return project_rules

    @staticmethod
    def load_gate_rules(
        directory: Path, *, gates: tuple[Gate, ...] = tuple(Gate)
    ) -> dict[Gate, EvidenceRequirements]:
        rules: dict[Gate, EvidenceRequirements] = {}
        for gate in gates:
            path = directory / f"{gate.value}.yaml"
            try:
                raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
                model = _GateRuleModel.model_validate(raw)
            except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
                raise GovernancePolicyError(
                    "CONFIG_INVALID", f"invalid or missing Gate policy {path}: {exc}"
                ) from exc
            if model.gate is not gate:
                raise GovernancePolicyError(
                    "CONFIG_INVALID",
                    f"Gate policy identity mismatch: {path} declares {model.gate.value}",
                )
            rules[gate] = model.requirements()
        return rules


def _require_monotonic_gate(
    gate: Gate,
    baseline: EvidenceRequirements,
    candidate: EvidenceRequirements,
) -> None:
    for field in ("artifacts", "artifact_types", "checks", "reviews", "records"):
        baseline_values = getattr(baseline, field)
        candidate_values = getattr(candidate, field)
        removed = baseline_values - candidate_values
        if removed:
            raise GovernancePolicyError(
                "CONFIG_INVALID",
                f"project {gate.value} policy relaxes baseline {field}: {sorted(removed)}",
            )
    for field in (
        "source_commit_required",
        "push_required",
        "enforce_allowed_paths",
    ):
        if getattr(baseline, field) and not getattr(candidate, field):
            raise GovernancePolicyError(
                "CONFIG_INVALID",
                f"project {gate.value} policy relaxes baseline git.{field}",
            )


def _normalize_impact_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    if _unsafe_repository_path(normalized) or normalized.startswith("."):
        raise GovernancePolicyError("ROUTING_PATH_UNMAPPED", f"unsafe impact path: {value}")
    return PurePosixPath(normalized).as_posix()


def _matches(path: str, pattern: str) -> bool:
    normalized_pattern = pattern.replace("\\", "/").strip("/")
    return fnmatchcase(path, normalized_pattern)


def _paths_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    for left_path in left:
        for right_path in right:
            if (
                left_path == right_path
                or left_path.startswith(f"{right_path.rstrip('/')}/")
                or right_path.startswith(f"{left_path.rstrip('/')}/")
            ):
                return True
    return False


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


def _unsafe_repository_path(value: str) -> bool:
    """Apply Windows and POSIX lexical rules regardless of the runtime host."""
    return (
        not value
        or PurePosixPath(value) == PurePosixPath(".")
        or PurePosixPath(value).is_absolute()
        or bool(PureWindowsPath(value).drive)
        or PureWindowsPath(value).is_reserved()
        or ":" in value  # Also excludes NTFS alternate data streams.
        or "\ufffd" in value
        or any(ord(character) < 32 for character in value)
        or any(
            part == ".." or (part not in {"", "."} and part.endswith((".", " ")))
            for part in value.split("/")
        )
    )


def _policy_pattern_matches(pattern: str, path: str) -> bool:
    # Windows governed files must remain protected when checked inside Linux OCI.
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


_PACKAGED_GATE_ROOT = Path(__file__).parents[1] / "resources" / "gates"
_REPOSITORY_GATE_ROOT = Path(__file__).parents[3] / "gates"
CORE_GATE_REQUIREMENTS.update(
    GovernancePolicyCompiler.load_gate_rules(
        _PACKAGED_GATE_ROOT
        if _PACKAGED_GATE_ROOT.is_dir()
        else _REPOSITORY_GATE_ROOT
    )
)


__all__ = [
    "BASELINE_POLICY",
    "BASELINE_PROTECTED_PATHS",
    "BASELINE_ROLE_BOUNDARIES",
    "CORE_GATE_REQUIREMENTS",
    "EffectiveGovernancePolicy",
    "EvidenceRequirements",
    "GovernanceMode",
    "GovernancePolicyCompiler",
    "GovernancePolicyError",
    "PathAccessDecision",
    "RoleBoundary",
]
