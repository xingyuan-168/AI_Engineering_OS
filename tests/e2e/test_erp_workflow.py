from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from codex_ai_os.pilots.erp_run import ErpPilotRunner


def test_empty_repository_reaches_audited_erp_release_candidate(tmp_path: Path) -> None:
    report = ErpPilotRunner().run(tmp_path / "erp-pilot")

    assert report.acceptance == {
        "PA-001": "passed",
        "PA-002": "passed",
        "PA-003": "passed",
        "PA-004": "passed",
        "PA-005": "passed",
        "PA-006": "passed",
        "PA-007": "passed:sandbox-unavailable-blocked",
        "PA-008": "passed",
        "PA-009": "passed",
        "PA-010": "passed",
    }
    assert len(report.task_commits) == 8
    assert len(set(report.task_commits)) == 8
    assert len(report.release_paths) == 5
    assert all(path.startswith("dist/release-candidate/") for path in report.release_paths)
    assert _git(report.project_root, "status", "--porcelain") == ""

    database = report.project_root / ".codex-os" / "state" / "state.db"
    with sqlite3.connect(database) as connection:
        run_status = connection.execute(
            "SELECT run_status FROM workflow_runs WHERE id = ?", (report.run_id,)
        ).fetchone()[0]
        task_count = connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE run_id = ?", (report.run_id,)
        ).fetchone()[0]
        handoff_count = connection.execute(
            "SELECT COUNT(*) FROM handoffs WHERE run_id = ?", (report.run_id,)
        ).fetchone()[0]
        approval_count = connection.execute(
            "SELECT COUNT(*) FROM approvals WHERE run_id = ?", (report.run_id,)
        ).fetchone()[0]
        worktree_count = connection.execute(
            "SELECT COUNT(*) FROM worktrees WHERE run_id = ?", (report.run_id,)
        ).fetchone()[0]
    assert run_status == "completed"
    assert (task_count, handoff_count, approval_count, worktree_count) == (8, 8, 5, 8)


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
