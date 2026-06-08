"""Batch LLM extraction and Kuzu writes for PyPI package relationships."""

import asyncio
import json
from typing import Any

import httpx
import kuzu
from anthropic import AsyncAnthropic

from .schema import get_database


# API endpoints
TOP_PACKAGES_URL = "https://hugovk.github.io/top-pypi-packages/top-pypi-packages-30-days.min.json"
PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"

EXTRACT_RELATIONSHIPS_PROMPT = """Extract package relationships from PyPI package metadata.

Given the package name and its summary/description, identify:
1. deprecated_by: If this package is deprecated, which package replaces it?
2. replacement_for: If this package is a replacement, for what package?
3. fork_of: If this package is a fork of another, which one?
4. wrapper_for: If this package is a wrapper, for what library/API?
5. ecosystem: What major ecosystem/framework does this belong to? (e.g., "django", "fastapi", "numpy", "llm", "pytorch")

Instructions:
- Extract ONLY from the provided metadata text
- Return null if a relationship is not evident from the text
- For wrapper_for, look for phrases like "wrapper", "wrapper for", "wrapper around"
- For fork_of, look for phrases like "fork of", "forked from"
- For replacement_for, look for "replacement for"
- For deprecated_by, look for "deprecated by", "superseded by"
- For ecosystem, identify the primary framework or library ecosystem

Return a JSON object with exactly these keys: deprecated_by, replacement_for, fork_of, wrapper_for, ecosystem.
All values should be strings or null.

Package Name: {package_name}
Package Summary: {summary}
Package Description: {description}

Return ONLY valid JSON, no other text."""

LLM_MODEL = "claude-haiku-4-5-20251001"


class RelationshipExtractor:
    """Extracts package relationships from PyPI metadata using LLM."""

    def __init__(self, api_key: str | None = None):
        self.client = AsyncAnthropic(api_key=api_key)

    async def extract_relationships(
        self, package_name: str, summary: str | None, description: str | None
    ) -> dict[str, str | None]:
        """Extract relationships from package metadata."""
        summary_text = summary or ""
        description_text = description or ""

        prompt = EXTRACT_RELATIONSHIPS_PROMPT.format(
            package_name=package_name,
            summary=summary_text[:2000],
            description=description_text[:4000],
        )

        response = await self.client.messages.create(
            model=LLM_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )

        content = response.content[0].text.strip()
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            result = {
                "deprecated_by": None,
                "replacement_for": None,
                "fork_of": None,
                "wrapper_for": None,
                "ecosystem": None,
            }
        return result


def parse_pypi_response(data: dict[str, Any]) -> tuple[str | None, str | None]:
    """Parse PyPI JSON response to get summary and description."""
    info = data.get("info", {})
    summary = info.get("summary")
    description = info.get("description")

    if not description:
        description = info.get("description_html")

    return summary, description


async def fetch_pypi_metadata(
    client: httpx.AsyncClient, package_name: str
) -> tuple[str | None, str | None] | None:
    """Fetch PyPI metadata for a package."""
    import re
    if not re.match(r"^[a-zA-Z0-9_\-\.]+$", package_name):
        return None
    try:
        response = await client.get(PYPI_JSON_URL.format(name=package_name))
        if response.status_code == 200:
            data = response.json()
            return parse_pypi_response(data)
    except (httpx.RequestError, json.JSONDecodeError):
        pass
    return None


async def fetch_top_packages(client: httpx.AsyncClient, limit: int) -> list[str]:
    """Fetch top packages from the JSON file."""
    try:
        response = await client.get(TOP_PACKAGES_URL)
        response.raise_for_status()
        data = response.json()
        rows = data.get("rows", [])
        return [row["project"] for row in rows[:limit]]
    except (httpx.RequestError, json.JSONDecodeError):
        return []


def _package_exists(conn: kuzu.Connection, package_name: str) -> bool:
    """Check if a package exists in the graph database."""
    canonical = package_name.lower()
    query = """
    MATCH (p:Package)
    WHERE p.name = $name
    RETURN count(*) > 0 AS exists
    """
    result = conn.execute(query, {"name": canonical})
    return result.get_next()[0]


