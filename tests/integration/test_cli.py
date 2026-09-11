from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

from typer.testing import CliRunner

from codex_ai_os.cli.app import app

runner = CliRunner()


def _json_output(output: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(output))


def _git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def test_init_creates_project_and_reports_blockers(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "init",
            str(tmp_path),
            "--project-id",
            "PROJECT-CLI",
            "--name",
            "CLI pilot",
            "--project-type",
            "backend",
            "--with",
            "frontend_design",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = _json_output(result.output)
    assert payload["ok"] is True
    assert payload["data"]["project_id"] == "PROJECT-CLI"
    assert payload["data"]["documents_ok"] is True
    assert payload["data"]["repository_ready"] is False
    assert payload["data"]["repository_blockers"] == ["NOT_GIT_REPOSITORY"]
    assert (tmp_path / "docs" / "design" / "PROTOTYPE.html").is_file()


def test_check_blocks_without_git(tmp_path: Path) -> None:
    runner.invoke(app, ["init", str(tmp_path), "--project-id", "PROJECT-CHK", "--json"])
    result = runner.invoke(app, ["check", str(tmp_path), "--json"])
    assert result.exit_code == 40
    payload = _json_output(result.output)
    assert payload["ok"] is False
    details = payload["error"]["details"]
    assert details["repository"]["repository_ready"] is False
    assert details["documents"]["ok"] is True


def test_check_passes_when_github_is_ready(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import codex_ai_os.application.repository as repository_module

    monkeypatch.setattr(repository_module, "_github_findings", lambda git, hosts: [])
    runner.invoke(app, ["init", str(tmp_path), "--project-id", "PROJECT-OK", "--json"])
    _git_repo(tmp_path)
    result = runner.invoke(app, ["check", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = _json_output(result.output)
    assert payload["data"]["repository"]["repository_ready"] is True
    assert payload["data"]["documents"]["ok"] is True


def test_finish_gate_blocks_then_passes(tmp_path: Path) -> None:
    runner.invoke(app, ["init", str(tmp_path), "--project-id", "PROJECT-FIN", "--json"])
    _git_repo(tmp_path)
    blocked = runner.invoke(app, ["finish", str(tmp_path), "--json"])
    assert blocked.exit_code == 40
    payload = _json_output(blocked.output)
    codes = {finding["code"] for finding in payload["error"]["details"]["findings"]}
    assert "MEMORY_MISSING" in codes
    failing = runner.invoke(
        app,
        [
            "finish",
            str(tmp_path),
            "--test-command",
            'python -c "import sys; sys.exit(3)"',
            "--memory-not-needed",
            "--json",
        ],
    )
    assert failing.exit_code == 40, failing.output
    failed_codes = {
        finding["code"] for finding in _json_output(failing.output)["error"]["details"]["findings"]
    }
    assert "TEST_COMMAND_FAILED" in failed_codes
    passing = runner.invoke(
        app,
        [
            "finish",
            str(tmp_path),
            "--test-command",
            'python -c "pass"',
            "--memory-not-needed",
            "--json",
        ],
    )
    assert passing.exit_code == 0, passing.output
    assert _json_output(passing.output)["data"]["allowed"] is True


def test_memory_candidate_loop_via_cli(tmp_path: Path) -> None:
    runner.invoke(app, ["init", str(tmp_path), "--project-id", "PROJECT-MEM", "--json"])
    recorded = runner.invoke(
        app,
        [
            "memory",
            "record",
            "--title",
            "Loop closes",
            "--summary",
            "candidate accept works",
            "--source",
            "src/a.py",
            "--candidate",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )
    assert recorded.exit_code == 0, recorded.output
    candidate_id = _json_output(recorded.output)["data"]["entry"]["id"]
    listed = runner.invoke(
        app,
        ["memory", "candidates", "--project-root", str(tmp_path), "--json"],
    )
    assert listed.exit_code == 0, listed.output
    assert len(_json_output(listed.output)["data"]["results"]) == 1
    accepted = runner.invoke(
        app,
        [
            "memory",
            "candidate",
            candidate_id,
            "--accept",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )
    assert accepted.exit_code == 0, accepted.output
    payload = _json_output(accepted.output)["data"]
    assert payload["accepted"] is True
    assert payload["entry"]["title"] == "Loop closes"
    listed_again = runner.invoke(
        app,
        ["memory", "candidates", "--project-root", str(tmp_path), "--json"],
    )
    assert _json_output(listed_again.output)["data"]["results"] == []
    rejected = runner.invoke(
        app,
        [
            "memory",
            "record",
            "--title",
            "Drop me",
            "--summary",
            "s",
            "--source",
            "src/a.py",
            "--candidate",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )
    assert rejected.exit_code == 0, rejected.output
    drop_id = _json_output(rejected.output)["data"]["entry"]["id"]
    dropped = runner.invoke(
        app,
        [
            "memory",
            "candidate",
            drop_id,
            "--reject",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )
    assert dropped.exit_code == 0, dropped.output
    missing = runner.invoke(
        app,
        [
            "memory",
            "candidate",
            "MEM-nope",
            "--accept",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )
    assert missing.exit_code == 2
    assert _json_output(missing.output)["error"]["code"] == "MEMORY_CANDIDATE_MISSING"


def test_worktree_lifecycle_via_cli(tmp_path: Path) -> None:
    runner.invoke(app, ["init", str(tmp_path), "--project-id", "PROJECT-WT", "--json"])
    _git_repo(tmp_path)
    prepared = runner.invoke(
        app, ["worktree", "prepare", "demo", "--project-root", str(tmp_path), "--json"]
    )
    assert prepared.exit_code == 0, prepared.output
    payload = _json_output(prepared.output)
    assert payload["data"]["branch"] == "codex/wt-demo"
    finished = runner.invoke(
        app, ["worktree", "finish", "demo", "--project-root", str(tmp_path), "--json"]
    )
    assert finished.exit_code == 0, finished.output
    cleaned = runner.invoke(
        app, ["worktree", "cleanup", "demo", "--project-root", str(tmp_path), "--json"]
    )
    assert cleaned.exit_code == 0, cleaned.output
    listing = runner.invoke(
        app, ["worktree", "list", "--project-root", str(tmp_path), "--json"]
    )
    assert _json_output(listing.output)["data"]["results"] == []


def test_memory_cli_round_trip(tmp_path: Path) -> None:
    runner.invoke(app, ["init", str(tmp_path), "--project-id", "PROJECT-MEM", "--json"])
    recorded = runner.invoke(
        app,
        [
            "memory",
            "record",
            "--title",
            "Prefer narrow gates",
            "--summary",
            "Keep the governance surface small and stateless.",
            "--source",
            "docs/GOVERNANCE_RULES.md",
            "--type",
            "lesson",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )
    assert recorded.exit_code == 0, recorded.output
    searched = runner.invoke(
        app, ["memory", "search", "gates", "--project-root", str(tmp_path), "--json"]
    )
    assert searched.exit_code == 0, searched.output
    results = _json_output(searched.output)["data"]["results"]
    assert any(item["title"] == "Prefer narrow gates" for item in results)
    reindexed = runner.invoke(
        app, ["memory", "reindex", "--project-root", str(tmp_path), "--json"]
    )
    assert reindexed.exit_code == 0, reindexed.output
