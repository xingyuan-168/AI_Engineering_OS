from __future__ import annotations

from pathlib import Path

from codex_ai_os.core.gates import (
    evaluate_code_start,
    evaluate_finish,
    evaluate_frontend,
    formal_write_blockers,
)

REQUIREMENT = "REQ-TEST"

RESEARCH_COMPLETE = (
    "# Research\n\n## Requirement\n\nrequirement_id: REQ-TEST\n"
    "summary: Pick an orchestration layer for the sample integration.\n"
    "\n## Candidates\n\n### Project A\n\n- URL: https://github.com/org/a\n"
    "\n## Decision\n\ndecision: build\nreason: none of the candidates fit the boundary.\n"
)


class FakeGitRunner:
    def __init__(self, status: str = "") -> None:
        self.status = status

    def run(self, *args: str, timeout: float = 30.0):
        from types import SimpleNamespace

        if args[:2] == ("rev-parse", "--show-toplevel"):
            return SimpleNamespace(returncode=0, stdout="/repo\n", stderr="")
        if args[:1] == ("remote",):
            url = "https://github.com/org/repo.git"
            return SimpleNamespace(returncode=0, stdout=url + "\n", stderr="")
        if args[:1] == ("ls-remote",):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:1] == ("ls-files",):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ("diff", "--name-only"):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:1] == ("status",):
            return SimpleNamespace(returncode=0, stdout=self.status, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _research(root: Path, text: str = RESEARCH_COMPLETE) -> Path:
    path = root / "docs" / "OPEN_SOURCE_RESEARCH.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _start(
    root: Path,
    change_class: str,
    status: str = "",
    requirement_id: str | None = REQUIREMENT,
):
    return evaluate_code_start(
        root,
        change_class=change_class,
        requirement_id=requirement_id,
        research_path="docs/OPEN_SOURCE_RESEARCH.md",
        github_hosts=("github.com",),
        runner=FakeGitRunner(status=status),
    )


def test_exempt_change_class_needs_no_research(tmp_path: Path) -> None:
    decision = _start(tmp_path, "bugfix")
    assert decision.allowed is True
    assert not any("RESEARCH" in f.code for f in decision.findings)


def test_research_required_class_blocks_without_document(tmp_path: Path) -> None:
    decision = _start(tmp_path, "major_feature")
    assert decision.allowed is False
    assert any(f.code == "OPEN_SOURCE_RESEARCH_MISSING" and f.blocking for f in decision.findings)


def test_research_gated_class_requires_requirement_id(tmp_path: Path) -> None:
    _research(tmp_path)
    decision = _start(tmp_path, "major_feature", requirement_id=None)
    assert decision.allowed is False
    missing = [
        f for f in decision.findings
        if f.code == "OPEN_SOURCE_RESEARCH_MISSING" and f.blocking
    ]
    assert missing


def test_empty_research_template_does_not_pass(tmp_path: Path) -> None:
    template = "# Open Source Research\n\n## Requirement\n\n## Candidates\n\n## Decision\n"
    _research(tmp_path, template)
    decision = _start(tmp_path, "major_feature")
    assert decision.allowed is False
    codes = {f.code for f in decision.findings}
    assert "OPEN_SOURCE_RESEARCH_STALE" in codes
    assert "OPEN_SOURCE_RESEARCH_INCOMPLETE" in codes


def test_decision_heading_without_metadata_does_not_pass(tmp_path: Path) -> None:
    _research(
        tmp_path,
        "# Research\n\n## Requirement\n\nrequirement_id: REQ-TEST\n\n## Decision\n\n- build\n",
    )
    decision = _start(tmp_path, "major_feature")
    assert decision.allowed is False
    codes = {f.code for f in decision.findings}
    assert "OPEN_SOURCE_RESEARCH_INCOMPLETE" in codes


def test_decision_without_reason_does_not_pass(tmp_path: Path) -> None:
    _research(
        tmp_path,
        RESEARCH_COMPLETE.replace(
            "reason: none of the candidates fit the boundary.\n", ""
        ),
    )
    decision = _start(tmp_path, "major_feature")
    assert decision.allowed is False
    assert any(f.code == "OPEN_SOURCE_RESEARCH_INCOMPLETE" for f in decision.findings)


def test_stale_requirement_does_not_unlock_new_work(tmp_path: Path) -> None:
    _research(tmp_path, RESEARCH_COMPLETE.replace("REQ-TEST", "REQ-OLD"))
    decision = _start(tmp_path, "major_feature")
    assert decision.allowed is False
    stale = [
        f for f in decision.findings
        if f.code == "OPEN_SOURCE_RESEARCH_STALE" and f.blocking
    ]
    assert stale


def test_complete_research_for_current_requirement_allows_start(tmp_path: Path) -> None:
    _research(tmp_path)
    decision = _start(tmp_path, "major_feature")
    assert decision.allowed is True


def test_explicit_no_candidate_statement_allows_start(tmp_path: Path) -> None:
    _research(
        tmp_path,
        RESEARCH_COMPLETE.replace(
            "### Project A\n\n- URL: https://github.com/org/a\n",
            "没有合适候选: no second runtime dependency is needed.\n",
        ),
    )
    decision = _start(tmp_path, "major_feature")
    assert decision.allowed is True


def test_formal_write_blockers_allow_ready_repo(tmp_path: Path) -> None:
    decision = formal_write_blockers(tmp_path, runner=FakeGitRunner())
    assert decision.allowed is True


