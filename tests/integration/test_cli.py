from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

from typer.testing import CliRunner

from codex_ai_os.cli.app import app

runner = CliRunner()


def _json_output(output: str) -> dict[str, Any]:
    raw: object = json.loads(output)
    return cast(dict[str, Any], raw)


def test_init_status_and_check_docs_json_contract(tmp_path: Path) -> None:
    init_result = runner.invoke(
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
            "--json",
        ],
    )
    assert init_result.exit_code == 0, init_result.output
    init_payload = _json_output(init_result.output)
    assert init_payload["ok"] is True
    assert init_payload["data"]["project_id"] == "PROJECT-CLI"

    status_result = runner.invoke(app, ["status", str(tmp_path), "--json"])
    assert status_result.exit_code == 0, status_result.output
    status_payload = _json_output(status_result.output)
    assert status_payload["data"]["schema_version"] == "0006"
    assert status_payload["data"]["events"] == 1

    docs_result = runner.invoke(app, ["check-docs", str(tmp_path), "--json"])
    assert docs_result.exit_code == 0, docs_result.output
    assert _json_output(docs_result.output)["ok"] is True


def test_check_docs_uses_exit_code_10_for_missing_document(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "init",
            str(tmp_path),
            "--project-id",
            "PROJECT-CLI",
            "--project-type",
            "backend",
        ],
    )
    assert result.exit_code == 0
    (tmp_path / "docs" / "API_SPEC.md").unlink()

    check = runner.invoke(app, ["check-docs", str(tmp_path), "--json"])

    assert check.exit_code == 10
    payload = _json_output(check.output)
    assert payload["error"]["code"] == "DOCS_INCOMPLETE"
    assert "docs/API_SPEC.md" in payload["error"]["details"]["missing"]


def test_workflow_cli_returns_dual_state_and_next_action(tmp_path: Path) -> None:
    initialized = runner.invoke(
        app,
        [
            "init",
            str(tmp_path),
            "--project-id",
            "PROJECT-CLI-WORKFLOW",
            "--project-type",
            "backend",
        ],
    )
    assert initialized.exit_code == 0
    _use_legacy_fixture_config(tmp_path)
    _initialize_git_repository(tmp_path)

    started = runner.invoke(
        app,
        [
            "run",
            "new-project",
            "--goal",
            "Build ERP procurement API",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )
    assert started.exit_code == 0, started.output
    payload = _json_output(started.output)
    assert payload["workflow_phase"] == "intake"
    assert payload["run_status"] == "running"
    assert payload["next_action"]["kind"] == "model_task"
    assert payload["next_action"]["branch"].startswith("agent/product-manager/")
    assert ".worktrees" in payload["next_action"]["worktree"]

    stepped = runner.invoke(
        app,
        [
            "step",
            str(payload["run_id"]),
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )
    assert stepped.exit_code == 0
    assert _json_output(stepped.output)["next_action"] == payload["next_action"]


def test_approval_cli_refuses_gate_before_evidence(tmp_path: Path) -> None:
    runner.invoke(
        app,
        [
            "init",
            str(tmp_path),
            "--project-id",
            "PROJECT-CLI-WORKFLOW",
            "--project-type",
            "backend",
        ],
    )
    _use_legacy_fixture_config(tmp_path)
    _initialize_git_repository(tmp_path)
    started = runner.invoke(
        app,
        [
            "run",
            "new-project",
            "--goal",
            "Build ERP procurement API",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )
    run_id = str(_json_output(started.output)["run_id"])

    approval = runner.invoke(
        app,
        [
            "approve",
            run_id,
            "--gate",
            "G0",
            "--reason",
            "too early",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )

    assert approval.exit_code == 20
    assert _json_output(approval.output)["error"]["code"] == "APPROVAL_REQUIRED"


def _initialize_git_repository(root: Path) -> None:
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "test: initialize CLI project")


def _use_legacy_fixture_config(root: Path) -> None:
    config = root / ".codex-os" / "project.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "schema_version: '1.1'", "schema_version: '1.0'"
        ),
        encoding="utf-8",
    )


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()
