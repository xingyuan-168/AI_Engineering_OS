# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from codex_ai_os.application import release
from codex_ai_os.application.plugin_packaging import (
    PluginPackageError,
    validate_candidate_plugin_package,
    validate_plugin_archive,
)
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.release import ReleaseCandidateService
from codex_ai_os.application.verification_cache import VerificationCachePrepareError
from codex_ai_os.application.workflow import WorkflowError
from codex_ai_os.domain.config import ProjectType
from codex_ai_os.domain.operations import (
    HostOperation,
    HostOperationKind,
    HostOperationStatus,
)


def _packaged_manifest() -> dict[str, object]:
    return {
        "name": "ai-engineering-os",
        "version": "0.2.0",
        "description": "Plugin package fixture",
        "skills": "./skills/",
        "mcpServers": "./.mcp.json",
    }


def _zip_bytes(entries: list[tuple[str, bytes, int]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name, content, mode in entries:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            bundle.writestr(info, content)
    return output.getvalue()


def _source_zip(
    *,
    manifest: dict[str, object] | None = None,
    manifest_bytes: bytes | None = None,
) -> bytes:
    content = manifest_bytes
    if content is None:
        content = (json.dumps(manifest or _packaged_manifest()) + "\n").encode()
    return _zip_bytes(
        [
            ("plugins/ai-engineering-os/.codex-plugin/plugin.json", content, 0o100644),
            ("plugins/ai-engineering-os/skills/README.md", b"# Skills\n", 0o100644),
            ("plugins/ai-engineering-os/.mcp.json", b"{}\n", 0o100644),
        ]
    )


def test_release_build_runner_is_valid_python() -> None:
    compile(release._RELEASE_BUILD_RUNNER, "<release-build-runner>", "exec")


def test_release_wheelhouse_binding_fails_closed_and_accepts_exact_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    service = ReleaseCandidateService(root)
    lock_path = root / "uv.lock"
    lock_path.write_bytes(b"version = 1\n")
    lock_hash = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    wheelhouse = root / ".codex-os" / "cache" / "verification" / lock_hash
    wheelhouse.mkdir(parents=True)
    manifest_path = wheelhouse / "verification-cache-manifest.json"
    manifest_path.write_bytes(b'{"schema_version":"1.2"}\n')
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    with pytest.raises(WorkflowError) as missing:
        service._wheelhouse(root, operation=_operation({}))
    assert missing.value.code == "SANDBOX_DEPENDENCIES_UNAVAILABLE"

    def invalid_cache(_root: Path, *, lock_path: Path) -> dict[str, object]:
        del lock_path
        raise VerificationCachePrepareError("DEPENDENCY_UNVERIFIED", "invalid cache")

    monkeypatch.setattr(release, "validate_verification_cache", invalid_cache)
    with pytest.raises(WorkflowError) as invalid:
        service._wheelhouse(
            root,
            operation=_operation({"verification_cache": {"uv_lock_hash": lock_hash}}),
        )
    assert invalid.value.code == "DEPENDENCY_UNVERIFIED"

    operation_id = "OP-CACHE"
    request_hash = "b" * 64

    def valid_cache(_root: Path, *, lock_path: Path) -> dict[str, object]:
        assert lock_path == root / "uv.lock"
        return {
            "operation_id": operation_id,
            "operation_request_hash": request_hash,
        }

    monkeypatch.setattr(release, "validate_verification_cache", valid_cache)
    with pytest.raises(WorkflowError) as drifted:
        service._wheelhouse(
            root,
            operation=_operation(
                {
                    "verification_cache": {
                        "uv_lock_hash": lock_hash,
                        "manifest_hash": "0" * 64,
                        "operation_id": operation_id,
                        "operation_request_hash": request_hash,
                    }
                }
            ),
        )
    assert drifted.value.code == "DEPENDENCY_UNVERIFIED"

    exact = _operation(
        {
            "verification_cache": {
                "uv_lock_hash": lock_hash,
                "manifest_hash": manifest_hash,
                "operation_id": operation_id,
                "operation_request_hash": request_hash,
            }
        }
    )
    assert service._wheelhouse(root, operation=exact) == (wheelhouse, manifest_hash)


def test_reproducible_git_epoch_rejects_command_output_and_ancient_dates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outputs = (
        subprocess.CompletedProcess([], 1, b"", b"missing"),
        subprocess.CompletedProcess([], 0, b"invalid", b""),
        subprocess.CompletedProcess([], 0, b"1", b""),
        subprocess.CompletedProcess([], 0, b"315532800", b""),
    )
    for index, completed in enumerate(outputs):
        def completed_runner(
            *_args: object,
            result: subprocess.CompletedProcess[bytes] = completed,
            **_kwargs: object,
        ) -> subprocess.CompletedProcess[bytes]:
            return result

        monkeypatch.setattr(release.subprocess, "run", completed_runner)
        if index < 3:
            with pytest.raises(WorkflowError) as invalid:
                ReleaseCandidateService._git_epoch(tmp_path, "a" * 40)
            assert invalid.value.code == "GIT_EVIDENCE_INVALID"
        else:
            assert ReleaseCandidateService._git_epoch(tmp_path, "a" * 40) == 315532800


def test_plugin_source_cachebuster_is_normalized_and_archive_is_reverified(
    tmp_path: Path,
) -> None:
    root, commit = _plugin_project(tmp_path, version="0.2.0+codex.fixture")
    service = ReleaseCandidateService(root)
    staging = root / ".codex-os" / "artifacts" / "staging"
    staging.mkdir(parents=True)

    source_version = service._stage_plugin_source(
        root,
        source_commit=commit,
        staging=staging,
    )

    assert source_version == "0.2.0+codex.fixture"
    archive = staging / "ai-engineering-os-plugin-0.2.0.zip"
    source_archive = staging / ".plugin-source.zip"
    with zipfile.ZipFile(source_archive) as source, zipfile.ZipFile(archive, "w") as bundle:
        for info in source.infolist():
            if info.is_dir():
                continue
            relative = info.filename.removeprefix("plugins/ai-engineering-os/")
            content = source.read(info)
            if relative == ".codex-plugin/plugin.json":
                manifest = json.loads(content.decode("utf-8"))
                manifest["version"] = "0.2.0"
                content = (
                    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                ).encode()
            bundle.writestr(f"ai-engineering-os/{relative}", content)
    manifest_hash = ReleaseCandidateService._plugin_manifest_hash(archive)
    candidate_manifest = {
        "source_plugin_manifest_version": source_version,
        "packaged_plugin_version": "0.2.0",
        "packaged_plugin_manifest_hash": manifest_hash,
    }
    evidence = validate_candidate_plugin_package(
        candidate_manifest,
        archive,
        expected_version="0.2.0",
    )
    assert evidence.manifest_hash == manifest_hash

    candidate_manifest["packaged_plugin_manifest_hash"] = "0" * 64
    with pytest.raises(PluginPackageError, match="hash drifted"):
        validate_candidate_plugin_package(
            candidate_manifest,
            archive,
            expected_version="0.2.0",
        )


def test_plugin_staging_rejects_a_wrong_source_base_version(tmp_path: Path) -> None:
    root, commit = _plugin_project(tmp_path, version="0.1.0+codex.fixture")
    service = ReleaseCandidateService(root)
    staging = root / ".codex-os" / "artifacts" / "staging"
    staging.mkdir(parents=True)

    with pytest.raises(WorkflowError) as mismatch:
        service._stage_plugin_source(root, source_commit=commit, staging=staging)

    assert mismatch.value.code == "VERSION_MISMATCH"


def test_plugin_package_validator_rejects_metadata_and_archive_drift(tmp_path: Path) -> None:
    valid_manifest = _packaged_manifest()
    valid_bytes = (json.dumps(valid_manifest, sort_keys=True) + "\n").encode()
    valid_hash = hashlib.sha256(valid_bytes).hexdigest()
    archive = tmp_path / "valid.zip"
    archive.write_bytes(
        _zip_bytes(
            [
                ("ai-engineering-os/.codex-plugin/plugin.json", valid_bytes, 0o100644),
                ("ai-engineering-os/.mcp.json", b"{}\n", 0o100644),
            ]
        )
    )
    candidate = {
        "source_plugin_manifest_version": "0.2.0+codex.fixture",
        "packaged_plugin_version": "0.2.0",
        "packaged_plugin_manifest_hash": valid_hash,
    }
    for key, value, message in (
        ("source_plugin_manifest_version", "0.1.0", "source Plugin"),
        ("packaged_plugin_version", "0.1.0", "packaged Plugin version"),
        ("packaged_plugin_manifest_hash", "short", "manifest hash is invalid"),
    ):
        drifted = {**candidate, key: value}
        with pytest.raises(PluginPackageError, match=message):
            validate_candidate_plugin_package(
                drifted,
                archive,
                expected_version="0.2.0",
            )

    cases = (
        (
            _zip_bytes(
                [
                    ("../escape", b"unsafe", 0o100644),
                    ("ai-engineering-os/.codex-plugin/plugin.json", valid_bytes, 0o100644),
                ]
            ),
            "member is unsafe",
        ),
        (_zip_bytes([("ai-engineering-os/README.md", b"missing", 0o100644)]), "missing"),
        (b"not-a-zip", "cannot be read"),
        (
            _zip_bytes(
                [("ai-engineering-os/.codex-plugin/plugin.json", b"{", 0o100644)]
            ),
            "invalid JSON",
        ),
        (
            _zip_bytes(
                [("ai-engineering-os/.codex-plugin/plugin.json", b"[]", 0o100644)]
            ),
            "not an object",
        ),
        (
            _zip_bytes(
                [
                    (
                        "ai-engineering-os/.codex-plugin/plugin.json",
                        (json.dumps({**valid_manifest, "name": "wrong"}) + "\n").encode(),
                        0o100644,
                    )
                ]
            ),
            "name is invalid",
        ),
        (
            _zip_bytes(
                [
                    (
                        "ai-engineering-os/.codex-plugin/plugin.json",
                        (json.dumps({**valid_manifest, "version": "0.1.0"}) + "\n").encode(),
                        0o100644,
                    )
                ]
            ),
            "wrong version",
        ),
    )
    for index, (content, message) in enumerate(cases):
        invalid = tmp_path / f"invalid-{index}.zip"
        invalid.write_bytes(content)
        with pytest.raises(PluginPackageError, match=message):
            validate_plugin_archive(invalid, expected_version="0.2.0")


@pytest.mark.parametrize(
    ("archive_bytes", "returncode", "code"),
    [
        (b"", 1, "GIT_EVIDENCE_INVALID"),
        (b"not-a-zip", 0, "GIT_EVIDENCE_INVALID"),
        (_zip_bytes([("outside.txt", b"outside", 0o100644)]), 0, "GIT_EVIDENCE_INVALID"),
        (
            _zip_bytes(
                [("plugins/ai-engineering-os/../escape", b"escape", 0o100644)]
            ),
            0,
            "GIT_EVIDENCE_INVALID",
        ),
        (
            _zip_bytes(
                [("plugins/ai-engineering-os/link", b"target", 0o120000)]
            ),
            0,
            "GIT_EVIDENCE_INVALID",
        ),
        (
            _zip_bytes(
                [("plugins/ai-engineering-os/README.md", b"missing manifest", 0o100644)]
            ),
            0,
            "RELEASE_INCOMPLETE",
        ),
        (
            _source_zip(manifest_bytes=b"{"),
            0,
            "RELEASE_INCOMPLETE",
        ),
        (
            _source_zip(manifest_bytes=b"[]"),
            0,
            "RELEASE_INCOMPLETE",
        ),
        (
            _source_zip(manifest={"name": "wrong", "version": "0.2.0"}),
            0,
            "RELEASE_INCOMPLETE",
        ),
        (
            _source_zip(
                manifest={
                    "name": "ai-engineering-os",
                    "version": "0.2.0",
                    "skills": 42,
                    "mcpServers": "./.mcp.json",
                }
            ),
            0,
            "RELEASE_INCOMPLETE",
        ),
        (
            _source_zip(
                manifest={
                    "name": "ai-engineering-os",
                    "version": "0.2.0",
                    "skills": "./missing/",
                    "mcpServers": "./.mcp.json",
                }
            ),
            0,
            "RELEASE_INCOMPLETE",
        ),
    ],
)
def test_plugin_source_staging_fails_closed_on_untrusted_git_archives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    archive_bytes: bytes,
    returncode: int,
    code: str,
) -> None:
    root = _project(tmp_path)
    service = ReleaseCandidateService(root)

    def archive_runner(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess([], returncode, archive_bytes, b"failure")

    monkeypatch.setattr(
        release.subprocess,
        "run",
        archive_runner,
    )
    staging = root / ".codex-os" / "artifacts" / "staging"
    staging.mkdir(parents=True)

    with pytest.raises(WorkflowError) as failure:
        service._stage_plugin_source(root, source_commit="a" * 40, staging=staging)

    assert failure.value.code == code


def test_plugin_manifest_hash_rejects_an_invalid_archive(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.zip"
    invalid.write_bytes(b"not-a-zip")

    with pytest.raises(PluginPackageError, match="manifest is missing"):
        ReleaseCandidateService._plugin_manifest_hash(invalid)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    ProjectInitializer().initialize(
        root,
        project_id="PROJECT-RELEASE-EDGES",
        name="Release edge fixture",
        project_type=ProjectType.BACKEND,
        schema_version="1.0",
    )
    return root


def _plugin_project(tmp_path: Path, *, version: str) -> tuple[Path, str]:
    root = _project(tmp_path)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Plugin Package Test")
    _git(root, "config", "user.email", "plugin-package@example.invalid")
    plugin = root / "plugins" / "ai-engineering-os"
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / "skills").mkdir()
    (plugin / "skills" / "README.md").write_text("# Skills\n", encoding="utf-8")
    (plugin / ".mcp.json").write_text("{}\n", encoding="utf-8")
    (plugin / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "ai-engineering-os",
                "version": version,
                "description": "Plugin package fixture",
                "skills": "./skills/",
                "mcpServers": "./.mcp.json",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "chore: add plugin fixture")
    return root, _git(root, "rev-parse", "HEAD")


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return completed.stdout.strip()


def _operation(request: dict[str, object]) -> HostOperation:
    return HostOperation(
        operation_id="OP-RELEASE",
        project_id="PROJECT-RELEASE-EDGES",
        run_id="RUN-RELEASE",
        kind=HostOperationKind.RELEASE_PREPARE,
        idempotency_key="release-prepare",
        request_hash="a" * 64,
        status=HostOperationStatus.RUNNING,
        state_version=1,
        attempt_count=1,
        request=request,
        result={},
        created_at="2026-08-29T00:00:00+00:00",
        updated_at="2026-08-29T00:00:00+00:00",
    )
