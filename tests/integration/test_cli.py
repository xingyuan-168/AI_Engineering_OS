from __future__ import annotations

import json
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
    assert status_payload["data"]["schema_version"] == "0001"
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
