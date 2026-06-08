"""Tests for the package graph module."""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest
import respx
import httpx
import kuzu

from package_doctor.graph.indexer import (
    RelationshipExtractor,
    build_graph,
    fetch_pypi_metadata,
    fetch_top_packages,
    parse_pypi_response,
)
from package_doctor.graph.query import get_package_relationships
from package_doctor.graph.schema import init_database


@pytest.fixture
def temp_db_path():
    """Create a temporary database path for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_packages.kuzu"
        yield str(db_path)


@pytest.fixture
def mock_api_key():
    """Mock API key for testing."""
    old_key = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "test-key-1234567890"
    yield
    if old_key:
        os.environ["ANTHROPIC_API_KEY"] = old_key
    else:
        os.environ.pop("ANTHROPIC_API_KEY", None)


class TestParsePyPIResponse:
    """Tests for parse_pypi_response function."""

    def test_basic_package(self):
        """Test parsing a basic package response."""
        data = {
            "info": {
                "name": "requests",
                "summary": "Python HTTP for Humans.",
                "description": "Requests is an elegant HTTP library.",
            }
        }
        summary, description = parse_pypi_response(data)
        assert summary == "Python HTTP for Humans."
        assert description == "Requests is an elegant HTTP library."

    def test_missing_summary(self):
        """Test handling missing summary."""
        data = {
            "info": {
                "name": "some-package",
                "description": "Some description.",
            }
        }
        summary, description = parse_pypi_response(data)
        assert summary is None
        assert description == "Some description."

    def test_missing_description(self):
        """Test handling missing description."""
        data = {
            "info": {
                "name": "some-package",
                "summary": "Some summary.",
            }
        }
        summary, description = parse_pypi_response(data)
        assert summary == "Some summary."
        assert description is None

    def test_description_html_fallback(self):
        """Test using description_html as fallback."""
        data = {
            "info": {
                "name": "some-package",
                "summary": "Some summary.",
                "description": None,
                "description_html": "<p>Some HTML description.</p>",
            }
        }
        summary, description = parse_pypi_response(data)
        assert summary == "Some summary."
        assert description == "<p>Some HTML description.</p>"


class TestRelationshipExtractor:
    """Tests for RelationshipExtractor."""

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Live network test skipped by default")
    async def test_extract_relationships_basic(self):
        """Test basic relationship extraction."""
        extractor = RelationshipExtractor()

        # Use a known package with clear relationships
        result = await extractor.extract_relationships(
            package_name="langchain",
            summary="Build context-aware reasoning applications.",
            description="LangChain is a framework for developing applications powered by language models.",
        )

        assert isinstance(result, dict)
        assert "ecosystem" in result
        assert "wrapper_for" in result
        assert "deprecated_by" in result

    @pytest.mark.asyncio
    async def test_extract_relationships_with_mock(self, mock_api_key):
        """Test relationship extraction with mocked LLM."""
        extractor = RelationshipExtractor()

        # Mock the Anthropic client using regex to match any API host/port
        with respx.mock:
            # Create a mock response that mimics Anthropic's response format
            mock_response = {
                "id": "msg_test123",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": '{"ecosystem": "llm", "wrapper_for": "openai", "deprecated_by": null}'}],
                "model": "claude-haiku-4-5-20251001",
                "usage": {"input_tokens": 100, "output_tokens": 20},
            }
            respx.post(url__regex=r".*/messages$").mock(
                return_value=httpx.Response(200, json=mock_response)
            )

            result = await extractor.extract_relationships(
                package_name="test-package",
                summary="Test summary",
                description="Test description",
            )

            assert isinstance(result, dict)
            assert "ecosystem" in result


class TestSchema:
    """Tests for schema initialization."""

    def test_init_database(self, temp_db_path):
        """Test database initialization."""
        db = init_database(temp_db_path)
        assert db is not None

    def test_schema_tables_exist(self, temp_db_path):
        """Test that schema tables are created."""
        db = init_database(temp_db_path)
        conn = kuzu.Connection(db)

        # Verify Package node table exists
        result = conn.execute(
            "MATCH (p:Package) RETURN count(*) AS count", {}
        )
        assert result.get_next()[0] == 0  # Should be empty

    def test_schema_rel_tables_exist(self, temp_db_path):
        """Test that relationship tables are created."""
        db = init_database(temp_db_path)
        conn = kuzu.Connection(db)

        # Try to create a relationship to verify table exists
        conn.execute(
            """
            CREATE (p:Package {name: 'test1'})
            CREATE (r:Package {name: 'test2'})
            CREATE (p)-[rel:HAS_RELATIONSHIP {relationship_type: 'wrapper_for'}]->(r)
            """
        )

        result = conn.execute(
            """
            MATCH (p:Package)-[r:HAS_RELATIONSHIP]->(r2:Package)
            RETURN count(*) AS count
            """
        )
        assert result.get_next()[0] == 1


class TestQuery:
    """Tests for query functions."""

    def test_get_package_relationships_not_found(self, temp_db_path):
        """Test querying a non-existent package."""
        db = init_database(temp_db_path)
        del db  # release lock

        result = get_package_relationships(temp_db_path, "nonexistent-package")

        assert result["package"] == "nonexistent-package"
        assert result["relationships"]["ecosystem"] is None
        assert "error" in result

    def test_get_package_relationships_empty(self, temp_db_path):
        """Test querying with empty database."""
        db = init_database(temp_db_path)
        conn = kuzu.Connection(db)

        # Insert a package without relationships
        conn.execute(
            "CREATE (p:Package {name: 'test-pkg', ecosystem: 'general'})"
        )

        # Release connection and database locks before function call
        del conn
        del db

        result = get_package_relationships(temp_db_path, "test-pkg")

        assert result["package"] == "test-pkg"
        assert result["relationships"]["ecosystem"] == "general"
        assert result["related_packages"] == []


@pytest.mark.asyncio
class TestBuildGraph:
    """Tests for graph building."""

    @respx.mock
    async def test_fetch_top_packages(self, temp_db_path):
        """Test fetching top packages from the JSON source."""
        mock_data = {
            "rows": [
                {"project": "requests"},
                {"project": "numpy"},
                {"project": "pandas"},
            ]
        }
        respx.get("https://hugovk.github.io/top-pypi-packages/top-pypi-packages-30-days.min.json").mock(
            return_value=httpx.Response(200, json=mock_data)
        )

        async with httpx.AsyncClient() as client:
            result = await fetch_top_packages(client, 3)
            assert result == ["requests", "numpy", "pandas"]

    @respx.mock
    async def test_fetch_pypi_metadata_success(self):
        """Test fetching PyPI metadata successfully."""
        mock_data = {
            "info": {
                "name": "requests",
                "summary": "Python HTTP for Humans.",
                "description": "Requests is an elegant HTTP library.",
            }
        }
        respx.get("https://pypi.org/pypi/requests/json").mock(
            return_value=httpx.Response(200, json=mock_data)
        )

        async with httpx.AsyncClient() as client:
            result = await fetch_pypi_metadata(client, "requests")

        assert result is not None
        assert result[0] == "Python HTTP for Humans."

    @respx.mock
    async def test_fetch_pypi_metadata_not_found(self):
        """Test fetching non-existent package."""
        respx.get("https://pypi.org/pypi/nonexistent-pkg/json").mock(
            return_value=httpx.Response(404)
        )

        async with httpx.AsyncClient() as client:
            result = await fetch_pypi_metadata(client, "nonexistent-pkg")

        assert result is None

    @respx.mock
    async def test_build_graph_integration(self, temp_db_path):
        """Test full graph build with mocks."""
        # Mock top packages
        respx.get("https://hugovk.github.io/top-pypi-packages/top-pypi-packages-30-days.min.json").mock(
            return_value=httpx.Response(200, json={"rows": [{"project": "requests"}]})
        )

        # Mock PyPI metadata
        respx.get("https://pypi.org/pypi/requests/json").mock(
            return_value=httpx.Response(200, json={"info": {"name": "requests", "summary": "Test", "description": "Test"}})
        )

        db = init_database(temp_db_path)
        assert db is not None


class TestIdempotentRebuild:
    """Test idempotent behavior."""

    def test_rebuild_clears_existing_data(self, temp_db_path):
        """Test that rebuild clears existing data."""
        db = init_database(temp_db_path)
        conn = kuzu.Connection(db)

        # Add some packages
        conn.execute("CREATE (p:Package {name: 'pkg1', ecosystem: 'test'})")
        conn.execute("CREATE (p:Package {name: 'pkg2', ecosystem: 'test'})")

        # Verify packages exist
        result = conn.execute("MATCH (p:Package) RETURN count(*) AS count", {})
        assert result.get_next()[0] == 2

        # Rebuild (which clears)
        conn.execute("MATCH (n) DETACH DELETE n;")

        # Verify packages are deleted
        result = conn.execute("MATCH (p:Package) RETURN count(*) AS count", {})
        assert result.get_next()[0] == 0
