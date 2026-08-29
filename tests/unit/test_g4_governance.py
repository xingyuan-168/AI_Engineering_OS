# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from codex_ai_os.application.g4 import (
    GitHubReleaseGovernanceService,
    ReleaseGovernanceError,
    _asset_map,
    _decode_json,
    _is_missing_release,
    _materialize_asset,
    _require_within,
)
from codex_ai_os.application.project import ProjectInitializer
from codex_ai_os.application.workflow import WorkflowEngine
from codex_ai_os.domain.config import GitPushPolicy, ProjectType
from codex_ai_os.domain.governance import G4ApprovalInput, ReleaseAuthority


def test_g4_fails_closed_before_external_publication(tmp_path: Path) -> None:
    root = _project(tmp_path / "preconditions")
    service = GitHubReleaseGovernanceService(root, runner=_NeverRunner())
    run = WorkflowEngine(root).start("Exercise G4 preconditions").run
    approval = _approval()

    wrong_version = G4ApprovalInput.model_construct(
        pr_number=approval.pr_number,
        pr_url=approval.pr_url,
        merge_commit=approval.merge_commit,
        version="0.3.0",
        release_authority=approval.release_authority,
    )
    with pytest.raises(ReleaseGovernanceError) as mismatch:
        service.authorize_and_publish(run, wrong_version)
    assert mismatch.value.code == "VERSION_MISMATCH"

    with pytest.raises(ReleaseGovernanceError) as incomplete:
        service.authorize_and_publish(run, approval)
    assert incomplete.value.code == "RELEASE_INCOMPLETE"

    coordinated = run.model_copy(
        update={"integration_branch": "workflow/run/integration", "integration_head": "a" * 40}
    )
    invalid_url = approval.model_copy(
        update={"pr_url": "https://gitlab.example/acme/os/pull/42"}
    )
    with pytest.raises(ReleaseGovernanceError) as invalid_pr:
        service.authorize_and_publish(coordinated, invalid_url)
    assert invalid_pr.value.code == "GITHUB_PR_INVALID"

    with pytest.raises(ReleaseGovernanceError) as no_candidate:
        service.authorize_and_publish(coordinated, approval)
    assert no_candidate.value.code == "RELEASE_INCOMPLETE"


def test_g4_command_json_tag_and_release_failures_are_auditable(tmp_path: Path) -> None:
    root = _project(tmp_path / "commands")
    failing = GitHubReleaseGovernanceService(root, runner=_RaisingRunner())
    assert failing._raw("git", "status").returncode == 1
    with pytest.raises(ReleaseGovernanceError) as command:
        failing._command("git", "fetch", "origin")
    assert command.value.code == "GITHUB_RELEASE_BLOCKED"

    invalid_json = GitHubReleaseGovernanceService(
        root, runner=lambda command, _cwd, _timeout: _result(command, stdout="not-json")
    )
    with pytest.raises(ReleaseGovernanceError) as malformed:
        invalid_json._json_command("gh", "pr", "view", "42")
    assert malformed.value.code == "GITHUB_PR_INVALID"
    non_object = GitHubReleaseGovernanceService(
        root, runner=lambda command, _cwd, _timeout: _result(command, stdout="[]")
    )
    with pytest.raises(ReleaseGovernanceError, match="non-object"):
        non_object._json_command("gh", "pr", "view", "42")

    local_conflict = GitHubReleaseGovernanceService(
        root,
        runner=lambda command, _cwd, _timeout: _result(command, stdout="b" * 40),
    )
    with pytest.raises(ReleaseGovernanceError) as local:
        local_conflict._ensure_tag("v0.2.0", "a" * 40, "release-owner")
    assert local.value.code == "TAG_CONFLICT"

    remote_conflict = GitHubReleaseGovernanceService(
        root, runner=_TagRunner(local="a" * 40, remote="b" * 40)
    )
    with pytest.raises(ReleaseGovernanceError) as remote:
        remote_conflict._ensure_tag("v0.2.0", "a" * 40, "release-owner")
    assert remote.value.code == "TAG_CONFLICT"

    created = _TagRunner(local=None, remote=None)
    GitHubReleaseGovernanceService(root, runner=created)._ensure_tag(
        "v0.2.0", "a" * 40, "release-owner"
    )
    assert any(command[:2] == ["git", "tag"] for command in created.commands)
    assert any(command[:2] == ["git", "push"] for command in created.commands)

    artifacts = root / ".codex-os" / "artifacts" / "test"
    artifacts.mkdir(parents=True)
    (artifacts / "artifact.whl").write_bytes(b"wheel")
    release_runner = _ReleaseRunner()
    service = GitHubReleaseGovernanceService(root, runner=release_runner)
    release = service._ensure_draft_release("v0.2.0", "e" * 40)
    assert release["id"] == "R_1"
    assert release["isDraft"] is True
    assert any(command[:3] == ["gh", "release", "create"] for command in release_runner.commands)

    asset_runner = _AssetRunner()
    asset_service = GitHubReleaseGovernanceService(root, runner=asset_runner)
    reconciled = asset_service._reconcile_assets(
        "v0.2.0",
        asset_runner.release(),
        {"artifact.whl": artifacts / "artifact.whl"},
    )
    assert reconciled["artifact.whl"]["sha256"]
    published = asset_service._publish_release("v0.2.0", asset_runner.release())
    assert published["isDraft"] is False


