from __future__ import annotations

import ast
import asyncio
import inspect
import re
import shlex
from pathlib import Path

from codex_ai_os.cli import mcp_server
from codex_ai_os.cli.app import app

EXPECTED_SKILLS = {
    "governance-entry",
    "open-source-research",
    "html-prototype",
    "frontend-design-review",
    "worktree-protocol",
    "document-impact",
    "memory-protocol",
    "finish-checklist",
}

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "plugins" / "ai-engineering-os" / "skills"


def test_skill_set_is_converged() -> None:
    names = {path.name for path in SKILLS_ROOT.iterdir() if path.is_dir()}
    assert names == EXPECTED_SKILLS


def test_each_skill_has_manifest_and_agent_profile() -> None:
    for name in sorted(EXPECTED_SKILLS):
        skill_md = SKILLS_ROOT / name / "SKILL.md"
        assert skill_md.is_file(), name
        text = skill_md.read_text(encoding="utf-8")
        assert text.startswith("---"), name
        assert "name: " + name in text, name
        assert "description:" in text, name
        assert (SKILLS_ROOT / name / "agents" / "openai.yaml").is_file(), name


def test_skill_documents_are_compact() -> None:
    for name in sorted(EXPECTED_SKILLS):
        skill_md = SKILLS_ROOT / name / "SKILL.md"
        lines = skill_md.read_text(encoding="utf-8").splitlines()
        assert len(lines) <= 60, (name, len(lines))


def test_skill_python_examples_match_real_mcp_signatures() -> None:
    count = 0
    for name in sorted(EXPECTED_SKILLS):
        text = (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")
        for example in re.findall(r"```python\n(.*?)```", text, re.S):
            for node in ast.walk(ast.parse(example)):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                function = getattr(mcp_server, node.func.id)
                kwargs: dict[str, object] = {}
                for item in node.keywords:
                    assert item.arg is not None, "examples must use explicit keyword arguments"
                    kwargs[item.arg] = ast.literal_eval(item.value)
                inspect.signature(function).bind(**kwargs)
                count += 1
    assert count >= 6


def test_research_example_satisfies_document_contract(tmp_path: Path) -> None:
    from codex_ai_os.core.gates import _research_findings

    text = (SKILLS_ROOT / "open-source-research/SKILL.md").read_text(encoding="utf-8")
    example = re.search(r"```markdown\n(.*?)```", text, re.S)
    assert example is not None
    document = tmp_path / "research.md"
    document.write_text(example.group(1), encoding="utf-8")
    assert not _research_findings(tmp_path, "major_feature", "REQ-EXAMPLE", "research.md")


def test_inline_calls_and_cli_finish_example_match_real_interfaces():
    from typer.core import TyperGroup
    from typer.main import get_command

    command = get_command(app)
    assert isinstance(command, TyperGroup)
    count = 0
    for file in SKILLS_ROOT.glob("*/SKILL.md"):
        text = file.read_text(encoding="utf-8")
        for inline in re.findall(r"(?<!`)`([^`\n]+)`(?!`)", text):
            match = re.match(r"(\w+)\(.*\)$", inline)
            if match and hasattr(mcp_server, match[1]):
                call = ast.parse(inline).body[0].value
                inspect.signature(getattr(mcp_server, match[1])).bind_partial(
                    **{keyword.arg: None for keyword in call.keywords}
                )
            if inline.startswith("codex-os finish "):
                args = shlex.split(inline)[2:]
                with command.make_context("codex-os", [], resilient_parsing=True) as parent:
                    finish = command.get_command(parent, "finish")
                    assert finish is not None
                    with finish.make_context("finish", args, parent=parent):
                        count += 1
    assert count >= 1


def test_registered_mcp_schema_and_cli_reject_old_attestations():
    from typer.testing import CliRunner

    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert len(tools) == 8
    tool = next(tool for tool in tools if tool.name == "governance_check")
    properties = tool.input_schema["properties"]
    assert properties["change_class"]["default"] is None
    assert {"type": "null"} in properties["change_class"]["anyOf"]
    assert {"test_command", "base_ref", "remote", "run_id", "action"} <= properties.keys()
    assert not {"tests_passed", "docs_synced", "ctx"} & properties.keys()
    assert tool.input_schema["additionalProperties"] is False
    import pytest
    from pydantic import ValidationError

    registered = mcp_server.mcp._tool_manager.get_tool("governance_check")
    assert registered is not None
    model = registered.fn_metadata.arg_model
    with pytest.raises(ValidationError, match="Extra inputs"):
        model.model_validate({"project_root": ".", "stage": "finish", "tests_passed": True})
    query = {"project_root": ".", "stage": "finish", "action": "status", "run_id": "fixture"}
    model.model_validate(query)
    for parameter, value in {
        "test_command": None,
        "base_ref": None,
        "remote": None,
        "change_class": "small_change",
        "frontend_impact": "none",
        "frontend_scope": "default",
        "requirement_id": None,
        "memory_written": False,
        "memory_not_needed": False,
    }.items():
        with pytest.raises(ValidationError, match="no execution parameters"):
            model.model_validate({**query, parameter: value})
    result = CliRunner().invoke(app, ["finish", "--tests-passed", "--docs-synced"])
    assert result.exit_code == 2
