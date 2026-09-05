from pathlib import Path

import pytest

from codex_ai_os.application.governance_policy import (
    GovernanceMode,
    GovernancePolicyCompiler,
    GovernancePolicyError,
    PathAccessDecision,
    RoleBoundary,
)
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.domain.config import ProjectType
from codex_ai_os.domain.workflow import Gate

PROJECT_ROOT = Path(__file__).parents[2]


def test_baseline_policy_is_fail_closed_and_default_deny() -> None:
    policy = GovernancePolicyCompiler(PROJECT_ROOT).compile(("backend-project",))

    assert policy.fail_closed is True
    assert policy.default_deny is True
    with pytest.raises(GovernancePolicyError) as caught:
        policy.role_boundary("self-reported-owner")
    assert caught.value.code == "ROLE_NOT_AUTHORIZED"


@pytest.mark.parametrize("oci_first", [False, True])
def test_formal_g3_policy_requires_complete_offline_release_matrix(
    tmp_path: Path, oci_first: bool,
) -> None:
    ProjectInitializer().initialize(
        tmp_path, project_id="PROJECT-MATRIX", name="Matrix",
        project_type=ProjectType.BACKEND, schema_version="1.2" if oci_first else "1.1",
    )
    if not oci_first:
        # Model an existing project, not a newly initialized OCI-first project.
        config_path = tmp_path / ".codex-os" / "project.yaml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace("environment_mode: oci-first\n", ""),
            encoding="utf-8",
        )
    policy = GovernancePolicyCompiler(tmp_path).compile(("backend-project",))

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
    } | ({
        "compose-build", "container-recreate", "host-cleanliness",
        "storage-persistence", "environment-smoke",
    } if oci_first else set())


def test_frontend_g2_requires_validated_and_reviewed_html_prototype() -> None:
    policy = GovernancePolicyCompiler(PROJECT_ROOT).compile(("frontend-project",))
    requirements = policy.requirements_for(Gate.G2)

    assert "html-prototype" in requirements.artifact_types
    assert "html-prototype-validator" in requirements.checks
    assert "ux-prototype" in requirements.reviews


def test_oci_first_policy_adds_environment_evidence(tmp_path: Path) -> None:
    ProjectInitializer().initialize(
        tmp_path, project_id="PROJECT-OCI", name="OCI", project_type=ProjectType.BACKEND
    )
    policy = GovernancePolicyCompiler(tmp_path).compile(("backend-project",))

    assert "docs/ENVIRONMENT.md" in policy.requirements_for(Gate.G2).artifacts
    assert "environment-contract" in policy.requirements_for(Gate.G2).checks
    assert "container-recreate" in policy.requirements_for(Gate.G3).checks
    assert "environment-reconciliation" in policy.requirements_for(Gate.G4).records


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
    (
        "../profiles/backend-project.yaml", "C:/outside.txt", "/outside.txt",
        "C:outside.txt", r"C:\outside.txt", r"\\server\share\outside.txt",
        r"\\?\C:\outside.txt", "profiles/../outside.txt", ".", "./",
        "profiles/file.yaml:stream", "profiles./backend-project.yaml",
        "profiles /backend-project.yaml", "bad\x00path", "bad\ufffdpath",
        "CON.txt", "profiles/NUL",
    ),
)
def test_governed_paths_reject_absolute_and_traversal(unsafe: str) -> None:
    policy = GovernancePolicyCompiler(PROJECT_ROOT).compile(("backend-project",))

    with pytest.raises(GovernancePolicyError) as caught:
        policy.path_access(unsafe, mutating=True)
    assert caught.value.code == "PATH_POLICY_VIOLATION"


@pytest.mark.parametrize("unsafe", ["/outside/**", "C:/outside/**", "C:outside/**"])
def test_project_policy_patterns_reject_both_platforms_absolute_paths(unsafe: str) -> None:
    with pytest.raises(GovernancePolicyError) as caught:
        GovernancePolicyCompiler(PROJECT_ROOT).compile(
            ("backend-project",), additional_protected_paths=(unsafe,),
        )
    assert caught.value.code == "CONFIG_INVALID"


@pytest.mark.parametrize(
    "path", ["./profiles/backend-project.yaml", "Profiles/backend-project.yaml"],
)
def test_protected_paths_cannot_bypass_via_dot_or_windows_case(path: str) -> None:
    policy = GovernancePolicyCompiler(PROJECT_ROOT).compile(("backend-project",))
    assert policy.path_access(path, mutating=True) is PathAccessDecision.DENY


def test_portable_policy_keeps_unicode_relative_paths_available() -> None:
    policy = GovernancePolicyCompiler(PROJECT_ROOT).compile(("backend-project",))
    assert policy.path_access("src/运营模块/service.py", mutating=True) is PathAccessDecision.ALLOW


@pytest.mark.parametrize(
    "unsafe", ["/src/codex_ai_os/application/file.py", "C:/src/file.py", "C:src/file.py"],
)
def test_impact_paths_are_rejected_before_normalization(unsafe: str) -> None:
    with pytest.raises(GovernancePolicyError) as caught:
        GovernancePolicyCompiler(PROJECT_ROOT).task_blueprints(("backend-project",), (unsafe,))
    assert caught.value.code == "ROUTING_PATH_UNMAPPED"


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
