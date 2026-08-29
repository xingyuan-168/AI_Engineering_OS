from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import cast

from mcp import Client
from typer.testing import CliRunner

from codex_ai_os.application.public_contracts import (
    PUBLIC_ENVELOPE_FIELDS,
    PUBLIC_TOOL_CONTRACTS,
    public_tool_names,
)
from codex_ai_os.application.responses import success_envelope
from codex_ai_os.cli import app as cli_app
from codex_ai_os.cli import mcp_server


def test_public_registry_matches_mcp_cli_application_and_api_docs() -> None:
    async def mcp_contracts() -> tuple[set[str], dict[str, set[str]]]:
        async with Client(mcp_server.mcp, raise_exceptions=True) as client:
            tools = await client.list_tools()
        return (
            {tool.name for tool in tools.tools},
            {
                tool.name: set(
                    cast(dict[str, object], tool.input_schema.get("properties", {}))
                )
                for tool in tools.tools
            },
        )

    names, schema_properties = asyncio.run(mcp_contracts())
    assert names == public_tool_names()

    runner = CliRunner()
    for contract in PUBLIC_TOOL_CONTRACTS:
        handler = getattr(mcp_server, contract.mcp_tool)
        source = inspect.getsource(handler)
        assert contract.application_service in source
        assert set(contract.concurrency_fields) <= schema_properties[contract.mcp_tool]
        for command in contract.cli_commands:
            result = runner.invoke(cli_app.app, [*command.split(), "--help"])
            assert result.exit_code == 0, (command, result.output)

    documentation = Path("docs/API_SPEC.md").read_text(encoding="utf-8")
    for contract in PUBLIC_TOOL_CONTRACTS:
        assert f"| `{contract.mcp_tool}` |" in documentation


def test_success_envelope_exactly_contains_the_registered_contract_fields() -> None:
    assert set(success_envelope({})) == PUBLIC_ENVELOPE_FIELDS
