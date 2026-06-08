"""Package Doctor MCP Server — exposes 4 tools to Claude Code agents."""
import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from package_doctor.models import (
    PackageInfo,
    VenvHealth,
    PreInstallResult,
    ModelStatus,
    ConflictEntry,
)
from package_doctor.store import get_connection, get_cached_package, upsert_package, get_cached_model, upsert_model
from package_doctor.collectors.pypi import fetch_package_info
from package_doctor.collectors.model_checker import check_model_version as _check_model
from package_doctor.conflict_analyzer import (
    parse_pip_freeze,
    parse_pip_check,
    build_fix_commands,
    check_pre_install_conflicts,
)

HAS_GRAPH = False
try:
    import kuzu
    from package_doctor.graph.mcp_tool import get_mcp_tool, handle_get_package_relationships
    HAS_GRAPH = True
except ImportError:
    pass

server = Server("package-doctor")


@server.list_tools()
async def list_tools() -> list[Tool]:
    tools = [
        Tool(
            name="check_package",
            description=(
                "Check a Python package for deprecation status, latest version, "
                "Python compatibility, and migration alternatives."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "package_name": {"type": "string", "description": "Name of the package to check"},
                    "version": {"type": "string", "description": "Optional version spec to check"},
                },
                "required": ["package_name"],
            },
        ),
        Tool(
            name="check_pre_install",
            description=(
                "Check if installing a package would conflict with the current venv. "
                "Pass pip_freeze output as a string."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "package_name": {"type": "string"},
                    "pip_freeze": {"type": "string", "description": "Output of 'pip freeze' in the current venv"},
                    "package_manager": {
                        "type": "string",
                        "enum": ["pip", "uv", "conda"],
                        "default": "pip",
                    },
                },
                "required": ["package_name", "pip_freeze"],
            },
        ),
        Tool(
            name="check_venv_health",
            description=(
                "Full venv health audit. Pass pip_freeze and pip_check output. "
                "Returns conflict map and ranked fix commands."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pip_freeze": {"type": "string", "description": "Output of 'pip freeze'"},
                    "pip_check": {"type": "string", "description": "Output of 'pip check'"},
                    "package_manager": {
                        "type": "string",
                        "enum": ["pip", "uv", "conda"],
                        "default": "pip",
                    },
                },
                "required": ["pip_freeze", "pip_check"],
            },
        ),
        Tool(
            name="check_model_version",
            description=(
                "Check if an LLM model ID is current, deprecated, or removed. "
                "Crawls official provider docs for fresh status."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_id": {"type": "string", "description": "Model ID to check (e.g., gpt-4, claude-3-opus-20240229)"},
                },
                "required": ["model_id"],
            },
        ),
    ]
    if HAS_GRAPH:
        tools.append(get_mcp_tool())
    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "check_package":
        result = await _tool_check_package(
            arguments["package_name"],
            arguments.get("version"),
        )
    elif name == "check_pre_install":
        result = await _tool_check_pre_install(
            arguments["package_name"],
            arguments["pip_freeze"],
            arguments.get("package_manager", "pip"),
        )
    elif name == "check_venv_health":
        result = await _tool_check_venv_health(
            arguments["pip_freeze"],
            arguments["pip_check"],
            arguments.get("package_manager", "pip"),
        )
    elif name == "check_model_version":
        result = await _tool_check_model_version(arguments["model_id"])
    elif name == "get_package_relationships":
        if not HAS_GRAPH:
            result = {"error": "Graph subsystem is not installed. Install package-doctor with the [graph] extra."}
        else:
            result = await handle_get_package_relationships(arguments["package_name"])
    else:
        result = {"error": f"Unknown tool: {name}"}

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _tool_check_package(package_name: str, version: Optional[str] = None) -> dict:
    from typing import Optional
    warnings: list[str] = []
    conn = get_connection()

    cached = get_cached_package(package_name, conn)
    if cached:
        pkg = PackageInfo(
            name=cached["name"],
            latest_version=cached.get("latest_version"),
            status=cached.get("status", "active"),
            deprecation_message=cached.get("deprecation_message"),
            alternative=cached.get("alternative"),
            python_requires=cached.get("python_requires"),
            last_release_date=cached.get("last_release_date"),
        )
    else:
        raw = await fetch_package_info(package_name)
        if raw is None:
            warnings.append(f"PyPI fetch failed for {package_name}")
            return PackageInfo(name=package_name, status="fetch_failed", warnings=warnings).model_dump()
        upsert_package(raw, conn)
        pkg = PackageInfo(
            name=raw["name"],
            latest_version=raw.get("latest_version"),
            status=raw.get("status", "active"),
            deprecation_message=raw.get("deprecation_message"),
            alternative=raw.get("alternative"),
            python_requires=raw.get("python_requires"),
            last_release_date=raw.get("last_release_date"),
        )

    result = pkg.model_dump()
    result["warnings"] = warnings
    return result


