"""Unit tests for the governance authorization kernel (ADR-0011)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codex_ai_os.application.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
    GovernanceAuthorizationKernel,
)
from codex_ai_os.application.governance_policy import (
    GovernanceMode,
    GovernancePolicyCompiler,
    governed_path_allowed,
)


@pytest.fixture(scope="module")
def policy_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A minimal initialized project root with profiles and gates."""

    root = tmp_path_factory.mktemp("auth-kernel")
    return root


@pytest.fixture(scope="module")
def kernel(policy_root: Path) -> GovernanceAuthorizationKernel:
    compiler = GovernancePolicyCompiler(policy_root)
    policy = compiler.compile(("backend-project",))
    return GovernanceAuthorizationKernel(policy)


@pytest.fixture(scope="module")
def maintenance_kernel(policy_root: Path) -> GovernanceAuthorizationKernel:
    compiler = GovernancePolicyCompiler(policy_root)
    policy = compiler.compile(("backend-project",), mode=GovernanceMode.MAINTENANCE)
    return GovernanceAuthorizationKernel(policy)


class TestRoleBoundaries:
    def test_unknown_role_is_denied(self, kernel: GovernanceAuthorizationKernel) -> None:
        outcome = kernel.authorize(
            AuthorizationRequest(
                principal="ghost",
                operation="write",
                tool="apply_patch",
                paths=("docs/notes.md",),
            )
        )
        assert outcome.decision is AuthorizationDecision.DENY
        assert outcome.rule_id == "ROLE_NOT_AUTHORIZED"

    def test_reviewer_cannot_write(self, kernel: GovernanceAuthorizationKernel) -> None:
        outcome = kernel.authorize(
            AuthorizationRequest(
                principal="reviewer",
                operation="write",
                tool="apply_patch",
                paths=("src/module.py",),
            )
        )
        assert outcome.decision is AuthorizationDecision.DENY
        assert outcome.rule_id == "ROLE_BOUNDARY_WRITE"

    def test_reviewer_cannot_execute(self, kernel: GovernanceAuthorizationKernel) -> None:
        outcome = kernel.authorize(
            AuthorizationRequest(
                principal="reviewer",
                operation="execute",
                tool="shell",
                command="git status",
            )
        )
        assert outcome.decision is AuthorizationDecision.DENY
        assert outcome.rule_id == "ROLE_BOUNDARY_EXECUTE"

    def test_backend_engineer_can_write(self, kernel: GovernanceAuthorizationKernel) -> None:
        outcome = kernel.authorize(
            AuthorizationRequest(
                principal="backend-engineer",
                operation="write",
                tool="apply_patch",
                paths=("src/codex_ai_os/application/service.py",),
            )
        )
        assert outcome.decision is AuthorizationDecision.ALLOW


