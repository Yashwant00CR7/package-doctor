import re
from typing import Optional

import httpx


PYPI_URL = "https://pypi.org/pypi/{name}/json"

_DEPRECATED_KEYWORDS = [
    "deprecated", "unmaintained", "no longer maintained",
    "use instead", "replaced by", "superseded", "archived",
    "end of life", "eol",
]

_ALTERNATIVE_PATTERNS = [
    re.compile(r"use (?:instead )?[`'\"]?([\w\-]+)[`'\"]?", re.IGNORECASE),
    re.compile(r"replaced by [` '\"]?([\w\-]+)[`'\"]?", re.IGNORECASE),
    re.compile(r"superseded by [` '\"]?([\w\-]+)[`'\"]?", re.IGNORECASE),
    re.compile(r"migrate to [` '\"]?([\w\-]+)[`'\"]?", re.IGNORECASE),
    re.compile(r"switch to [` '\"]?([\w\-]+)[`'\"]?", re.IGNORECASE),
]


def _extract_alternative(text: str) -> Optional[str]:
    for pat in _ALTERNATIVE_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1)
    return None


def _is_deprecated(pypi_data: dict) -> tuple[bool, Optional[str], Optional[str]]:
    """Returns (is_deprecated, message, alternative)."""
    info = pypi_data.get("info", {})

    # Check classifiers
    classifiers = info.get("classifiers", [])
    for c in classifiers:
        if "inactive" in c.lower() or "abandoned" in c.lower():
            return True, c, None

    # Check description and summary for deprecation keywords
    summary = info.get("summary", "") or ""
    description = info.get("description", "") or ""
    combined = f"{summary}\n{description}"

    for kw in _DEPRECATED_KEYWORDS:
        if kw in combined.lower():
            # Try to find alternative
            alt = _extract_alternative(combined)
            msg = summary if kw in summary.lower() else f"Package mentions: {kw}"
            return True, msg, alt

    return False, None, None


async def fetch_package_info(name: str) -> Optional[dict]:
    """Fetch package metadata from PyPI. Returns None on network failure."""
    if not re.match(r"^[a-zA-Z0-9_\-\.]+$", name):
        return {"name": name, "status": "not_found", "latest_version": None}
    url = PYPI_URL.format(name=name)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return {"name": name, "status": "not_found", "latest_version": None}
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, Exception):
        return None

    info = data.get("info", {})
    releases = data.get("releases", {})

    latest_version = info.get("version")
    python_requires = info.get("requires_python")

    # Find latest release date
    last_release_date = None
    if latest_version and latest_version in releases:
        files = releases[latest_version]
        if files:
            upload_times = [f.get("upload_time") for f in files if f.get("upload_time")]
            if upload_times:
                last_release_date = max(upload_times)

    is_dep, dep_msg, alt = _is_deprecated(data)

    return {
        "name": name.lower(),
        "latest_version": latest_version,
        "status": "deprecated" if is_dep else "active",
        "deprecation_message": dep_msg,
        "alternative": alt,
        "python_requires": python_requires,
        "last_release_date": last_release_date,
        "raw_pypi": {"info": info},
    }
