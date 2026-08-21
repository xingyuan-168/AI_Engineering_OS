from pathlib import Path

import pytest

from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.domain.config import ProjectType
from codex_ai_os.infrastructure.database import Database
from codex_ai_os.infrastructure.memory import MemoryStore, MemoryStoreError


def test_memory_candidate_activation_search_and_source_invalidation(tmp_path: Path) -> None:
    store, database = _store(tmp_path, "PROJECT-MEMORY")
    _write(tmp_path, "docs/source.md", "# Decision source\n")
    _write(tmp_path, "docs/memory.md", "Use SQLite for auditable state.\n")

    candidate = store.create_candidate(
        record_type="decision",
        title="SQLite state decision",
        content_ref="docs/memory.md",
        source_refs=("docs/source.md",),
        confidence=0.9,
        tags=("architecture", "sqlite"),
    )
    active = store.activate(candidate.id)

    assert candidate.status == "candidate"
    assert active.status == "active"
    assert store.activate(candidate.id) == active
    assert store.search("sqlite") == [active]

    _write(tmp_path, "docs/source.md", "# Changed decision source\n")
    assert store.invalidate_changed_sources() == (candidate.id,)
    assert store.get(candidate.id).status == "invalidated"
    assert store.search("sqlite") == []

    with database.connection() as connection:
        event_types = [
            str(row["event_type"])
            for row in connection.execute(
                "SELECT event_type FROM events WHERE event_type LIKE 'memory.%' ORDER BY sequence"
            ).fetchall()
        ]
    assert event_types == ["memory.candidate", "memory.activated", "memory.invalidated"]


def test_memory_rejects_secrets_low_confidence_and_unsafe_sources(tmp_path: Path) -> None:
    store, _database = _store(tmp_path, "PROJECT-POLICY")
    _write(tmp_path, "docs/source.md", "# Source\n")
    credential_fixture = f"{'pass'}{'word'}={'fixture-value'}\n"
    _write(tmp_path, "docs/secret.md", credential_fixture)

    with pytest.raises(MemoryStoreError, match="suspected secret"):
        store.create_candidate(
            record_type="project",
            title="Unsafe memory",
            content_ref="docs/secret.md",
            source_refs=("docs/source.md",),
            confidence=0.9,
        )

    _write(tmp_path, "docs/memory.md", "A tentative observation.\n")
    tentative = store.create_candidate(
        record_type="experience",
        title="Tentative",
        content_ref="docs/memory.md",
        source_refs=("docs/source.md",),
        confidence=0.6,
    )
    with pytest.raises(MemoryStoreError, match="activation threshold"):
        store.activate(tentative.id)

    with pytest.raises(MemoryStoreError, match="unsafe"):
        store.create_candidate(
            record_type="bug",
            title="Traversal",
            content_ref="../outside.md",
            source_refs=("docs/source.md",),
            confidence=0.9,
        )


def test_memory_search_is_isolated_by_project(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first, _ = _store(first_root, "PROJECT-FIRST")
    second, _ = _store(second_root, "PROJECT-SECOND")
    for root in (first_root, second_root):
        _write(root, "docs/source.md", "# Shared source\n")
        _write(root, "docs/memory.md", "Shared searchable phrase.\n")

    first_record = first.create_candidate(
        record_type="project",
        title="First shared memory",
        content_ref="docs/memory.md",
        source_refs=("docs/source.md",),
        confidence=0.9,
    )
    second_record = second.create_candidate(
        record_type="project",
        title="Second shared memory",
        content_ref="docs/memory.md",
        source_refs=("docs/source.md",),
        confidence=0.9,
    )
    first.activate(first_record.id)
    second.activate(second_record.id)

    assert [record.id for record in first.search("shared")] == [first_record.id]
    assert [record.id for record in second.search("shared")] == [second_record.id]


def _store(root: Path, project_id: str) -> tuple[MemoryStore, Database]:
    result = ProjectInitializer().initialize(
        root,
        project_id=project_id,
        name=project_id,
        project_type=ProjectType.GENERIC,
    )
    database = Database(result.database_path)
    return MemoryStore(database, root, project_id), database


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