def test_formal_write_blockers_fail_without_git(tmp_path: Path) -> None:
    decision = formal_write_blockers(tmp_path, runner=FakeGitRunner())
    class NotGit(FakeGitRunner):
        def run(self, *args: str, timeout: float = 30.0):
            if args[:2] == ("rev-parse", "--show-toplevel"):
                from types import SimpleNamespace

                return SimpleNamespace(returncode=1, stdout="", stderr="not a repo")
            return super().run(*args, timeout=timeout)

    decision = formal_write_blockers(tmp_path, runner=NotGit())
    assert decision.allowed is False
    assert any(f.code == "NOT_GIT_REPOSITORY" for f in decision.findings)


def test_user_uncommitted_work_does_not_block_start(tmp_path: Path) -> None:
    _research(tmp_path)
    decision = _start(tmp_path, "major_feature", status="?? notes.txt\n")
    # Code Start never flags or blocks on the user's own uncommitted work.
    assert not any(f.code == "UNCOMMITTED_WORK" for f in decision.findings)
    assert not any(f.code == "COPY_STYLE" for f in decision.findings)
    assert decision.allowed is True


def test_user_uncommitted_work_warns_but_does_not_block_finish(tmp_path: Path) -> None:
    decision = evaluate_finish(
        tmp_path,
        tests_passed=True,
        docs_synced=True,
        memory_not_needed=True,
        runner=FakeGitRunner(status="?? notes.txt\n"),
    )
    uncommitted = [f for f in decision.findings if f.code == "UNCOMMITTED_WORK"]
    assert uncommitted, decision.findings
    for finding in uncommitted:
        assert finding.blocking is False
    assert decision.allowed is True


def test_copy_style_directory_blocks_start(tmp_path: Path) -> None:
    _research(tmp_path)
    (tmp_path / "docs" / "backup").mkdir(parents=True)
    (tmp_path / "docs" / "backup" / "old.md").write_text("x", encoding="utf-8")
    decision = _start(tmp_path, "major_feature")
    assert decision.allowed is False
    assert any(f.code == "COPY_STYLE_DIRECTORY" and f.blocking for f in decision.findings)


def test_root_version_directory_blocks_but_nested_is_fine(tmp_path: Path) -> None:
    (tmp_path / "api" / "v1").mkdir(parents=True)
    (tmp_path / "api" / "v1" / "routes.py").write_text("x", encoding="utf-8")
    decision = _start(tmp_path, "bugfix")
    assert not any(f.code == "COPY_STYLE_DIRECTORY" for f in decision.findings)
    (tmp_path / "src_v2").mkdir()
    decision = _start(tmp_path, "bugfix")
    assert any(f.code == "COPY_STYLE_DIRECTORY" and f.blocking for f in decision.findings)


def test_unreachable_remote_blocks_start(tmp_path: Path) -> None:
    _research(tmp_path)
    from types import SimpleNamespace

    class Unreachable(FakeGitRunner):
        def run(self, *args: str, timeout: float = 30.0):
            if args[:1] == ("ls-remote",):
                return SimpleNamespace(returncode=128, stdout="", stderr="could not read")
            return super().run(*args, timeout=timeout)

    decision = evaluate_code_start(
        tmp_path,
        change_class="bugfix",
        research_path="docs/OPEN_SOURCE_RESEARCH.md",
        github_hosts=("github.com",),
        runner=Unreachable(),
    )
    assert decision.allowed is False
    assert any(f.code == "GITHUB_REMOTE_UNREACHABLE" for f in decision.findings)


def test_frontend_gate_layering(tmp_path: Path) -> None:
    exempt = evaluate_frontend(
        tmp_path,
        impact="copy_change",
        approved=False,
        prototype_path="docs/design/PROTOTYPE.html",
        ui_spec_path="docs/design/UI_SPEC.md",
    )
    assert exempt.allowed is True
    blocked = evaluate_frontend(
        tmp_path,
        impact="new_page",
        approved=False,
        prototype_path="docs/design/PROTOTYPE.html",
        ui_spec_path="docs/design/UI_SPEC.md",
    )
    assert blocked.allowed is False
    codes = {f.code for f in blocked.findings}
    assert "FRONTEND_PROTOTYPE_MISSING" in codes
    assert "FRONTEND_UI_SPEC_MISSING" in codes
    assert "FRONTEND_APPROVAL_MISSING" in codes
    prototype = tmp_path / "docs" / "design" / "PROTOTYPE.html"
    prototype.parent.mkdir(parents=True, exist_ok=True)
    prototype.write_text("<html></html>", encoding="utf-8")
    (tmp_path / "docs" / "design" / "UI_SPEC.md").write_text("# UI\n", encoding="utf-8")
    allowed = evaluate_frontend(
        tmp_path,
        impact="new_page",
        approved=True,
        prototype_path="docs/design/PROTOTYPE.html",
        ui_spec_path="docs/design/UI_SPEC.md",
    )
    assert allowed.allowed is True


def test_finish_gate_requires_task_facts(tmp_path: Path) -> None:
    blocked = evaluate_finish(
        tmp_path,
        tests_passed=False,
        docs_synced=False,
        runner=FakeGitRunner(),
    )
    assert blocked.allowed is False
    codes = {f.code for f in blocked.findings}
    assert "TESTS_NOT_PASSED" in codes
    assert "DOCS_NOT_SYNCED" in codes
    assert "MEMORY_MISSING" in codes
    allowed = evaluate_finish(
        tmp_path,
        tests_passed=True,
        docs_synced=True,
        memory_written=False,
        memory_not_needed=True,
        runner=FakeGitRunner(),
    )
    assert allowed.allowed is True


def test_finish_without_memory_fact_blocks(tmp_path: Path) -> None:
    decision = evaluate_finish(
        tmp_path,
        tests_passed=True,
        docs_synced=True,
        memory_written=False,
        memory_not_needed=False,
        runner=FakeGitRunner(),
    )
    assert decision.allowed is False
    assert any(f.code == "MEMORY_MISSING" for f in decision.findings)
