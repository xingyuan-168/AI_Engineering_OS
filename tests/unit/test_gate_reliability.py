from __future__ import annotations

import json
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from typer.testing import CliRunner

from codex_ai_os.adapters.git import GitRunner
from codex_ai_os.application.finish_execution import FinishExecution, query_execution
from codex_ai_os.cli.app import app
from codex_ai_os.core.gates import hygiene_findings
from codex_ai_os.core.github_remote import check_remote
from codex_ai_os.infrastructure.config import ConfigError, _load_yaml_mapping
from codex_ai_os.infrastructure.errors import DiagnosticError


@pytest.mark.parametrize("path", ["D:/资料 有空格/project.yaml", r"\\server\share\配置.yaml"])
def test_configuration_codes_preserve_path_and_exception(path):
    class Missing:
        def __str__(self):
            return path

        def read_text(self, **kwargs):
            raise FileNotFoundError("missing: " + path)

    with pytest.raises(ConfigError) as error:
        _load_yaml_mapping(cast(Path, Missing()))
    assert error.value.code == "CONFIG_MISSING"
    assert error.value.path == path
    assert error.value.details["exception"] == "missing: " + path


def test_cli_preflight_never_initializes_missing_configuration(tmp_path):
    result = CliRunner().invoke(app, ["finish", str(tmp_path), "--base-ref", "HEAD", "--json"])
    assert json.loads(result.output)["error"]["code"] == "CONFIG_MISSING"
    assert not (tmp_path / ".codex-os").exists()


def test_boot_identity_is_unchanged_after_disk_update(tmp_path):
    import codex_ai_os.runtime_identity as identity

    script = tmp_path / "runtime_identity.py"
    script.write_bytes(Path(identity.__file__).read_bytes())
    before = runpy.run_path(str(script))
    (tmp_path / "updated.py").write_text("pass\n")
    assert before["runtime_identity"]() == before["BOOT_IDENTITY"]
    assert (
        runpy.run_path(str(script))["BOOT_IDENTITY"]["build_fingerprint"]
        != before["BOOT_IDENTITY"]["build_fingerprint"]
    )


def test_remote_push_selection_and_distinct_upstream(governed_repo, monkeypatch):
    git = GitRunner(governed_repo)
    origin = "git@github.com:example/origin.git"
    target = "ssh://git@ssh.github.com:443/example/target.git"
    for args in (
        ("remote", "add", "origin", origin),
        ("remote", "add", "cv-ocr", target),
        ("config", "branch.main.remote", "origin"),
        ("config", "branch.main.pushRemote", "cv-ocr"),
    ):
        assert git.run(*args).returncode == 0
    calls = []
    original = GitRunner.run

    def run(self, *args, **kwargs):
        if args[0] == "ls-remote":
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(GitRunner, "run", run)
    result = check_remote(git, {"github.com"})
    assert result.selected == "cv-ocr" and result.upstream == "origin" and not result.errors
    assert calls == [("ls-remote", "--", target), ("ls-remote", "--", origin)]
    calls.clear()
    assert check_remote(git, {"github.com"}, remote="origin").selected == "origin"
    assert calls == [("ls-remote", "--", origin)]


