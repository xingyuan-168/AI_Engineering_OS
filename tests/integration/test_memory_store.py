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


def test_accept_candidate_merges_and_removes_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = store.record_candidate(
        record_type="decision",
        title="Merge me",
        summary="candidate loop closes",
        source="src/a.py",
    )
    merged = store.accept_candidate(entry.id)
    assert merged.title == "Merge me"
    candidate_file = (
        tmp_path / "docs" / "memory" / "memory.jsonl"
    ).parent / (entry.id + ".json")
    assert not candidate_file.exists()
    candidates = store.candidates()
    assert candidates == ()
    entries, invalid = store.load()
    assert invalid == ()
    assert [item.title for item in entries] == ["Merge me"]
    hits = store.search("merge me")
    assert len(hits) == 1


def test_reject_candidate_discards_without_jsonl_write(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = store.record_candidate(
        record_type="lesson",
        title="Drop me",
        summary="not worth keeping",
        source="src/a.py",
    )
    store.reject_candidate(entry.id)
    assert store.candidates() == ()
    entries, _ = store.load()
    assert entries == ()


def test_unknown_candidate_fails_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(MemoryStoreError) as excinfo:
        store.accept_candidate("MEM-does-not-exist")
    assert excinfo.value.code == "MEMORY_CANDIDATE_MISSING"
    with pytest.raises(MemoryStoreError) as excinfo:
        store.reject_candidate("../escape")
    assert excinfo.value.code == "MEMORY_CANDIDATE_MISSING"


def test_accept_duplicate_candidate_cleans_up(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record(record_type="decision", title="Already here", summary="s", source="docs/x.md")
    entry = store.record_candidate(
        record_type="decision",
        title="Already here",
        summary="same title from a subagent",
        source="src/a.py",
    )
    with pytest.raises(MemoryStoreError) as excinfo:
        store.accept_candidate(entry.id)
    assert excinfo.value.code == "MEMORY_DUPLICATE"
    # The stale candidate is cleaned up so the loop cannot stay stuck.
    assert store.candidates() == ()


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
