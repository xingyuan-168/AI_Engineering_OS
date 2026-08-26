"""Create and verify lock-bound pip-audit snapshots for offline Gate execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from codex_ai_os.domain.versions import RUNTIME_VERSIONS

SNAPSHOT_SCHEMA_VERSION = RUNTIME_VERSIONS.api
MAX_SNAPSHOT_AGE = timedelta(hours=24)


class AuditSnapshotError(RuntimeError):
    """Raised when an offline dependency-audit snapshot cannot be trusted."""


def build_snapshot(
    *,
    lock_content: bytes,
    requirements_content: bytes,
    audit_report: Mapping[str, Any],
    tool_version: str,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a validated snapshot from a successful online pip-audit JSON report."""

    expected = _requirements(requirements_content.decode("utf-8", errors="strict"))
    audited = _audited_dependencies(audit_report)
    _require_same_dependencies(expected, audited)
    _require_no_vulnerabilities(audit_report)
    timestamp = generated_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise AuditSnapshotError("generated_at must include a timezone")
    report = dict(audit_report)
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "kind": "dependency-audit-snapshot",
        "generated_at": timestamp.astimezone(UTC).isoformat(),
        "platform": {
            "system": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        },
        "tool": {"name": "pip-audit", "version": tool_version, "exit_code": 0},
        "lock_sha256": _sha256(lock_content),
        "requirements_sha256": _sha256(requirements_content),
        "report_sha256": _report_hash(report),
        "report": report,
    }