def test_local_git_setup_does_not_consume_shared_network_budget(governed_repo, monkeypatch):
    from codex_ai_os.core import github_remote

    git = GitRunner(governed_repo)
    git.run("remote", "add", "origin", "git@github.com:example/upstream.git")
    git.run("remote", "add", "target", "git@github.com:example/target.git")
    git.run("config", "branch.main.remote", "origin")
    clock, budgets = [100.0], []
    original = GitRunner.run
    monkeypatch.setattr(github_remote, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def run(self, *args, **kwargs):
        if args[0] == "ls-remote":
            budgets.append(kwargs["timeout"])
            clock[0] += 1.75
            return subprocess.CompletedProcess(args, 0, "", "")
        clock[0] += 0.2
        return original(self, *args, **kwargs)

    monkeypatch.setattr(GitRunner, "run", run)
    result = check_remote(git, {"github.com"}, remote="target", timeout=5)
    assert not result.errors and budgets == [5, 3.25]


def test_remote_ambiguity_has_no_origin_fallback(governed_repo):
    git = GitRunner(governed_repo)
    git.run("remote", "add", "origin", "git@github.com:example/a.git")
    git.run("remote", "add", "other", "git@github.com:example/b.git")
    assert check_remote(git, {"github.com"}).errors[0][0] == "GITHUB_REMOTE_AMBIGUOUS"


def test_31_provenance_cases_and_hidden_copies(governed_repo):
    root = governed_repo
    git = GitRunner(root)
    (root / ".gitignore").write_text(".codex-os/state/\n.worktrees/\nbuild*/\nthird_party/\n")
    git.run("add", ".gitignore")
    git.run("commit", "-qm", "fixture ignore rules")
    for index in range(6):
        build = root / f"build-cv-{index}"
        build.mkdir()
        (build / "CMakeCache.txt").write_text(
            f"CMAKE_HOME_DIRECTORY:INTERNAL={root.as_posix()}\n"
            f"CMAKE_CACHEFILE_DIR:INTERNAL={build.as_posix()}\n",
            encoding="utf-8",
        )
        for path in (
            "CompilerIdCXX/Debug",
            "CompilerIdCXX/tmp",
            "VCTargetsPath/x64/Debug",
            "x64/Debug",
        ):
            (build / "CMakeFiles/3.31.6-msvc6" / path).mkdir(parents=True)
    dependency = root / "third_party/src/opencv-5.0.0"
    dependency.mkdir(parents=True)
    nested = GitRunner(dependency)
    for args in (
        ("init", "-q"),
        ("config", "user.name", "Fixture"),
        ("config", "user.email", "fixture@example.com"),
        ("remote", "add", "origin", "https://github.com/opencv/opencv.git"),
    ):
        assert nested.run(*args).returncode == 0
    paths = [
        "doc/tutorials/app/_old/lesson.md",
        "doc/tutorials/others/_old/lesson.md",
        "doc/tutorials/imgproc/imgtrans/distance_transformation/images/final.jpeg",
        "modules/core/src/copy.cpp",
        "modules/dnn/src/cuda/fill_copy.cu",
        "modules/dnn/src/cuda4dnn/kernels/fill_copy.hpp",
        "modules/videoio/src/cap_dc1394_v2.cpp",
        "modules/core/src/legitimate_patch.cpp",
    ]
    for name in paths:
        path = dependency / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic upstream fixture\n")
    nested.run("add", ".")
    nested.run("commit", "-qm", "synthetic pinned upstream")
    sha = nested.run("rev-parse", "HEAD").stdout.strip()
    (dependency / ".git/FETCH_HEAD").write_text(
        f"{sha}\t\t'{sha}' of https://github.com/opencv/opencv\n"
    )
    (dependency / paths[-1]).write_text("legitimate unrelated patch\n")
    findings = hygiene_findings(root, git)
    copies = [f for f in findings if f.code.startswith("COPY_STYLE")]
    assert len(copies) == 31 and all(not f.blocking and f.details for f in copies)
    assert not any(f.blocking for f in findings)
    for name in (
        "build/src_v2",
        "vendor/src_v2",
        "build-cv-0/CMakeFiles/3.31.6-msvc6/CompilerIdCXX/tmp/src_v2",
    ):
        path = root / name
        path.mkdir(parents=True)
        (path / "project.cpp").write_text("human source copy\n")
    (dependency / "doc/tutorials/app/_old/copied_project.cpp").write_text("human source copy\n")
    bad = {f.path for f in hygiene_findings(root, git) if f.blocking}
    assert {
        "build/src_v2",
        "vendor/src_v2",
        "third_party/src/opencv-5.0.0/doc/tutorials/app/_old",
    } <= bad
    assert "build-cv-0/CMakeFiles/3.31.6-msvc6/CompilerIdCXX/tmp" in bad


def test_run_identity_status_is_readonly_and_duplicate_is_rejected(governed_repo):
    execution = FinishExecution(governed_repo, base_ref="HEAD", command=None)
    before = execution.path.read_bytes()
    assert query_execution(governed_repo, execution.run_id)["execution_status"] == "running"
    assert before == execution.path.read_bytes()
    with pytest.raises(DiagnosticError, match="already exists"):
        FinishExecution(governed_repo, base_ref="HEAD", command=None, run_id=execution.run_id)
    for value in ("../../anything", "not-a-uuid"):
        with pytest.raises(DiagnosticError):
            query_execution(governed_repo, value)


def test_lost_owner_query_and_late_cancellation_preserve_records(governed_repo):
    from codex_ai_os.core.gates import GateDecision, GateName

    execution = FinishExecution(governed_repo, base_ref="HEAD", command=None)
    execution.record["owner"]["identity"] = "different process creation time"
    execution.save()
    execution.close()
    before = execution.path.read_bytes()
    assert query_execution(governed_repo, execution.run_id)["execution_status"] == "interrupted"
    assert before == execution.path.read_bytes()
    execution = FinishExecution(governed_repo, base_ref="HEAD", command=None)
    execution.end(decision=GateDecision(GateName.FINISH, True, ()))
    before = execution.path.read_bytes()
    execution.cancel.set()
    execution.end(error=RuntimeError("late disconnect"))
    assert before == execution.path.read_bytes()
    assert query_execution(governed_repo, execution.run_id)["decision"]["allowed"] is True


def test_disposable_git_paths_preserve_nul_names_and_renames():
    from codex_ai_os.core.gates import disposable_findings

    names = ["报错提示/中文 空格.html", 'src/  quoted"name.tmp ', r"src/literal\backslash.py"]

    class Git:
        def run(self, *args):
            assert "-z" in args
            return subprocess.CompletedProcess(
                args,
                0,
                "R  " + names[0] + "\0old name\0?? " + names[1] + "\0 M " + names[2] + "\0",
                "",
            )

    assert [f.path for f in disposable_findings(cast(GitRunner, Git()))] == names


def test_output_pollution_blocks_both_start_hygiene_and_finish(governed_repo):
    from codex_ai_os.application.finish_execution import run_finish

    output = governed_repo / "output"
    output.mkdir()
    (output / "scratch.log").write_text("synthetic scratch")
    assert any(
        f.code == "OUTPUT_IMPURE" and f.blocking
        for f in hygiene_findings(governed_repo, GitRunner(governed_repo))
    )
    record = run_finish(governed_repo, base_ref="HEAD", test_command=None, memory_not_needed=True)
    assert not record["allowed"] and "OUTPUT_IMPURE" in record["blocked_by"]


def test_cleanup_diagnostics_preserve_action_and_unknown_host_rule(governed_repo):
    from codex_ai_os.application.hook_gateway import explain_hook_payload

    command = "Remove-Item -Recurse -LiteralPath input"
    result = explain_hook_payload(
        {"tool_name": "Bash", "cwd": str(governed_repo), "tool_input": {"command": command}}
    )
    assert result["decision"] == "deny" and result["layer"] == "aios"
    assert result["requested_action"] == command and result["requested_paths"] == ["input"]
    assert result["resolved_paths"] == [str(governed_repo / "input")]
    assert "specific host rule was not provided" in result["host_policy"]
    result = explain_hook_payload(
        {"tool_name": "Bash", "cwd": str(governed_repo), "tool_input": {"command": "rm $target"}}
    )
    assert result["decision"] == "deny" and result["target_status"] == "unknown"


def test_plain_source_copy_under_ignored_build_is_blocked(governed_repo):
    source = governed_repo / "src/main.cpp"
    source.parent.mkdir()
    source.write_text("int main() { return 0; }\n")
    git = GitRunner(governed_repo)
    git.run("add", "src/main.cpp")
    git.run("commit", "-qm", "source fixture")
    copied = governed_repo / "build/main.cpp"
    copied.parent.mkdir()
    copied.write_bytes(source.read_bytes())
    assert any(
        f.code == "PROJECT_SOURCE_COPY" and f.blocking for f in hygiene_findings(governed_repo, git)
    )


def test_runtime_selector_rejects_other_product_and_uses_explicit_entry(tmp_path, monkeypatch):
    from codex_ai_os import runtime_entry

    entry = tmp_path / "path with spaces/codex-os.exe"
    entry.parent.mkdir()
    entry.write_text("synthetic executable fixture")
    monkeypatch.setenv("CODEX_OS_RUNTIME", str(entry))
    calls = []

    def probe(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "data": {
                        "runtime": {
                            "product": "aios-governance",
                            "api_version": "2.0",
                            "config_versions": ["1.3"],
                        }
                    }
                }
            ),
            "",
        )

    monkeypatch.setattr(runtime_entry, "controlled_run", probe)
    with pytest.raises(runtime_entry.EntryError) as rejected:
        runtime_entry.select_runtime(tmp_path)
    assert rejected.value.code == "AIOS_RUNTIME_INCOMPATIBLE"
    assert calls[0][0] == str(entry)


def test_unknown_runtime_configuration_family_is_not_migrated(tmp_path):
    (tmp_path / ".aios").mkdir()
    (tmp_path / ".aios/project.yaml").write_text("root: .\n")
    result = CliRunner().invoke(app, ["check", str(tmp_path), "--json"])
    assert json.loads(result.output)["error"]["code"] == "CONFIG_RUNTIME_MISMATCH"
    assert not (tmp_path / ".codex-os").exists()