async def process_batch(
    extractor: RelationshipExtractor,
    db: kuzu.Database,
    client: httpx.AsyncClient,
    package_names: list[str],
    semaphore_llm: asyncio.Semaphore,
    semaphore_pypi: asyncio.Semaphore,
) -> list[dict[str, Any]]:
    """Process a batch of packages, extracting relationships and writing to Kuzu."""
    results = []
    conn = kuzu.Connection(db)

    async def fetch_with_semaphore(name: str) -> tuple[str, str | None, str | None] | None:
        async with semaphore_pypi:
            metadata = await fetch_pypi_metadata(client, name)
            if metadata:
                return (name, metadata[0], metadata[1])
            return None

    fetch_tasks = [fetch_with_semaphore(name) for name in package_names]
    fetched = await asyncio.gather(*fetch_tasks)
    fetched = [f for f in fetched if f is not None]

    async def extract_with_semaphore(
        name: str, summary: str | None, description: str | None
    ) -> dict[str, Any]:
        async with semaphore_llm:
            relationships = await extractor.extract_relationships(name, summary, description)
            return {
                "name": name,
                "relationships": relationships,
                "summary": summary,
                "description": description,
            }

    extract_tasks = [
        extract_with_semaphore(name, summary, description)
        for name, summary, description in fetched
    ]
    extracted = await asyncio.gather(*extract_tasks)

    for result in extracted:
        name = result["name"]
        relationships = result["relationships"]
        ecosystem = relationships.get("ecosystem")

        if _package_exists(conn, name):
            continue

        conn.execute("BEGIN TRANSACTION;")

        try:
            insert_query = """
            CREATE (p:Package {name: $name, ecosystem: $ecosystem})
            """
            conn.execute(insert_query, {"name": name, "ecosystem": ecosystem or ""})

            rel_types = ["deprecated_by", "replacement_for", "fork_of", "wrapper_for"]
            for rel_type in rel_types:
                related_name = relationships.get(rel_type)
                if related_name:
                    related_name_lower = related_name.lower()
                    conn.execute(
                        """
                        MERGE (related:Package {name: $related_name})
                        """,
                        {"related_name": related_name_lower},
                    )
                    conn.execute(
                        """
                        MATCH (p:Package), (r:Package)
                        WHERE p.name = $name AND r.name = $related_name
                        CREATE (p)-[rel:HAS_RELATIONSHIP {relationship_type: $rel_type}]->(r)
                        """,
                        {
                            "name": name,
                            "related_name": related_name_lower,
                            "rel_type": rel_type,
                        },
                    )

            conn.execute("COMMIT;")
            results.append({"name": name, "success": True})
        except Exception as e:
            conn.execute("ROLLBACK;")
            results.append({"name": name, "success": False, "error": str(e)})

    return results


async def build_graph(
    db_path: str,
    limit: int = 1000,
    batch_size: int = 20,
    pypi_semaphore: int = 20,
    llm_semaphore: int = 5,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Build the package relationship graph.

    Args:
        db_path: Path to the Kuzu database
        limit: Maximum number of packages to process
        batch_size: Number of packages per LLM batch
        pypi_semaphore: Concurrency limit for PyPI fetches
        llm_semaphore: Concurrency limit for LLM calls
        api_key: Optional Anthropic API key

    Returns:
        List of processing results
    """
    db = get_database(db_path)

    async with httpx.AsyncClient() as client:
        extractor = RelationshipExtractor(api_key=api_key)

        package_names = await fetch_top_packages(client, limit)
        print(f"Fetched {len(package_names)} top packages")

        all_results = []
        semaphore_pypi = asyncio.Semaphore(pypi_semaphore)
        semaphore_llm = asyncio.Semaphore(llm_semaphore)

        for i in range(0, len(package_names), batch_size):
            batch = package_names[i : i + batch_size]
            print(f"Processing batch {i // batch_size + 1}: {len(batch)} packages")

            batch_results = await process_batch(
                extractor, db, client, batch, semaphore_llm, semaphore_pypi
            )
            all_results.extend(batch_results)

            await asyncio.sleep(0.1)

        return all_results


async def rebuild_graph(
    db_path: str,
    limit: int = 1000,
    batch_size: int = 20,
    pypi_semaphore: int = 20,
    llm_semaphore: int = 5,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Rebuild the graph from scratch (clears existing data)."""
    db = get_database(db_path)
    conn = kuzu.Connection(db)

    conn.execute("MATCH (n) DETACH DELETE n;")

    return await build_graph(
        db_path=db_path,
        limit=limit,
        batch_size=batch_size,
        pypi_semaphore=pypi_semaphore,
        llm_semaphore=llm_semaphore,
        api_key=api_key,
    )
