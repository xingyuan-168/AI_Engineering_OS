from __future__ import annotations

from pathlib import Path

import pytest

from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.memory import MemoryStore, MemoryStoreError


def _store(tmp_path: Path) -> MemoryStore:
    database = Database(tmp_path / "state.db")
    database.migrate()
    return MemoryStore(database, tmp_path)


def test_record_and_search(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record(
        record_type="lesson",
        title="Gates are stateless",
        summary="Same inputs produce the same gate decision.",
        source="docs/GOVERNANCE_RULES.md",
        tags=("gates",),
    )
    hits = store.search("stateless")
    assert len(hits) == 1
    assert hits[0].title == "Gates are stateless"
    entries, invalid = store.load()
    assert invalid == ()
    assert len(entries) == 1


def test_duplicate_active_type_title_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record(record_type="decision", title="Adopt gates", summary="s", source="docs/x.md")
    with pytest.raises(MemoryStoreError) as excinfo:
        store.record(record_type="decision", title="adopt gates", summary="s2", source="docs/x.md")
    assert excinfo.value.code == "MEMORY_DUPLICATE"


def test_secrets_are_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(MemoryStoreError) as excinfo:
        store.record(
            record_type="bug",
            title="leak",
            summary="token " + "ghp_" + "0123456789" + "abcdefghij" + "klmnopqrst",
            source="docs/x.md",
        )
    assert "SECRET" in excinfo.value.code


def test_invalid_jsonl_blocks_writes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = tmp_path / "docs" / "memory" / "memory.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(MemoryStoreError) as excinfo:
        store.record(record_type="bug", title="t", summary="s", source="docs/x.md")
    assert excinfo.value.code == "MEMORY_JSONL_INVALID"


def test_candidate_flow_never_touches_jsonl(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_candidate(
        record_type="pattern",
        title="Candidate only",
        summary="submitted by a subagent",
        source="src/a.py",
    )
    assert not (tmp_path / "docs" / "memory" / "memory.jsonl").exists()
    candidates = store.candidates()
    assert len(candidates) == 1
    assert candidates[0].title == "Candidate only"
    entries, _ = store.load()
    assert entries == ()


def test_reindex_rebuilds_from_source_of_truth(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record(record_type="bug", title="Root cause fixed", summary="s", source="docs/x.md")
    with store.database.connection() as connection:
        connection.execute("DELETE FROM memory_index")
    result = store.reindex()
    assert result.indexed == 1
    assert result.invalid_lines == ()
    hits = store.search("root cause")
    assert len(hits) == 1


def test_superseded_status_is_searchable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record(record_type="decision", title="Old way", summary="s", source="docs/x.md")
    store.record(
        record_type="decision",
        title="New way",
        summary="s2",
        source="docs/x.md",
        status="superseded",
        superseded_by="MEM-000000000000",
    )
    active = store.search("way")
    assert [entry.title for entry in active] == ["Old way"]
    everything = store.search("way", statuses=("active", "superseded"))
    assert len(everything) == 2
