"""Read-only policy probes; deletion payloads are never executed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codex_ai_os.adapters.git import GitRunner
from codex_ai_os.application import cleanup_policy
from codex_ai_os.application.hook_gateway import authorize_hook_payload, explain_hook_payload
from codex_ai_os.cli.app import app
from codex_ai_os.core.gates import evaluate_finish, evaluate_frontend, write_frontend_approval
from codex_ai_os.infrastructure.config import resolve_runtime_root


def payload(cwd: Path, command: str, tool: str = "Bash") -> dict:
    return {"cwd": str(cwd), "tool_name": tool, "tool_input": {"command": command}}


def test_cleanup_is_about_target_not_cwd(
    governed_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp = tmp_path / "system-temp"
    leaf = temp / "本任务 build"
    leaf.mkdir(parents=True)
    asset = tmp_path / "user-assets"
    asset.mkdir()
    monkeypatch.setattr(cleanup_policy.tempfile, "gettempdir", lambda: str(temp))
    allowed = explain_hook_payload(
        payload(governed_repo, f'Remove-Item -LiteralPath "{leaf}" -Recurse -Force')
    )
    assert allowed["decision"] == "allow"
    assert allowed["rule_id"] == "CLEANUP_TARGET_CHECKED"
    denied = explain_hook_payload(
        payload(leaf, f'Remove-Item -LiteralPath "{asset}" -Recurse -Force')
    )
    assert denied["decision"] == "deny"
    assert leaf.is_dir() and asset.is_dir()


@pytest.mark.parametrize(
    "relative", ["", "build", "dist", "input", "output", ".git", "build/task/input"]
)
def test_cleanup_rejects_broad_and_protected_targets(governed_repo: Path, relative: str) -> None:
    target = governed_repo / relative
    target.mkdir(parents=True, exist_ok=True)
    result = explain_hook_payload(
        payload(governed_repo, f'Remove-Item -LiteralPath "{target}" -Recurse')
    )
    assert result["decision"] == "deny", result


def test_cleanup_rejects_tracked_files_and_protected_descendants(governed_repo: Path) -> None:
    target = governed_repo / "build/task"
    target.mkdir(parents=True)
    (target / "source.py").write_text("x = 1\n", encoding="utf-8")
    git = GitRunner(governed_repo)
    assert git.run("add", "-f", "build/task/source.py").returncode == 0
    result = explain_hook_payload(payload(governed_repo, 'rm -rf "build/task"'))
    assert result["decision"] == "deny"
    assert "tracked" in result["reason"]
    another = governed_repo / "build/another/input"
    another.mkdir(parents=True)
    assert (
        explain_hook_payload(payload(governed_repo, 'rm -rf "build/another"'))["decision"] == "deny"
    )


@pytest.mark.parametrize(
    "command",
    [
        "Remove-Item $target -Recurse",
        "rm -rf build/*",
        "cmd /c rd /s /q build/task",
        "cd build; rm -rf task",
        "rm -rf build/a build/b",
    ],
)
def test_cleanup_does_not_guess_unresolved_targets(governed_repo: Path, command: str) -> None:
    assert explain_hook_payload(payload(governed_repo, command))["decision"] == "deny"


def test_cleanup_and_write_reparse_boundaries(
    governed_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = governed_repo / "build/link"
    link.mkdir(parents=True)
    monkeypatch.setattr(Path, "is_junction", lambda path: path == link)
    assert explain_hook_payload(payload(governed_repo, "rm -rf build/link"))["decision"] == "deny"
    result = explain_hook_payload(
        payload(governed_repo, "*** Add File: build/link/file.txt\n+x", "apply_patch")
    )
    assert result["rule_id"] == "WRITE_TARGET_REPARSE"


def test_nested_and_external_cwd_cannot_bypass_target_owner(
    governed_repo: Path, tmp_path: Path
) -> None:
    nested = governed_repo / "docs/中文"
    nested.mkdir(parents=True)
    assert resolve_runtime_root(nested).checkout_root == governed_repo
    for cwd, path in (
        (nested, "../../input/file.txt"),
        (tmp_path, str(governed_repo / "input/资料.txt")),
    ):
        result = explain_hook_payload(payload(cwd, f"*** Update File: {path}\n+x", "apply_patch"))
        assert result["decision"] == "deny", result


def test_move_to_input_checks_source_and_destination(governed_repo: Path) -> None:
    patch = (
        "*** Begin Patch\n*** Update File: docs/a.md\n*** Move to: input/a.md\n"
        "@@\n-a\n+b\n*** End Patch"
    )
    result = authorize_hook_payload(payload(governed_repo, patch, "apply_patch"))
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_explain_never_executes_command(governed_repo: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["authorize-hook", "--explain"],
        input=json.dumps(payload(governed_repo, "git branch -d imaginary")),
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)["data"]
    assert data["layer"] == "aios"
    assert data["decision"] == "allow"
    assert {"rule_id", "targets", "reason", "next_step"} <= data.keys()


def test_frontend_revocation_and_content_binding(tmp_path: Path) -> None:
    spec = tmp_path / "docs/design/UI_SPEC.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# Reviewed design\n", encoding="utf-8")
    prototype = spec.with_name("PROTOTYPE.html")
    prototype.write_text("<html>reviewed</html>\n", encoding="utf-8")

    def approve() -> None:
        write_frontend_approval(
            spec, scope="dashboard", approved_by="user", approved_on="2026-09-24"
        )

    def allowed(scope: str = "dashboard") -> bool:
        return evaluate_frontend(tmp_path, impact="new_page", scope=scope).allowed

    approve()
    assert allowed()
    assert not allowed("other")
    write_frontend_approval(
        spec, scope="dashboard", approved_by="user", approved_on="2026-09-24", decision="rejected"
    )
    assert not allowed()
    approve()
    prototype.write_text("changed", encoding="utf-8")
    assert not allowed()
    approve()
    spec.write_text(spec.read_text(encoding="utf-8") + "New requirement\n", encoding="utf-8")
    assert not allowed()
    assert evaluate_frontend(tmp_path, impact="css_fix").allowed


def test_legacy_and_malformed_approval_do_not_pass(tmp_path: Path) -> None:
    spec = tmp_path / "docs/design/UI_SPEC.md"
    spec.parent.mkdir(parents=True)
    spec.with_name("PROTOTYPE.html").write_text("html", encoding="utf-8")
    for scope in ("default", '"unterminated'):
        spec.write_text(
            f"approval:\n  type: frontend\n  status: approved\n  scope: {scope}\n"
            "  approved_by: user\n  approved_at: 2026-09-24\n",
            encoding="utf-8",
        )
        assert not evaluate_frontend(tmp_path, impact="new_page").allowed


def test_finish_requires_base_and_includes_committed_unicode_paths(governed_repo: Path) -> None:
    git = GitRunner(governed_repo)
    base = git.run("rev-parse", "HEAD").stdout.strip()
    source = governed_repo / "src/中文.py"
    source.parent.mkdir()
    source.write_text("x = 1\n", encoding="utf-8")
    git.run("add", "src")
    git.run("commit", "-qm", "new source")
    missing = evaluate_finish(governed_repo, test_command=None, memory_not_needed=True)
    assert "FINISH_BASE_REQUIRED" in missing.blocked_by
    result = evaluate_finish(
        governed_repo, test_command=None, memory_not_needed=True, base_ref=base
    )
    assert "CODE_START_UNVERIFIED" in result.blocked_by
    assert any(f.path == "src/中文.py" for f in result.findings)


def test_finish_checks_staged_whitespace(governed_repo: Path) -> None:
    git = GitRunner(governed_repo)
    target = governed_repo / "note.md"
    target.write_text("trailing spaces  \n", encoding="utf-8")
    git.run("add", "note.md")
    target.write_text("working copy clean\n", encoding="utf-8")
    result = evaluate_finish(
        governed_repo, test_command=None, memory_not_needed=True, base_ref="HEAD"
    )
    assert any(f.code.startswith("GIT_DIFF_CHECK") and f.blocking for f in result.findings)


def test_finish_empty_tree_for_unborn_repo(tmp_path: Path) -> None:
    git = GitRunner(tmp_path)
    git.run("init", "-q")
    (tmp_path / ".codex-os").mkdir()
    (tmp_path / ".codex-os/project.yaml").write_text(
        "project_id: PROJECT-EMPTY\nname: Empty\nroot: .\n", encoding="utf-8"
    )
    result = evaluate_finish(
        tmp_path, test_command=None, memory_not_needed=True, base_ref="EMPTY_TREE"
    )
    assert result.allowed, result.findings
