"""Deterministic offline checks used by the formal G3 verification matrix."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from codex_ai_os.domain.config import ProjectConfig
from codex_ai_os.domain.versions import RUNTIME_VERSIONS
from codex_ai_os.infrastructure.config import load_yaml_model
from codex_ai_os.infrastructure.documents import DocumentManager


class FormalCheckError(RuntimeError):
    """Raised when a formal verification input cannot be proven valid."""


def check_documents(root: Path) -> None:
    # This internal, commit-bound G3 action checks the registered source
    # Worktree. Public runtime calls must continue rejecting managed Worktrees.
    config = load_yaml_model(root.resolve() / ".codex-os" / "project.yaml", ProjectConfig)
    report = DocumentManager(root).check(config.project_type.value)
    if not report.ok:
        raise FormalCheckError(
            "document governance failed: "
            + json.dumps(asdict(report), sort_keys=True, default=list)
        )


def check_build_install(root: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="codex-os-build-") as temporary_value:
        temporary = Path(temporary_value)
        dist = temporary / "dist"
        site = temporary / "site"
        dist.mkdir()
        _run(
            [
                sys.executable,
                "-m",
                "build",
                "--no-isolation",
                "--outdir",
                str(dist),
            ],
            cwd=root,
        )
        wheels = tuple(sorted(dist.glob("*.whl")))
        sdists = tuple(sorted(dist.glob("*.tar.gz")))
        if len(wheels) != 1 or len(sdists) != 1:
            raise FormalCheckError("build must produce exactly one wheel and one sdist")
        _run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-compile",
                "--no-deps",
                "--no-index",
                "--target",
                str(site),
                str(wheels[0]),
            ],
            cwd=temporary,
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(site)
        _run(
            [
                sys.executable,
                "-c",
                (
                    "from codex_ai_os.domain.versions import RUNTIME_VERSIONS; "
                    f"assert RUNTIME_VERSIONS.software == {RUNTIME_VERSIONS.software!r}"
                ),
            ],
            cwd=temporary,
            environment=environment,
        )


def check_real_oci() -> None:
    container_environment = any(
        key.casefold() == "container" and bool(value) for key, value in os.environ.items()
    )
    if not (
        Path("/.dockerenv").is_file()
        or Path("/run/.containerenv").is_file()
        or container_environment
    ):
        raise FormalCheckError("formal real-oci check is not running inside a container")
    if os.name == "nt":
        raise FormalCheckError("formal real-oci check cannot run on the Windows host")


def check_image_sbom(cache: Path) -> None:
    payload = _json_object(cache / "image-sbom.cdx.json")
    if payload.get("bomFormat") != "CycloneDX":
        raise FormalCheckError("image SBOM is not CycloneDX")
    serial = payload.get("serialNumber")
    if not isinstance(serial, str) or not serial.startswith("urn:uuid:"):
        raise FormalCheckError("image SBOM has no stable serial number")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise FormalCheckError("image SBOM metadata is missing")


def check_image_scan(cache: Path) -> None:
    payload = _json_object(cache / "image-scan.json")
    results = payload.get("Results")
    if not isinstance(results, list):
        raise FormalCheckError("Trivy image report has no Results array")
    blockers: list[str] = []
    for result in cast(list[object], results):
        if not isinstance(result, dict):
            raise FormalCheckError("Trivy image result is invalid")
        raw_vulnerabilities = cast(dict[str, object], result).get("Vulnerabilities")
        if raw_vulnerabilities is None:
            vulnerabilities: list[object] = []
        elif isinstance(raw_vulnerabilities, list):
            vulnerabilities = cast(list[object], raw_vulnerabilities)
        else:
            raise FormalCheckError("Trivy vulnerabilities value is invalid")
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                raise FormalCheckError("Trivy vulnerability entry is invalid")
            finding = cast(dict[str, object], vulnerability)
            severity = str(finding.get("Severity") or "").casefold()
            if severity in {"high", "critical"}:
                blockers.append(str(finding.get("VulnerabilityID") or "unknown"))
    if blockers:
        raise FormalCheckError(
            f"Trivy image report contains unapproved high/critical findings: {blockers}"
        )


def main(arguments: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if arguments is None else arguments)
    if not args:
        print("formal check name is required", file=sys.stderr)
        return 2
    name = args.pop(0)
    root = Path(args.pop(0) if args else "/workspace")
    try:
        if name == "docs":
            check_documents(root)
        elif name == "build-install":
            check_build_install(root)
        elif name == "real-oci":
            check_real_oci()
        elif name == "image-sbom":
            check_image_sbom(root)
        elif name == "image-scan":
            check_image_scan(root)
        else:
            raise FormalCheckError(f"unknown formal check: {name}")
    except (FormalCheckError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _json_object(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FormalCheckError(f"expected JSON object: {path}")
    return cast(dict[str, Any], value)


def _run(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=600,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise FormalCheckError(detail or f"command failed: {command[:3]}")


if __name__ == "__main__":  # pragma: no cover - exercised through OCI verification
    raise SystemExit(main())