def test_final_release_manifest_is_idempotent_and_immutable(tmp_path: Path) -> None:
    root = _project(tmp_path / "manifest")
    service = GitHubReleaseGovernanceService(root, runner=_NeverRunner())
    run = WorkflowEngine(root).start("Write final manifest").run.model_copy(
        update={"integration_branch": "workflow/run/integration", "integration_head": "a" * 40}
    )
    release: dict[str, object] = {
        "source_commit": "9" * 40,
        "integration_source_commit": "9" * 40,
        "candidate_commit": "8" * 40,
        "manifest_hash": "1" * 64,
        "sbom_hash": "2" * 64,
        "checksums_hash": "3" * 64,
        "rollback_hash": "4" * 64,
        "registry_index_digest": "5" * 64,
        "platform_digest": "6" * 64,
    }
    first = service._write_final_manifest(
        run=run,
        approval=_approval(),
        release=release,
        tag="v0.2.0",
        release_id="R_1",
        release_url="https://github.com/example/ai-os/releases/tag/v0.2.0",
        reconciled_assets={"artifact.whl": {"sha256": "7" * 64}},
    )
    assert service._write_final_manifest(
        run=run,
        approval=_approval(),
        release=release,
        tag="v0.2.0",
        release_id="R_1",
        release_url="https://github.com/example/ai-os/releases/tag/v0.2.0",
        reconciled_assets={"artifact.whl": {"sha256": "7" * 64}},
    ) == first
    target = root / first[0]
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["requirement_baseline"] == "REQ-1.6.2"
    target.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ReleaseGovernanceError) as changed:
        service._write_final_manifest(
            run=run,
            approval=_approval(),
            release=release,
            tag="v0.2.0",
            release_id="R_1",
            release_url="https://github.com/example/ai-os/releases/tag/v0.2.0",
            reconciled_assets={"artifact.whl": {"sha256": "7" * 64}},
        )
    assert changed.value.code == "RELEASE_SOURCE_CHANGED"


def test_release_assets_resume_partial_and_unknown_uploads(tmp_path: Path) -> None:
    root = _project(tmp_path / "asset-reconcile")
    first = root / ".codex-os" / "artifacts" / "first.whl"
    second = root / ".codex-os" / "artifacts" / "second.tar.gz"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    partial = _AssetRunner()
    partial.assets[first.name] = first.read_bytes()
    service = GitHubReleaseGovernanceService(root, runner=partial)
    reconciled = service._reconcile_assets(
        "v0.2.0", partial.release(), {first.name: first, second.name: second}
    )
    assert set(reconciled) == {first.name, second.name}
    partial.draft = False
    assert service._reconcile_assets(
        "v0.2.0", partial.release(), {first.name: first, second.name: second}
    ) == reconciled

    conflicting = _AssetRunner()
    conflicting.assets[first.name] = b"different"
    with pytest.raises(ReleaseGovernanceError) as drifted:
        GitHubReleaseGovernanceService(root, runner=conflicting)._reconcile_assets(
            "v0.2.0", conflicting.release(), {first.name: first}
        )
    assert drifted.value.code == "GITHUB_RELEASE_ASSET_CONFLICT"

    unknown = _AssetRunner(fail_after_upload_once=True)
    unknown_service = GitHubReleaseGovernanceService(root, runner=unknown)
    with pytest.raises(ReleaseGovernanceError) as interrupted:
        unknown_service._reconcile_assets(
            "v0.2.0", unknown.release(), {first.name: first}
        )
    assert interrupted.value.code == "GITHUB_RELEASE_BLOCKED"
    assert unknown_service._reconcile_assets(
        "v0.2.0", unknown.release(), {first.name: first}
    )[first.name]["sha256"]


