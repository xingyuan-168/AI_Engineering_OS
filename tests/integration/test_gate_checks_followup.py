from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

from codex_ai_os.adapters.git import GitRunner
from codex_ai_os.cli.mcp_server import governance_check
from codex_ai_os.core.checks import _diff_check_findings, _ruff_findings


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=True, encoding="utf-8"
    ).stdout.strip()


def test_mcp_does_not_invent_change_class(governed_repo, monkeypatch):
    monkeypatch.setattr("codex_ai_os.core.gates._github_findings", lambda *a, **k: [])
    source = governed_repo / "src/module.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    result = asyncio.run(
        governance_check(str(governed_repo), "finish", base_ref="HEAD", memory_not_needed=True)
    )
    assert result["data"]["allowed"] is False
    assert "CODE_START_UNVERIFIED" in result["data"]["blocked_by"]
    result = asyncio.run(governance_check(str(governed_repo), "start"))
    assert result["ok"] is False and result["error"]["code"] == "GATE_INPUT_INVALID"


@pytest.mark.parametrize("formal", [False, True])
def test_cli_and_mcp_missing_type_agree(governed_repo, formal):
    from typer.testing import CliRunner

    from codex_ai_os.cli.app import app

    path = governed_repo / ("src/module.py" if formal else "docs/note.md")
    path.parent.mkdir(exist_ok=True)
    path.write_text("# fixture\n", encoding="utf-8")
    cli = CliRunner().invoke(
        app,
        [
            "finish",
            str(governed_repo),
            "--base-ref",
            "HEAD",
            "--memory-not-needed",
            "--json",
        ],
    )
    mcp = asyncio.run(
        governance_check(
            str(governed_repo),
            "finish",
            base_ref="HEAD",
            memory_not_needed=True,
        )
    )
    payload = json.loads(cli.output)
    cli_data = payload.get("data") or payload["error"]["details"]
    assert cli_data["allowed"] == mcp["data"]["allowed"] == (not formal)
    assert cli_data["blocked_by"] == mcp["data"]["blocked_by"]


@pytest.mark.parametrize(
    ("filename", "config"),
    [
        ("pyproject.toml", '[tool.ruff.lint]\nselect = ["F"]\n'),
        ("ruff.toml", '[lint]\nselect = ["F"]\n'),
        (".ruff.toml", '[lint]\nselect = ["F"]\n'),
    ],
)
def test_all_ruff_config_forms_enforce_real_errors(tmp_path, filename, config):
    (tmp_path / filename).write_text(config, encoding="utf-8")
    (tmp_path / "main.py").write_text("print(undefined_name)\n", encoding="utf-8")
    assert any(f.blocking and f.code == "RUFF_FAILED" for f in _ruff_findings(tmp_path))


def test_ruff_invalid_config_does_not_skip(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff.lint\n", encoding="utf-8")
    assert any(f.blocking for f in _ruff_findings(tmp_path))


def test_whitespace_failure_preserves_actual_diagnostics(governed_repo):
    path = governed_repo / "readme.md"
    path.write_text("baseline\n", encoding="utf-8")
    git(governed_repo, "add", "readme.md")
    git(governed_repo, "commit", "-qm", "fixture text")
    path.write_text("trailing whitespace  \n", encoding="utf-8")
    issue = next(
        f
        for f in _diff_check_findings(GitRunner(governed_repo), "HEAD")
        if f.code == "GIT_DIFF_CHECK"
    )
    assert issue.details is not None
    assert issue.details["stage"] == "unstaged"
    assert issue.details["exit_code"] == 2
    assert "readme.md:1:" in issue.details["stdout"]


def test_ruff_standalone_priority_and_missing_tool(tmp_path, monkeypatch):
    from codex_ai_os.core import checks

    (tmp_path / "pyproject.toml").write_text("invalid TOML [", encoding="utf-8")
    (tmp_path / "ruff.toml").write_text("invalid TOML [", encoding="utf-8")
    (tmp_path / ".ruff.toml").write_text('[lint]\nselect = ["F"]\n', encoding="utf-8")
    assert not _ruff_findings(tmp_path)
    monkeypatch.setattr(checks.importlib.util, "find_spec", lambda name: None)
    assert _ruff_findings(tmp_path)[0].code == "RUFF_UNAVAILABLE"


def test_diff_command_failure_preserves_stage_and_output(governed_repo, monkeypatch):
    original = GitRunner.run

    def fail(self, *args, **kwargs):
        if args[:2] == ("diff", "--check"):
            return subprocess.CompletedProcess(args, 128, "", "invalid revision D:/中文 dir")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(GitRunner, "run", fail)
    findings = _diff_check_findings(GitRunner(governed_repo), "HEAD")
    assert [f.code for f in findings] == ["GIT_DIFF_CHECK_FAILED"] * 3
    assert all(
        f.details and f.details["stderr"] == "invalid revision D:/中文 dir" for f in findings
    )
    assert [f.details["stage"] for f in findings if f.details] == [
        "committed",
        "staged",
        "unstaged",
    ]
