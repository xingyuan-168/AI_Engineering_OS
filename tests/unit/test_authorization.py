from __future__ import annotations

from pathlib import Path

from codex_ai_os.application.authorization import (
    HOST_COMMAND_RULES,
    AuthorizationDecision,
    AuthorizationOperation,
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
        include=frozenset(),
    )
    return GovernanceAuthorizationKernel(GovernancePolicyCompiler(tmp_path).compile())


def test_input_is_denied(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    outcome = kernel.authorize(
        AuthorizationRequest(
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
            operation="write",
            tool="apply_patch",
            paths=("src/app.py",),
        )
    )
    assert outcome.decision is AuthorizationDecision.ALLOW


def test_output_directory_is_writable(tmp_path: Path) -> None:
    # output/ is the final-deliverable directory: its purity is governed by
    # Finish/repository hygiene, not by a write ban (P0-4).
    kernel = _kernel(tmp_path)
    outcome = kernel.authorize(
        AuthorizationRequest(
            operation="write",
            tool="apply_patch",
            paths=("output/report.md",),
        )
    )
    assert outcome.decision is AuthorizationDecision.ALLOW


def test_governance_rule_files_are_denied(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    for path in ("AGENTS.md", ".codex-os/project.yaml", "plugins/ai-engineering-os/x.py"):
        outcome = kernel.authorize(
            AuthorizationRequest(
                operation="write",
                tool="apply_patch",
                paths=(path,),
            )
        )
        assert outcome.decision is AuthorizationDecision.DENY, path


def test_env_and_state_are_denied(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    for path in (".env", "config/.env", ".codex-os/state/state.db"):
        outcome = kernel.authorize(
            AuthorizationRequest(
                operation="write",
                tool="apply_patch",
                paths=(path,),
            )
        )
        assert outcome.decision is AuthorizationDecision.DENY, path


def test_execute_engineering_commands_are_allowed(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    for command in ("pip install requests", "npm install", "cargo build", "ruff check src"):
        outcome = kernel.authorize(
            AuthorizationRequest(
                operation="execute",
                tool="shell",
                command=command,
            )
        )
        assert (
            outcome.decision is AuthorizationDecision.ALLOW
            and outcome.rule_id == "HOST_COMMAND_ALLOWED"
        ), command


def test_execute_destructive_commands_are_denied(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    for command in (
        "git push --force origin main",
        "git reset --hard HEAD~1",
        "git clean -fd",
    ):
        outcome = kernel.authorize(
            AuthorizationRequest(
                operation="execute",
                tool="shell",
                command=command,
            )
        )
        assert outcome.decision is AuthorizationDecision.DENY, command


def test_invalid_operation_fails_closed(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    outcome = kernel.authorize(
        AuthorizationRequest(
            operation="transition",
            tool="workflow",
        )
    )
    assert (
        outcome.decision is AuthorizationDecision.DENY
        and outcome.rule_id == "OPERATION_INVALID"
    )


def test_operations_are_only_write_and_execute() -> None:
    # Roles and workflow transitions are deleted: the kernel judges
    # operations, not principals (ADR-0016).
    assert set(AuthorizationOperation) == {
        AuthorizationOperation.WRITE,
        AuthorizationOperation.EXECUTE,
    }


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