class TestWritePolicy:
    def test_protected_path_is_denied(self, kernel: GovernanceAuthorizationKernel) -> None:
        outcome = kernel.authorize(
            AuthorizationRequest(
                principal="backend-engineer",
                operation="write",
                tool="apply_patch",
                paths=(".git/config",),
            )
        )
        assert outcome.decision is AuthorizationDecision.DENY
        assert outcome.rule_id == "PATH_POLICY_VIOLATION"
        assert ".git/config" in outcome.denied_paths

    def test_env_file_is_denied(self, kernel: GovernanceAuthorizationKernel) -> None:
        outcome = kernel.authorize(
            AuthorizationRequest(
                principal="backend-engineer",
                operation="write",
                tool="apply_patch",
                paths=("configs/.env",),
            )
        )
        assert outcome.decision is AuthorizationDecision.DENY

    def test_unsafe_path_is_denied(self, kernel: GovernanceAuthorizationKernel) -> None:
        for path in ("../outside.md", "C:/Windows/system32", ""):
            outcome = kernel.authorize(
                AuthorizationRequest(
                    principal="backend-engineer",
                    operation="write",
                    tool="apply_patch",
                    paths=(path,),
                )
            )
            assert outcome.decision is AuthorizationDecision.DENY, path

    def test_governance_rule_path_denied_for_external_sources_in_runtime(
        self, kernel: GovernanceAuthorizationKernel
    ) -> None:
        outcome = kernel.authorize(
            AuthorizationRequest(
                principal="backend-engineer",
                operation="write",
                tool="apply_patch",
                source="hook",
                paths=("profiles/backend-project.yaml",),
            )
        )
        assert outcome.decision is AuthorizationDecision.DENY
        assert "profiles/backend-project.yaml" in outcome.denied_paths

    def test_governance_rule_path_asks_in_maintenance_mode(
        self, maintenance_kernel: GovernanceAuthorizationKernel
    ) -> None:
        outcome = maintenance_kernel.authorize(
            AuthorizationRequest(
                principal="backend-engineer",
                operation="write",
                tool="apply_patch",
                source="hook",
                paths=("profiles/backend-project.yaml",),
            )
        )
        assert outcome.decision is AuthorizationDecision.ASK
        assert outcome.rule_id == "GOVERNANCE_RULE_APPROVAL"
        assert "profiles/backend-project.yaml" in outcome.ask_paths

    def test_internal_source_passes_governance_paths_in_maintenance_mode(
        self, maintenance_kernel: GovernanceAuthorizationKernel
    ) -> None:
        outcome = maintenance_kernel.authorize(
            AuthorizationRequest(
                principal="backend-engineer",
                operation="write",
                tool="initializer",
                source="internal",
                paths=(".codex-os/execution-policy.yaml",),
            )
        )
        assert outcome.decision is AuthorizationDecision.ALLOW

    def test_internal_source_cannot_touch_sensitive_paths(
        self, maintenance_kernel: GovernanceAuthorizationKernel
    ) -> None:
        outcome = maintenance_kernel.authorize(
            AuthorizationRequest(
                principal="backend-engineer",
                operation="write",
                tool="initializer",
                source="internal",
                paths=(".codex-os/state/state.db",),
            )
        )
        assert outcome.decision is AuthorizationDecision.DENY

    def test_task_scope_violation_is_denied(
        self, kernel: GovernanceAuthorizationKernel
    ) -> None:
        outcome = kernel.authorize(
            AuthorizationRequest(
                principal="backend-engineer",
                operation="write",
                tool="apply_patch",
                task_id="task-1",
                paths=("src/codex_ai_os/frontend/widget.py",),
            ),
            task_allowed_paths=("src/codex_ai_os/application/",),
        )
        assert outcome.decision is AuthorizationDecision.DENY
        assert "src/codex_ai_os/frontend/widget.py" in outcome.denied_paths

    def test_task_scope_inside_allowance_is_allowed(
        self, kernel: GovernanceAuthorizationKernel
    ) -> None:
        outcome = kernel.authorize(
            AuthorizationRequest(
                principal="backend-engineer",
                operation="write",
                tool="apply_patch",
                task_id="task-1",
                paths=("src/codex_ai_os/application/service.py",),
            ),
            task_allowed_paths=("src/codex_ai_os/application/",),
        )
        assert outcome.decision is AuthorizationDecision.ALLOW

    def test_denied_path_wins_over_ask(self, kernel: GovernanceAuthorizationKernel) -> None:
        outcome = kernel.authorize(
            AuthorizationRequest(
                principal="backend-engineer",
                operation="write",
                tool="apply_patch",
                source="hook",
                paths=("profiles/backend-project.yaml", ".git/config"),
            )
        )
        assert outcome.decision is AuthorizationDecision.DENY


class TestExecutePolicy:
    def test_dangerous_commands_are_denied(
        self, kernel: GovernanceAuthorizationKernel
    ) -> None:
        for command in (
            "git push --force origin main",
            "git reset --hard HEAD~1",
            "rm -rf /",
            "pip install requests",
            "docker volume prune -f",
        ):
            outcome = kernel.authorize(
                AuthorizationRequest(
                    principal="backend-engineer",
                    operation="execute",
                    tool="shell",
                    command=command,
                )
            )
            assert outcome.decision is AuthorizationDecision.DENY, command

    def test_benign_command_is_allowed(self, kernel: GovernanceAuthorizationKernel) -> None:
        outcome = kernel.authorize(
            AuthorizationRequest(
                principal="backend-engineer",
                operation="execute",
                tool="shell",
                command="git status --porcelain",
            )
        )
        assert outcome.decision is AuthorizationDecision.ALLOW

    def test_transition_is_allowed_for_known_roles(
        self, kernel: GovernanceAuthorizationKernel
    ) -> None:
        outcome = kernel.authorize(
            AuthorizationRequest(
                principal="backend-engineer",
                operation="transition",
                tool="mcp:approve",
            )
        )
        assert outcome.decision is AuthorizationDecision.ALLOW

    def test_unknown_operation_is_denied(self, kernel: GovernanceAuthorizationKernel) -> None:
        outcome = kernel.authorize(
            AuthorizationRequest(
                principal="backend-engineer",
                operation="teleport",
                tool="shell",
            )
        )
        assert outcome.decision is AuthorizationDecision.DENY
        assert outcome.rule_id == "OPERATION_INVALID"


class TestSharedPathMatcher:
    def test_prefix_and_exact_matches(self) -> None:
        allowed = ("src/codex_ai_os/application/", "pyproject.toml")
        assert governed_path_allowed("src/codex_ai_os/application/service.py", allowed)
        assert governed_path_allowed("pyproject.toml", allowed)
        assert not governed_path_allowed("src/codex_ai_os/domain/service.py", allowed)
        assert not governed_path_allowed("pyproject.toml.bak", allowed)

    def test_unsafe_paths_never_match(self) -> None:
        allowed = ("src/",)
        assert not governed_path_allowed("../secret.md", allowed)
        assert not governed_path_allowed("/etc/passwd", allowed)
        assert not governed_path_allowed("C:/Windows/system32", allowed)

    def test_backslash_normalization(self) -> None:
        allowed = ("src/codex_ai_os/application/",)
        assert governed_path_allowed("src\\codex_ai_os\\application\\service.py", allowed)
