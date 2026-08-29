# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from codex_ai_os.application import release
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