def verify_snapshot(
    snapshot_path: Path,
    requirements_path: Path,
    lock_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fail closed unless the snapshot matches this lock, platform, and dependency set."""

    snapshot = _load_mapping(snapshot_path, "audit snapshot")
    requirements_content = requirements_path.read_bytes()
    lock_content = lock_path.read_bytes()
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise AuditSnapshotError("unsupported audit snapshot schema")
    if snapshot.get("kind") != "dependency-audit-snapshot":
        raise AuditSnapshotError("invalid audit snapshot kind")
    if snapshot.get("lock_sha256") != _sha256(lock_content):
        raise AuditSnapshotError("audit snapshot does not match uv.lock")
    if snapshot.get("requirements_sha256") != _sha256(requirements_content):
        raise AuditSnapshotError("audit snapshot does not match requirements.txt")
    _require_current_platform(snapshot.get("platform"))
    _require_fresh(snapshot.get("generated_at"), now=now)
    tool = _as_mapping(snapshot.get("tool"), "tool")
    if tool.get("name") != "pip-audit" or tool.get("exit_code") != 0:
        raise AuditSnapshotError("snapshot was not produced by a successful pip-audit run")
    report = _as_mapping(snapshot.get("report"), "report")
    if snapshot.get("report_sha256") != _report_hash(report):
        raise AuditSnapshotError("audit report hash mismatch")
    expected = _requirements(requirements_content.decode("utf-8", errors="strict"))
    audited = _audited_dependencies(report)
    _require_same_dependencies(expected, audited)
    _require_no_vulnerabilities(report)
    return {
        "valid": True,
        "dependencies": len(audited),
        "lock_sha256": snapshot["lock_sha256"],
        "report_sha256": snapshot["report_sha256"],
        "generated_at": snapshot["generated_at"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("requirements", type=Path)
    parser.add_argument("lock", type=Path)
    arguments = parser.parse_args(argv)
    try:
        result = verify_snapshot(arguments.snapshot, arguments.requirements, arguments.lock)
    except (AuditSnapshotError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"dependency audit snapshot rejected: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _requirements(content: str) -> dict[str, str]:
    dependencies: dict[str, str] = {}
    for number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith(("#", "--hash=")):
            continue
        candidate = line.removesuffix("\\").strip()
        try:
            requirement = Requirement(candidate)
        except InvalidRequirement as exc:
            raise AuditSnapshotError(
                f"unsupported requirement at line {number}: {candidate}"
            ) from exc
        if requirement.url is not None or requirement.extras:
            raise AuditSnapshotError(f"non-pinned requirement at line {number}: {candidate}")
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        specifiers = tuple(requirement.specifier)
        if (
            len(specifiers) != 1
            or specifiers[0].operator != "=="
            or specifiers[0].version.endswith(".*")
        ):
            raise AuditSnapshotError(f"requirement is not exactly pinned: {candidate}")
        name = canonicalize_name(requirement.name)
        version = specifiers[0].version
        previous = dependencies.setdefault(name, version)
        if previous != version:
            raise AuditSnapshotError(f"conflicting versions for requirement: {name}")
    if not dependencies:
        raise AuditSnapshotError("requirements.txt contains no applicable dependencies")
    return dependencies


def _audited_dependencies(report: Mapping[str, Any]) -> dict[str, str]:
    raw_dependencies_value = report.get("dependencies")
    if not isinstance(raw_dependencies_value, list):
        raise AuditSnapshotError("pip-audit report has no dependency list")
    raw_dependencies = cast(list[object], raw_dependencies_value)
    dependencies: dict[str, str] = {}
    for raw in raw_dependencies:
        dependency = _as_mapping(raw, "dependency")
        name_value = dependency.get("name")
        version_value = dependency.get("version")
        if not isinstance(name_value, str) or not isinstance(version_value, str):
            raise AuditSnapshotError("pip-audit dependency is missing name or version")
        name = canonicalize_name(name_value)
        if name in dependencies:
            raise AuditSnapshotError(f"duplicate audited dependency: {name}")
        dependencies[name] = version_value
    if not dependencies:
        raise AuditSnapshotError("pip-audit report contains no dependencies")
    return dependencies


def _require_same_dependencies(expected: dict[str, str], audited: dict[str, str]) -> None:
    if expected == audited:
        return
    missing = sorted(set(expected) - set(audited))
    unexpected = sorted(set(audited) - set(expected))
    mismatched = sorted(
        name for name in set(expected) & set(audited) if expected[name] != audited[name]
    )
    raise AuditSnapshotError(
        "audit dependency set mismatch: "
        f"missing={missing}, unexpected={unexpected}, mismatched={mismatched}"
    )


def _require_no_vulnerabilities(report: Mapping[str, Any]) -> None:
    raw_dependencies_value = report.get("dependencies")
    assert isinstance(raw_dependencies_value, list)
    raw_dependencies = cast(list[object], raw_dependencies_value)
    vulnerable: list[str] = []
    for raw in raw_dependencies:
        dependency = _as_mapping(raw, "dependency")
        vulnerabilities = dependency.get("vulns")
        if not isinstance(vulnerabilities, list):
            raise AuditSnapshotError("pip-audit dependency has no vulnerability list")
        if vulnerabilities:
            vulnerable.append(str(dependency.get("name", "<unknown>")))
    if vulnerable:
        raise AuditSnapshotError(
            "known vulnerabilities found in: " + ", ".join(sorted(vulnerable))
        )


def _require_current_platform(value: object) -> None:
    current = {
        "system": platform.system().lower(),
        "machine": platform.machine().lower(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }
    if dict(_as_mapping(value, "platform")) != current:
        raise AuditSnapshotError("audit snapshot platform does not match the sandbox")


def _require_fresh(value: object, *, now: datetime | None) -> None:
    if not isinstance(value, str):
        raise AuditSnapshotError("audit snapshot has no generated_at timestamp")
    try:
        generated_at = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AuditSnapshotError("audit snapshot timestamp is invalid") from exc
    if generated_at.tzinfo is None:
        raise AuditSnapshotError("audit snapshot timestamp has no timezone")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise AuditSnapshotError("current time must include a timezone")
    age = current.astimezone(UTC) - generated_at.astimezone(UTC)
    if age < -timedelta(minutes=5) or age > MAX_SNAPSHOT_AGE:
        raise AuditSnapshotError("audit snapshot is expired or from the future")


def _load_mapping(path: Path, label: str) -> Mapping[str, Any]:
    return _as_mapping(json.loads(path.read_text(encoding="utf-8")), label)


def _as_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise AuditSnapshotError(f"{label} must be a JSON object")
    return cast(dict[str, Any], value)


def _report_hash(report: Mapping[str, Any]) -> str:
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return _sha256(encoded)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


if __name__ == "__main__":  # pragma: no cover - exercised by the sandbox integration
    raise SystemExit(main())