async def _tool_check_pre_install(
    package_name: str, pip_freeze: str, package_manager: str
) -> dict:
    warnings: list[str] = []

    would_conflict, conflicts, fix_cmds = check_pre_install_conflicts(
        package_name, pip_freeze, package_manager
    )

    # Also fetch package deprecation info
    conn = get_connection()
    cached = get_cached_package(package_name, conn)
    if not cached:
        raw = await fetch_package_info(package_name)
        if raw:
            upsert_package(raw, conn)
            pkg_info = PackageInfo(
                name=raw["name"],
                latest_version=raw.get("latest_version"),
                status=raw.get("status", "active"),
                deprecation_message=raw.get("deprecation_message"),
                alternative=raw.get("alternative"),
            )
        else:
            pkg_info = None
            warnings.append(f"PyPI fetch failed for {package_name}")
    else:
        pkg_info = PackageInfo(
            name=cached["name"],
            latest_version=cached.get("latest_version"),
            status=cached.get("status", "active"),
            deprecation_message=cached.get("deprecation_message"),
            alternative=cached.get("alternative"),
        )

    if pkg_info and pkg_info.status == "deprecated":
        warnings.append(f"{package_name} is deprecated: {pkg_info.deprecation_message or 'see alternative'}")

    result = PreInstallResult(
        package_name=package_name,
        would_conflict=would_conflict,
        conflicting_packages=conflicts,
        resolution_commands=fix_cmds,
        package_info=pkg_info,
        warnings=warnings,
    )
    return result.model_dump()


async def _tool_check_venv_health(
    pip_freeze: str, pip_check: str, package_manager: str
) -> dict:
    warnings: list[str] = []
    installed = parse_pip_freeze(pip_freeze)
    conflicts = parse_pip_check(pip_check)
    fix_cmds = build_fix_commands(conflicts, package_manager)

    # Check deprecation status for all installed packages concurrently
    conn = get_connection()
    async def _check_one(name: str) -> PackageInfo | None:
        cached = get_cached_package(name, conn)
        if cached:
            if cached.get("status") in ("deprecated", "abandoned"):
                return PackageInfo(
                    name=cached["name"],
                    latest_version=cached.get("latest_version"),
                    status=cached.get("status", "active"),
                    deprecation_message=cached.get("deprecation_message"),
                    alternative=cached.get("alternative"),
                )
            return None
        raw = await fetch_package_info(name)
        if raw:
            upsert_package(raw, conn)
            if raw.get("status") in ("deprecated", "abandoned"):
                return PackageInfo(
                    name=raw["name"],
                    latest_version=raw.get("latest_version"),
                    status=raw.get("status", "active"),
                    deprecation_message=raw.get("deprecation_message"),
                    alternative=raw.get("alternative"),
                )
        return None

    # Limit concurrency to avoid hammering PyPI
    semaphore = asyncio.Semaphore(10)
    async def _guarded(name: str):
        async with semaphore:
            try:
                return await _check_one(name)
            except Exception:
                return None

    results = await asyncio.gather(*[_guarded(n) for n in installed])
    deprecated = [r for r in results if r is not None]

    health = VenvHealth(
        total_packages=len(installed),
        conflicts=conflicts,
        deprecated_packages=deprecated,
        ranked_fix_commands=fix_cmds,
        warnings=warnings,
    )
    return health.model_dump()


async def _tool_check_model_version(model_id: str) -> dict:
    conn = get_connection()
    cached = get_cached_model(model_id, conn)
    if cached:
        return ModelStatus(
            model_id=cached["model_id"],
            provider=cached.get("provider"),
            status=cached["status"],
            eol_date=cached.get("eol_date"),
            successor_model=cached.get("successor_model"),
            last_checked=cached.get("checked_at", ""),
            source_url=cached.get("source_url"),
        ).model_dump()

    raw = await _check_model(model_id)
    upsert_model(raw, conn)
    return ModelStatus(
        model_id=raw["model_id"],
        provider=raw.get("provider"),
        status=raw["status"],
        eol_date=raw.get("eol_date"),
        successor_model=raw.get("successor_model"),
        last_checked=raw.get("last_checked", ""),
        source_url=raw.get("source_url"),
        warnings=raw.get("warnings", []),
    ).model_dump()


def main():
    asyncio.run(_run())


async def _run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    main()
