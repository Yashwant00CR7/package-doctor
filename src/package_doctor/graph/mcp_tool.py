"""MCP tool for getting package relationships."""

from typing import Any

from mcp.server.models import InitializationOptions
from mcp.types import (
    ClientCapabilities,
    InitializeResult,
    Tool,
)

from .query import get_package_relationships


def get_mcp_tool() -> Tool:
    """Create the get_package_relationships MCP tool."""
    return Tool(
        name="get_package_relationships",
        description=(
            "Get package relationships from the PyPI package graph. "
            "Returns deprecated_by, replacement_for, fork_of, wrapper_for, ecosystem relationships "
            "and a list of related packages."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "package_name": {
                    "type": "string",
                    "description": "Name of the PyPI package to look up",
                },
            },
            "required": ["package_name"],
        },
    )


async def handle_get_package_relationships(
    package_name: str, db_path: str | None = None
) -> dict[str, Any]:
    """Handle the get_package_relationships tool call.

    Args:
        package_name: Name of the package to look up
        db_path: Optional path to the Kuzu database (uses default if not provided)

    Returns:
        Dictionary with package name, relationships, and related packages
    """
    if db_path is None:
        # Default path relative to project
        from pathlib import Path

        db_path = str(Path(__file__).parent.parent.parent.parent / ".data" / "packages.kuzu")

    return get_package_relationships(db_path, package_name)
