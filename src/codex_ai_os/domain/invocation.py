"""Trusted local adapter identity and request correlation context."""

from __future__ import annotations

import getpass
from enum import StrEnum

from pydantic import Field

from codex_ai_os.domain.config import StrictModel
from codex_ai_os.domain.ids import new_id


class InvocationSource(StrEnum):
    CLI = "cli"
    MCP = "mcp"


class InvocationContext(StrictModel):
    """Identity facts constructed by an adapter, never accepted from a request body."""

    request_id: str = Field(pattern=r"^REQ-")
    correlation_id: str = Field(pattern=r"^CORR-")
    principal: str = Field(min_length=1, max_length=256)
    source: InvocationSource
    capabilities: tuple[str, ...] = ()

    @classmethod
    def local(cls, source: InvocationSource) -> InvocationContext:
        username = getpass.getuser().strip() or "unknown"
        return cls(
            request_id=new_id("REQ"),
            correlation_id=new_id("CORR"),
            principal=f"os:{username}",
            source=source,
            capabilities=("invoke",),
        )


__all__ = ["InvocationContext", "InvocationSource"]
