# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from codex_ai_os.application import verification_cache as cache


def test_verification_cache_scalar_contracts_fail_closed(tmp_path: Path) -> None:
    assert cache._target_platform("linux-arm64", "3.12")["machine"] == "aarch64"
    assert cache._pip_platform("linux-amd64") == "manylinux2014_x86_64"
    for platform, python_version in (("windows-amd64", "3.12"), ("linux-amd64", "3.11")):
        with pytest.raises(cache.VerificationCachePrepareError) as invalid:
            cache._target_platform(platform, python_version)
        assert invalid.value.code == "PLATFORM_MISMATCH"

    for value in ("", "/absolute", "../escape", "nested/../escape"):
        with pytest.raises(cache.VerificationCachePrepareError, match="unsafe"):
            cache._safe_relative(value)
    assert cache._safe_relative("nested\\wheel.whl") == "nested/wheel.whl"
    assert cache._is_digest("sha256:" + "a" * 64)
    assert not cache._is_digest("sha256:short")

    for value in (None, "", "not-a-date", "2026-08-29T00:00:00"):
        with pytest.raises(cache.VerificationCachePrepareError):
            cache._parse_timestamp(value, "fixture")
    assert cache._parse_timestamp("2026-08-29T00:00:00Z", "fixture").tzinfo is UTC

    with pytest.raises(cache.VerificationCachePrepareError, match="required"):
        cache._required_text({}, "approval")
    assert cache._required_text({"approval": " APPROVED "}, "approval") == " APPROVED "
    with pytest.raises(cache.VerificationCachePrepareError, match="mapping"):
        cache._as_mapping([], "mapping")

    current = datetime(2026, 8, 29, tzinfo=UTC)
    with pytest.raises(cache.VerificationCachePrepareError, match="timestamps"):
        cache._require_fresh_manifest(
            {
                "prepared_at": "2026-08-30T00:00:00+00:00",
                "expires_at": "2026-08-30T01:00:00+00:00",
            },
            now=current,
        )
    with pytest.raises(cache.VerificationCachePrepareError) as expired:
        cache._require_fresh_manifest(
            {
                "prepared_at": "2026-08-27T00:00:00+00:00",
                "expires_at": "2026-08-28T00:00:00+00:00",
            },
            now=current,
        )
    assert expired.value.code == "VERIFICATION_CACHE_EXPIRED"

    payload = tmp_path / "payload.json"
    payload.write_text("[]", encoding="utf-8")
    with pytest.raises(cache.VerificationCachePrepareError, match="object"):
        cache._load_json(payload)
    for content in (b"not-json", b"[]"):
        with pytest.raises(cache.VerificationCachePrepareError) as invalid_json:
            cache._load_json_bytes(content, "inspect")
        assert invalid_json.value.code == "SANDBOX_IMAGE_UNAVAILABLE"


def test_verification_image_and_descriptor_edges(tmp_path: Path) -> None:
    digest = "sha256:" + "b" * 64
    assert cache._platform_digest({"ID": digest}) == digest
    with pytest.raises(cache.VerificationCachePrepareError, match="platform digest"):
        cache._platform_digest({})
    cache._require_image_platform(
        {"os": "linux", "architecture": "arm64"},
        {"system": "linux", "machine": "aarch64"},
    )
    with pytest.raises(cache.VerificationCachePrepareError) as mismatch:
        cache._require_image_platform(
            {"Os": "windows", "Architecture": "amd64"},
            {"system": "linux", "machine": "x86_64"},
        )
    assert mismatch.value.code == "PLATFORM_MISMATCH"

    artifact = tmp_path / "artifact.json"
    artifact.write_text("{}", encoding="utf-8")
    digest_value = cache._sha256(artifact)
    cache._require_file_descriptor(
        tmp_path,
        {"path": "artifact.json", "sha256": digest_value},
        expected_path="artifact.json",
    )
    for descriptor in (
        {"path": "wrong", "sha256": digest_value},
        {"path": "artifact.json", "sha256": "0" * 64},
    ):
        with pytest.raises(cache.VerificationCachePrepareError):
            cache._require_file_descriptor(
                tmp_path, descriptor, expected_path="artifact.json"
            )

    sbom = tmp_path / "sbom.json"
    scan = tmp_path / "scan.json"
    sbom.write_text(json.dumps({"bomFormat": "SPDX"}), encoding="utf-8")
    scan.write_text(json.dumps({"Results": []}), encoding="utf-8")
    with pytest.raises(cache.VerificationCachePrepareError, match="SBOM"):
        cache._validate_image_reports(sbom, scan)
    sbom.write_text(
        json.dumps({"bomFormat": "CycloneDX", "serialNumber": "urn:uuid:fixture"}),
        encoding="utf-8",
    )
    cases = cast(
        tuple[tuple[object, str], ...],
        (
        (None, "Results"),
        (["invalid"], "result"),
        ([{"Vulnerabilities": {}}], "vulnerabilities"),
        ([{"Vulnerabilities": ["invalid"]}], "entry"),
        (
            [{"Vulnerabilities": [{"Severity": "HIGH", "VulnerabilityID": "CVE-1"}]}],
            "CVE-1",
        ),
        ),
    )
    for results, message in cases:
        scan.write_text(json.dumps({"Results": results}), encoding="utf-8")
        with pytest.raises(cache.VerificationCachePrepareError, match=message):
            cache._validate_image_reports(sbom, scan)
    scan.write_text(
        json.dumps(
            {
                "Results": [
                    {},
                    {
                        "Vulnerabilities": [
                            {"Severity": "LOW", "VulnerabilityID": "CVE-LOW"}
                        ]
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    cache._validate_image_reports(sbom, scan)


def test_verification_command_and_permission_helpers(tmp_path: Path) -> None:
    for stderr, stdout, expected in (
        (b"stderr", b"stdout", "stderr"),
        (b"", b"stdout", "stdout"),
        (b"", b"", "exit code 9"),
    ):
        result = subprocess.CompletedProcess(["tool"], 9, stdout, stderr)
        assert expected in cache._command_error(["tool"], result)

    nested = tmp_path / "nested"
    nested.mkdir()
    file = nested / "value.txt"
    file.write_text("value", encoding="utf-8")
    assert cache._tree_is_read_only(tmp_path) is False
    cache._mark_read_only(tmp_path)
    assert cache._tree_is_read_only(tmp_path) is True
    assert cache._file_hashes(tmp_path) == {"nested/value.txt": cache._sha256(file)}
    assert cache._tree_hash(tmp_path)
    file.chmod(stat.S_IREAD | stat.S_IWRITE)
