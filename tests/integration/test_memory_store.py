from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
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
    candidate_file = (tmp_path / "docs" / "memory" / "memory.jsonl").parent / (entry.id + ".json")
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


def test_accept_conflicting_candidate_preserves_it(tmp_path: Path) -> None:
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
    assert store.candidates() == (entry,)


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


def test_invalid_jsonl_preserves_candidate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = store.record_candidate(record_type="bug", title="Keep", summary="s", source="src/x.py")
    target = tmp_path / "docs/memory/memory.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text("invalid\n", encoding="utf-8")
    with pytest.raises(MemoryStoreError, match="invalid lines"):
        store.accept_candidate(entry.id)
    assert store.candidates() == (entry,)
    assert target.read_text(encoding="utf-8") == "invalid\n"


def test_identical_already_merged_candidate_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = store.record_candidate(record_type="bug", title="Same", summary="s", source="src/x.py")
    store.record(record_type="bug", title="Same", summary="s", source="src/x.py")
    assert store.accept_candidate(entry.id).id == entry.id
    assert not store.candidates()
    assert len(store.load()[0]) == 1


def test_concurrent_memory_writers_do_not_lose_records(tmp_path: Path) -> None:
    store = _store(tmp_path)

    def write(index: int) -> None:
        MemoryStore(store.database, tmp_path).record(
            record_type="lesson", title=f"Concurrent {index}", summary="s", source="src/x.py"
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(8)))
    assert len(store.load()[0]) == 8
    assert len(store.search("Concurrent", limit=20)) == 8


def test_search_refreshes_changed_jsonl_and_rejects_invalid_facts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record(record_type="bug", title="Old", summary="s", source="src/x.py")
    target = tmp_path / "docs/memory/memory.jsonl"
    data = json.loads(target.read_text(encoding="utf-8"))
    data["title"] = "New"
    target.write_text(json.dumps(data) + "\n", encoding="utf-8")
    assert not store.search("Old")
    assert store.search("New")
    data["tags"] = ["token=" + "synthetic-fixture"]
    target.write_text(json.dumps(data) + "\n", encoding="utf-8")
    assert store.reindex().invalid_lines
    with pytest.raises(MemoryStoreError, match="suspected secret"):
        store.search("New")


def test_secrets_in_tags_are_rejected_consistently(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for writer in (store.record, store.record_candidate):
        with pytest.raises(MemoryStoreError):
            writer(
                record_type="bug",
                title="t",
                summary="s",
                source="x.py",
                tags=("token=" + "synthetic-fixture",),
            )


def test_candidate_collision_is_not_overwritten(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = store.record_candidate(
        record_type="bug", title="t", summary="s", source="x.py", tags=("original",)
    )
    with pytest.raises(MemoryStoreError, match="collision"):
        store.record_candidate(
            record_type="bug", title="t", summary="s", source="x.py", tags=("changed",)
        )
    assert store.candidates() == (entry,)