def test_g4_json_asset_and_materialization_helpers_fail_closed(tmp_path: Path) -> None:
    assert _decode_json(_result([], stdout='{"ok": true}')) == {"ok": True}
    for value in ("not-json", "[]"):
        with pytest.raises(ReleaseGovernanceError) as invalid:
            _decode_json(_result([], stdout=value))
        assert invalid.value.code == "GITHUB_PR_INVALID"

    assert _asset_map({"assets": [{"name": "artifact.whl", "id": "A1"}]})[
        "artifact.whl"
    ]["id"] == "A1"
    invalid_assets: tuple[object, ...] = (
        {"bad": 1},
        ["invalid"],
        [{"name": ""}],
        [{"name": "same"}, {"name": "same"}],
    )
    for assets in invalid_assets:
        with pytest.raises(ReleaseGovernanceError):
            _asset_map({"assets": assets})

    managed = tmp_path / "managed"
    managed.mkdir()
    source = managed / "source.whl"
    source.write_bytes(b"wheel")
    target = managed / "publication" / "artifact.whl"
    assert _materialize_asset(source, target).read_bytes() == b"wheel"
    assert _materialize_asset(source, target) == target.resolve()
    target.write_bytes(b"different")
    with pytest.raises(ReleaseGovernanceError, match="differs"):
        _materialize_asset(source, target)
    with pytest.raises(ReleaseGovernanceError, match="regular file"):
        _materialize_asset(managed / "missing", managed / "other")
    residue_target = managed / "residue.whl"
    residue_target.with_name(".residue.whl.tmp").write_bytes(b"residue")
    with pytest.raises(ReleaseGovernanceError) as residue:
        _materialize_asset(source, residue_target)
    assert residue.value.code == "OPERATION_RECONCILE_REQUIRED"

    _require_within(source, managed, "asset")
    with pytest.raises(ReleaseGovernanceError) as escaped:
        _require_within(tmp_path / "outside", managed, "asset")
    assert escaped.value.code == "PATH_ESCAPE"
    assert _is_missing_release(_result([], returncode=1, stderr="HTTP 404"))
    assert not _is_missing_release(_result([], returncode=1, stderr="permission denied"))


def test_g4_remote_asset_and_release_state_fail_closed(tmp_path: Path) -> None:
    root = _project(tmp_path / "remote-edges")
    service = GitHubReleaseGovernanceService(root, runner=_AssetRunner())
    assets: tuple[dict[str, object], ...] = (
        {},
        {"apiUrl": "http://api.github.com/assets/1"},
        {"apiUrl": "https://user@example.invalid/assets/1"},
    )
    for asset in assets:
        with pytest.raises(ReleaseGovernanceError) as untrusted:
            service._remote_asset_hash(asset)
        assert untrusted.value.code == "GITHUB_RELEASE_BLOCKED"
    unavailable = GitHubReleaseGovernanceService(root, runner=_RaisingRunner())
    with pytest.raises(ReleaseGovernanceError, match="download"):
        unavailable._remote_asset_hash(
            {"apiUrl": "https://api.github.com/repos/example/releases/assets/1"}
        )
    with pytest.raises(ReleaseGovernanceError, match="inspect"):
        unavailable._release_data("v0.2.0")
    with pytest.raises(ReleaseGovernanceError) as ancestry:
        unavailable._require_ancestor("a" * 40, "b" * 40)
    assert ancestry.value.code == "GIT_EVIDENCE_INVALID"

    artifact = root / "artifact.whl"
    artifact.write_bytes(b"wheel")
    with pytest.raises(ReleaseGovernanceError, match="unauthorized"):
        service._reconcile_assets(
            "v0.2.0",
            {
                "isDraft": True,
                "assets": [
                    {
                        "name": "extra.bin",
                        "apiUrl": "https://api.github.com/assets/1",
                    }
                ],
            },
            {artifact.name: artifact},
        )
    with pytest.raises(ReleaseGovernanceError, match="missing asset"):
        service._reconcile_assets(
            "v0.2.0", {"isDraft": False, "assets": []}, {artifact.name: artifact}
        )
    with pytest.raises(ReleaseGovernanceError, match="regular file"):
        service._reconcile_assets(
            "v0.2.0",
            {"isDraft": True, "assets": []},
            {"missing.whl": root / "missing.whl"},
        )

    class _StillDraftRunner(_AssetRunner):
        def __call__(
            self, command: list[str], cwd: Path, timeout: float
        ) -> subprocess.CompletedProcess[bytes]:
            if command[:3] == ["gh", "release", "edit"]:
                return _result(command)
            return super().__call__(command, cwd, timeout)

    still_draft = GitHubReleaseGovernanceService(root, runner=_StillDraftRunner())
    with pytest.raises(ReleaseGovernanceError, match="still a draft"):
        still_draft._publish_release("v0.2.0", {"isDraft": True})


