"""Stable, sortable identifiers for runtime records."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4


def new_id(prefix: str) -> str:
    """Create a human-readable identifier with UTC time and random entropy."""

    normalized = prefix.strip().upper()
    if not normalized or not normalized.replace("-", "").isalnum():
        raise ValueError("identifier prefix must be alphanumeric with optional hyphens")
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return f"{normalized}-{timestamp}-{uuid4().hex[:8].upper()}"
