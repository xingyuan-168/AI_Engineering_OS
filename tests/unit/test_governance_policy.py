from pathlib import Path

import pytest

from codex_ai_os.application.governance_policy import (
    GovernanceMode,
    GovernancePolicyCompiler,
    GovernancePolicyError,
    PathAccessDecision,
    RoleBoundary,
)
from codex_ai_os.domain.workflow import Gate

PROJECT_ROOT = Path(__file__).parents[2]


def test_baseline_policy_is_fail_closed_and_default_deny() -> None:
    policy = GovernancePolicyCompiler(PROJECT_ROOT).compile(("backend-project",))

    assert policy.fail_closed is True
    assert policy.default_deny is True
    with pytest.raises(GovernancePolicyError) as caught:
        policy.role_boundary("self-reported-owner")
    assert caught.value.code == "ROLE_NOT_AUTHORIZED"


def test_formal_g3_policy_requires_complete_offline_release_matrix() -> None:
    policy = GovernancePolicyCompiler(PROJECT_ROOT).compile(("backend-project",))

    assert policy.requirements_for(Gate.G3).checks == {
        "pytest",
        "ruff",
        "pyright",
        "docs",
        "secret-scan",
        "dependency-audit",
        "bandit",
        "plugin-validator",
        "skill-validator",
        "agent-validator",
        "hook-fixture",
        "mcp-contract",
        "build-install",
        "security-scan",
        "real-oci",
        "image-sbom",
        "image-scan",
    }


def test_frontend_g2_requires_validated_and_reviewed_html_prototype() -> None:
    policy = GovernancePolicyCompiler(PROJECT_ROOT).compile(("frontend-project",))
    requirements = policy.requirements_for(Gate.G2)

    assert "html-prototype" in requirements.artifact_types
    assert "html-prototype-validator" in requirements.checks
    assert "ux-prototype" in requirements.reviews


def test_project_role_policy_can_tighten_but_cannot_raise_capabilities() -> None:
    policy = GovernancePolicyCompiler(PROJECT_ROOT).compile(
        ("backend-project",),
        role_restrictions={
            "backend-engineer": RoleBoundary(
                can_write=False,
                can_execute_command=True,
                prohibited_actions=frozenset({"project-only-operation"}),
            ),
            "reviewer": RoleBoundary(can_write=True, can_execute_command=True),
        },
    )

    backend = policy.role_boundary("backend-engineer")
    reviewer = policy.role_boundary("reviewer")
    assert backend.can_write is False
    assert backend.can_execute_command is True
    assert "project-only-operation" in backend.prohibited_actions
    assert reviewer.can_write is False
    assert reviewer.can_execute_command is False

    with pytest.raises(GovernancePolicyError) as caught:
        GovernancePolicyCompiler(PROJECT_ROOT).compile(
            ("backend-project",),
            role_restrictions={
                "unknown": RoleBoundary(can_write=False, can_execute_command=False)
            },
        )
    assert caught.value.code == "ROLE_NOT_AUTHORIZED"


def test_runtime_and_maintenance_path_policy_are_monotonic() -> None:
    compiler = GovernancePolicyCompiler(PROJECT_ROOT)
    runtime = compiler.compile(("backend-project",))
    maintenance = compiler.compile(
        ("backend-project",), mode=GovernanceMode.MAINTENANCE
    )

    assert runtime.path_access("profiles/backend-project.yaml", mutating=True) == (
        PathAccessDecision.DENY
    )
    assert maintenance.path_access(
        "profiles/backend-project.yaml", mutating=True
    ) == PathAccessDecision.APPROVAL_REQUIRED
    assert maintenance.path_access("secrets.env", mutating=True) == (
        PathAccessDecision.DENY
    )
    assert runtime.path_access("profiles/backend-project.yaml", mutating=False) == (
        PathAccessDecision.ALLOW
    )


@pytest.mark.parametrize(
    "unsafe",
    ("../profiles/backend-project.yaml", "C:/outside.txt", "/outside.txt"),
)
def test_governed_paths_reject_absolute_and_traversal(unsafe: str) -> None:
    policy = GovernancePolicyCompiler(PROJECT_ROOT).compile(("backend-project",))

    with pytest.raises(GovernancePolicyError) as caught:
        policy.path_access(unsafe, mutating=True)
    assert caught.value.code == "PATH_POLICY_VIOLATION"


def test_policy_hash_binds_mode_roles_and_project_paths() -> None:
    compiler = GovernancePolicyCompiler(PROJECT_ROOT)
    baseline = compiler.compile(("backend-project",))
    maintenance = compiler.compile(
        ("backend-project",), mode=GovernanceMode.MAINTENANCE
    )
    tightened = compiler.compile(
        ("backend-project",),
        role_restrictions={
            "backend-engineer": RoleBoundary(False, True)
        },
        additional_protected_paths=("project-policy/**",),
    )

    assert len({baseline.policy_hash, maintenance.policy_hash, tightened.policy_hash}) == 3
    assert tightened.path_access("project-policy/rule.yaml", mutating=True) == (
        PathAccessDecision.DENY
    )
