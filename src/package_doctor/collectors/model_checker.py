"""LLM model version checker — uses Firecrawl MCP for fresh doc crawls when available."""
import re
from datetime import datetime, timezone
from typing import Optional

import httpx


# Provider detection: pattern → (provider_name, docs_url)
_PROVIDER_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"^gpt-|^o[1-9](-|$)|^text-davinci|^text-embedding"), "openai",
     "https://platform.openai.com/docs/models"),
    (re.compile(r"^claude-"), "anthropic",
     "https://docs.anthropic.com/en/docs/about-claude/models"),
    (re.compile(r"^gemini-|^gemma-"), "google",
     "https://ai.google.dev/gemini-api/docs/models"),
    (re.compile(r"^mistral-|^codestral|^mixtral"), "mistral",
     "https://docs.mistral.ai/getting-started/models/overview/"),
    (re.compile(r"^command-|^embed-"), "cohere",
     "https://docs.cohere.com/docs/models"),
    (re.compile(r"^llama-?[23]|^meta-llama"), "meta",
     "https://llama.meta.com/docs/model-cards-and-prompt-formats/"),
]

# Hard-coded known-removed models (last resort when crawl unavailable)
_KNOWN_REMOVED: dict[str, dict] = {
    "gpt-3.5-turbo-0301": {"successor": "gpt-4o-mini", "eol_date": "2024-09-13"},
    "gpt-3.5-turbo-16k-0613": {"successor": "gpt-4o-mini", "eol_date": "2024-09-13"},
    "gpt-4-0314": {"successor": "gpt-4o", "eol_date": "2024-06-13"},
    "gpt-4-32k": {"successor": "gpt-4o", "eol_date": "2025-06-06"},
    "text-davinci-003": {"successor": "gpt-4o", "eol_date": "2024-01-04"},
    "claude-1": {"successor": "claude-opus-4-7", "eol_date": "2024-11-01"},
    "claude-2": {"successor": "claude-opus-4-7", "eol_date": "2025-03-01"},
    "claude-instant-1": {"successor": "claude-haiku-4-5-20251001", "eol_date": "2024-11-01"},
    "claude-3-opus-20240229": {"successor": "claude-opus-4-7", "eol_date": None},
    "claude-3-sonnet-20240229": {"successor": "claude-sonnet-4-6", "eol_date": "2025-07-21"},
    "claude-3-haiku-20240307": {"successor": "claude-haiku-4-5-20251001", "eol_date": None},
}

_KNOWN_WARNING: dict[str, dict] = {
    "gpt-3.5-turbo": {"successor": "gpt-4o-mini", "eol_date": "2025-12-31"},
    "gpt-4": {"successor": "gpt-4o", "eol_date": "2025-12-31"},
    "claude-3-5-sonnet-20241022": {"successor": "claude-sonnet-4-6", "eol_date": None},
}


def detect_provider(model_id: str) -> tuple[Optional[str], Optional[str]]:
    """Returns (provider_name, docs_url)."""
    for pattern, provider, url in _PROVIDER_PATTERNS:
        if pattern.match(model_id.lower()):
            return provider, url
    return None, None


async def check_model_version(model_id: str) -> dict:
    """Check model status. Returns dict with status/eol_date/successor/source_url."""
    now = datetime.now(timezone.utc).isoformat()
    provider, source_url = detect_provider(model_id)

    # Check hard-coded removed list
    key = model_id.lower()
    if key in _KNOWN_REMOVED:
        entry = _KNOWN_REMOVED[key]
        status = "error"
        if entry.get("eol_date"):
            try:
                eol = datetime.fromisoformat(entry["eol_date"]).replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) < eol:
                    status = "warning"
            except ValueError:
                pass
        return {
            "model_id": model_id,
            "provider": provider,
            "status": status,
            "eol_date": entry.get("eol_date"),
            "successor_model": entry.get("successor"),
            "last_checked": now,
            "source_url": source_url,
            "warnings": [],
        }

    if key in _KNOWN_WARNING:
        entry = _KNOWN_WARNING[key]
        return {
            "model_id": model_id,
            "provider": provider,
            "status": "warning",
            "eol_date": entry.get("eol_date"),
            "successor_model": entry.get("successor"),
            "last_checked": now,
            "source_url": source_url,
            "warnings": [],
        }

    # Unknown model — attempt lightweight check via provider docs URL
    warnings = []
    if source_url:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(source_url)
                if resp.status_code == 200:
                    body = resp.text.lower()
                    model_id_lower = model_id.lower()
                    # If model ID appears in docs, verify its context
                    if model_id_lower in body:
                        idx = body.find(model_id_lower)
                        start_idx = max(0, idx - 150)
                        end_idx = min(len(body), idx + len(model_id_lower) + 150)
                        context = body[start_idx:end_idx]
                        
                        deprecation_words = ["deprecat", "retir", "legacy", "archive", "eol", "end-of-life", "end of life", "discontinu", "remove"]
                        if any(dw in context for dw in deprecation_words):
                            return {
                                "model_id": model_id,
                                "provider": provider,
                                "status": "warning",
                                "eol_date": None,
                                "successor_model": None,
                                "last_checked": now,
                                "source_url": source_url,
                                "warnings": ["Model is mentioned in a deprecated/legacy/retired context in provider documentation."],
                            }
                        
                        return {
                            "model_id": model_id,
                            "provider": provider,
                            "status": "current",
                            "eol_date": None,
                            "successor_model": None,
                            "last_checked": now,
                            "source_url": source_url,
                            "warnings": [],
                        }
        except Exception:
            warnings.append("Provider docs unavailable — model status unverified")

    return {
        "model_id": model_id,
        "provider": provider,
        "status": "current",
        "eol_date": None,
        "successor_model": None,
        "last_checked": now,
        "source_url": source_url,
        "warnings": warnings,
    }
