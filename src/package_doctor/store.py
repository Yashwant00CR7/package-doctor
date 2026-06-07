import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


DB_PATH = Path.home() / ".package-doctor" / "cache.db"
PACKAGE_TTL_HOURS = 24
MODEL_TTL_HOURS = 6


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_stale(fetched_at: Optional[str], ttl_hours: int) -> bool:
    if not fetched_at:
        return True
    try:
        ts = datetime.fromisoformat(fetched_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - ts > timedelta(hours=ttl_hours)
    except ValueError:
        return True


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS packages (
            name TEXT PRIMARY KEY,
            latest_version TEXT,
            status TEXT DEFAULT 'active',
            deprecation_message TEXT,
            alternative TEXT,
            python_requires TEXT,
            last_release_date TEXT,
            fetched_at TEXT,
            raw_pypi TEXT
        );

        CREATE TABLE IF NOT EXISTS model_versions (
            model_id TEXT PRIMARY KEY,
            provider TEXT,
            status TEXT,
            eol_date TEXT,
            successor_model TEXT,
            checked_at TEXT,
            source_url TEXT
        );
    """)
    conn.commit()


def get_cached_package(name: str, conn: sqlite3.Connection) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM packages WHERE name = ?", (name.lower(),)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    if _is_stale(d.get("fetched_at"), PACKAGE_TTL_HOURS):
        return None
    return d


def upsert_package(data: dict, conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO packages
            (name, latest_version, status, deprecation_message, alternative,
             python_requires, last_release_date, fetched_at, raw_pypi)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data.get("name", "").lower(),
            data.get("latest_version"),
            data.get("status", "active"),
            data.get("deprecation_message"),
            data.get("alternative"),
            data.get("python_requires"),
            data.get("last_release_date"),
            _utcnow(),
            json.dumps(data.get("raw_pypi")) if data.get("raw_pypi") else None,
        ),
    )
    conn.commit()


def get_cached_model(model_id: str, conn: sqlite3.Connection) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM model_versions WHERE model_id = ?", (model_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    if _is_stale(d.get("checked_at"), MODEL_TTL_HOURS):
        return None
    # Auto-upgrade status if eol_date has passed
    if d.get("eol_date") and d.get("status") == "warning":
        try:
            eol = datetime.fromisoformat(d["eol_date"])
            if eol.tzinfo is None:
                eol = eol.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= eol:
                d["status"] = "error"
        except ValueError:
            pass
    return d


def upsert_model(data: dict, conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO model_versions
            (model_id, provider, status, eol_date, successor_model, checked_at, source_url)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data.get("model_id"),
            data.get("provider"),
            data.get("status"),
            data.get("eol_date"),
            data.get("successor_model"),
            _utcnow(),
            data.get("source_url"),
        ),
    )
    conn.commit()
