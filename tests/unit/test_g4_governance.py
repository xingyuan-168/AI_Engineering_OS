# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from codex_ai_os.application.g4 import (
    GitHubReleaseGovernanceService,
    ReleaseGovernanceError,
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
    release = GitHubReleaseGovernanceService(root, runner=release_runner)._ensure_github_release(
        "v0.2.0", artifacts
    )
    assert release["id"] == "R_1"
    assert any(command[:3] == ["gh", "release", "create"] for command in release_runner.commands)


def test_final_release_manifest_is_idempotent_and_immutable(tmp_path: Path) -> None:
    root = _project(tmp_path / "manifest")
    service = GitHubReleaseGovernanceService(root, runner=_NeverRunner())
    run = WorkflowEngine(root).start("Write final manifest").run.model_copy(
        update={"integration_branch": "workflow/run/integration", "integration_head": "a" * 40}
    )
    release: dict[str, object] = {
        "source_commit": "9" * 40,
        "manifest_hash": "1" * 64,
        "sbom_hash": "2" * 64,
        "checksums_hash": "3" * 64,
        "rollback_hash": "4" * 64,
    }
    first = service._write_final_manifest(
        run=run,
        approval=_approval(),
        release=release,
        tag="v0.2.0",
        release_id="R_1",
        release_url="https://github.com/example/ai-os/releases/tag/v0.2.0",
    )
    assert service._write_final_manifest(
        run=run,
        approval=_approval(),
        release=release,
        tag="v0.2.0",
        release_id="R_1",
        release_url="https://github.com/example/ai-os/releases/tag/v0.2.0",
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
        )
    assert changed.value.code == "RELEASE_SOURCE_CHANGED"


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
                return _result(command, returncode=1)
            return _result(
                command,
                stdout=json.dumps(
                    {
                        "id": "R_1",
                        "url": "https://github.com/example/ai-os/releases/tag/v0.2.0",
                        "isDraft": False,
                        "tagName": "v0.2.0",
                    }
                ),
            )
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
    command: list[str], *, returncode: int = 0, stdout: str = ""
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(command, returncode, stdout.encode(), b"failure")


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
