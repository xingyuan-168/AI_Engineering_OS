"""Validation shared by candidate assembly and G4 publication."""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast


class PluginPackageError(ValueError):
    """Raised when a packaged Plugin cannot be proven to match the release version."""


@dataclass(frozen=True, slots=True)
class PluginArchiveEvidence:
    version: str
    manifest_hash: str


def validate_candidate_plugin_package(
    manifest: Mapping[str, object],
    archive: Path,
    *,
    expected_version: str,
) -> PluginArchiveEvidence:
    """Validate candidate metadata and the manifest embedded in its Plugin ZIP."""

    source_version = manifest.get("source_plugin_manifest_version")
    packaged_version = manifest.get("packaged_plugin_version")
    packaged_hash = manifest.get("packaged_plugin_manifest_hash")
    if not isinstance(source_version, str) or source_version.partition("+")[0] != expected_version:
        raise PluginPackageError("source Plugin manifest base version does not match release")
    if packaged_version != expected_version:
        raise PluginPackageError("packaged Plugin version does not match release")
    if not isinstance(packaged_hash, str) or len(packaged_hash) != 64:
        raise PluginPackageError("packaged Plugin manifest hash is invalid")
    return validate_plugin_archive(
        archive,
        expected_version=expected_version,
        expected_manifest_hash=packaged_hash,
    )


def validate_plugin_archive(
    archive: Path,
    *,
    expected_version: str,
    expected_manifest_hash: str | None = None,
) -> PluginArchiveEvidence:
    """Open a Plugin ZIP and verify safe members plus its embedded manifest."""

    manifest_name = "ai-engineering-os/.codex-plugin/plugin.json"
    try:
        with zipfile.ZipFile(archive) as bundle:
            names: set[str] = set()
            for info in bundle.infolist():
                name = info.filename
                member = PurePosixPath(name)
                if (
                    not name
                    or member.is_absolute()
                    or ".." in member.parts
                    or not member.parts
                    or member.parts[0] != "ai-engineering-os"
                    or name in names
                ):
                    raise PluginPackageError(f"Plugin archive member is unsafe: {name!r}")
                names.add(name)
            if manifest_name not in names:
                raise PluginPackageError("Plugin archive manifest is missing")
            manifest_bytes = bundle.read(manifest_name)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise PluginPackageError("Plugin archive cannot be read") from exc

    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    if expected_manifest_hash is not None and manifest_hash != expected_manifest_hash:
        raise PluginPackageError("packaged Plugin manifest hash drifted")
    try:
        value: object = json.loads(manifest_bytes.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PluginPackageError("packaged Plugin manifest is invalid JSON") from exc
    if not isinstance(value, dict):
        raise PluginPackageError("packaged Plugin manifest is not an object")
    manifest = cast(dict[str, object], value)
    if manifest.get("name") != "ai-engineering-os":
        raise PluginPackageError("packaged Plugin name is invalid")
    if manifest.get("version") != expected_version:
        raise PluginPackageError("packaged Plugin manifest contains the wrong version")
    return PluginArchiveEvidence(version=expected_version, manifest_hash=manifest_hash)
