from __future__ import annotations

from pathlib import Path

from codex_ai_os.application.authorization import (
    HOST_COMMAND_RULES,
    AuthorizationDecision,
    AuthorizationRequest,
    GovernanceAuthorizationKernel,
)
from codex_ai_os.application.governance_policy import GovernancePolicyCompiler
from codex_ai_os.application.project import ProjectInitializer


def _kernel(tmp_path: Path) -> GovernanceAuthorizationKernel:
    ProjectInitializer().initialize(
        tmp_path,
        project_id="PROJECT-AUTH",
        name="Auth",
        project_type="generic",
        risk_level="low",
        include=frozenset(),
    )
    return GovernanceAuthorizationKernel(GovernancePolicyCompiler(tmp_path).compile())


def test_protected_paths_are_denied(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    outcome = kernel.authorize(
        AuthorizationRequest(
            principal="backend-engineer",
            operation="write",
            tool="apply_patch",
            paths=("input/spec.md",),
        )
    )
    assert outcome.decision is AuthorizationDecision.DENY


def test_regular_source_paths_are_allowed(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    outcome = kernel.authorize(
        AuthorizationRequest(
            principal="backend-engineer",
            operation="write",
            tool="apply_patch",
            paths=("src/app.py",),
        )
    )
    assert outcome.decision is AuthorizationDecision.ALLOW


def test_unknown_role_fails_closed_to_deny(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    outcome = kernel.authorize(
        AuthorizationRequest(
            principal="nobody",
            operation="write",
            tool="apply_patch",
            paths=("src/app.py",),
        )
    )
    assert outcome.decision is AuthorizationDecision.DENY


def test_host_rules_keep_dangerous_commands() -> None:
    patterns = [rule[0] for rule in HOST_COMMAND_RULES]
    dangerous = [
        "git push --force origin main",
        "git reset --hard HEAD~1",
        "git clean -fd",
        "cmd /c rd /s /q C:\\unsafe",
        "cmd /c del /f /s C:\\unsafe\\*",
        "powershell -NoProfile Remove-Item C:\\x -Recurse -Force",
        "git update-ref -d refs/heads/main",
    ]
    for command in dangerous:
        assert any(p.search(command) for p in patterns), command


def test_host_rules_release_engineering_commands() -> None:
    patterns = [rule[0] for rule in HOST_COMMAND_RULES]
    engineering = [
        "pip install requests",
        "python -m pip install requests",
        "npm install",
        "pnpm build",
        "yarn add react",
        "poetry install",
        "cargo build",
        "sed -i s/a/b/ file.txt",
    ]
    for command in engineering:
        assert not any(p.search(command) for p in patterns), command
