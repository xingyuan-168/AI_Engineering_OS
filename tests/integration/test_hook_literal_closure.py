"""Dangerous command text is test data; these tests never execute a payload."""

import pytest

from codex_ai_os.application.hook_gateway import explain_hook_payload


def explain(root, command, tool="Bash"):
    return explain_hook_payload(
        {"cwd": str(root), "tool_name": tool, "tool_input": {"command": command}}
    )


@pytest.mark.parametrize(
    "command",
    [
        "Write-Output 'git push --force origin main'",
        '# git push --force origin main\ngit --version',
        "<# git push --force origin main #> git --version",
        "$data = @'\ngit push --force origin main\n'@\ngit --version",
        'python -c "print(\'git push --force origin main\')"',
    ],
)
def test_dangerous_literals_are_not_operations(governed_repo, command):
    assert explain(governed_repo, command)["decision"] == "allow"


def test_patch_body_is_data_not_shell(governed_repo):
    command = "*** Add File: tests/fixture.py\n+COMMAND = 'git push --force origin main'\n"
    assert explain(governed_repo, command, "apply_patch")["decision"] == "allow"


@pytest.mark.parametrize(
    "command",
    [
        "git push --force origin main",
        "git.exe push origin main -f",
        "& 'C:/Program Files/Git/bin/git.exe' push --force-with-lease origin main",
        'pwsh -Command "git push --force origin main"',
        'cmd /c "git.exe push --force origin main"',
        "Write-Output 'harmless'; git push --force origin main",
        "$result = git push --force origin main",
    ],
)
def test_real_force_operations_still_deny_without_execution(governed_repo, command):
    result = explain(governed_repo, command)
    assert result["decision"] == "deny"
    assert result["rule_id"] == "HOST_GIT_FORCE_PUSH"


@pytest.mark.parametrize(
    "command",
    [
        "& $entry push --force origin main",
        'git -C "$target" reset --hard',
    ],
)
def test_unresolved_explicit_git_context_does_not_allow(governed_repo, command):
    assert explain(governed_repo, command)["decision"] == "deny"
