"""LLM-based relationship extraction from package metadata."""
import re
from typing import Optional

import httpx


PYPI_URL = "https://pypi.org/pypi/{name}/json"

# Hard-coded relationships (no LLM needed)
_KNOWN_REPLACEMENTS: dict[str, dict] = {
    "langchain-community": {
        "replacement_for": "langchain-core",
        "reason": "Community integrations moved to core package",
    },
    "langchain-openai": {
        "replacement_for": "langchain-openai",
        "reason": "Official OpenAI integration",
    },
    "openai": {
        "replacement_for": "openai",
        "reason": "Official OpenAI SDK",
    },
    "anthropic": {
        "replacement_for": "anthropic",
        "reason": "Official Anthropic SDK",
    },
}

_KNOWN_DEPRECATED: dict[str, str] = {
    "django-rest-framework": "djangorestframework",
    "django-rest-framework-json-api": "djangorestframework-jsonapi",
    "flask-restful": "flask-api",
    "boto": "boto3",
    "boto3": "boto3",
    "botocore": "botocore",
}


def detect_ecosystem(name: str, summary: str, description: str) -> Optional[str]:
    """Detect package ecosystem from name/metadata."""
    text = f"{name} {summary} {description}".lower()

    ecosystems = {
        "llm": ["llm", "lm", "gpt", "claude", "gemini", "mistral", "openai", "anthropic"],
        "web": ["django", "flask", "fastapi", "aiohttp", "sanic", "tornado"],
        "data": ["pandas", "numpy", "scipy", "pyarrow", "polars"],
        "ml": ["torch", "tensorflow", "jax", "sklearn", "xgboost"],
        "db": ["sqlalchemy", "postgres", "mysql", "redis", "mongo", "cassandra"],
        "queue": ["celery", "rabbitmq", "kafka", "redis", "sqs"],
        "auth": ["auth", "oauth", "jwt", "login", "password"],
        "testing": ["pytest", "unittest", "mock", "test"],
    }

    for eco, keywords in ecosystems.items():
        if any(kw in text for kw in keywords):
            return eco

    return None


def extract_relationships_llm(name: str, summary: str, description: str) -> dict:
    """
    Extract package relationships using LLM prompt.

    Returns dict with: deprecated_by, replacement_for, fork_of, wrapper_for
    """
    prompt = f"""Extract package relationships from PyPI metadata. Return JSON only.

Package: {name}
Summary: {summary}
Description: {description[:1000]}

Extract:
1. deprecated_by: package that replaces this one (if this is deprecated)
2. replacement_for: if this package is a replacement, which one does it replace
3. fork_of: if this is a fork of another package
4. wrapper_for: list of packages this wraps/provides unified API for

Rules:
- Return null for relationships not evident from text
- No speculation - only extract what's explicit
- Return JSON with exact keys above

Output:"""

    # For now, return empty - real extraction needs MCP tool call
    return {
        "deprecated_by": None,
        "replacement_for": None,
        "fork_of": None,
        "wrapper_for": [],
    }


def extract_relationships(name: str, summary: str, description: str) -> dict:
    """Extract relationships - uses hard-coded first, falls back to detection."""
    # Check hard-coded replacements
    if name in _KNOWN_REPLACEMENTS:
        return _KNOWN_REPLACEMENTS[name] | {"wrapper_for": []}

    # Check deprecated mappings
    if name in _KNOWN_DEPRECATED:
        return {
            "deprecated_by": _KNOWN_DEPRECATED[name],
            "replacement_for": _KNOWN_DEPRECATED[name],
            "fork_of": None,
            "wrapper_for": [],
        }

    # Detect ecosystem
    ecosystem = detect_ecosystem(name, summary, description)

    # For packages that end with "-community", check for core replacement
    if name.endswith("-community"):
        base = name.replace("-community", "")
        # Check if there's a corresponding -core or main package
        if f"{base}-core" in summary.lower() or f"{base}.core" in summary.lower():
            return {
                "deprecated_by": f"{base}-core",
                "replacement_for": f"{base}-core",
                "fork_of": None,
                "wrapper_for": [],
            }

    return {
        "deprecated_by": None,
        "replacement_for": None,
        "fork_of": None,
        "wrapper_for": [],
    }


async def fetch_pypi_metadata(name: str) -> dict:
    """Fetch package metadata from PyPI JSON API."""
    if not re.match(r"^[a-zA-Z0-9_\-\.]+$", name):
        return {"name": name, "summary": "", "description": "", "version": None}
    url = PYPI_URL.format(name=name)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                info = data.get("info", {})
                return {
                    "name": name,
                    "summary": info.get("summary", ""),
                    "description": info.get("description", ""),
                    "version": info.get("version"),
                }
    except Exception:
        pass

    return {"name": name, "summary": "", "description": "", "version": None}
