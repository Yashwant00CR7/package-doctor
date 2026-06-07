import pytest
import respx
import httpx

from package_doctor.collectors.pypi import fetch_package_info
from package_doctor.collectors.model_checker import check_model_version
from package_doctor.conflict_analyzer import parse_pip_freeze, parse_pip_check, build_fix_commands, check_pre_install_conflicts
from package_doctor.models import ConflictEntry


PYPI_RESPONSE_ACTIVE = {
    "info": {
        "name": "requests",
        "version": "2.31.0",
        "summary": "Python HTTP for Humans.",
        "requires_python": ">=3.7",
        "classifiers": [],
        "description": "Requests is an elegant HTTP library.",
    },
    "releases": {
        "2.31.0": [{"upload_time": "2023-05-22T15:00:00"}]
    },
}

PYPI_RESPONSE_DEPRECATED = {
    "info": {
        "name": "langchain-community",
        "version": "0.3.0",
        "summary": "Deprecated. Use langchain-core instead.",
        "requires_python": ">=3.9",
        "classifiers": [],
        "description": "This package is deprecated. Use langchain-core instead.",
    },
    "releases": {
        "0.3.0": [{"upload_time": "2024-01-01T00:00:00"}]
    },
}


@pytest.mark.asyncio
@respx.mock
async def test_fetch_active_package():
    respx.get("https://pypi.org/pypi/requests/json").mock(
        return_value=httpx.Response(200, json=PYPI_RESPONSE_ACTIVE)
    )
    result = await fetch_package_info("requests")
    assert result is not None
    assert result["latest_version"] == "2.31.0"
    assert result["status"] == "active"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_deprecated_package():
    respx.get("https://pypi.org/pypi/langchain-community/json").mock(
        return_value=httpx.Response(200, json=PYPI_RESPONSE_DEPRECATED)
    )
    result = await fetch_package_info("langchain-community")
    assert result is not None
    assert result["status"] == "deprecated"
    assert result["alternative"] == "langchain-core"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_missing_package():
    respx.get("https://pypi.org/pypi/nonexistent-xyz-pkg/json").mock(
        return_value=httpx.Response(404)
    )
    result = await fetch_package_info("nonexistent-xyz-pkg")
    assert result is not None
    assert result["status"] == "not_found"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_network_failure():
    respx.get("https://pypi.org/pypi/requests/json").mock(
        side_effect=httpx.ConnectError("timeout")
    )
    result = await fetch_package_info("requests")
    assert result is None


def test_parse_pip_freeze_basic():
    freeze = """
requests==2.31.0
numpy==1.24.0
# comment line
-e git+https://...
"""
    result = parse_pip_freeze(freeze)
    assert result["requests"] == "2.31.0"
    assert result["numpy"] == "1.24.0"
    assert "comment" not in result


def test_parse_pip_check_conflict():
    pip_check = (
        "sphinx 4.3.0 requires docutils<0.18,>=0.14, "
        "but you have docutils 0.18.1 which is incompatible."
    )
    conflicts = parse_pip_check(pip_check)
    assert len(conflicts) == 1
    assert conflicts[0].package == "docutils"
    assert conflicts[0].required_by == "sphinx"
    assert conflicts[0].installed_version == "0.18.1"


def test_parse_pip_check_clean():
    result = parse_pip_check("No broken requirements found.")
    assert result == []


def test_build_fix_commands_pip():
    conflicts = [
        ConflictEntry(package="docutils", required_spec="<0.18,>=0.14", installed_version="0.18.1", required_by="sphinx")
    ]
    cmds = build_fix_commands(conflicts, "pip")
    assert cmds[0] == 'pip install "docutils<0.18,>=0.14"'


def test_build_fix_commands_uv():
    conflicts = [
        ConflictEntry(package="numpy", required_spec=">=1.24", installed_version="1.23.0", required_by="scipy")
    ]
    cmds = build_fix_commands(conflicts, "uv")
    assert cmds[0] == 'uv add "numpy>=1.24"'


def test_pre_install_already_installed():
    freeze = "requests==2.28.0\nnumpy==1.24.0\n"
    would_conflict, conflicts, cmds = check_pre_install_conflicts("requests", freeze, "pip")
    assert would_conflict is True
    assert conflicts[0].package == "requests"


def test_pre_install_clean():
    freeze = "numpy==1.24.0\n"
    would_conflict, conflicts, cmds = check_pre_install_conflicts("requests", freeze, "pip")
    assert would_conflict is False
    assert conflicts == []


@pytest.mark.asyncio
async def test_model_known_removed():
    result = await check_model_version("gpt-3.5-turbo-0301")
    assert result["status"] == "error"
    assert result["successor_model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_model_known_warning():
    result = await check_model_version("gpt-3.5-turbo")
    assert result["status"] == "warning"
    assert result["successor_model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_model_claude_removed():
    result = await check_model_version("claude-2")
    assert result["status"] == "error"
    assert "claude-opus" in result["successor_model"]


@pytest.mark.asyncio
async def test_model_detect_provider_openai():
    result = await check_model_version("gpt-4o-mini")
    assert result["provider"] == "openai"


@pytest.mark.asyncio
async def test_model_detect_provider_anthropic():
    result = await check_model_version("claude-3-5-sonnet-20241022")
    assert result["provider"] == "anthropic"