class _NeverRunner:
    def __call__(
        self, command: list[str], _cwd: Path, _timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError(f"unexpected external command: {command}")


class _RaisingRunner:
    def __call__(
        self, _command: list[str], _cwd: Path, _timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        raise OSError("unavailable")


class _TagRunner:
    def __init__(self, *, local: str | None, remote: str | None) -> None:
        self.local = local
        self.remote = remote
        self.commands: list[list[str]] = []

    def __call__(
        self, command: list[str], _cwd: Path, _timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(command)
        if command[:3] == ["git", "rev-parse", "refs/tags/v0.2.0^{}"]:
            return _result(command, returncode=1) if self.local is None else _result(
                command, stdout=self.local
            )
        if command[:3] == ["git", "ls-remote", "--tags"]:
            stdout = "" if self.remote is None else (
                f"{self.remote}\trefs/tags/v0.2.0^{{}}\n"
            )
            return _result(command, stdout=stdout)
        return _result(command)


class _ReleaseRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.views = 0

    def __call__(
        self, command: list[str], _cwd: Path, _timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(command)
        if command[:3] == ["gh", "release", "view"]:
            self.views += 1
            if self.views == 1:
                return _result(command, returncode=1, stderr="release not found")
            return _result(
                command,
                stdout=json.dumps(
                    {
                        "id": "R_1",
                        "url": "https://github.com/example/ai-os/releases/tag/v0.2.0",
                        "isDraft": True,
                        "tagName": "v0.2.0",
                        "assets": [],
                    }
                ),
            )
        return _result(command)


class _AssetRunner:
    def __init__(self, *, fail_after_upload_once: bool = False) -> None:
        self.assets: dict[str, bytes] = {}
        self.draft = True
        self.fail_after_upload_once = fail_after_upload_once

    def release(self) -> dict[str, object]:
        return {
            "id": "R_1",
            "url": "https://github.com/example/ai-os/releases/tag/v0.2.0",
            "isDraft": self.draft,
            "tagName": "v0.2.0",
            "assets": [
                {
                    "id": index,
                    "name": name,
                    "apiUrl": f"https://api.github.com/assets/{index}",
                    "size": len(content),
                }
                for index, (name, content) in enumerate(sorted(self.assets.items()), 1)
            ],
        }

    def __call__(
        self, command: list[str], _cwd: Path, _timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        if command[:3] == ["gh", "release", "upload"]:
            path = Path(command[-1])
            self.assets[path.name] = path.read_bytes()
            if self.fail_after_upload_once:
                self.fail_after_upload_once = False
                return _result(command, returncode=1, stderr="connection reset")
            return _result(command)
        if command[:3] == ["gh", "release", "view"]:
            return _result(command, stdout=json.dumps(self.release()))
        if command[:2] == ["gh", "api"]:
            index = int(command[2].rsplit("/", 1)[1]) - 1
            content = sorted(self.assets.items())[index][1]
            return _result(command, stdout=content)
        if command[:3] == ["gh", "release", "edit"]:
            self.draft = False
            return _result(command)
        return _result(command)


def _approval() -> G4ApprovalInput:
    return G4ApprovalInput(
        pr_number=42,
        pr_url="https://github.com/example/ai-os/pull/42",
        merge_commit="e" * 40,
        version="0.2.0",
        release_authority=ReleaseAuthority(
            authorized=True,
            scope="tag-and-github-release",
            authorized_by="release-owner",
        ),
    )


def _project(root: Path) -> Path:
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    ProjectInitializer().initialize(
        root,
        project_id=f"PROJECT-{root.name.upper()}",
        name="G4 fixture",
        project_type=ProjectType.BACKEND,
        git_push_policy=GitPushPolicy.FIXTURE_LOCAL_ONLY,
        schema_version="1.1",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "chore: initialize G4 fixture")
    return root


def _result(
    command: list[str],
    *,
    returncode: int = 0,
    stdout: str | bytes = "",
    stderr: str = "failure",
) -> subprocess.CompletedProcess[bytes]:
    output = stdout if isinstance(stdout, bytes) else stdout.encode()
    return subprocess.CompletedProcess(command, returncode, output, stderr.encode())


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
