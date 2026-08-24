from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from codex_ai_os.application.offline_audit import (
    AuditSnapshotError,
    build_snapshot,
    main,
    verify_snapshot,
)

NOW = datetime.now(UTC)
LOCK = b"version = 1\n"
REQUIREMENTS = (
    b"demo-package==1.2.3 \\\n"
    b"    --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
)
REPORT: dict[str, Any] = {
    "dependencies": [{"name": "demo-package", "version": "1.2.3", "vulns": []}],
    "fixes": [],
}
AUDIT_REPORT_CASES: list[tuple[dict[str, Any], str]] = [
    ({}, "no dependency list"),
    ({"dependencies": [{"name": 1, "version": "1", "vulns": []}]}, "name or version"),
    (
        {
            "dependencies": [
                {"name": "demo-package", "version": "1.2.3", "vulns": []},
                {"name": "demo_package", "version": "1.2.3", "vulns": []},
            ]
        },
        "duplicate audited dependency",
    ),
    (
        {"dependencies": [{"name": "other", "version": "9", "vulns": []}]},
        "dependency set mismatch",
    ),
    (
        {"dependencies": [{"name": "demo-package", "version": "1.2.3"}]},
        "no vulnerability list",
    ),
]


def _set(key: str, value: object) -> Callable[[dict[str, Any]], None]:
    def mutation(snapshot: dict[str, Any]) -> None:
        snapshot[key] = value

    return mutation


def test_build_and_verify_lock_bound_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot_path, requirements_path, lock_path = _write_snapshot(tmp_path)

    result = verify_snapshot(
        snapshot_path,
        requirements_path,
        lock_path,
        now=NOW + timedelta(hours=1),
    )

    assert result["valid"] is True
    assert result["dependencies"] == 1
    assert main([str(snapshot_path), str(requirements_path), str(lock_path)]) == 0
    assert '"valid": true' in capsys.readouterr().out


def test_build_rejects_incomplete_or_vulnerable_audit() -> None:
    with pytest.raises(AuditSnapshotError, match="contains no dependencies"):
        _build({"dependencies": [], "fixes": []})

    vulnerable = copy.deepcopy(REPORT)
    vulnerable["dependencies"][0]["vulns"] = [{"id": "PYSEC-TEST"}]
    with pytest.raises(AuditSnapshotError, match="known vulnerabilities"):
        _build(vulnerable)


@pytest.mark.parametrize(
    ("requirements", "message"),
    [
        (b"not a requirement\n", "unsupported requirement"),
        (b"demo[extra]==1.0\n", "non-pinned requirement"),
        (b"demo>=1.0\n", "not exactly pinned"),
        (b"demo==1.*\n", "not exactly pinned"),
        (b"demo==1.0\ndemo==2.0\n", "conflicting versions"),
        (b"# no packages\n", "no applicable dependencies"),
        (b"demo==1.0 ; python_version < '1'\n", "no applicable dependencies"),
    ],
)
def test_build_rejects_unlocked_requirements(requirements: bytes, message: str) -> None:
    with pytest.raises(AuditSnapshotError, match=message):
        build_snapshot(
            lock_content=LOCK,
            requirements_content=requirements,
            audit_report=REPORT,
            tool_version="2.10.1",
            generated_at=NOW,
        )


@pytest.mark.parametrize(
    ("report", "message"),
    AUDIT_REPORT_CASES,
)
def test_build_rejects_malformed_audit_report(
    report: dict[str, Any], message: str
) -> None:
    with pytest.raises(AuditSnapshotError, match=message):
        _build(report)


def test_build_rejects_naive_timestamp() -> None:
    with pytest.raises(AuditSnapshotError, match="timezone"):
        build_snapshot(
            lock_content=LOCK,
            requirements_content=REQUIREMENTS,
            audit_report=REPORT,
            tool_version="2.10.1",
            generated_at=datetime(2026, 8, 24),
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (_set("schema_version", "1.0"), "schema"),
        (_set("kind", "wrong"), "kind"),
        (_set("lock_sha256", "0" * 64), "uv.lock"),
        (_set("requirements_sha256", "0" * 64), "requirements"),
        (_set("platform", {"system": "wrong"}), "platform"),
        (_set("report_sha256", "0" * 64), "report hash"),
        (
            _set("tool", {"name": "other", "version": "1", "exit_code": 0}),
            "successful pip-audit",
        ),
    ],
)
def test_verify_rejects_tampered_snapshot(
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    snapshot_path, requirements_path, lock_path = _write_snapshot(tmp_path)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    mutation(snapshot)
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    with pytest.raises(AuditSnapshotError, match=message):
        verify_snapshot(snapshot_path, requirements_path, lock_path, now=NOW)


def test_verify_rejects_expired_snapshot_and_cli_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot_path, requirements_path, lock_path = _write_snapshot(tmp_path)

    with pytest.raises(AuditSnapshotError, match="expired"):
        verify_snapshot(
            snapshot_path,
            requirements_path,
            lock_path,
            now=NOW + timedelta(hours=25),
        )
    snapshot_path.write_text("not-json", encoding="utf-8")
    assert main([str(snapshot_path), str(requirements_path), str(lock_path)]) == 1
    assert "snapshot rejected" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("generated_at", "message"),
    [
        (None, "no generated_at"),
        ("invalid", "timestamp is invalid"),
        ("2026-08-24T00:00:00", "no timezone"),
    ],
)
def test_verify_rejects_invalid_snapshot_time(
    tmp_path: Path, generated_at: object, message: str
) -> None:
    snapshot_path, requirements_path, lock_path = _write_snapshot(tmp_path)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["generated_at"] = generated_at
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    with pytest.raises(AuditSnapshotError, match=message):
        verify_snapshot(snapshot_path, requirements_path, lock_path, now=NOW)


def test_verify_rejects_non_object_and_naive_current_time(tmp_path: Path) -> None:
    snapshot_path, requirements_path, lock_path = _write_snapshot(tmp_path)
    snapshot_path.write_text("[]", encoding="utf-8")
    with pytest.raises(AuditSnapshotError, match="JSON object"):
        verify_snapshot(snapshot_path, requirements_path, lock_path, now=NOW)

    snapshot_path.write_text(json.dumps(_build(REPORT)), encoding="utf-8")
    with pytest.raises(AuditSnapshotError, match="current time"):
        verify_snapshot(
            snapshot_path,
            requirements_path,
            lock_path,
            now=datetime(2026, 8, 24),
        )


def _write_snapshot(tmp_path: Path) -> tuple[Path, Path, Path]:
    snapshot_path = tmp_path / "audit-snapshot.json"
    requirements_path = tmp_path / "requirements.txt"
    lock_path = tmp_path / "uv.lock"
    snapshot_path.write_text(json.dumps(_build(REPORT)), encoding="utf-8")
    requirements_path.write_bytes(REQUIREMENTS)
    lock_path.write_bytes(LOCK)
    return snapshot_path, requirements_path, lock_path


def _build(report: dict[str, Any]) -> dict[str, Any]:
    return build_snapshot(
        lock_content=LOCK,
        requirements_content=REQUIREMENTS,
        audit_report=report,
        tool_version="2.10.1",
        generated_at=NOW,
    )
