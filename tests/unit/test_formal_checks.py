from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_ai_os.application.formal_checks import (
    FormalCheckError,
    check_image_sbom,
    check_image_scan,
)


def test_formal_image_evidence_requires_cyclonedx_and_zero_blockers(
    tmp_path: Path,
) -> None:
    (tmp_path / "image-sbom.cdx.json").write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "serialNumber": "urn:uuid:00000000-0000-0000-0000-000000000001",
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "image-scan.json").write_text(
        json.dumps({"Results": [{"Vulnerabilities": []}]}), encoding="utf-8"
    )

    check_image_sbom(tmp_path)
    check_image_scan(tmp_path)

    (tmp_path / "image-scan.json").write_text(
        json.dumps(
            {
                "Results": [
                    {
                        "Vulnerabilities": [
                            {"VulnerabilityID": "CVE-FIXTURE", "Severity": "CRITICAL"}
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FormalCheckError, match="CVE-FIXTURE"):
        check_image_scan(tmp_path)


def test_formal_image_evidence_rejects_unstructured_reports(tmp_path: Path) -> None:
    (tmp_path / "image-sbom.cdx.json").write_text("{}", encoding="utf-8")
    (tmp_path / "image-scan.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FormalCheckError, match="CycloneDX"):
        check_image_sbom(tmp_path)
    with pytest.raises(FormalCheckError, match="Results"):
        check_image_scan(tmp_path)
