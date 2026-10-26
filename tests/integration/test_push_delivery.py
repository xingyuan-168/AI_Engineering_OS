"""Real isolated Git histories; only the network transport is replaced by local bare repos."""

from __future__ import annotations

import asyncio
import json
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from codex_ai_os.adapters.git import GitRunner
from codex_ai_os.application.finish_execution import query_execution, run_finish

REMOTE_REF = "refs/heads/task"
URL = "https://github.com/synthetic/delivery.git"


@pytest.fixture
def delivery_repo(governed_repo, monkeypatch):
    root = governed_repo
    bare = root.parent / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    original = GitRunner.run
    probes = []
    transports = {URL: bare}

    def transport(self, *args, **kwargs):
        if args[0] == "ls-remote":
            probes.append(args)
            args = (*args[:2], str(transports[args[2]]), *args[3:])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(GitRunner, "run", transport)
    git = GitRunner(root)
    assert git.run("remote", "add", "cv-ocr", URL).returncode == 0
    base = git.run("rev-parse", "HEAD").stdout.strip()

    def publish(target=bare):
        assert git.run("push", str(target), "HEAD:" + REMOTE_REF).returncode == 0

    def commit(name="readme.md", content="new documentation\n"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content, encoding="utf-8")
        assert git.run("add", "--", name).returncode == 0
        assert git.run("commit", "-qm", "docs: fixture change").returncode == 0
        return git.run("rev-parse", "HEAD").stdout.strip()

    def finish(**kwargs):
        args: dict[str, Any] = dict(
            base_ref=base,
            test_command=None,
            memory_not_needed=True,
            remote="cv-ocr",
            remote_ref=REMOTE_REF,
        )
        args.update(kwargs)
        return run_finish(root, **args)

    return SimpleNamespace(
        root=root,
        bare=bare,
        git=git,
        base=base,
        commit=commit,
        publish=publish,
        finish=finish,
        probes=probes,
        transports=transports,
    )


def test_finish_requires_explicit_delivery_target(delivery_repo):
    record = run_finish(
        delivery_repo.root,
        base_ref=delivery_repo.base,
        test_command=None,
        memory_not_needed=True,
    )
    assert not record["allowed"]
    assert "PUSH_TARGET_REQUIRED" in record["blocked_by"]


def test_unpublished_document_commit_blocks(delivery_repo):
    repo = delivery_repo
    repo.publish()
    repo.commit()
    record = repo.finish()
    assert record["execution_status"] == "completed" and not record["allowed"]
    assert "PUSH_REQUIRED" in record["blocked_by"]
    assert not record["delivery"]["verified"]


def test_published_commit_passes_with_user_materials_preserved(delivery_repo):
    repo = delivery_repo
    commit = repo.commit()
    repo.publish()
    user_file = repo.root / "用户资料.txt"
    user_file.write_text("user material\n", encoding="utf-8")
    record = repo.finish()
    assert record["allowed"], record
    assert record["delivery"]["commit"] == commit
    assert record["delivery"]["verified"]
    assert record["delivery"]["remote"] == "cv-ocr"
    assert record["delivery"]["remote_ref"] == REMOTE_REF
    assert len(repo.probes) == 1
    assert user_file.read_text(encoding="utf-8") == "user material\n"


def test_missing_remote_branch_blocks(delivery_repo):
    record = delivery_repo.finish()
    assert "PUSH_REF_MISSING" in record["blocked_by"]


def test_tracked_uncommitted_change_cannot_claim_old_pushed_head(delivery_repo):
    repo = delivery_repo
    repo.commit()
    repo.publish()
    (repo.root / "readme.md").write_text("not delivered\n", encoding="utf-8")
    record = repo.finish()
    assert "PUSH_UNCOMMITTED_CHANGES" in record["blocked_by"]


def test_second_push_destination_must_also_contain_commit(delivery_repo):
    repo = delivery_repo
    second = repo.root.parent / "second.git"
    subprocess.run(["git", "init", "--bare", "-q", str(second)], check=True)
    other = "https://github.com/synthetic/second.git"
    repo.transports[other] = second
    repo.git.run("remote", "set-url", "--add", "--push", "cv-ocr", URL)
    repo.git.run("remote", "set-url", "--add", "--push", "cv-ocr", other)
    repo.publish()
    record = repo.finish()
    assert not record["allowed"]
    assert "PUSH_REF_MISSING" in record["blocked_by"]
    assert len(repo.probes) == 2
    repo.publish(second)
    assert repo.finish()["allowed"]


def test_record_query_never_refreshes_or_changes_remote_evidence(delivery_repo):
    repo = delivery_repo
    repo.publish()
    record = repo.finish()
    path = repo.root / ".codex-os/state/check-runs" / (record["run_id"] + ".json")
    before = path.read_bytes()
    repo.commit()
    repo.probes.clear()
    query = query_execution(repo.root, record["run_id"])
    assert query["delivery"] == record["delivery"]
    assert path.read_bytes() == before
    assert not repo.probes


def test_legacy_passed_record_does_not_claim_delivery(delivery_repo):
    repo = delivery_repo
    repo.publish()
    record = repo.finish()
    path = repo.root / ".codex-os/state/check-runs" / (record["run_id"] + ".json")
    legacy = json.loads(path.read_text(encoding="utf-8"))
    legacy.pop("delivery", None)
    path.write_text(json.dumps(legacy), encoding="utf-8")
    before = path.read_bytes()
    query = query_execution(repo.root, record["run_id"])
    assert query["decision"]["allowed"]
    assert query["delivery"]["verified"] is None
    assert path.read_bytes() == before


