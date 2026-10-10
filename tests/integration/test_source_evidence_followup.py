from __future__ import annotations

import shlex
import subprocess
import sys

import pytest

from codex_ai_os.adapters.git import GitRunner
from codex_ai_os.core.gates import hygiene_findings


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=True, encoding="utf-8"
    ).stdout.strip()


def dependency(root):
    dep = root / "vendor/dependency"
    dep.mkdir(parents=True)
    for args in (
        ("init", "-q"),
        ("config", "user.name", "Fixture"),
        ("config", "user.email", "fixture@example.com"),
        ("remote", "add", "origin", "https://github.com/opencv/opencv.git"),
    ):
        git(dep, *args)
    (dep / "copy.cpp").write_text("int upstream() { return 1; }\n", encoding="utf-8")
    git(dep, "add", "copy.cpp")
    git(dep, "commit", "-qm", "fixture pinned source")
    sha = git(dep, "rev-parse", "HEAD")
    (dep / ".git/FETCH_HEAD").write_text(
        sha + "\t\tbranch 'main' of https://github.com/opencv/opencv.git\n", encoding="utf-8"
    )
    return dep


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_upstream_proof_reads_content_despite_index_flags(governed_repo, flag):
    dep = dependency(governed_repo)
    assert not any(f.blocking for f in hygiene_findings(governed_repo, GitRunner(governed_repo)))
    git(dep, "update-index", flag, "copy.cpp")
    (dep / "copy.cpp").write_text("int project_copy() { return 2; }\n", encoding="utf-8")
    assert any(f.blocking for f in hygiene_findings(governed_repo, GitRunner(governed_repo)))


def test_nested_input_is_not_a_repository_input_waiver(governed_repo):
    path = governed_repo / "vendor/input/copy.cpp"
    path.parent.mkdir(parents=True)
    path.write_text("project copy\n", encoding="utf-8")
    assert any(
        f.path == "vendor/input/copy.cpp" and f.blocking
        for f in hygiene_findings(governed_repo, GitRunner(governed_repo))
    )


def test_upstream_crlf_is_content_equivalent(governed_repo):
    dep = dependency(governed_repo)
    git(dep, "config", "core.autocrlf", "true")
    path = dep / "copy.cpp"
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert not any(f.blocking for f in hygiene_findings(governed_repo, GitRunner(governed_repo)))


def test_external_content_filter_is_never_executed(governed_repo):
    dep = dependency(governed_repo)
    marker = dep / "filter-ran"
    script = dep / "filter.py"
    script.write_text("from pathlib import Path\nPath('filter-ran').touch()\n", encoding="utf-8")
    (dep / ".gitattributes").write_text("*.cpp filter=fixture\n", encoding="utf-8")
    git(
        dep,
        "config",
        "filter.fixture.clean",
        shlex.join([sys.executable.replace("\\", "/"), "filter.py"]),
    )
    findings = hygiene_findings(governed_repo, GitRunner(governed_repo))
    assert any(f.code == "PROVENANCE_CHECK_FAILED" and f.blocking for f in findings)
    assert not marker.exists()


def test_upstream_directory_checks_ignored_extra_content(governed_repo):
    dep = dependency(governed_repo)
    directory = dep / "manual_old"
    directory.mkdir()
    (directory / "readme.md").write_text("upstream document\n", encoding="utf-8")
    (dep / ".gitignore").write_text("*.ignored\n", encoding="utf-8")
    git(dep, "add", "manual_old", ".gitignore")
    git(dep, "commit", "-qm", "fixture upstream directory")
    sha = git(dep, "rev-parse", "HEAD")
    (dep / ".git/FETCH_HEAD").write_text(
        sha + "\t\tbranch 'main' of https://github.com/opencv/opencv.git\n", encoding="utf-8"
    )
    assert not any(f.blocking for f in hygiene_findings(governed_repo, GitRunner(governed_repo)))
    (directory / "unrecorded.ignored").write_text("unproven addition\n", encoding="utf-8")
    assert any(
        f.blocking and f.path == "vendor/dependency/manual_old"
        for f in hygiene_findings(governed_repo, GitRunner(governed_repo))
    )
