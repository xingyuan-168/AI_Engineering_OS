from __future__ import annotations

import json
import runpy
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codex_ai_os.adapters.git import GitRunner
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.cli.app import app
from codex_ai_os.cli.mcp_server import approval_record, governance_check, project_init
from codex_ai_os.core.gates import evaluate_frontend
from codex_ai_os.core.worktree import WorktreeManager
from codex_ai_os.domain.config import ProjectType
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.memory import MemoryStore, MemoryStoreError

ROOT = Path(__file__).resolve().parents[2]


def test_initializer_preserves_existing_input_assets(tmp_path: Path) -> None:
    inputs = tmp_path / "input"
    inputs.mkdir()
    asset = inputs / "用户资料.txt"
    asset.write_bytes(b"user-owned bytes\r\n")
    result = ProjectInitializer().initialize(
        tmp_path,
        project_id="PROJECT-INPUT",
        name="Test",
        project_type=ProjectType.GENERIC,
        include=frozenset({"docker"}),
    )
    assert result.document_report.ok
    assert list(inputs.iterdir()) == [asset]
    assert asset.read_bytes() == b"user-owned bytes\r\n"
    assert "dockerfile: docker/Dockerfile" in (tmp_path / "compose.yaml").read_text()
    assert "context: ." in (tmp_path / "compose.yaml").read_text()


def test_cli_and_mcp_explicit_migration_never_initialize_documents(governed_repo: Path) -> None:
    database = Database(governed_repo / ".codex-os/state/state.db")
    database.migrate()
    before = {
        str(p.relative_to(governed_repo)): p.read_bytes()
        for p in governed_repo.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }
    cli = CliRunner().invoke(app, ["init", str(governed_repo), "--migrate-runtime", "--json"])
    assert cli.exit_code == 0, cli.output
    mcp = project_init(project_root=str(governed_repo), migrate_runtime=True)
    assert mcp["ok"], mcp
    assert not (governed_repo / "input").exists()
    assert not (governed_repo / "docs").exists()
    for relative, content in before.items():
        if not relative.endswith(".db"):
            assert (governed_repo / relative).read_bytes() == content


def test_mcp_rejection_revokes_even_when_index_is_unavailable(governed_repo: Path) -> None:
    spec = governed_repo / "docs/design/UI_SPEC.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# UI\n", encoding="utf-8")
    spec.with_name("PROTOTYPE.html").write_text("<html>review</html>", encoding="utf-8")
    args = dict(
        project_root=str(governed_repo),
        gate="frontend",
        subject="test",
        decided_by="user",
        scope="test",
    )
    assert approval_record(**args, decision="approved")["ok"]
    assert evaluate_frontend(governed_repo, impact="new_page", scope="test").allowed
    database = Database(governed_repo / ".codex-os/state/state.db")
    with database.connection() as connection:
        connection.execute("CREATE TABLE user_asset (id TEXT)")
    rejected = approval_record(**args, decision="rejected")
    assert rejected["ok"] and rejected["data"]["warnings"]
    assert not evaluate_frontend(governed_repo, impact="new_page", scope="test").allowed
    assert "status: rejected" in spec.read_text(encoding="utf-8")


def test_registered_worktree_cli_mcp_and_memory_context(governed_repo: Path) -> None:
    database = Database(governed_repo / ".codex-os/state/state.db")
    database.migrate()
    manager = WorktreeManager(governed_repo, database=database)
    manager.prepare(name="context")
    nested = governed_repo / ".worktrees/context/docs/nested"
    nested.mkdir(parents=True)
    store = MemoryStore(database, nested)
    with pytest.raises(MemoryStoreError, match="only submit candidates"):
        store.record(record_type="bug", title="No direct write", summary="s", source="src/x.py")
    entry = store.record_candidate(
        record_type="bug", title="Candidate", summary="s", source="src/x.py"
    )
    assert MemoryStore(database, governed_repo).candidates()[0].id == entry.id
    import asyncio

    result = asyncio.run(
        governance_check(
            project_root=str(nested), stage="finish", base_ref="HEAD", memory_not_needed=True
        )
    )
    assert result["ok"] and result["data"]["allowed"], result
    cli = CliRunner().invoke(
        app, ["finish", str(nested), "--base-ref", "HEAD", "--memory-not-needed", "--json"]
    )
    assert cli.exit_code == 0, cli.output
    manager.cleanup(name="context")


def test_secret_scan_missing_and_staged_content(
    governed_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = runpy.run_path(str(ROOT / "scripts/secret_scan_incremental.py"))
    monkeypatch.chdir(governed_repo)
    assert script["main"](["missing.py"]) == 2
    assert not json.loads(capsys.readouterr().out)["valid"]
    target = governed_repo / "secret.py"
    # Deliberately synthetic token, assembled to keep this test source itself secret-free.
    token = "ghp_" + "0123456789abcdefghij" + "klmnopqrstuvwxyz"
    target.write_text("credential = '" + token + "'\n", encoding="utf-8")
    assert GitRunner(governed_repo).run("add", "secret.py").returncode == 0
    target.write_text("clean = True\n", encoding="utf-8")
    assert script["main"](["secret.py"]) == 0
    capsys.readouterr()
    assert script["main"](["--staged"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["valid"] is False
    assert token not in json.dumps(data)


def test_index_failure_preserves_jsonl_and_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()
    store = MemoryStore(database, tmp_path)
    entry = store.record_candidate(record_type="bug", title="Recover", summary="s", source="x.py")
    original = store._reindex_locked

    def fail():
        raise sqlite3.OperationalError("fixture index unavailable")

    monkeypatch.setattr(store, "_reindex_locked", fail)
    with pytest.raises(sqlite3.OperationalError):
        store.accept_candidate(entry.id)
    assert len(store.load()[0]) == 1 and store.candidates() == (entry,)
    monkeypatch.setattr(store, "_reindex_locked", original)
    assert store.accept_candidate(entry.id).id == entry.id
    assert not store.candidates()