def test_cached_remote_tracking_ref_cannot_replace_live_observation(delivery_repo):
    repo = delivery_repo
    repo.publish()
    repo.commit()
    repo.git.run("update-ref", "refs/remotes/cv-ocr/task", "HEAD")
    assert "PUSH_REQUIRED" in repo.finish()["blocked_by"]


def _advance_remote(repo):
    peer = repo.root.parent / "peer"
    subprocess.run(["git", "clone", "-q", "-b", "task", str(repo.bare), str(peer)], check=True)
    git = GitRunner(peer)
    git.run("config", "user.name", "Fixture")
    git.run("config", "user.email", "fixture@example.com")
    (peer / "remote-note.md").write_text("remote advance\n", encoding="utf-8")
    git.run("add", "remote-note.md")
    git.run("commit", "-qm", "docs: remote advance")
    assert git.run("push", str(repo.bare), "HEAD:" + REMOTE_REF).returncode == 0


@pytest.mark.parametrize("fetch_objects", [False, True])
def test_remote_advance_needs_actual_ancestry_objects(delivery_repo, fetch_objects):
    repo = delivery_repo
    repo.publish()
    _advance_remote(repo)
    if fetch_objects:
        assert repo.git.run("fetch", str(repo.bare), REMOTE_REF).returncode == 0
    record = repo.finish()
    assert record["allowed"] is fetch_objects
    if not fetch_objects:
        assert "PUSH_UNVERIFIABLE" in record["blocked_by"]


def test_diverged_remote_blocks(delivery_repo):
    repo = delivery_repo
    repo.publish()
    _advance_remote(repo)
    repo.commit()
    repo.git.run("fetch", str(repo.bare), REMOTE_REF)
    assert "PUSH_DIVERGED" in repo.finish()["blocked_by"]


def test_head_changes_during_checks_block(delivery_repo, monkeypatch):
    repo = delivery_repo
    repo.publish()

    def change_head(*args, **kwargs):
        repo.commit()
        return []

    monkeypatch.setattr("codex_ai_os.core.checks._test_command_findings", change_head)
    assert "PUSH_HEAD_CHANGED" in repo.finish()["blocked_by"]


@pytest.mark.parametrize("hidden_flag", ["--assume-unchanged", "--skip-worktree"])
def test_index_flags_do_not_hide_undelivered_content(delivery_repo, hidden_flag):
    repo = delivery_repo
    repo.commit()
    repo.publish()
    repo.git.run("update-index", hidden_flag, "readme.md")
    (repo.root / "readme.md").write_text("hidden change\n", encoding="utf-8")
    assert "PUSH_UNCOMMITTED_CHANGES" in repo.finish()["blocked_by"]


def test_cli_and_mcp_share_the_actual_delivery_contract(delivery_repo):
    from typer.testing import CliRunner

    from codex_ai_os.cli.app import app
    from codex_ai_os.cli.mcp_server import governance_check

    repo = delivery_repo
    repo.publish()
    cli = CliRunner().invoke(
        app,
        [
            "finish",
            str(repo.root),
            "--base-ref",
            repo.base,
            "--remote",
            "cv-ocr",
            "--remote-ref",
            REMOTE_REF,
            "--memory-not-needed",
            "--json",
        ],
    )
    mcp = asyncio.run(
        governance_check(
            str(repo.root),
            "finish",
            base_ref=repo.base,
            remote="cv-ocr",
            remote_ref=REMOTE_REF,
            memory_not_needed=True,
        )
    )
    assert cli.exit_code == 0, cli.output
    assert json.loads(cli.output)["data"]["allowed"] == mcp["data"]["allowed"] is True
    invalid = CliRunner().invoke(
        app,
        [
            "finish",
            str(repo.root),
            "--status",
            "--run-id",
            mcp["data"]["run_id"],
            "--remote-ref",
            "",
            "--json",
        ],
    )
    assert invalid.exit_code == 2


def test_target_and_fixture_errors_stop_before_long_commands(delivery_repo):
    repo = delivery_repo
    marker = repo.root / "must-not-run"
    record = repo.finish(remote_ref="main", test_command="unavailable-command")
    assert "PUSH_TARGET_INVALID" in record["blocked_by"]
    assert record["steps"]["test_command"]["status"] == "not_run"
    assert not marker.exists()
    config = repo.root / ".codex-os/project.yaml"
    config.write_text(
        config.read_text(encoding="utf-8") + "git_push_policy: fixture_local_only\n",
        encoding="utf-8",
    )
    assert "PUSH_FIXTURE_NOT_DELIVERY" in repo.finish()["blocked_by"]


def test_worktree_target_is_explicit_and_cleanup_does_not_require_main_merge(governed_repo):
    from codex_ai_os.cli.mcp_server import worktree_manage

    git = GitRunner(governed_repo)
    original = git.run("rev-parse", "HEAD").stdout.strip()
    assert git.run("branch", "codex/task").returncode == 0
    prepared = worktree_manage(
        str(governed_repo), "prepare", name="delivery-target", target_branch="codex/task"
    )
    assert prepared["ok"] and prepared["data"]["target_branch"] == "codex/task"
    path = governed_repo / prepared["data"]["path"]
    (path / "note.md").write_text("reviewed task\n", encoding="utf-8")
    working = GitRunner(path)
    working.run("add", "note.md")
    working.run("commit", "-qm", "docs: task")
    assert git.run("fetch", ".", prepared["data"]["branch"] + ":codex/task").returncode == 0
    assert worktree_manage(str(governed_repo), "finish", name="delivery-target")["ok"]
    result = worktree_manage(str(governed_repo), "cleanup", name="delivery-target")
    assert result["ok"], result
    assert not path.exists()
    assert git.run("rev-parse", "HEAD").stdout.strip() == original
