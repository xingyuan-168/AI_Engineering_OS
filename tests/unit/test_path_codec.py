# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codex_ai_os.application.doctor import DoctorService
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.workflow import WorkflowEngine
from codex_ai_os.cli.app import app
from codex_ai_os.domain.config import GitPushPolicy, ProjectType
from codex_ai_os.infrastructure.path_codec import (
    PathEncodingError,
    path_from_state,
    state_path,
)


def test_state_path_round_trips_unicode_and_rejects_replacement_character(
    tmp_path: Path,
) -> None:
    root = tmp_path / "中文项目" / "工作树"
    root.mkdir(parents=True)

    encoded = state_path(root)

    assert encoded == root.resolve().as_posix()
    assert path_from_state(encoded).resolve() == root.resolve()
    with pytest.raises(PathEncodingError, match=r"U\+FFFD"):
        state_path(tmp_path / "AI-\ufffd-OS")
    with pytest.raises(PathEncodingError, match=r"U\+FFFD"):
        path_from_state("D:/AI-\ufffd-OS")


def test_unicode_project_worktree_sqlite_and_cli_json_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "中文项目"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Unicode Fixture")
    _git(root, "config", "user.email", "unicode@example.invalid")
    ProjectInitializer().initialize(
        root,
        project_id="PROJECT-UNICODE",
        name="中文工程",
        project_type=ProjectType.BACKEND,
        git_push_policy=GitPushPolicy.FIXTURE_LOCAL_ONLY,
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "chore: initialize unicode fixture")

    started = WorkflowEngine(root).start("验证中文路径")
    assert started.active_task is not None
    assert started.active_task.worktree is not None
    expected_root = root.resolve().as_posix()
    expected_worktree = Path(started.active_task.worktree).resolve().as_posix()
    with WorkflowEngine(root, readonly=True).store.database.read_connection() as connection:
        project_root = str(
            connection.execute("SELECT root FROM projects WHERE id = 'PROJECT-UNICODE'").fetchone()[
                0
            ]
        )
        task_worktree = str(
            connection.execute(
                "SELECT worktree FROM tasks WHERE id = ?", (started.active_task.id,)
            ).fetchone()[0]
        )
    assert project_root == expected_root
    assert task_worktree == expected_worktree
    assert "中文项目" in task_worktree
    assert "\ufffd" not in task_worktree

    result = CliRunner().invoke(
        app,
        ["status", str(root), "--run-id", started.run.id, "--json"],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["next_action"]["worktree"] == expected_worktree
    assert "中文项目" in result.stdout
    assert "\ufffd" not in result.stdout


def test_doctor_reports_corrupt_path_record_without_guessing_recovery(tmp_path: Path) -> None:
    root = tmp_path / "doctor-project"
    root.mkdir()
    initialized = ProjectInitializer().initialize(
        root,
        project_id="PROJECT-CORRUPT-PATH",
        name="Corrupt path fixture",
        project_type=ProjectType.BACKEND,
        git_push_policy=GitPushPolicy.FIXTURE_LOCAL_ONLY,
    )
    with WorkflowEngine(root, readonly=True).store.database.connection() as connection:
        connection.execute(
            "UPDATE projects SET root = ? WHERE id = ?",
            ("D:/Projects/AI-\ufffd-OS", initialized.config.project_id),
        )
        connection.commit()

    check = DoctorService(root)._path_encoding_check()

    assert check.ok is False
    detail = json.loads(check.detail)
    assert detail["code"] == "PATH_ENCODING_CORRUPT"
    assert detail["records"] == [{"field": "root", "record": 1, "table": "projects"}]

    result = CliRunner().invoke(
        app,
        ["doctor", "--project-root", str(root), "--json"],
    )
    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "PATH_ENCODING_CORRUPT"
    path_check = next(
        item for item in payload["error"]["details"]["checks"] if item["name"] == "path-encoding"
    )
    assert path_check["ok"] is False


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
